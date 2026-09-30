"""API chế độ lớp học trực tiếp (feature_spec mục 7.2): hồ sơ phòng, sound check, chụp bảng, nhãn người nói,
xin phép giảng viên, model phụ (nhận diện giọng, dịch nháp), pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import col, select

from app.audio.soundcheck import SoundCheck
from app.audio.sources import CompanionSource, make_source
from app.config import LOCAL_MODEL_CHOICES
from app.context import AppContext
from app.db.models import BoardCapture, Course, LectureSession, RoomProfile, Segment, SessionMarker, utcnow
from app.db.session import db_session
from app.llm.board_ocr import MAX_UPLOAD_BYTES, BoardError, capture_out
from app.routers.deps import api_error, get_ctx, require_session
from app.schemas import (
    ConsentIn,
    ConsentMessageIn,
    DraftPrepareIn,
    RoomProfileIn,
    SoundCheckIn,
    SpeakerPatch,
    course_out,
    marker_out,
    room_profile_out,
    segment_out,
)
from app.services.privacy import redact_segments

router = APIRouter()


# ---------------------------------------------------------------------------
# Hồ sơ phòng học (IC-FR-23)
# ---------------------------------------------------------------------------


@router.get("/room-profiles")
def list_room_profiles(ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    with db_session(ctx.engine) as s:
        rows = list(s.exec(select(RoomProfile).order_by(col(RoomProfile.updated_at).desc())))
    return [room_profile_out(r) for r in rows]


@router.post("/room-profiles", status_code=201)
def create_room_profile(body: RoomProfileIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    row = RoomProfile(name=body.name.strip())
    if body.vad_threshold is not None:
        row.vad_threshold = body.vad_threshold
    if body.recommended_denoise is not None:
        row.recommended_denoise = body.recommended_denoise
    row.min_level_dbfs = body.min_level_dbfs
    with db_session(ctx.engine) as s:
        s.add(row)
        s.commit()
        s.refresh(row)
    return room_profile_out(row)


def _profile(ctx: AppContext, profile_id: int) -> RoomProfile:
    with db_session(ctx.engine) as s:
        row = s.get(RoomProfile, profile_id)
    if row is None:
        raise api_error(404, "ROOM_PROFILE_NOT_FOUND", "Không tìm thấy hồ sơ phòng học")
    return row


@router.get("/room-profiles/{profile_id}")
def get_room_profile(profile_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return room_profile_out(_profile(ctx, profile_id))


@router.put("/room-profiles/{profile_id}")
def update_room_profile(profile_id: int, body: RoomProfileIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        row = s.get(RoomProfile, profile_id)
        if row is None:
            raise api_error(404, "ROOM_PROFILE_NOT_FOUND", "Không tìm thấy hồ sơ phòng học")
        row.name = body.name.strip()
        if body.vad_threshold is not None:
            row.vad_threshold = body.vad_threshold
        if body.recommended_denoise is not None:
            row.recommended_denoise = body.recommended_denoise
        if "min_level_dbfs" in body.model_fields_set:
            row.min_level_dbfs = body.min_level_dbfs
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
    return room_profile_out(row)


@router.delete("/room-profiles/{profile_id}")
def delete_room_profile(profile_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        row = s.get(RoomProfile, profile_id)
        if row is None:
            raise api_error(404, "ROOM_PROFILE_NOT_FOUND", "Không tìm thấy hồ sơ phòng học")
        for sess in s.exec(select(LectureSession).where(LectureSession.room_profile_id == profile_id)):
            sess.room_profile_id = None
            s.add(sess)
        s.delete(row)
        s.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Sound check (IC-FR-20..23)
# ---------------------------------------------------------------------------


def _save_profile(ctx: AppContext, check: SoundCheck) -> None:
    if check.room_profile_id is None or check.result is None:
        return
    r = check.result
    rec = r.recommended
    with db_session(ctx.engine) as s:
        row = s.get(RoomProfile, check.room_profile_id)
        if row is None:
            return
        row.noise_floor_dbfs = round(r.noise_floor_dbfs, 1)
        row.rating = r.rating
        row.snr_db = None if r.snr_db is None else round(r.snr_db, 1)
        if r.speech_level_dbfs is not None or row.min_level_dbfs is None:
            # Chỉ ghi đè ngưỡng khi đo được lời nói (kết quả đầy đủ).
            row.speech_level_dbfs = None if r.speech_level_dbfs is None else round(r.speech_level_dbfs, 1)
            row.vad_threshold = rec["vad_threshold"]
            row.min_level_dbfs = rec["min_level_dbfs"]
            row.recommended_denoise = rec["recommended_denoise"]
        row.updated_at = utcnow()
        s.add(row)
        s.commit()


@router.post("/soundcheck", status_code=201)
async def start_soundcheck(body: SoundCheckIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    if body.room_profile_id is not None:
        _profile(ctx, body.room_profile_id)
    if body.source == "mic_companion":
        owner = ctx.companion.sink_owner
        if owner and owner.startswith("session:"):
            raise api_error(409, "COMPANION_BUSY", "Điện thoại đang làm micro cho một buổi học đang chạy.")
        if ctx.companion.mic_conn is None and not any(c.wants_mic for c in ctx.companion.conns.values()):
            raise api_error(409, "COMPANION_NOT_CONNECTED", "Chưa có điện thoại nào ở chế độ Micro. Hãy ghép nối điện thoại trước.")
        source: CompanionSource | Any = CompanionSource()
    else:
        source = make_source("mic_local", device_id=body.device_id or ctx.settings.get().mic_device)

    def finish(check: SoundCheck) -> None:
        if isinstance(check.source, CompanionSource):
            ctx.companion.detach_sink(check.source)

    check = SoundCheck(source, room_profile_id=body.room_profile_id, on_done=lambda c: _save_profile(ctx, c), on_finish=finish)
    if isinstance(source, CompanionSource):
        ctx.companion.attach_sink(source, owner=f"soundcheck:{check.id}")
    await ctx.soundchecks.start(check)
    return check.as_dict()


@router.get("/soundcheck/{check_id}")
def get_soundcheck(check_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    check = ctx.soundchecks.get(check_id)
    if check is None:
        raise api_error(404, "SOUNDCHECK_NOT_FOUND", "Không tìm thấy lượt kiểm tra âm thanh")
    out = check.as_dict()
    if check.room_profile_id is not None and check.state == "done":
        out["room_profile"] = room_profile_out(_profile(ctx, check.room_profile_id))
    return out


@router.post("/soundcheck/{check_id}/cancel")
async def cancel_soundcheck(check_id: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    check = ctx.soundchecks.get(check_id)
    if check is None:
        raise api_error(404, "SOUNDCHECK_NOT_FOUND", "Không tìm thấy lượt kiểm tra âm thanh")
    await check.cancel()
    return check.as_dict()


# ---------------------------------------------------------------------------
# Chụp bảng (IC-FR-40..44)
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/board-captures")
def list_board_captures(session_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    require_session(ctx, session_id)
    return [capture_out(c) for c in ctx.boards.list(session_id)]


@router.post("/sessions/{session_id}/board-captures", status_code=201)
async def upload_board_capture(
    session_id: int,
    file: UploadFile = File(...),
    t_ms: int | None = Form(default=None),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    require_session(ctx, session_id)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise api_error(413, "FILE_TOO_LARGE", "Ảnh quá lớn (tối đa 20 MB).")
    try:
        row = await ctx.boards.capture(session_id, data, t_ms=t_ms, source="laptop")
    except BoardError as e:
        raise api_error(400, "INVALID_IMAGE", str(e)) from e
    return capture_out(row)


@router.get("/board-captures/{capture_id}/image")
def board_capture_image(capture_id: int, thumb: bool = False, ctx: AppContext = Depends(get_ctx)) -> FileResponse:
    with db_session(ctx.engine) as s:
        row = s.get(BoardCapture, capture_id)
    path = None if row is None else (row.thumb_path if thumb and row.thumb_path else row.image_path)
    if path is None or not Path(path).exists():
        raise api_error(404, "CAPTURE_NOT_FOUND", "Không tìm thấy ảnh")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/board-captures/{capture_id}/retry")
async def retry_board_capture(capture_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    row = await ctx.boards.retry(capture_id)
    if row is None:
        raise api_error(404, "CAPTURE_NOT_FOUND", "Không tìm thấy ảnh")
    return capture_out(row)


@router.delete("/board-captures/{capture_id}")
async def delete_board_capture(capture_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    if ctx.boards.delete(capture_id) is None:
        raise api_error(404, "CAPTURE_NOT_FOUND", "Không tìm thấy ảnh")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Nhãn người nói (IC-FR-31)
# ---------------------------------------------------------------------------


@router.patch("/segments/{segment_id}/speaker")
async def patch_speaker(segment_id: int, body: SpeakerPatch, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            raise api_error(404, "SEGMENT_NOT_FOUND", "Không tìm thấy segment")
        seg.speaker = body.speaker
        seg.speaker_score = 1.0 if body.speaker != "unknown" else None
        s.add(seg)
        s.commit()
        s.refresh(seg)
    runner = ctx.runners.peek(seg.session_id)
    if runner is not None:
        runner.learn_speaker(segment_id, body.speaker)
    ctx.hub.publish(seg.session_id, {"type": "segment.speaker", "id": seg.id, "speaker": seg.speaker, "score": seg.speaker_score})
    if body.speaker == "other" and seg.status == "translated" and ctx.settings.get().discard_other_speech:
        redact_segments(ctx.engine, [segment_id])
    return segment_out(seg)


# ---------------------------------------------------------------------------
# Mốc sự kiện trong transcript
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/markers")
def list_markers(session_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        rows = list(s.exec(select(SessionMarker).where(SessionMarker.session_id == session_id).order_by(SessionMarker.t_ms)))
    return [marker_out(m) for m in rows]


# ---------------------------------------------------------------------------
# Xin phép giảng viên (IC-FR-80/81)
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/consent")
def confirm_consent(course_id: int, body: ConsentIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        course = s.get(Course, course_id)
        if course is None:
            raise api_error(404, "COURSE_NOT_FOUND", "Không tìm thấy môn học")
        course.consent_confirmed_at = utcnow() if body.confirmed else None
        s.add(course)
        s.commit()
        s.refresh(course)
    return course_out(course)


@router.post("/courses/{course_id}/consent-message")
async def consent_message(course_id: int, body: ConsentMessageIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        course = s.get(Course, course_id)
    if course is None:
        raise api_error(404, "COURSE_NOT_FOUND", "Không tìm thấy môn học")
    if not ctx.assistant_llm.has_key:
        raise api_error(400, "NO_API_KEY", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
    name = course.name + (f" ({course.name_it})" if course.name_it else "")
    return await ctx.assistant.consent_message(
        course_name=name, lecturer=body.lecturer or course.lecturer, student_name=body.student_name.strip()
    )


# ---------------------------------------------------------------------------
# Model phụ + pin
# ---------------------------------------------------------------------------


@router.get("/speaker-model")
def speaker_model_status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return ctx.speaker_models.status()


@router.post("/speaker-model/prepare")
async def speaker_model_prepare(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    ctx.speaker_models.start_prepare()
    return ctx.speaker_models.status()


@router.get("/offline/draft")
def draft_status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Model dịch cục bộ đang dùng + danh sách model chọn được (đã tải chưa, tiến độ tải)."""
    return {**ctx.draft.status(), "models": ctx.draft.catalog()}


@router.post("/offline/draft/prepare")
async def draft_prepare(body: DraftPrepareIn | None = None, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    repo = body.repo if body else None
    if repo and repo != ctx.draft.repo:
        if repo not in {str(c["repo"]) for c in LOCAL_MODEL_CHOICES}:
            raise api_error(400, "UNKNOWN_MODEL", "Model dịch cục bộ không nằm trong danh sách hỗ trợ.")
        ctx.draft.start_download(repo)  # tải trước, model đang dùng vẫn chạy tiếp
    else:
        ctx.draft.start_prepare(download=True)
    return {**ctx.draft.status(), "models": ctx.draft.catalog()}


@router.get("/power")
def power_status(ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return {"battery": ctx.power.status()}


@router.get("/whisper-models/{model}")
def whisper_model_cached(model: str, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    return {"model": model, "cached": ctx.stt.model_cached(model)}


@router.get("/inclass/status")
def inclass_status(ctx: AppContext = Depends(get_ctx), session_id: int | None = Query(default=None)) -> dict[str, Any]:
    """Tổng hợp cho màn hình chuẩn bị buổi học."""
    from app.audio.preprocess import available_engines

    return {
        "denoise_engines": available_engines(),
        "speaker_model": ctx.speaker_models.status(),
        "draft_model": ctx.draft.status(),
        "battery": ctx.power.status(),
        "companion": {"running": ctx.companion_server.running, **ctx.companion.status()},
        "session_id": session_id,
    }
