"""Ngữ cảnh trợ lý (Flow C): prompt caching theo khối transcript, lịch sử chat, rolling summary."""

from __future__ import annotations

import json

from sqlmodel import select

from app.db.models import ChatMessage, Course, LectureSession, Segment, TranscriptBlockSummary
from app.db.session import db_session
from app.llm import assistant as assistant_mod
from app.llm.assistant import TRANSCRIPT_CHUNK, split_chunks


def seed(ctx, n: int, *, step_ms: int = 6000, status: str = "translated") -> int:
    with db_session(ctx.engine) as s:
        course = Course(name="Statistica", name_it="Statistica Multivariata", lecturer="Prof. Rossi")
        s.add(course)
        s.commit()
        s.refresh(course)
        sess = LectureSession(title="Lezione 1", course_id=course.id)
        s.add(sess)
        s.commit()
        s.refresh(sess)
        for i in range(n):
            s.add(Segment(session_id=sess.id, seq=i + 1, t_start_ms=i * step_ms, t_end_ms=i * step_ms + 5000,
                          text_it=f"frase {i + 1}", text_vi=f"câu {i + 1}", status=status,
                          confidence=0.3 if i == 2 else 0.9))
        s.commit()
    return sess.id


def add_segments(ctx, sid: int, start: int, n: int, step_ms: int = 6000) -> None:
    with db_session(ctx.engine) as s:
        for i in range(start, start + n):
            s.add(Segment(session_id=sid, seq=i + 1, t_start_ms=i * step_ms, t_end_ms=i * step_ms + 5000,
                          text_it=f"frase {i + 1}", text_vi=f"câu {i + 1}", status="translated"))
        s.commit()


def breakpoints(req) -> list[dict]:
    marks = []
    for msg in req.messages:
        content = msg["content"]
        if isinstance(content, list):
            marks += [b["cache_control"] for b in content if "cache_control" in b]
    return marks


def test_split_chunks_only_closes_final_segments():
    segs = [Segment(session_id=1, seq=i, t_start_ms=0, t_end_ms=0, text_it="x", status="translated")
            for i in range(TRANSCRIPT_CHUNK * 2 + 5)]
    segs[TRANSCRIPT_CHUNK + 3].status = "translating"
    closed, tail = split_chunks(segs)
    assert len(closed) == 1
    assert len(tail) == TRANSCRIPT_CHUNK + 5


async def test_request_structure_and_breakpoints(actx):
    sid = seed(actx, 95)
    req = await actx.assistant.build_request(sid, "Giảng viên định nghĩa overfitting thế nào?")
    assert req.system[0]["text"].startswith("Bạn là trợ lý học tập")
    first = req.messages[0]["content"]
    assert "Statistica Multivariata" in first[0]["text"] and "<glossary>" in first[0]["text"]
    assert first[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    parts = [b for b in first if b["type"] == "text" and b["text"].startswith("<transcript part=")]
    assert len(parts) == 2  # 95 segment → 2 khối đóng + 15 dòng mới
    assert parts[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "[00:12] IT: frase 3 (?)" in parts[0]["text"]  # đánh dấu độ tin cậy thấp
    final = req.messages[-1]["content"]
    assert "<transcript_recent>" in final[0]["text"] and "frase 95" in final[0]["text"]
    assert final[-1]["text"] == "Giảng viên định nghĩa overfitting thế nào?"
    assert len(breakpoints(req)) <= 4
    assert not req.summary_mode


async def test_closed_chunks_are_byte_stable_as_transcript_grows(actx):
    sid = seed(actx, 85)
    req1 = await actx.assistant.build_request(sid, "q1")
    add_segments(actx, sid, 85, 50)
    req2 = await actx.assistant.build_request(sid, "q2")
    p1 = [b["text"] for b in req1.messages[0]["content"] if b["text"].startswith("<transcript part=")]
    p2 = [b["text"] for b in req2.messages[0]["content"] if b["text"].startswith("<transcript part=")]
    assert len(p2) == len(p1) + 1
    assert p2[: len(p1)] == p1  # tiền tố không đổi → đọc lại được cache (AC-05)


async def test_history_is_included_and_starts_with_user(actx):
    sid = seed(actx, 10)
    with db_session(actx.engine) as s:
        s.add(ChatMessage(session_id=sid, role="assistant", content="(câu trả lời mồ côi)"))
        s.add(ChatMessage(session_id=sid, role="user", content="Câu hỏi 1"))
        s.add(ChatMessage(session_id=sid, role="assistant", content="Trả lời 1"))
        s.add(ChatMessage(session_id=sid, role="user", content="Câu hỏi 2"))
        s.commit()
    req = await actx.assistant.build_request(sid, "Câu hỏi 2")
    roles = [m["role"] for m in req.messages]
    assert roles == ["user", "user", "assistant", "user"]
    assert req.messages[1]["content"][0]["text"] == "Câu hỏi 1"
    assert req.messages[2]["content"][0]["cache_control"] == {"type": "ephemeral"}


async def test_answer_and_compose_are_saved(actx, harness):
    sid = seed(actx, 5)
    streamed: list[str] = []
    msg, meta = await actx.assistant.answer(sid, "Thầy vừa nói gì?", streamed.append)
    assert "".join(streamed) == msg.content
    assert meta["summary_mode"] is False
    call = harness.llm.calls[-1]
    assert call.purpose == "chat" and call.model == "claude-sonnet-5"

    msg2, payload = await actx.assistant.compose_it(sid, "Thầy có thể giải thích lại công thức không?")
    assert payload["question_it"].startswith("Professore")
    assert json.loads(msg2.content) == payload
    compose_call = harness.llm.calls[-1]
    assert "Prof. Rossi" in compose_call.system
    with db_session(actx.engine) as s:
        modes = [m.mode for m in s.exec(select(ChatMessage).where(ChatMessage.session_id == sid))]
    assert modes == ["qa", "qa", "compose_it", "compose_it"]


async def test_rolling_summary_when_context_too_long(actx, harness, monkeypatch):
    # 2 giờ bài giảng; hạ ngưỡng để kích hoạt rolling summary
    sid = seed(actx, 720, step_ms=10_000)
    monkeypatch.setattr(assistant_mod, "TOKEN_LIMIT", 2000)
    req = await actx.assistant.build_request(sid, "Tóm tắt 5 phút gần nhất")
    assert req.summary_mode
    first = req.messages[0]["content"]
    summary_block = next(b for b in first if b["type"] == "text" and b["text"].startswith("<transcript_summary>"))
    # Phút 0–60 được tóm tắt theo khối 30 phút; ≥ 30 phút gần nhất (căn theo khối) giữ nguyên văn.
    assert "Khối 1 [00:00" in summary_block["text"] and "Khối 2 [30:00" in summary_block["text"]
    verbatim = "\n".join(b["text"] for b in first if b["text"].startswith("<transcript part="))
    verbatim += req.messages[-1]["content"][0]["text"]
    assert "IT: frase 1\n" not in verbatim  # phần cũ đã được thay bằng tóm tắt
    assert "[60:00] IT: frase 361" in verbatim and "frase 720" in verbatim
    with db_session(actx.engine) as s:
        rows = list(s.exec(select(TranscriptBlockSummary).where(TranscriptBlockSummary.session_id == sid)))
    assert {r.block_index for r in rows} == {0, 1}
    n_calls = len([c for c in harness.llm.calls if c.purpose == "rolling_summary"])
    await actx.assistant.build_request(sid, "câu khác")  # tóm tắt đã lưu → không gọi lại
    assert len([c for c in harness.llm.calls if c.purpose == "rolling_summary"]) == n_calls
