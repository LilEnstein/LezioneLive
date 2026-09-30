"""Dịch Ý → Việt từng segment (FR-TR-01..06, prompt mục 11.1).

Ba đường dịch theo cài đặt ``translate_provider``: Claude API (Haiku 4.5), gói Claude Pro/Max qua Claude Code, hoặc
model NLLB trên máy (``local`` — miễn phí, không cần mạng, chất lượng thấp hơn).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.llm.client import LLMClient, LLMError
from app.llm.prompt_library import PromptLibrary

if TYPE_CHECKING:
    from app.llm.claude_code import ClaudeCodeClient
    from app.offline.draft_translator import DraftTranslator
    from app.services.settings_store import SettingsStore

EN_MARKER = "<<<EN>>>"
GLOSSARY_PROMPT_LIMIT = 400


@dataclass
class GlossaryEntry:
    term_it: str
    term_vi: str
    term_en: str | None = None
    note: str | None = None


@dataclass
class TranslationContext:
    course_name: str
    glossary: list[GlossaryEntry] = field(default_factory=list)
    previous: list[tuple[str, str | None]] = field(default_factory=list)  # (it, vi)
    slide_summary: str | None = None
    secondary_language: str = "none"
    # Chế độ lớp học trực tiếp (prompt translate_inclass_v1, feature_spec mục 9.1)
    inclass: bool = False
    speaker: str = "unknown"  # lecturer | other | unknown
    recent_board_text: str | None = None


SPEAKER_LABELS = {
    "lecturer": "lecturer (giảng viên)",
    "other": "other (sinh viên hỏi)",
    "unknown": "chưa xác định (thường là giảng viên)",
}


@dataclass
class TranslationOutput:
    vi: str
    en: str | None = None
    kind: str = "claude"  # claude | local (lưu vào Segment.translation_kind)


def format_glossary(entries: list[GlossaryEntry], limit: int = GLOSSARY_PROMPT_LIMIT) -> str:
    if not entries:
        return "(chưa có thuật ngữ)"
    lines = []
    for e in entries[:limit]:
        line = f"- {e.term_it} → {e.term_vi}"
        if e.term_en:
            line += f" (EN: {e.term_en})"
        if e.note:
            line += f" — {e.note}"
        lines.append(line)
    return "\n".join(lines)


def format_previous(previous: list[tuple[str, str | None]]) -> str:
    if not previous:
        return "(chưa có, đây là câu đầu tiên)"
    parts = []
    for it, vi in previous:
        parts.append(f"[IT] {it}")
        parts.append(f"[VI] {vi}" if vi else "[VI] (chưa dịch)")
    return "\n".join(parts)


_QUOTES = "\"'“”„«»‘’`"


def clean_translation(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] in _QUOTES and text[-1] in _QUOTES:
        text = text[1:-1].strip()
    return re.sub(r"[ \t]+\n", "\n", text)


class DualStreamSplitter:
    """Tách luồng text thành phần tiếng Việt (trước ``<<<EN>>>``) và tiếng Anh (sau)."""

    def __init__(self, marker: str = EN_MARKER) -> None:
        self.marker = marker
        self._buf = ""
        self._in_en = False
        self._vi: list[str] = []
        self._en: list[str] = []

    def _partial_suffix(self) -> int:
        for k in range(min(len(self.marker) - 1, len(self._buf)), 0, -1):
            if self.marker.startswith(self._buf[-k:]):
                return k
        return 0

    def feed(self, chunk: str) -> str:
        """Trả về phần tiếng Việt có thể hiển thị ngay."""
        if self._in_en:
            self._en.append(chunk)
            return ""
        self._buf += chunk
        idx = self._buf.find(self.marker)
        if idx >= 0:
            vi_part, en_part = self._buf[:idx], self._buf[idx + len(self.marker) :]
            self._buf = ""
            self._in_en = True
            self._vi.append(vi_part)
            self._en.append(en_part)
            return vi_part
        keep = self._partial_suffix()
        emit = self._buf[: len(self._buf) - keep]
        self._buf = self._buf[len(self._buf) - keep :]
        self._vi.append(emit)
        return emit

    def finish(self) -> str:
        rest, self._buf = self._buf, ""
        if not self._in_en:
            self._vi.append(rest)
            return rest
        return ""

    @property
    def vi(self) -> str:
        return "".join(self._vi)

    @property
    def en(self) -> str | None:
        text = "".join(self._en).strip()
        return text or None


def _batch_schema(with_en: bool) -> dict[str, Any]:
    props: dict[str, Any] = {"n": {"type": "integer"}, "vi": {"type": "string"}}
    required = ["n", "vi"]
    if with_en:
        props["en"] = {"type": "string"}
        required.append("en")
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


class Translator:
    def __init__(
        self,
        llm: LLMClient,
        prompts: PromptLibrary,
        settings: SettingsStore,
        claude_code: ClaudeCodeClient | None = None,
        local: DraftTranslator | None = None,
    ) -> None:
        self._api = llm
        self._prompts = prompts
        self._settings = settings
        self.claude_code = claude_code
        self.local = local

    def _uses_claude_code(self) -> bool:
        return self.claude_code is not None and self._settings.get().translate_provider == "claude_code"

    @property
    def engine(self) -> str:
        """Đường dịch đang dùng: ``api`` | ``claude_code`` | ``local``."""
        provider = self._settings.get().translate_provider
        if provider == "local" and self.local is not None:
            return "local"
        return "claude_code" if self._uses_claude_code() else "api"

    @property
    def _llm(self) -> Any:
        return self.claude_code if self._uses_claude_code() else self._api

    @property
    def ready(self) -> bool:
        """Có đường dịch dùng được (API key; Claude Code khi chọn gói Pro; model cục bộ đã tải khi chọn máy)."""
        if self.engine == "local":
            return self.local.ready or self.local.cached()  # type: ignore[union-attr]
        if self._uses_claude_code():
            return self.claude_code.available  # type: ignore[union-attr]
        return self._api.has_key

    async def _translate_local(self, texts: list[str], ctx: TranslationContext) -> list[TranslationOutput]:
        local = self.local
        assert local is not None
        if not local.ready and not await asyncio.shield(local.start_prepare(download=False)):
            raise LLMError(
                "no_key",
                local.error or "Chưa tải model dịch cục bộ — mở menu Dịch trên màn hình học để tải.",
            )
        try:
            pairs = await local.translate_many(
                texts,
                glossary=[(g.term_it, g.term_vi) for g in ctx.glossary],
                with_en=ctx.secondary_language == "en",
            )
        except Exception as e:  # noqa: BLE001
            raise LLMError("bad_request", f"Model dịch cục bộ lỗi: {type(e).__name__}: {e}") from e
        return [TranslationOutput(vi, en, kind="local") for vi, en in pairs]

    def build_system(self, ctx: TranslationContext) -> list[dict[str, Any]]:
        secondary_rule = (
            "6. Sau bản dịch tiếng Việt, xuống dòng và viết đúng một dòng <<<EN>>>, rồi viết bản dịch\n"
            "   tiếng Anh tương ứng (cùng các quy tắc trên).\n"
            if ctx.secondary_language == "en"
            else ""
        )
        stable, volatile = self._prompts.render_parts(
            "translate_inclass_v1" if ctx.inclass else "translate_v1",
            course_name=ctx.course_name or "(chưa đặt tên)",
            glossary_table=format_glossary(ctx.glossary),
            slide_summary=ctx.slide_summary or "(không có)",
            secondary_rule=secondary_rule,
            previous_segments_it_vi=format_previous(ctx.previous),
            recent_board_text=ctx.recent_board_text or "(không có)",
            speaker_label=SPEAKER_LABELS.get(ctx.speaker, ctx.speaker),
        )
        # Phần ổn định (quy tắc + thuật ngữ + slide) đặt breakpoint cache; chỉ có tác dụng khi đủ dài
        # (Haiku 4.5 cần >= 4096 token), ngắn hơn thì API tự bỏ qua, không tốn thêm.
        blocks: list[dict[str, Any]] = [{"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}}]
        if volatile:
            blocks.append({"type": "text", "text": volatile})
        return blocks

    async def translate_stream(
        self,
        text_it: str,
        ctx: TranslationContext,
        *,
        session_id: int | None,
        on_delta: Callable[[str], None] | None = None,
    ) -> TranslationOutput:
        if self.engine == "local":
            return (await self._translate_local([text_it], ctx))[0]
        settings = self._settings.get()
        with_en = ctx.secondary_language == "en"
        splitter = DualStreamSplitter() if with_en else None

        def handle(chunk: str) -> None:
            out = splitter.feed(chunk) if splitter is not None else chunk
            if out and on_delta is not None:
                on_delta(out)

        result = await self._llm.stream(
            purpose="translate",
            session_id=session_id,
            model=settings.translate_model,
            max_tokens=800 if with_en else 400,
            system=self.build_system(ctx),
            messages=[{"role": "user", "content": text_it}],
            route="fast",
            temperature=0.2,
            on_text=handle,
            max_retries=0,  # hàng đợi dịch tự backoff (mục 10.2)
            timeout=45.0,
        )
        if splitter is not None:
            rest = splitter.finish()
            if rest and on_delta is not None:
                on_delta(rest)
            return TranslationOutput(clean_translation(splitter.vi), clean_translation(splitter.en or "") or None)
        return TranslationOutput(clean_translation(result.text))

    async def translate_batch(
        self,
        texts: list[str],
        ctx: TranslationContext,
        *,
        session_id: int | None,
        speakers: list[str] | None = None,
    ) -> list[TranslationOutput]:
        """Gộp nhiều segment thành 1 request khi hàng đợi dài (đuổi kịp sau khi mạng chậm/mất)."""
        if self.engine == "local":
            return await self._translate_local(texts, ctx)
        settings = self._settings.get()
        with_en = ctx.secondary_language == "en"
        if ctx.inclass and speakers:
            # Người nói từng câu ghi ngay trong danh sách (prompt trong lớp đọc nhãn "(sinh viên hỏi)").
            ctx = TranslationContext(**{**ctx.__dict__, "speaker": "xem nhãn ở từng câu"})
            numbered = "\n".join(
                f"[{i + 1}] {'(sinh viên hỏi) ' if sp == 'other' else ''}{t}"
                for i, (t, sp) in enumerate(zip(texts, speakers, strict=True))
            )
        else:
            numbered = "\n".join(f"[{i + 1}] {t}" for i, t in enumerate(texts))
        user = self._prompts.render(
            "translate_batch_v1",
            count=len(texts),
            numbered_segments=numbered,
            en_hint=', "en" (bản dịch tiếng Anh)' if with_en else "",
        )
        data, _ = await self._llm.json(
            purpose="translate",
            session_id=session_id,
            model=settings.translate_model,
            max_tokens=min(8000, 350 * len(texts) * (2 if with_en else 1) + 300),
            system=self.build_system(ctx),
            messages=[{"role": "user", "content": user}],
            schema=_batch_schema(with_en),
            route="fast",
        )
        by_n: dict[int, TranslationOutput] = {}
        for item in data.get("items", []) if isinstance(data, dict) else []:
            try:
                n = int(item["n"])
            except (KeyError, TypeError, ValueError):
                continue
            vi = clean_translation(str(item.get("vi", "")))
            if vi:
                en = clean_translation(str(item.get("en", ""))) or None if with_en else None
                by_n[n] = TranslationOutput(vi, en)
        missing = [i + 1 for i in range(len(texts)) if (i + 1) not in by_n]
        if missing:
            raise LLMError("bad_request", f"Bản dịch gộp thiếu câu số {missing}")
        return [by_n[i + 1] for i in range(len(texts))]
