"""Tiện ích cho thread hỏi đáp (nhiều cuộc trò chuyện trong một buổi học)."""

from __future__ import annotations

TITLE_MAX = 60


def thread_title(question: str) -> str:
    """Tiêu đề tự động: dòng đầu của câu hỏi, gọn khoảng trắng, cắt ≤ ``TITLE_MAX`` ký tự (không tốn lượt gọi LLM)."""
    first = next((ln for ln in question.splitlines() if ln.strip()), "")
    text = " ".join(first.split())
    if len(text) <= TITLE_MAX:
        return text
    cut = text[: TITLE_MAX - 1].rsplit(" ", 1)[0] or text[: TITLE_MAX - 1]
    return cut.rstrip(" ,.;:") + "…"
