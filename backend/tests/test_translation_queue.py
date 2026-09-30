"""Hàng đợi dịch (mục 10.2): song song ≤ 3, gộp khi tồn đọng, backoff, dịch bù khi có mạng, key sai."""

from __future__ import annotations

import asyncio

from app.db.models import LectureSession, Segment
from app.db.session import db_session
from app.llm.client import LLMError

from .conftest import default_responder, last_user_text


def make_session(ctx, n: int) -> tuple[int, list[int]]:
    with db_session(ctx.engine) as s:
        sess = LectureSession(title="Test")
        s.add(sess)
        s.commit()
        s.refresh(sess)
        ids = []
        for i in range(n):
            seg = Segment(session_id=sess.id, seq=i + 1, t_start_ms=i * 5000, t_end_ms=i * 5000 + 4000,
                          text_it=f"frase {i + 1}")
            s.add(seg)
            s.commit()
            s.refresh(seg)
            ids.append(seg.id)
    return sess.id, ids


def segments(ctx, session_id: int) -> list[Segment]:
    from sqlmodel import select

    with db_session(ctx.engine) as s:
        return list(s.exec(select(Segment).where(Segment.session_id == session_id).order_by(Segment.seq)))


def drain_events(queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def wait_until(pred, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("Hết thời gian chờ điều kiện")


async def test_concurrency_limit_and_streaming_events(actx, harness):
    harness.llm.delay = 0.05
    sid, ids = make_session(actx, 7)
    events = actx.hub.subscribe(sid)
    for seg_id in ids:
        actx.translation.enqueue(sid, seg_id)
    assert await actx.translation.drain(sid, timeout=5)
    segs = segments(actx, sid)
    assert all(s.status == "translated" for s in segs)
    assert [s.text_vi for s in segs] == [f"VI: frase {i + 1}" for i in range(7)]
    assert harness.llm.max_active <= 3
    evs = drain_events(events)
    for seg_id in ids:
        kinds = [e["type"] for e in evs if e.get("id") == seg_id]
        assert kinds[0] == "segment.translating"
        assert "segment.translation.delta" in kinds
        assert kinds[-1] == "segment.translated"
    # context: câu thứ 2 phải thấy câu 1 trong prompt (3 segment trước)
    call2 = next(c for c in harness.llm.calls if last_user_text(c) == "frase 5")
    assert "[IT] frase 4" in call2.system[1]["text"]


async def test_backlog_is_batched(actx, harness):
    harness.llm.set_has_key(False)  # đang bị chặn → dồn hàng đợi
    sid, ids = make_session(actx, 15)
    for seg_id in ids:
        actx.translation.enqueue(sid, seg_id)
    await asyncio.sleep(0.05)
    assert actx.translation.queue_len(sid) == 15
    harness.llm.set_has_key(True)
    actx.translation.wake_all()
    assert await actx.translation.drain(sid, timeout=5)
    batch_calls = [c for c in harness.llm.calls if "schema" in c.kwargs]
    assert batch_calls, "phải có request dịch gộp khi hàng đợi > 10"
    segs = segments(actx, sid)
    assert all(s.status == "translated" for s in segs)
    assert len(harness.llm.calls) < 15


async def test_rate_limit_backs_off_and_keeps_translating_status(actx, harness):
    state = {"n": 0}

    def responder(call):
        state["n"] += 1
        if state["n"] == 1:
            return LLMError("rate_limit", "429", status=429)
        return default_responder(call)

    harness.llm.responder = responder
    sid, ids = make_session(actx, 1)
    events = actx.hub.subscribe(sid)
    actx.translation.enqueue(sid, ids[0])
    await wait_until(lambda: state["n"] >= 1)
    await asyncio.sleep(0.05)
    assert segments(actx, sid)[0].status == "translating"
    assert actx.translation.slow
    assert await actx.translation.drain(sid, timeout=5)
    assert segments(actx, sid)[0].status == "translated"
    warnings = [e for e in drain_events(events) if e["type"] == "warning"]
    assert [w["code"] for w in warnings] == ["API_RATE_LIMIT"]


async def test_network_failure_then_catch_up(actx, harness):
    harness.llm.responder = lambda call: LLMError("network", "offline")
    harness.online["value"] = False
    sid, ids = make_session(actx, 3)
    events = actx.hub.subscribe(sid)
    actx.translation.enqueue(sid, ids[0])
    await wait_until(lambda: segments(actx, sid)[0].status == "failed")
    assert segments(actx, sid)[0].error == "network"
    assert not actx.connectivity.online
    # Trong lúc mất mạng: segment mới nằm chờ (pending_translation, IC-FR-50), không bị đánh lỗi
    actx.translation.enqueue(sid, ids[1])
    actx.translation.enqueue(sid, ids[2])
    await asyncio.sleep(0.1)
    assert [s.status for s in segments(actx, sid)[1:]] == ["pending_translation", "pending_translation"]
    assert actx.translation.queue_len(sid) == 2
    # Có mạng lại → dịch bù toàn bộ
    harness.llm.responder = default_responder
    harness.online["value"] = True
    await wait_until(lambda: all(s.status == "translated" for s in segments(actx, sid)))
    evs = drain_events(events)
    codes = [e.get("code") for e in evs if e["type"] == "warning"]
    assert codes.count("NETWORK_DOWN") == 1
    states = [e for e in evs if e["type"] == "network.state"]
    assert any(not e["online"] and e["pending_translations"] == 2 for e in states)
    assert any(e["online"] for e in states)
    assert all(s.translation_kind == "claude" for s in segments(actx, sid))


async def test_invalid_key_stops_and_resumes_after_key_update(actx, harness):
    harness.llm.responder = lambda call: LLMError("auth", "401", status=401)
    sid, ids = make_session(actx, 2)
    events = actx.hub.subscribe(sid)
    actx.translation.enqueue(sid, ids[0])
    await wait_until(lambda: segments(actx, sid)[0].status == "failed")
    actx.translation.enqueue(sid, ids[1])
    await asyncio.sleep(0.1)
    assert segments(actx, sid)[1].status == "transcribed"  # dừng dịch, giữ transcript
    assert [e["code"] for e in drain_events(events) if e["type"] == "warning"] == ["API_KEY_INVALID"]
    harness.llm.responder = default_responder
    actx.translation.on_api_key_changed()
    await wait_until(lambda: all(s.status == "translated" for s in segments(actx, sid)))


async def test_edit_during_translation_discards_stale_result(actx, harness):
    harness.llm.delay = 0.2
    sid, ids = make_session(actx, 1)
    actx.translation.enqueue(sid, ids[0])
    await wait_until(lambda: segments(actx, sid)[0].status == "translating")
    with db_session(actx.engine) as s:
        seg = s.get(Segment, ids[0])
        seg.text_it_edited = "frase corretta"
        seg.status = "transcribed"
        s.add(seg)
        s.commit()
    actx.translation.enqueue(sid, ids[0], front=True)
    assert await actx.translation.drain(sid, timeout=5)
    seg = segments(actx, sid)[0]
    assert seg.text_vi == "VI: frase corretta"
    assert seg.status == "translated"


async def test_disabled_translation_and_drain_timeout(actx, harness):
    sid, ids = make_session(actx, 2)
    actx.translation.set_enabled(sid, False)
    actx.translation.enqueue(sid, ids[0])
    assert await actx.translation.drain(sid, timeout=0.5)
    assert segments(actx, sid)[0].status == "transcribed"
    actx.translation.set_enabled(sid, True)  # bật lại → dịch các segment còn thiếu
    assert await actx.translation.drain(sid, timeout=5)
    assert all(s.status == "translated" for s in segments(actx, sid))

    harness.llm.set_has_key(False)
    sid2, ids2 = make_session(actx, 1)
    actx.translation.enqueue(sid2, ids2[0])
    assert not await actx.translation.drain(sid2, timeout=0.3)
