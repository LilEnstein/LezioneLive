"""Chế độ lớp học trực tiếp: xin phép, sound check, chụp bảng, người nói, quyền riêng tư, ngoại tuyến, pin,
chuyển nguồn dự phòng (feature_spec mục 14)."""

from __future__ import annotations

import asyncio
import io
import json

import numpy as np
import pytest
from sqlmodel import select

from app.audio.pcm import float32_to_pcm16
from app.audio.sources.base import QueuedSource
from app.companion.hub import DeviceConn
from app.db.models import BoardCapture, Course, LectureSession, RoomProfile, Segment, SessionMarker
from app.db.session import db_session
from app.pipeline import session_runner as sr

from .conftest import last_user_text
from .test_api import add_segments, wait_for
from .test_soundcheck import ListSource, noise, speech
from .test_translation_queue import drain_events, wait_until
from .test_ws_pipeline import speech_audio, speech_frames


def course(client, consent: bool = True, **kw) -> int:
    c = client.post("/api/courses", json={"name": "Statistica", "lecturer": "Prof. Rossi", **kw}).json()
    if consent:
        client.post(f"/api/courses/{c['id']}/consent", json={"confirmed": True})
    return c["id"]


def inclass_session(client, course_id: int, **body) -> int:
    r = client.post("/api/sessions", json={"course_id": course_id, "audio_mode": "mic_companion", **body})
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def png(w: int = 800, h: int = 600) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Xin phép (IC-AC-09) & bắt đầu phiên
# ---------------------------------------------------------------------------


def test_consent_is_required_before_inclass_recording(client):
    cid = course(client, consent=False)
    r = client.post("/api/sessions", json={"audio_mode": "mic_companion"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "COURSE_REQUIRED"
    r = client.post("/api/sessions", json={"course_id": cid, "audio_mode": "mic_companion"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    assert client.post("/api/sessions", json={"course_id": cid, "audio_mode": "tab"}).status_code == 201  # online: không cần

    msg = client.post(f"/api/courses/{cid}/consent-message", json={"student_name": "Sang"}).json()
    assert msg["email_it"].startswith("Gentile") and msg["back_translation_vi"]
    call = next(c for c in client.ctx.llm.calls if c.purpose == "consent")
    system = call.system if isinstance(call.system, str) else ""
    assert "KHÔNG lưu âm thanh" in system and "Sang" in system and "Prof. Rossi" in system

    confirmed = client.post(f"/api/courses/{cid}/consent", json={"confirmed": True}).json()
    assert confirmed["consent_confirmed_at"]
    sid = inclass_session(client, cid)
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["session"]["inclass"] and detail["course"]["consent_confirmed_at"]
    assert detail["runner"]["inclass"] and detail["runner"]["denoise_level"] == "light"


def test_saving_audio_needs_second_confirmation(client):
    cid = course(client)
    client.put("/api/settings", json={"save_raw_audio": True})
    r = client.post("/api/sessions", json={"course_id": cid, "audio_mode": "mic_companion"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AUDIO_SAVE_CONFIRM_REQUIRED"
    assert client.post("/api/sessions", json={"course_id": cid, "audio_mode": "mic_companion",
                                              "confirm_save_audio": True}).status_code == 201


def test_default_settings_keep_no_audio_on_disk(client, harness):
    """IC-AC-09: cài đặt mặc định → kết thúc phiên không có file âm thanh nào."""
    cid = course(client)
    sid = inclass_session(client, cid)
    conn = DeviceConn(device_id=1, name="Pixel", mode="mic")
    ctx = client.ctx
    client.portal.call(lambda: ctx.companion.add(conn))  # type: ignore[attr-defined]
    for frame in speech_frames()[:50]:
        client.portal.call(ctx.companion.on_audio, conn, frame)  # type: ignore[attr-defined]
    client.post(f"/api/sessions/{sid}/end")
    audio_dir = harness.env.data_dir / "audio"
    assert not audio_dir.exists() or not any(audio_dir.iterdir())


def test_mic_local_probe_errors_block_session(client, monkeypatch):
    from app.audio.sources.base import SourceError
    from app.routers import sessions as sessions_router

    cid = course(client)

    def denied(device_id):
        raise SourceError("Không mở được micro. Kiểm tra quyền micro.")

    monkeypatch.setattr(sessions_router, "probe_microphone", denied)
    r = client.post("/api/sessions", json={"course_id": cid, "audio_mode": "mic_local"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MIC_UNAVAILABLE"  # IC-E-01
    assert "quyền micro" in r.json()["detail"]["message"]


# ---------------------------------------------------------------------------
# Hồ sơ phòng & sound check (IC-AC-02)
# ---------------------------------------------------------------------------


def test_room_profiles_and_soundcheck_api(client, monkeypatch):
    from app.routers import inclass

    room = client.post("/api/room-profiles", json={"name": "Aula 5 — Ecotekne"}).json()
    assert room["vad_threshold"] == 0.6 and room["recommended_denoise"] == "light"
    audio = np.concatenate([noise(10, -30), speech(20) * 0.3 + noise(20, -30, seed=9)[: speech(20).size]])
    monkeypatch.setattr(inclass, "make_source", lambda kind, device_id=None: ListSource(audio))
    started = client.post("/api/soundcheck", json={"source": "mic_local", "room_profile_id": room["id"]}).json()
    assert started["state"] in ("starting", "noise", "speech", "analyzing", "done")
    done = wait_for(lambda: (d := client.get(f"/api/soundcheck/{started['id']}").json())["state"] == "done" and d)
    assert done["result"]["rating"] == "poor" and len(done["result"]["tips"]) >= 2
    updated = client.get(f"/api/room-profiles/{room['id']}").json()
    assert updated["rating"] == "poor" and updated["recommended_denoise"] == "strong"
    assert updated["min_level_dbfs"] is not None and updated["vad_threshold"] == 0.65

    # IC-AC-02: chọn lại phòng → ngưỡng VAD và khử nhiễu áp dụng tự động
    cid = course(client, name="Econometria")
    sid = inclass_session(client, cid, room_profile_id=room["id"])
    runner = client.ctx.runners.peek(sid)
    assert runner.segmenter.threshold == 0.65 and runner.denoise_level == "strong"
    assert runner.min_speech_ms == 800
    assert client.get(f"/api/sessions/{sid}").json()["room_profile"]["name"] == "Aula 5 — Ecotekne"

    assert client.post("/api/soundcheck", json={"source": "mic_companion"}).status_code == 409
    assert client.delete(f"/api/room-profiles/{room['id']}").json() == {"deleted": True}
    assert client.get(f"/api/sessions/{sid}").json()["session"]["room_profile_id"] is None


# ---------------------------------------------------------------------------
# Chụp bảng (IC-AC-06)
# ---------------------------------------------------------------------------


def test_board_capture_extracts_suggests_and_feeds_translation(client, harness):
    cid = course(client)
    sid = inclass_session(client, cid)
    r = client.post(f"/api/sessions/{sid}/board-captures", files={"file": ("bang.png", png(4000, 3000), "image/png")},
                    data={"t_ms": "60000"})
    assert r.status_code == 201, r.text
    cap = r.json()
    assert cap["status"] == "done" and cap["t_ms"] == 60000
    assert max(cap["width"], cap["height"]) == 1568  # IC-FR-44
    assert cap["latex"] and "varianza campionaria" in cap["extracted_it"].lower()
    board_call = next(c for c in harness.llm.calls if c.purpose == "board")
    assert board_call.model == "claude-sonnet-5"
    assert board_call.messages[0]["content"][0]["type"] == "image"
    sugg = client.get(f"/api/sessions/{sid}/glossary-suggestions").json()
    assert {s["term_it"] for s in sugg} == {"varianza campionaria", "media"}
    assert all(s["capture_id"] == cap["id"] for s in sugg)
    varianza = next(s for s in sugg if s["term_it"] == "varianza campionaria")
    term = client.post(f"/api/glossary-suggestions/{varianza['id']}/accept").json()  # người dùng duyệt ✓
    assert term["term_vi"] == "phương sai mẫu"
    img = client.get(cap["thumb_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    # 10 phút sau khi chụp: bản dịch có nội dung bảng trong ngữ cảnh (prompt dịch trong lớp)
    ids = add_segments(client, sid, [("La varianza campionaria qui, come vedete", None)])
    with db_session(client.ctx.engine) as s:
        seg = s.get(Segment, ids[0])
        seg.t_start_ms, seg.t_end_ms = 120_000, 125_000
        s.add(seg)
        s.commit()
    assert client.post(f"/api/segments/{ids[0]}/retry").json() == {"queued": True}
    wait_for(lambda: client.get(f"/api/sessions/{sid}").json()["segments"][0]["status"] == "translated")
    call = next(c for c in harness.llm.calls if c.purpose == "translate")
    stable, volatile = call.system[0]["text"], call.system[1]["text"]
    assert "BỐI CẢNH BỔ SUNG" in stable and "phương sai mẫu" in stable  # thuật ngữ đã duyệt vào glossary
    assert "[Ảnh bảng lúc 01:00]" in volatile and "Varianza campionaria" in volatile
    assert "NGƯỜI NÓI" in volatile

    listed = client.get(f"/api/sessions/{sid}/board-captures").json()
    assert [c["id"] for c in listed] == [cap["id"]]
    assert client.get(f"/api/sessions/{sid}").json()["board_captures"][0]["id"] == cap["id"]


def test_unreadable_board_does_not_invent_content(client, harness):
    """IC-E-06 / IC-AC-06: ảnh mờ → lưu ảnh, không trích xuất, thông báo hướng dẫn chụp lại."""
    cid = course(client)
    sid = inclass_session(client, cid)
    harness.llm.responder = lambda call: {"text_it": "", "latex": [], "terms": [], "translation_vi": "",
                                          "unreadable": "Ảnh bị loá toàn bộ"}
    cap = client.post(f"/api/sessions/{sid}/board-captures",
                      files={"file": ("mo.jpg", png(), "image/png")}).json()
    assert cap["status"] == "unreadable"
    assert cap["error"] == "Không đọc được ảnh. Thử chụp thẳng góc, tránh loá đèn."
    assert cap["extracted_it"] is None and cap["terms"] == []
    assert client.get(f"/api/sessions/{sid}/glossary-suggestions").json() == []
    with db_session(client.ctx.engine) as s:
        row = s.get(BoardCapture, cap["id"])
    assert row is not None and row.image_path
    bad = client.post(f"/api/sessions/{sid}/board-captures", files={"file": ("x.png", b"not an image", "image/png")})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "INVALID_IMAGE"


# ---------------------------------------------------------------------------
# Người nói & quyền riêng tư (IC-AC-05)
# ---------------------------------------------------------------------------


def test_speaker_correction_and_discard_other_speech(client, harness):
    client.put("/api/settings", json={"discard_other_speech": True})
    cid = course(client)
    sid = inclass_session(client, cid)
    ids = add_segments(client, sid, [
        ("Oggi parliamo di varianza.", "Hôm nay nói về phương sai."),
        ("Professore, scusi, può ripetere?", "Thưa thầy, thầy nhắc lại được không?"),
        ("Certo, la varianza misura la dispersione.", "Tất nhiên, phương sai đo độ phân tán."),
    ])
    r = client.patch(f"/api/segments/{ids[1]}/speaker", json={"speaker": "other"})
    assert r.status_code == 200 and r.json()["speaker"] == "other"
    client.patch(f"/api/segments/{ids[0]}/speaker", json={"speaker": "lecturer"})
    with db_session(client.ctx.engine) as s:
        other = s.get(Segment, ids[1])
        assert other.status == "redacted" and other.text_it == "" and other.text_vi is None
    client.post(f"/api/sessions/{sid}/end")
    wait_for(lambda: client.get(f"/api/sessions/{sid}/summary").json()["status"] == "done")
    summary_call = next(c for c in harness.llm.calls if c.purpose == "summary")
    assert "può ripetere" not in last_user_text(summary_call)
    md = client.get(f"/api/sessions/{sid}/export", params={"format": "md"}).text
    assert "può ripetere" not in md and "Lời người khác — không lưu" in md
    assert "🎓" in md
    search = client.get("/api/search", params={"q": "ripetere"}).json()
    assert search["results"] == []


# ---------------------------------------------------------------------------
# Export (IC-FR-43, mục 11)
# ---------------------------------------------------------------------------


def test_export_has_privacy_line_board_image_and_markers(client):
    cid = course(client)
    sid = inclass_session(client, cid)
    add_segments(client, sid, [("uno", "một"), ("due", "hai"), ("tre", "ba")])
    client.post(f"/api/sessions/{sid}/board-captures", files={"file": ("b.png", png(), "image/png")},
                data={"t_ms": "7000"})
    with db_session(client.ctx.engine) as s:
        s.add(SessionMarker(session_id=sid, t_ms=10_000, kind="source_fallback",
                            message="Mất kết nối điện thoại — chuyển sang micro laptop"))
        s.commit()
    md = client.get(f"/api/sessions/{sid}/export", params={"format": "md"}).text
    body = md.split("---", 2)[2]
    assert body.lstrip().startswith("> ⚠️ Tài liệu học tập cá nhân")
    transcript = md.split("## Transcript song ngữ", 1)[1]
    order = [transcript.index(x) for x in ("🇮🇹 uno", "🇮🇹 due", "📷", "Mất kết nối điện thoại", "🇮🇹 tre")]
    assert order == sorted(order)  # ảnh bảng (00:07) và mốc gián đoạn (00:10) đúng vị trí thời gian
    assert "data:image/jpeg;base64," in transcript
    html = client.get(f"/api/sessions/{sid}/export", params={"format": "html"}).text
    assert '<img alt="Ảnh bảng 00:07" src="data:image/jpeg;base64,' in html
    assert "<math" in html  # LaTeX từ ảnh bảng → MathML


# ---------------------------------------------------------------------------
# Runner: nguồn dự phòng, người nói, SNR, pin, ngoại tuyến
# ---------------------------------------------------------------------------


class FakeMic(QueuedSource):
    kind = "mic_local"
    opened = 0

    async def _open(self) -> None:
        FakeMic.opened += 1
        self.device_name = "Microphone Array (Realtek)"


def make_inclass(ctx, *, source: str = "mic_companion", battery_saver: bool = False) -> int:
    with db_session(ctx.engine) as s:
        c = Course(name="Statistica")
        s.add(c)
        s.commit()
        s.refresh(c)
        sess = LectureSession(title="Lezione", course_id=c.id, audio_mode=source, capture_source=source,
                              battery_saver=battery_saver)
        s.add(sess)
        s.commit()
        s.refresh(sess)
        return sess.id  # type: ignore[return-value]


async def feed_speech(ctx, conn: DeviceConn) -> None:
    """Điện thoại gửi file mẫu (nhanh hơn thời gian thực nhưng nhường event loop để pipeline xử lý kịp)."""
    pcm = float32_to_pcm16(speech_audio())
    frames = [pcm[i : i + 3200] for i in range(0, len(pcm), 3200)] + [bytes(3200)] * 25
    for i, frame in enumerate(frames):
        ctx.companion.on_audio(conn, frame)
        if i % 20 == 19:
            await asyncio.sleep(0.01)


def events_of(queue, kind: str) -> list[dict]:
    return [e for e in drain_events(queue) if e["type"] == kind]


async def test_phone_disconnect_falls_back_to_laptop_mic_and_back(actx, monkeypatch):
    """IC-FR-07 / IC-E-03 / IC-AC-03: mất điện thoại 3 s → micro laptop + mốc gián đoạn; có lại → điện thoại."""
    monkeypatch.setattr(sr, "FALLBACK_AFTER_S", 0.3)
    monkeypatch.setattr(sr, "HEALTH_INTERVAL_S", 0.05)
    real = sr.make_source
    monkeypatch.setattr(sr, "make_source", lambda kind, device_id=None: FakeMic() if kind == "mic_local"
                        else real(kind, device_id=device_id))
    sid = make_inclass(actx)
    queue = actx.hub.subscribe(sid)
    runner = await actx.runners.get(sid)
    conn = DeviceConn(device_id=1, name="Pixel", mode="mic")
    actx.companion.add(conn)
    for _ in range(5):
        actx.companion.on_audio(conn, bytes(3200))
        await asyncio.sleep(0.02)
    assert runner.health().connected
    actx.companion.remove(conn)
    await wait_until(lambda: runner.fallback_active, timeout=3)
    assert runner.status_event()["active_source"] == "mic_local"
    warnings = [e["code"] for e in drain_events(queue) if e["type"] == "warning"]
    assert "COMPANION_LOST" in warnings
    with db_session(actx.engine) as s:
        markers = list(s.exec(select(SessionMarker).where(SessionMarker.session_id == sid)))
    assert [m.kind for m in markers] == ["source_fallback"]

    conn2 = DeviceConn(device_id=1, name="Pixel", mode="mic")
    actx.companion.add(conn2)

    async def stream() -> None:
        for _ in range(40):
            actx.companion.on_audio(conn2, bytes(3200))
            await asyncio.sleep(0.02)

    feeder = asyncio.create_task(stream())
    await wait_until(lambda: not runner.fallback_active, timeout=3)
    await feeder
    with db_session(actx.engine) as s:
        kinds = [m.kind for m in s.exec(select(SessionMarker).where(SessionMarker.session_id == sid))]
    assert kinds == ["source_fallback", "source_restored"]
    assert runner.status_event()["active_source"] == "mic_companion"


async def test_speaker_tags_flow_into_segments_and_translation(actx, harness, monkeypatch):
    from app.audio.speaker_tagger import SpeakerTagger

    monkeypatch.setattr(SpeakerTagger, "ENROLL_MS", 3000)
    monkeypatch.setattr(SpeakerTagger, "MIN_ENROLL_SPEECH_MS", 2000)
    monkeypatch.setattr(SpeakerTagger, "MIN_ENROLL_SEGMENTS", 2)
    rng = np.random.default_rng(1)
    lect, other = rng.standard_normal(32), rng.standard_normal(32)
    plan = iter(["L", "L", "L", "O", "L", "L", "L", "L"])

    class PlanEmbedder:
        name, kind = "plan", "neural"

        def embed(self, audio):
            base = lect if next(plan, "L") == "L" else other
            v = base + 0.05 * rng.standard_normal(32)
            return v / np.linalg.norm(v)

    harness.speaker_models._embedder = PlanEmbedder()
    sid = make_inclass(actx)
    queue = actx.hub.subscribe(sid)
    await actx.runners.get(sid)
    conn = DeviceConn(device_id=1, name="Pixel", mode="mic")
    actx.companion.add(conn)
    await feed_speech(actx, conn)
    await asyncio.sleep(0)

    def segs():
        with db_session(actx.engine) as s:
            return list(s.exec(select(Segment).where(Segment.session_id == sid).order_by(Segment.seq)))

    await wait_until(lambda: len(segs()) >= 4 and all(s.speaker != "unknown" for s in segs()), timeout=20)
    speakers = [s.speaker for s in segs()]
    assert speakers[3] == "other" and speakers.count("lecturer") >= 3
    evs = events_of(queue, "segment.speaker")
    assert {e["id"] for e in evs} >= {s.id for s in segs()}
    await actx.translation.drain(sid, 10)
    other_seg = segs()[3]
    call = next(c for c in harness.llm.calls if c.purpose == "translate" and last_user_text(c) == other_seg.text_it)
    assert "other (sinh viên hỏi)" in call.system[1]["text"]


async def test_low_snr_warning_after_two_minutes(actx):
    """IC-E-04: SNR < 8 dB kéo dài 2 phút → cảnh báo một lần."""
    sid = make_inclass(actx)
    queue = actx.hub.subscribe(sid)
    runner = await actx.runners.get(sid)
    for i in range(100):
        runner.snr.add(i * 100, -40.0 if i % 2 else -45.0, bool(i % 2))
    assert runner.snr.snr() == pytest.approx(5.0)
    runner._check_snr(1000.0)
    runner._check_snr(1000.0 + 60)
    assert events_of(queue, "warning") == []
    runner._check_snr(1000.0 + 121)
    runner._check_snr(1000.0 + 200)
    assert [w["code"] for w in events_of(queue, "warning")] == ["LOW_SNR"]


async def test_battery_warnings_and_auto_saver(actx, harness, monkeypatch):
    """IC-AC-08 / IC-E-07: pin < 20% cảnh báo đúng 1 lần; < 10% tự bật tiết kiệm pin."""
    monkeypatch.setattr(sr, "BATTERY_CHECK_S", 0.0)
    monkeypatch.setattr(sr, "HEALTH_INTERVAL_S", 0.05)
    actx.power.max_age_s = 0.0
    harness.battery.percent, harness.battery.plugged = 15.0, False
    sid = make_inclass(actx)
    queue = actx.hub.subscribe(sid)
    runner = await actx.runners.get(sid)
    await asyncio.sleep(0.4)
    assert [w["code"] for w in events_of(queue, "warning")] == ["BATTERY_LOW"]
    harness.battery.percent = 8.0
    await wait_until(lambda: runner.battery_saver, timeout=3)
    assert runner.tagger is None and runner.pre is not None and "rnnoise" not in runner.pre.engine
    assert sid in harness.stt.saver_sessions  # Whisper small int8
    assert runner.status_event()["battery"]["critical"]
    await asyncio.sleep(0.2)
    codes = [w["code"] for w in events_of(queue, "warning")]
    assert codes.count("BATTERY_CRITICAL") == 1 and "BATTERY_LOW" not in codes
    with db_session(actx.engine) as s:
        assert s.get(LectureSession, sid).battery_saver
        assert [m.kind for m in s.exec(select(SessionMarker).where(SessionMarker.session_id == sid))] == ["battery_saver"]
    await actx.runners.end(sid)
    assert sid not in harness.stt.saver_sessions


def test_patch_session_options_mid_class(client):
    cid = course(client)
    sid = inclass_session(client, cid)
    r = client.patch(f"/api/sessions/{sid}", json={"denoise_level": "strong", "battery_saver": True})
    assert r.status_code == 200
    out = r.json()
    assert out["denoise_level"] == "strong" and out["battery_saver"] is True
    status = client.get(f"/api/sessions/{sid}").json()["runner"]
    assert status["denoise_level"] == "strong" and status["battery_saver"] and not status["speaker_tagging"]
    assert status["denoise_engine"] == "spectral_strong"


async def test_offline_draft_translation_then_claude(actx, harness):
    """IC-AC-07: mất mạng → segment chờ dịch có bản nháp "nháp"; có mạng → bản Claude thay thế."""

    class FakeDraft:
        ready = True

        def start_prepare(self, download: bool = True):
            return None

        async def translate(self, text: str, **_) -> str:
            return f"(nháp) {text}"

    actx.translation.draft = FakeDraft()
    actx.settings.update({"offline_draft": True})
    harness.online["value"] = False
    actx.connectivity.mark_offline()
    sid = make_inclass(actx)
    queue = actx.hub.subscribe(sid)
    with db_session(actx.engine) as s:
        ids = []
        for i in range(3):
            seg = Segment(session_id=sid, seq=i + 1, t_start_ms=i * 5000, t_end_ms=i * 5000 + 4000,
                          text_it=f"frase {i + 1}")
            s.add(seg)
            s.commit()
            s.refresh(seg)
            ids.append(seg.id)
    for seg_id in ids:
        actx.translation.enqueue(sid, seg_id)

    def rows():
        with db_session(actx.engine) as s:
            return list(s.exec(select(Segment).where(Segment.session_id == sid).order_by(Segment.seq)))

    await wait_until(lambda: all(r.translation_kind == "draft" for r in rows()))
    assert all(r.status == "pending_translation" and r.text_vi.startswith("(nháp)") for r in rows())
    evs = drain_events(queue)
    assert sum(e["type"] == "segment.draft" for e in evs) == 3
    state = [e for e in evs if e["type"] == "network.state"][-1]
    assert state == {"type": "network.state", "online": False, "pending_translations": 3}
    harness.online["value"] = True
    await wait_until(lambda: all(r.status == "translated" and r.translation_kind == "claude" for r in rows()), timeout=5)
    batch = [c for c in harness.llm.calls if c.purpose == "translate" and "schema" in c.kwargs]
    assert batch and len(json.loads(json.dumps(batch[0].kwargs["schema"]))) > 0  # dịch bù gộp (≤ 5 câu)
    assert "frase 1" in last_user_text(batch[0])


async def test_room_profile_level_gate_drops_quiet_utterances(actx):
    """IC-FR-13: utterance nhỏ hơn ngưỡng hiệu chỉnh không đưa vào STT."""
    with db_session(actx.engine) as s:
        room = RoomProfile(name="Test", min_level_dbfs=-5.0, vad_threshold=0.5)
        s.add(room)
        s.commit()
        s.refresh(room)
    sid = make_inclass(actx)
    with db_session(actx.engine) as s:
        sess = s.get(LectureSession, sid)
        sess.room_profile_id = room.id
        s.add(sess)
        s.commit()
    queue = actx.hub.subscribe(sid)
    await actx.runners.get(sid)
    conn = DeviceConn(device_id=1, name="Pixel", mode="mic")
    actx.companion.add(conn)
    await feed_speech(actx, conn)
    await asyncio.sleep(1.5)
    evs = drain_events(queue)
    assert any(e["type"] == "vad.dropped" for e in evs)
    assert not any(e["type"] == "segment.transcribed" for e in evs)


def test_speak_it_mode_and_phone_speak(client):
    """IC-AC-10: câu hỏi tiếng Việt → câu tiếng Ý ≤ 25 từ kèm phiên âm nhấn âm."""
    cid = course(client)
    sid = inclass_session(client, cid)
    add_segments(client, sid, [("La formula della varianza.", "Công thức phương sai.")])
    with client.stream("POST", f"/api/sessions/{sid}/chat", json={"message": "Thầy nhắc lại công thức?",
                                                                  "mode": "speak_it"}) as r:
        body = "".join(r.iter_text())
    result = json.loads(body.split("event: result\ndata: ", 1)[1].split("\n", 1)[0])
    assert len(result["question_it"].split()) <= 25 and result["word_count"] <= 25
    assert "-" in result["pronunciation"] and any(ch.isupper() for ch in result["pronunciation"])
    hist = client.get(f"/api/sessions/{sid}/chat", params={"mode": "speak_it"}).json()
    assert hist[-1]["data"]["question_it"] == result["question_it"]


def test_migration_adds_columns_to_old_database(tmp_path):
    from app.db.migrations import run_migrations
    from app.db.session import make_engine

    engine = make_engine(tmp_path / "old.db")
    run_migrations(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO course(name, created_at) VALUES ('X', datetime('now'))")
        conn.exec_driver_sql(
            "INSERT INTO session(title, started_at, status, audio_mode, capture_source, stt_provider, cost_usd_est,"
            " cost_warned, translation_enabled, active_ms, post_status, battery_saver)"
            " VALUES ('old', datetime('now'), 'ended', 'loopback', 'tab', 'whisper_local', 0, 0, 1, 0, 'none', 0)"
        )
        for table, column in (("session", "capture_source"), ("segment", "speaker"),
                              ("segment", "translation_kind"), ("course", "consent_confirmed_at")):
            conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.exec_driver_sql("DELETE FROM schema_migrations WHERE version = 2")
    assert run_migrations(engine) == [2]
    with db_session(engine) as s:
        sess = s.exec(select(LectureSession)).first()
        assert sess.capture_source == "loopback"  # lấy lại từ audio_mode
    assert run_migrations(engine) == []
    engine.dispose()
