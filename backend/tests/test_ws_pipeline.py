"""Tích hợp WebSocket end-to-end: stream file WAV giả lập qua /ws/session/{id} (Flow B)."""

from __future__ import annotations

import time

import numpy as np
from fastapi.testclient import TestClient
from sqlmodel import select

from app.audio.pcm import float32_to_pcm16, read_wav, resample
from app.audio.segmenter import Segmenter
from app.audio.vad import make_vad
from app.db.models import LectureSession, Segment
from app.db.session import db_session
from app.main import create_app

from .conftest import FIXTURES, context_factory

WS_HEADERS = {"origin": "http://localhost:5173"}


def speech_audio() -> np.ndarray:
    audio, sr = read_wav(FIXTURES / "audio" / "speech_en_pauses.wav")
    return resample(audio, sr, 16000)


def speech_frames() -> list[bytes]:
    pcm = float32_to_pcm16(speech_audio())
    frame = 3200  # 1600 mẫu Int16 = 100 ms
    return [pcm[i : i + frame] for i in range(0, len(pcm), frame)]


def expected_utterances() -> int:
    """Số câu mà Segmenter (Silero, tham số mặc định) cắt được từ file mẫu — tính offline, tất định."""
    seg = Segmenter(make_vad())
    events = seg.push(speech_audio()) + seg.flush()
    return sum(e.kind == "utterance" for e in events)


def collect(ws, until, timeout: float = 15.0) -> list[dict]:
    events: list[dict] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        events.append(ws.receive_json())  # server phát status mỗi 2 s nên không kẹt mãi
        if until(events):
            return events
    raise AssertionError(f"Không nhận đủ sự kiện: {[e['type'] for e in events]}")


def test_ws_streams_audio_to_bilingual_segments(client, harness):
    sid = client.post("/api/sessions", json={"title": "WS"}).json()["session_id"]
    with client.websocket_connect(f"ws://127.0.0.1:8765/ws/session/{sid}", headers=WS_HEADERS) as ws:
        first = ws.receive_json()
        assert first["type"] == "status" and first["state"] == "live"
        for frame in speech_frames():
            ws.send_bytes(frame)

        expected = expected_utterances()
        assert expected >= 4

        def done(evs):
            translated = {e["id"] for e in evs if e["type"] == "segment.translated"}
            transcribed = {e["id"] for e in evs if e["type"] == "segment.transcribed"}
            return len(transcribed) >= expected and transcribed <= translated

        events = collect(ws, done)
        types = [e["type"] for e in events]
        assert "vad.speech_start" in types and "vad.speech_end" in types
        assert "segment.translation.delta" in types
        assert "cost.update" in types
        transcribed = [e for e in events if e["type"] == "segment.transcribed"]
        # đúng thứ tự thời gian, seq tăng dần, có confidence
        assert [e["seq"] for e in transcribed] == sorted(e["seq"] for e in transcribed)
        assert all(a["t_end_ms"] <= b["t_start_ms"] + 1 for a, b in zip(transcribed, transcribed[1:], strict=False))
        assert all(e["t_end_ms"] - e["t_start_ms"] <= 12000 for e in transcribed)
        assert any(e["confidence"] < 0.5 for e in transcribed)

        # Ctrl+B: ghim segment mới nhất (AC-07)
        pin = client.post(f"/api/sessions/{sid}/pins", json={}).json()
        assert pin["segment_id"] == transcribed[-1]["id"]
        collect(ws, lambda evs: any(e["type"] == "pin.created" for e in evs))

        ws.send_json({"type": "control", "action": "pause"})
        collect(ws, lambda evs: any(e["type"] == "status" and e["state"] == "paused" for e in evs))
        ws.send_json({"type": "control", "action": "resume"})
        collect(ws, lambda evs: any(e["type"] == "status" and e["state"] == "live" for e in evs))

    with db_session(client.ctx.engine) as s:
        segs = list(s.exec(select(Segment).where(Segment.session_id == sid).order_by(Segment.seq)))
    assert len(segs) >= 4
    assert all(sg.status == "translated" and sg.text_vi.startswith("VI: frase") for sg in segs)
    # prompt Whisper có tên môn học/thuật ngữ (FR-STT-04)
    assert all(p and p.startswith("Lezione universitaria") for p in harness.stt.provider.prompts)
    metrics = client.get(f"/api/sessions/{sid}/metrics").json()
    assert metrics["segments"] == len(segs)
    assert metrics["it_latency_ms"]["p50"] is not None and metrics["vi_latency_ms"]["p95"] is not None
    csv_text = client.get(f"/api/sessions/{sid}/metrics", params={"format": "csv"}).text
    assert csv_text.splitlines()[0] == "seq,segment_id,audio_ms,stt_ms,it_latency_ms,vi_latency_ms"


def test_ws_rejects_foreign_origin_and_unknown_session(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    sid = client.post("/api/sessions", json={}).json()["session_id"]
    with pytest.raises(WebSocketDisconnect) as ei:
        with client.websocket_connect(f"ws://127.0.0.1:8765/ws/session/{sid}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert ei.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as ei:
        with client.websocket_connect("ws://127.0.0.1:8765/ws/session/999999", headers=WS_HEADERS) as ws:
            ws.receive_json()
    assert ei.value.code == 4404


def test_source_ended_pauses_session(client):
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    with client.websocket_connect(f"ws://127.0.0.1:8765/ws/session/{sid}", headers=WS_HEADERS) as ws:
        ws.receive_json()
        ws.send_json({"type": "control", "action": "source_ended"})
        events = collect(ws, lambda evs: any(e["type"] == "warning" for e in evs))
        warning = next(e for e in events if e["type"] == "warning")
        assert warning["code"] == "SOURCE_ENDED"
    assert client.get(f"/api/sessions/{sid}").json()["session"]["status"] == "paused"


def test_restart_keeps_transcript_and_resumes_numbering(harness):
    """AC-04: tắt backend giữa phiên → khởi động lại vẫn còn segment, phiên chuyển sang tạm dừng."""
    app = create_app(harness.env, context_factory=context_factory(harness))
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        sid = c.post("/api/sessions", json={"title": "Crash"}).json()["session_id"]
        with c.websocket_connect(f"ws://127.0.0.1:8765/ws/session/{sid}", headers=WS_HEADERS) as ws:
            ws.receive_json()
            for frame in speech_frames()[:120]:  # ~12 s đầu
                ws.send_bytes(frame)
            for _ in range(20):  # 2 s im lặng để câu đang dở kết thúc
                ws.send_bytes(bytes(3200))
            collect(ws, lambda evs: sum(e["type"] == "segment.transcribed" for e in evs) >= 2)
        with db_session(app.state.ctx.engine) as s:
            before = len(list(s.exec(select(Segment).where(Segment.session_id == sid))))
    assert before >= 2

    app2 = create_app(harness.env, context_factory=context_factory(harness))
    with TestClient(app2, base_url="http://127.0.0.1:8765") as c2:
        detail = c2.get(f"/api/sessions/{sid}").json()
        assert detail["session"]["status"] == "paused"
        assert len(detail["segments"]) == before
        assert c2.post(f"/api/sessions/{sid}/resume").json() == {"status": "live"}
        with c2.websocket_connect(f"ws://127.0.0.1:8765/ws/session/{sid}", headers=WS_HEADERS) as ws:
            ws.receive_json()
            for frame in speech_frames()[120:]:
                ws.send_bytes(frame)
            events = collect(ws, lambda evs: any(e["type"] == "segment.transcribed" for e in evs))
        new = next(e for e in events if e["type"] == "segment.transcribed")
        assert new["seq"] == before + 1
        last_end = detail["segments"][-1]["t_end_ms"]
        assert new["t_start_ms"] >= last_end  # mốc thời gian nối tiếp
        with db_session(app2.state.ctx.engine) as s:
            assert s.get(LectureSession, sid).status == "live"


async def test_silence_warning_after_60s(actx, monkeypatch):
    import asyncio

    from app.pipeline import session_runner as sr

    monkeypatch.setattr(sr, "SILENCE_WARN_S", 0.2)
    monkeypatch.setattr(sr, "STATUS_INTERVAL_S", 0.05)
    with db_session(actx.engine) as s:
        sess = LectureSession(title="silence")
        s.add(sess)
        s.commit()
        s.refresh(sess)
    queue = actx.hub.subscribe(sess.id)
    runner = await actx.runners.get(sess.id)
    runner.push_audio(float32_to_pcm16(np.zeros(16000, dtype=np.float32)), "ws-1")
    await asyncio.sleep(0.5)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    warnings = [e for e in events if e["type"] == "warning"]
    assert [w["code"] for w in warnings] == ["SILENCE_60S"]
