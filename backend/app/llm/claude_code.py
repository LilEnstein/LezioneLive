"""Dịch bằng gói Claude Pro/Max qua Claude Code CLI (``claude -p``) thay cho Anthropic API.

Dùng hạn mức gói đăng ký của người dùng trên chính máy họ (không cần API key / credit). Mỗi lần gọi khởi động một
tiến trình ``claude`` không tool, không MCP, không đọc cài đặt dự án; system prompt truyền qua file để không vướng giới
hạn độ dài dòng lệnh. Giao diện ``stream``/``json`` giống :class:`app.llm.client.LLMClient` (phần Translator dùng).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.llm.client import LLMError, LLMResult, extract_json

log = logging.getLogger(__name__)

# Biến môi trường khiến CLI dùng API key (đang hết credit) thay vì đăng nhập gói Pro.
_STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_AUTH = re.compile(
    r"failed to authenticate|not logged in|please run .*login|invalid api key|oauth|/login", re.I
)
_QUOTA = re.compile(r"usage limit|limit reached|limit will reset|out of (extra )?usage|credit balance", re.I)
_BUSY = re.compile(r"overloaded|529|rate limit|429|5\d\d|timed? ?out", re.I)


def find_claude_exe() -> str | None:
    """Tìm file thực thi ``claude`` (ưu tiên bản native; bản npm trên Windows có ``claude.exe`` trong node_modules)."""
    override = os.environ.get("LEZIONE_CLAUDE_PATH")
    if override and Path(override).exists():
        return override
    if sys.platform == "win32":
        exe = shutil.which("claude.exe")
        if exe:
            return exe
        shim = shutil.which("claude.cmd") or shutil.which("claude")
        if shim:
            native = (
                Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            )
            if native.exists():
                return str(native)
        return None
    return shutil.which("claude")


def _system_text(system: str | list[dict[str, Any]] | None) -> str:
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")


def _content_text(content: str | list[dict[str, Any]], images: list[dict[str, Any]] | None = None) -> str:
    """Text của một tin nhắn. Block ảnh được gom vào ``images`` và thay bằng nhãn "[Ảnh k]" tại chỗ."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif b.get("type") == "image":
            if images is None:
                parts.append("(ảnh)")
            else:
                images.append({"type": "image", "source": b["source"]})
                parts.append(f"[Ảnh {len(images)}]")
        elif b.get("type") == "document":
            # CLI -p không nhận PDF; phía gọi đã gửi text trích từ slide khi có thể.
            parts.append(f'(Tài liệu "{b.get("title", "PDF")}" không gửi kèm được qua Claude Code.)')
    return "\n\n".join(parts)


def _user_text(messages: list[dict[str, Any]], images: list[dict[str, Any]] | None = None) -> str:
    """Gộp hội thoại nhiều lượt thành một prompt (CLI -p chỉ nhận một tin nhắn người dùng)."""
    if len(messages) == 1:
        return _content_text(messages[0]["content"], images)
    turns = []
    for m in messages[:-1]:
        tag = "user" if m["role"] == "user" else "assistant"
        turns.append(f"<{tag}>\n{_content_text(m['content'], images)}\n</{tag}>")
    return (
        "Dưới đây là các lượt trước của cuộc hội thoại (user = học viên, assistant = bạn).\n"
        "<conversation>\n" + "\n".join(turns) + "\n</conversation>\n\n"
        "Tin nhắn mới nhất của học viên — hãy trả lời tin nhắn này:\n"
        + _content_text(messages[-1]["content"], images)
    )


def _user_content(messages: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Prompt cho CLI: text thuần, hoặc (khi có ảnh) các cặp nhãn "[Ảnh k]" + ảnh rồi tới text."""
    images: list[dict[str, Any]] = []
    text = _user_text(messages, images)
    if not images:
        return text
    blocks: list[dict[str, Any]] = []
    for k, img in enumerate(images, 1):
        blocks += [{"type": "text", "text": f"[Ảnh {k}]"}, img]
    blocks.append({"type": "text", "text": text})
    return blocks


def classify_cli_error(text: str) -> LLMError:
    if _QUOTA.search(text):
        # Như lỗi key: hàng đợi chuyển sang dịch nháp, không thử lại từng câu cho tới khi người dùng bấm thử lại.
        return LLMError("auth", f"Hết hạn mức gói Claude cho model này (Claude Code): {text[:200]}")
    if _AUTH.search(text):
        return LLMError("auth", "Claude Code chưa đăng nhập — chạy lệnh `claude auth login` trong terminal.")
    if _BUSY.search(text):
        return LLMError("server", f"Claude Code tạm lỗi: {text[:200]}")
    return LLMError("bad_request", f"Claude Code lỗi: {text[:200]}")


class ClaudeCodeClient:
    def __init__(self, workdir: Path, exe_finder: Callable[[], str | None] = find_claude_exe) -> None:
        # Thư mục trống riêng: CLI không nhặt CLAUDE.md / cài đặt của dự án nào.
        self.workdir = workdir
        self._find = exe_finder
        self._exe: str | None = None

    @property
    def exe(self) -> str | None:
        if self._exe is None or not Path(self._exe).exists():
            self._exe = self._find()
        return self._exe

    @property
    def available(self) -> bool:
        return self.exe is not None

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
        env["PYTHONUTF8"] = "1"
        return env

    def auth_status(self) -> dict[str, Any]:
        exe = self.exe
        if exe is None:
            return {"installed": False, "logged_in": False}
        try:
            out = subprocess.run(
                [exe, "auth", "status"],
                capture_output=True,
                timeout=20,
                env=self._env(),
                creationflags=_NO_WINDOW,
                encoding="utf-8",
                errors="replace",
            )
            data = json.loads(out.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            return {"installed": True, "logged_in": False, "error": str(e)}
        return {
            "installed": True,
            "logged_in": bool(data.get("loggedIn")),
            "auth_method": data.get("authMethod"),
            "subscription": data.get("subscriptionType"),
        }

    def _run(
        self,
        *,
        model: str,
        system: str,
        prompt: str | list[dict[str, Any]],
        timeout: float,
        on_text: Callable[[str], None] | None,
        effort: str | None = None,
    ) -> tuple[str, str]:
        """Chạy ``claude -p`` một lượt. Trả về (text, model thực tế CLI đã dùng)."""
        exe = self.exe
        if exe is None:
            raise LLMError("no_key", "Không tìm thấy Claude Code (lệnh `claude`) trên máy.")
        self.workdir.mkdir(parents=True, exist_ok=True)
        fd, sp_path = tempfile.mkstemp(prefix="sys-", suffix=".txt", dir=self.workdir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(system or "Bạn là trợ lý dịch thuật.")
        args = [
            exe, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model,
            "--system-prompt-file", sp_path,
            "--tools", "",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]  # fmt: skip
        if effort:
            args += ["--effort", effort]
        if not isinstance(prompt, str):
            # Có ảnh: gửi một tin nhắn người dùng dạng stream-json (content block như Messages API).
            args += ["--input-format", "stream-json"]
            message = {"type": "user", "message": {"role": "user", "content": prompt}}
            prompt = json.dumps(message, ensure_ascii=False) + "\n"
        try:
            proc = subprocess.Popen(
                args,
                cwd=self.workdir,
                env=self._env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as e:
            os.unlink(sp_path)
            raise LLMError("no_key", f"Không chạy được Claude Code: {e}") from e

        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        streamed: list[str] = []
        final: dict[str, Any] | None = None
        used_model = model
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "stream_event":
                    delta = (ev.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        streamed.append(delta["text"])
                        if on_text is not None:
                            on_text(delta["text"])
                elif ev.get("type") == "system" and ev.get("subtype") == "init" and ev.get("model"):
                    used_model = str(ev["model"])
                elif ev.get("type") == "result":
                    final = ev
            proc.wait(timeout=10)
            stderr = proc.stderr.read() if proc.stderr else ""
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
            try:
                os.unlink(sp_path)
            except OSError:
                pass

        if final is None:
            if stderr.strip():
                raise classify_cli_error(stderr.strip())
            raise LLMError(
                "server",
                f"Claude Code không trả kết quả (quá {timeout:.0f}s hoặc thoát mã {proc.returncode}).",
            )
        text = str(final.get("result") or "")
        if final.get("is_error") or final.get("subtype") not in (None, "success"):
            raise classify_cli_error(text or stderr)
        return text or "".join(streamed), used_model

    async def stream(
        self,
        *,
        purpose: str,
        session_id: int | None,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
        timeout: float | None = None,
        effort: str | None = None,
        **_: Any,
    ) -> LLMResult:
        loop = asyncio.get_running_loop()
        cb = (lambda s: loop.call_soon_threadsafe(on_text, s)) if on_text is not None else None
        text, used_model = await asyncio.to_thread(
            self._run,
            model=model,
            system=_system_text(system),
            prompt=_user_content(messages),
            timeout=timeout or 90.0,
            on_text=cb,
            effort=effort,
        )
        # Dùng hạn mức gói đăng ký: không ghi chi phí API.
        return LLMResult(text=text, model=used_model, stop_reason="end_turn")

    async def ping(self, model: str, effort: str | None = None) -> dict[str, Any]:
        """Nút "Thử model": gọi một câu rất ngắn, báo model thật CLI dùng và thời gian phản hồi."""
        started = time.perf_counter()
        result = await self.stream(
            purpose="ping",
            session_id=None,
            model=model,
            max_tokens=20,
            system="Trả lời cực ngắn bằng tiếng Việt.",
            messages=[{"role": "user", "content": "Chào bạn, trả lời đúng một từ: sẵn sàng"}],
            timeout=120.0,
            effort=effort,
        )
        return {
            "ok": True,
            "model": result.model,
            "reply": result.text.strip()[:100],
            "seconds": round(time.perf_counter() - started, 1),
        }

    async def json(
        self,
        *,
        purpose: str,
        session_id: int | None,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        system: str | list[dict[str, Any]] | None = None,
        on_text: Callable[[str], None] | None = None,
        timeout: float | None = None,
        effort: str | None = None,
        **_: Any,
    ) -> tuple[Any, LLMResult]:
        hint = "\n\nChỉ trả về JSON hợp lệ đúng schema sau, không kèm chữ nào khác:\n" + json.dumps(
            schema, ensure_ascii=False
        )
        patched = [{"role": "user", "content": _user_text(messages) + hint}]
        result = await self.stream(
            purpose=purpose,
            session_id=session_id,
            model=model,
            max_tokens=max_tokens,
            messages=patched,
            system=system,
            on_text=on_text,
            timeout=timeout or 180.0,
            effort=effort,
        )
        try:
            return extract_json(result.text), result
        except (ValueError, json.JSONDecodeError) as e:
            raise LLMError("bad_request", "Claude Code trả về JSON không hợp lệ.") from e
