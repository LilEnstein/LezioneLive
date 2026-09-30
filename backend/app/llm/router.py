"""Chọn đường gọi Claude cho trợ lý học tập (hỏi đáp, tóm tắt, flashcard…): Anthropic API hoặc gói Pro qua Claude Code.

Có cùng giao diện với :class:`app.llm.client.LLMClient` ở những chỗ các service dùng (``stream``, ``json``,
``count_tokens``, ``has_key``, ``usage``) nên AssistantService/Summarizer/FlashcardService không cần biết đang
đi đường nào.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.llm.client import LLMClient, LLMError

if TYPE_CHECKING:
    from app.llm.claude_code import ClaudeCodeClient
    from app.services.settings_store import SettingsStore

# Hỏi đáp / tóm tắt với model lớn + effort cao có thể mất vài phút qua CLI.
CLAUDE_CODE_TIMEOUT_S = 900.0


class AssistantLLM:
    def __init__(self, api: LLMClient, claude_code: ClaudeCodeClient | None, settings: SettingsStore) -> None:
        self.api = api
        self.claude_code = claude_code
        self._settings = settings

    # ------------------------------------------------------------- chọn đường
    @property
    def uses_claude_code(self) -> bool:
        return self.claude_code is not None and self._settings.get().assistant_provider == "claude_code"

    @property
    def has_key(self) -> bool:
        """Dùng được trợ lý chưa (API key, hoặc Claude Code khi chọn gói Pro)."""
        if self.uses_claude_code:
            return self.claude_code.available  # type: ignore[union-attr]
        return self.api.has_key

    @property
    def supports_documents(self) -> bool:
        """Claude Code không nhận block PDF: slide gửi dạng text trích từ PDF."""
        return not self.uses_claude_code

    @property
    def usage(self) -> Any:
        return self.api.usage

    def _effort(self) -> str | None:
        effort = self._settings.get().assistant_effort
        return None if effort == "auto" else effort

    def _cc_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        settings = self._settings.get()
        if kwargs.get("model") == settings.assistant_model:
            kwargs["model"] = settings.assistant_cc_model  # model trợ lý chọn riêng cho Claude Code
        kwargs.setdefault("timeout", CLAUDE_CODE_TIMEOUT_S)
        return kwargs

    # -------------------------------------------------------------- gọi model
    async def stream(self, **kwargs: Any) -> Any:
        if self.uses_claude_code:
            kwargs = self._cc_kwargs(kwargs)
            return await self.claude_code.stream(effort=self._effort(), **kwargs)  # type: ignore[union-attr]
        return await self.api.stream(**kwargs)

    async def json(self, **kwargs: Any) -> Any:
        if self.uses_claude_code:
            kwargs = self._cc_kwargs(kwargs)
            return await self.claude_code.json(effort=self._effort(), **kwargs)  # type: ignore[union-attr]
        return await self.api.json(**kwargs)

    async def count_tokens(self, **kwargs: Any) -> int:
        if self.uses_claude_code:
            raise LLMError("bad_request", "Claude Code không hỗ trợ đếm token, dùng ước lượng.")
        return await self.api.count_tokens(**kwargs)
