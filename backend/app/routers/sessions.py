"""/sessions, /segments, /pins, tóm tắt, chi phí, số đo độ trễ, đề xuất thuật ngữ."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlmodel import col, select

from app.audio.sources.base import SourceError
from app.audio.sources.mic_local import probe_microphone
from app.config import INCLASS_SOURCES
from app.context import AppContext
from app.db.models import (
    BoardCapture,
    Course,
    Flashcard,
    GlossarySuggestion,
    LectureSession,
    Pin,
    RoomProfile,
    Segment,
    SessionMarker,
    SlideDeck,
    Summary,
)
from app.db.session import db_session
from app.llm.board_ocr import capture_out
from app.routers.deps import api_error, get_ctx, require_session
from app.schemas import (
    PinIn,
    PinPatch,
    SegmentPatch,
    SessionIn,
    SessionPatch,
    SuggestionAccept,
    ToggleIn,
    deck_out,
    flashcard_out,
    iso,
    marker_out,
    pin_out,
    room_profile_out,
    segment_out,
    session_out,
    suggestion_out,
    term_out,
)
from app.services.pins import create_pin as create_pin_record

router = APIRouter()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get("/sessions")
def list_sessions(
    course_id: int | None = None,
    status: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    ctx: AppContext = Depends(get_ctx),
) -> list[dict[str, Any]]:
    with db_session(ctx.engine) as s:
        stmt = select(LectureSession)
        if course_id is not None:
            stmt = stmt.where(LectureSession.course_id == course_id)
        if status:
            stmt = stmt.where(LectureSession.status == status)
        sessions = list(s.exec(stmt.order_by(col(LectureSession.started_at).desc()).limit(limit)))
        courses = {c.id: c for c in s.exec(select(Course))}
        seg_counts = dict(
            s.exec(select(Segment.session_id, func.count(Segment.id)).group_by(Segment.session_id)).all()
        )
        pin_counts = dict(s.exec(select(Pin.session_id, func.count(Pin.id)).group_by(Pin.session_id)).all())
    return [
        session_out(
            sess,
            courses.get(sess.course_id),
            segment_count=seg_counts.get(sess.id, 0),
            pin_count=pin_counts.get(sess.id, 0),
        )
        for sess in sessions
    ]


@router.post("/sessions", status_code=201)
async def create_session(body: SessionIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    settings = ctx.settings.get()
    if not ctx.stt.ready:
        ctx.stt.start_prepare()
        raise api_error(
            409,
            "STT_NOT_READY",
            "Nhận dạng giọng nói chưa sẵn sàng (đang tải/nạp model). Vui lòng đợi rồi thử lại.",
        )
    course = None
    inclass = body.audio_mode in INCLASS_SOURCES
    with db_session(ctx.engine) as s:
        if body.course_id is not None:
            course = s.get(Course, body.course_id)
            if course is None:
                raise api_error(404, "COURSE_NOT_FOUND", "Không tìm thấy môn học")
        if inclass:
            _check_inclass(ctx, body, course)
            if body.room_profile_id is not None and s.get(RoomProfile, body.room_profile_id) is None:
                raise api_error(404, "ROOM_PROFILE_NOT_FOUND", "Không tìm thấy hồ sơ phòng học")
    if inclass and body.audio_mode == "mic_local":
        try:  # IC-E-01: không bắt đầu phiên khi không mở được micro
            await asyncio.to_thread(probe_microphone, body.mic_device or settings.mic_device)
        except SourceError as e:
            raise api_error(409, "MIC_UNAVAILABLE", str(e)) from e
    with db_session(ctx.engine) as s:
        title = (body.title or "").strip() or f"Buổi học {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        sess = LectureSession(
            course_id=body.course_id,
            title=title,
            audio_mode=body.audio_mode,
            capture_source=body.audio_mode,
            stt_provider=settings.stt_provider,
            status="live",
        )
        if inclass:
            sess.room_profile_id = body.room_profile_id
            sess.denoise_level = body.denoise_level
            sess.battery_saver = body.battery_saver
            sess.mic_device = body.mic_device
        s.add(sess)
        s.commit()
        s.refresh(sess)
    await ctx.runners.get(sess.id)  # type: ignore[arg-type]
    return {"session_id": sess.id, "session": session_out(sess, course)}


def _check_inclass(ctx: AppContext, body: SessionIn, course: Course | None) -> None:
    """Điều kiện bắt đầu phiên thu âm trực tiếp (IC-FR-80, IC-FR-83, IC-AC-09)."""
    if course is None:
        raise api_error(422, "COURSE_REQUIRED", "Chế độ lớp học trực tiếp cần chọn môn học (để ghi nhận việc xin phép).")
    if course.consent_confirmed_at is None:
        raise api_error(
            409,
            "CONSENT_REQUIRED",
            "Bạn cần xác nhận đã xin phép giảng viên sử dụng công cụ phụ đề cho môn này trước khi thu âm.",
        )
    if ctx.settings.get().save_raw_audio and not body.confirm_save_audio:
        raise api_error(
            409,
            "AUDIO_SAVE_CONFIRM_REQUIRED",
            "Đang bật lưu âm thanh gốc. Với lớp học trực tiếp, hãy xác nhận thêm lần nữa (giọng giảng viên và bạn học "
            "là dữ liệu cá nhân) hoặc tắt lưu âm thanh trong Cài đặt.",
        )


def _session_detail(ctx: AppContext, sess: LectureSession, after_seq: int, limit: int) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        course = s.get(Course, sess.course_id) if sess.course_id else None
        segs = list(
            s.exec(
                select(Segment)
                .where(Segment.session_id == sess.id, Segment.seq > after_seq)
                .order_by(Segment.seq)
                .limit(limit + 1)
            )
        )
        pins = list(s.exec(select(Pin).where(Pin.session_id == sess.id).order_by(Pin.id)))
        decks = list(s.exec(select(SlideDeck).where(SlideDeck.session_id == sess.id).order_by(SlideDeck.id)))
        has_summary = s.get(Summary, sess.id) is not None
        boards = list(s.exec(select(BoardCapture).where(BoardCapture.session_id == sess.id).order_by(BoardCapture.t_ms)))
        markers = list(
            s.exec(select(SessionMarker).where(SessionMarker.session_id == sess.id).order_by(SessionMarker.t_ms))
        )
        room = s.get(RoomProfile, sess.room_profile_id) if sess.room_profile_id else None
    has_more = len(segs) > limit
    runner = ctx.runners.peek(sess.id)  # type: ignore[arg-type]
    return {
        "session": session_out(sess, course, has_summary=has_summary),
        "course": {
            "id": course.id,
            "name": course.name,
            "name_it": course.name_it,
            "lecturer": course.lecturer,
            "consent_confirmed_at": iso(course.consent_confirmed_at),
        }
        if course
        else None,
        "segments": [segment_out(sg) for sg in segs[:limit]],
        "has_more": has_more,
        "pins": [pin_out(p) for p in pins],
        "slides": [deck_out(d) for d in decks],
        "board_captures": [capture_out(b) for b in boards],
        "markers": [marker_out(m) for m in markers],
        "room_profile": room_profile_out(room) if room else None,
        "runner": runner.status_event() if runner else None,
    }


@router.get("/sessions/{session_id}")
def get_session(
    session_id: int,
    after_seq: int = -1,
    limit: int = Query(5000, ge=1, le=20000),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    sess = require_session(ctx, session_id)
    return _session_detail(ctx, sess, after_seq, limit)


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: int, body: SessionPatch, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        sess = s.get(LectureSession, session_id)
        if sess is None:
            raise api_error(404, "SESSION_NOT_FOUND", "Không tìm thấy phiên học")
        if body.title is not None:
            sess.title = body.title.strip()
        if "course_id" in body.model_fields_set:
            sess.course_id = body.course_id
        s.add(sess)
        s.commit()
        s.refresh(sess)
    ctx.context.invalidate(session_id)
    # Tuỳ chọn lớp học trong lúc học: khử nhiễu (IC-FR-11), tiết kiệm pin (IC-FR-70)
    runner = ctx.runners.peek(session_id)
    if body.denoise_level is not None or body.battery_saver is not None:
        with db_session(ctx.engine) as s:
            row = s.get(LectureSession, session_id)
            if row is not None and runner is None:
                if body.denoise_level is not None:
                    row.denoise_level = body.denoise_level
                if body.battery_saver is not None:
                    row.battery_saver = body.battery_saver
                s.add(row)
                s.commit()
        if runner is not None:
            if body.battery_saver is not None:
                runner.set_battery_saver(body.battery_saver)
            if body.denoise_level is not None:
                runner.set_denoise(body.denoise_level)
        with db_session(ctx.engine) as s:
            sess = s.get(LectureSession, session_id) or sess
    return session_out(sess)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    if ctx.runners.peek(session_id) is not None:
        await ctx.runners.end(session_id)
    with db_session(ctx.engine) as s:
        deck_ids = [d.id for d in s.exec(select(SlideDeck).where(SlideDeck.session_id == session_id))]
    for deck_id in deck_ids:
        await ctx.slides.delete(deck_id)  # type: ignore[arg-type]
    with db_session(ctx.engine) as s:
        sess = s.get(LectureSession, session_id)
        if sess is not None:
            s.delete(sess)
            s.commit()
    shutil.rmtree(ctx.env.data_dir / "slides" / str(session_id), ignore_errors=True)
    shutil.rmtree(ctx.env.data_dir / "board" / str(session_id), ignore_errors=True)
    shutil.rmtree(ctx.env.data_dir / "chat" / str(session_id), ignore_errors=True)
    return {"deleted": True}


@router.post("/sessions/{session_id}/pause")
async def pause_session(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    sess = require_session(ctx, session_id)
    if sess.status == "ended":
        raise api_error(409, "SESSION_ENDED", "Phiên đã kết thúc")
    runner = await ctx.runners.get(session_id)
    if runner is not None:
        await runner.pause()
    return {"status": "paused"}


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    sess = require_session(ctx, session_id)
    if sess.status == "ended":
        raise api_error(409, "SESSION_ENDED", "Phiên đã kết thúc")
    runner = await ctx.runners.get(session_id)
    if runner is None:
        raise api_error(409, "SESSION_ENDED", "Phiên đã kết thúc")
    await runner.resume()
    return {"status": "live"}


@router.post("/sessions/{session_id}/end")
async def end_session(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    sess = require_session(ctx, session_id)
    if sess.status != "ended":
        await ctx.runners.end(session_id)
    ctx.post.start(session_id)
    return {"status": "ended", "post_status": "pending"}


@router.post("/sessions/{session_id}/translation")
async def toggle_translation(session_id: int, body: ToggleIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    ctx.translation.set_enabled(session_id, body.enabled)
    runner = ctx.runners.peek(session_id)
    if runner is not None:
        runner.publish_status()
    return {"translation_enabled": body.enabled}


@router.post("/sessions/{session_id}/retry-failed")
async def retry_failed(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    ctx.translation.auth_failed = False
    n = ctx.translation.requeue(session_id, statuses=("failed", "transcribed"))
    ctx.translation.wake_all()
    return {"requeued": n}


@router.post("/sessions/{session_id}/retranslate")
async def retranslate_machine(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """Dịch lại bằng Claude các câu đang là bản dịch máy (nháp / model cục bộ)."""
    require_session(ctx, session_id)
    if ctx.translation.local_engine:
        raise api_error(409, "LOCAL_ENGINE", "Đang dịch bằng model trên máy — chọn Claude trước khi dịch lại.")
    return {"requeued": ctx.translation.retranslate_machine(session_id)}


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------


@router.patch("/segments/{segment_id}")
async def patch_segment(segment_id: int, body: SegmentPatch, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    """FR-TR-05: sửa text Ý rồi tự dịch lại."""
    text = body.text_it_edited.strip()
    with db_session(ctx.engine) as s:
        seg = s.get(Segment, segment_id)
        if seg is None:
            raise api_error(404, "SEGMENT_NOT_FOUND", "Không tìm thấy segment")
        seg.text_it_edited = text if text and text != seg.text_it else None
        seg.status = "transcribed"
        seg.error = None
        s.add(seg)
        s.commit()
        s.refresh(seg)
    ctx.hub.publish(seg.session_id, {"type": "segment.updated", "segment": segment_out(seg)})
    ctx.translation.enqueue(seg.session_id, seg.id, front=True)  # type: ignore[arg-type]
    return segment_out(seg)


@router.post("/segments/{segment_id}/retry")
async def retry_segment(segment_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        seg = s.get(Segment, segment_id)
    if seg is None:
        raise api_error(404, "SEGMENT_NOT_FOUND", "Không tìm thấy segment")
    ctx.translation.auth_failed = False
    ctx.translation.enqueue(seg.session_id, segment_id, front=True)
    ctx.translation.wake_all()
    return {"queued": True}


# ---------------------------------------------------------------------------
# Pins
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/pins")
def list_pins(session_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        pins = list(s.exec(select(Pin).where(Pin.session_id == session_id).order_by(Pin.id)))
    return [pin_out(p) for p in pins]


@router.post("/sessions/{session_id}/pins", status_code=201)
async def create_pin(session_id: int, body: PinIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    return create_pin_record(ctx, session_id, body.segment_id, body.kind, body.note)


@router.patch("/pins/{pin_id}")
async def patch_pin(pin_id: int, body: PinPatch, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        pin = s.get(Pin, pin_id)
        if pin is None:
            raise api_error(404, "PIN_NOT_FOUND", "Không tìm thấy ghim")
        if body.kind is not None:
            pin.kind = body.kind
        if "note" in body.model_fields_set:
            pin.note = body.note
        s.add(pin)
        s.commit()
        s.refresh(pin)
    ctx.hub.publish(pin.session_id, {"type": "pin.updated", "pin": pin_out(pin)})
    return pin_out(pin)


@router.delete("/pins/{pin_id}")
async def delete_pin(pin_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        pin = s.get(Pin, pin_id)
        if pin is None:
            raise api_error(404, "PIN_NOT_FOUND", "Không tìm thấy ghim")
        session_id = pin.session_id
        s.delete(pin)
        s.commit()
    ctx.hub.publish(session_id, {"type": "pin.deleted", "id": pin_id})
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Summary / flashcards / cost / metrics
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/summary")
def get_summary(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    sess = require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        summary = s.get(Summary, session_id)
    return {
        "status": sess.post_status,
        "error": sess.post_error,
        "running": ctx.post.running(session_id),
        "summary": {"content_md": summary.content_md, "model": summary.model, "created_at": iso(summary.created_at)}
        if summary
        else None,
    }


@router.post("/sessions/{session_id}/summary/regenerate", status_code=202)
async def regenerate_summary(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    if not ctx.assistant_llm.has_key:
        raise api_error(400, "NO_API_KEY", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
    ctx.post.start(session_id, summary=True, flashcards=False)
    return {"status": "pending"}


@router.get("/sessions/{session_id}/flashcards")
def session_flashcards(session_id: int, ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        cards = list(s.exec(select(Flashcard).where(Flashcard.session_id == session_id).order_by(Flashcard.id)))
    return [flashcard_out(c) for c in cards]


@router.post("/sessions/{session_id}/flashcards/regenerate", status_code=202)
async def regenerate_flashcards(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    if not ctx.assistant_llm.has_key:
        raise api_error(400, "NO_API_KEY", "Chưa có API key Claude hoặc chưa bật gói Pro cho trợ lý (Cài đặt → Trợ lý học tập).")
    ctx.post.start(session_id, summary=False, flashcards=True)
    return {"status": "pending"}


@router.get("/sessions/{session_id}/cost")
def session_cost(session_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    require_session(ctx, session_id)
    return ctx.usage.breakdown(session_id)


@router.get("/sessions/{session_id}/metrics", response_model=None)
def session_metrics(
    session_id: int, format: str = "json", ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any] | PlainTextResponse:
    require_session(ctx, session_id)
    if format == "csv":
        return PlainTextResponse(
            ctx.metrics.csv(session_id),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="latency_session_{session_id}.csv"'},
        )
    return ctx.metrics.summary(session_id)


# ---------------------------------------------------------------------------
# Glossary suggestions (FR-SL-02)
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/glossary-suggestions")
def list_suggestions(
    session_id: int, status: str = "pending", ctx: AppContext = Depends(get_ctx)
) -> list[dict[str, Any]]:
    require_session(ctx, session_id)
    with db_session(ctx.engine) as s:
        rows = list(
            s.exec(
                select(GlossarySuggestion)
                .where(GlossarySuggestion.session_id == session_id, GlossarySuggestion.status == status)
                .order_by(GlossarySuggestion.term_it)
            )
        )
    return [suggestion_out(r) for r in rows]


@router.post("/glossary-suggestions/{suggestion_id}/accept")
def accept_suggestion(
    suggestion_id: int, body: SuggestionAccept | None = None, ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    term = ctx.slides.accept_suggestion(suggestion_id, term_vi=body.term_vi if body else None)
    if term is None:
        raise api_error(404, "SUGGESTION_NOT_FOUND", "Không tìm thấy đề xuất đang chờ duyệt")
    return term_out(term)


@router.post("/glossary-suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    if not ctx.slides.reject_suggestion(suggestion_id):
        raise api_error(404, "SUGGESTION_NOT_FOUND", "Không tìm thấy đề xuất")
    return {"rejected": True}
