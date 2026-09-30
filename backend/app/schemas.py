"""Model request và hàm serialize response cho API (thời gian trả về dạng ISO-8601 UTC có hậu tố Z)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import INCLASS_SOURCES
from app.db.models import (
    ChatMessage,
    ChatThread,
    CompanionDevice,
    Course,
    Flashcard,
    GlossarySuggestion,
    GlossaryTerm,
    LectureSession,
    Pin,
    RoomProfile,
    Segment,
    SessionMarker,
    SlideDeck,
)

DenoiseLevelIn = Literal["off", "light", "strong"]

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ApiKeyIn(BaseModel):
    api_key: str = Field(min_length=10, max_length=512)


class SttKeyIn(BaseModel):
    provider: Literal["deepgram", "azure"]
    api_key: str = Field(min_length=8, max_length=512)


class CourseIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    name_it: str | None = Field(default=None, max_length=200)
    lecturer: str | None = Field(default=None, max_length=200)


class SessionIn(BaseModel):
    course_id: int | None = None
    title: str | None = Field(default=None, max_length=200)
    audio_mode: Literal["tab", "loopback", "mic_local", "mic_companion"] = "tab"
    # Chế độ lớp học trực tiếp
    room_profile_id: int | None = None
    denoise_level: DenoiseLevelIn | None = None
    battery_saver: bool = False
    mic_device: str | None = Field(default=None, max_length=300)
    confirm_save_audio: bool = False  # IC-FR-83: xác nhận thêm khi bật lưu âm thanh


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    course_id: int | None = None
    denoise_level: DenoiseLevelIn | None = None
    battery_saver: bool | None = None


class SegmentPatch(BaseModel):
    text_it_edited: str = Field(max_length=4000)


class SpeakerPatch(BaseModel):
    speaker: Literal["lecturer", "other", "unknown"]


class RoomProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    vad_threshold: float | None = Field(default=None, ge=0.1, le=0.95)
    recommended_denoise: DenoiseLevelIn | None = None
    min_level_dbfs: float | None = Field(default=None, ge=-100, le=0)


class SoundCheckIn(BaseModel):
    source: Literal["mic_local", "mic_companion"] = "mic_local"
    device_id: str | None = Field(default=None, max_length=300)
    room_profile_id: int | None = None


class ConsentIn(BaseModel):
    confirmed: bool = True


class ConsentMessageIn(BaseModel):
    student_name: str = Field(default="", max_length=120)
    lecturer: str | None = Field(default=None, max_length=200)


class PairIn(BaseModel):
    ip: str | None = Field(default=None, max_length=64)
    session_id: int | None = None


class PinIn(BaseModel):
    segment_id: int | None = None
    kind: Literal["confused", "important", "todo"] = "confused"
    note: str | None = Field(default=None, max_length=2000)


class PinPatch(BaseModel):
    kind: Literal["confused", "important", "todo"] | None = None
    note: str | None = Field(default=None, max_length=2000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    mode: Literal["qa", "compose_it", "speak_it"] = "qa"
    thread_id: int | None = None  # chỉ cho "qa"; bỏ trống → tạo cuộc trò chuyện mới
    images: list[str] = Field(default_factory=list, max_length=4)  # data URL ảnh gửi kèm (chỉ "qa")


class ChatThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatThreadPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class TermIn(BaseModel):
    term_it: str = Field(min_length=1, max_length=300)
    term_vi: str = Field(min_length=1, max_length=300)
    term_en: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)
    course_id: int | None = None


class SuggestionAccept(BaseModel):
    term_vi: str | None = Field(default=None, max_length=300)


class ReviewIn(BaseModel):
    grade: int = Field(ge=0, le=5)


class FlashcardPatch(BaseModel):
    front_it: str | None = Field(default=None, min_length=1, max_length=300)
    back_vi: str | None = Field(default=None, min_length=1, max_length=500)
    pos: str | None = Field(default=None, max_length=60)
    example_it: str | None = Field(default=None, max_length=2000)
    example_vi: str | None = Field(default=None, max_length=2000)


class ToggleIn(BaseModel):
    enabled: bool


class DraftPrepareIn(BaseModel):
    repo: str | None = None  # model khác model đang dùng: chỉ tải về, chưa nạp


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def course_out(c: Course, **extra: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "name_it": c.name_it,
        "lecturer": c.lecturer,
        "created_at": iso(c.created_at),
        "consent_confirmed_at": iso(c.consent_confirmed_at),
        **extra,
    }


def session_out(s: LectureSession, course: Course | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "course_id": s.course_id,
        "course_name": course.name if course else None,
        "title": s.title,
        "started_at": iso(s.started_at),
        "ended_at": iso(s.ended_at),
        "status": s.status,
        "audio_mode": s.audio_mode,
        "capture_source": s.capture_source,
        "inclass": s.capture_source in INCLASS_SOURCES,
        "room_profile_id": s.room_profile_id,
        "denoise_level": s.denoise_level,
        "battery_saver": s.battery_saver,
        "stt_provider": s.stt_provider,
        "cost_usd_est": round(s.cost_usd_est or 0.0, 6),
        "cost_warned": s.cost_warned,
        "translation_enabled": s.translation_enabled,
        "active_ms": s.active_ms,
        "post_status": s.post_status,
        "post_error": s.post_error,
        **extra,
    }


def segment_out(seg: Segment) -> dict[str, Any]:
    return {
        "id": seg.id,
        "seq": seg.seq,
        "t_start_ms": seg.t_start_ms,
        "t_end_ms": seg.t_end_ms,
        "text_it": seg.text_it,
        "text_it_edited": seg.text_it_edited,
        "confidence": seg.confidence,
        "text_vi": seg.text_vi,
        "text_en": seg.text_en,
        "status": seg.status,
        "error": seg.error,
        "speaker": seg.speaker,
        "speaker_score": seg.speaker_score,
        "snr_db": seg.snr_db,
        "translation_kind": seg.translation_kind,
    }


def room_profile_out(r: RoomProfile) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "noise_floor_dbfs": r.noise_floor_dbfs,
        "speech_level_dbfs": r.speech_level_dbfs,
        "snr_db": r.snr_db,
        "rating": r.rating,
        "vad_threshold": r.vad_threshold,
        "min_level_dbfs": r.min_level_dbfs,
        "recommended_denoise": r.recommended_denoise,
        "updated_at": iso(r.updated_at),
    }


def marker_out(m: SessionMarker) -> dict[str, Any]:
    return {"id": m.id, "t_ms": m.t_ms, "kind": m.kind, "message": m.message, "created_at": iso(m.created_at)}


def device_out(d: CompanionDevice, **extra: Any) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "paired_at": iso(d.paired_at),
        "last_seen_at": iso(d.last_seen_at),
        "revoked": d.revoked,
        **extra,
    }


def pin_out(p: Pin) -> dict[str, Any]:
    return {
        "id": p.id,
        "session_id": p.session_id,
        "segment_id": p.segment_id,
        "kind": p.kind,
        "note": p.note,
        "created_at": iso(p.created_at),
    }


def deck_out(d: SlideDeck) -> dict[str, Any]:
    return {
        "id": d.id,
        "session_id": d.session_id,
        "filename": d.filename,
        "page_count": d.page_count,
        "size_bytes": d.size_bytes,
        "summary": d.summary,
        "has_text": bool(d.extracted_text.replace("--- Trang", "").strip()),
        "uploaded_to_claude": bool(d.file_id),
        "status": d.status,
        "error": d.error,
        "created_at": iso(d.created_at),
    }


def term_out(t: GlossaryTerm) -> dict[str, Any]:
    return {
        "id": t.id,
        "course_id": t.course_id,
        "term_it": t.term_it,
        "term_vi": t.term_vi,
        "term_en": t.term_en,
        "note": t.note,
        "source": t.source,
        "updated_at": iso(t.updated_at),
    }


def suggestion_out(s: GlossarySuggestion) -> dict[str, Any]:
    return {
        "id": s.id,
        "session_id": s.session_id,
        "deck_id": s.deck_id,
        "capture_id": s.capture_id,
        "course_id": s.course_id,
        "term_it": s.term_it,
        "term_vi": s.term_vi,
        "term_en": s.term_en,
        "note": s.note,
        "status": s.status,
    }


def thread_out(t: ChatThread, message_count: int = 0) -> dict[str, Any]:
    return {
        "id": t.id,
        "session_id": t.session_id,
        "title": t.title,
        "message_count": message_count,
        "created_at": iso(t.created_at),
        "updated_at": iso(t.updated_at),
    }


def chat_out(m: ChatMessage, images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "images": images or [],
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "mode": m.mode,
        "thread_id": m.thread_id,
        "model": m.model,
        "created_at": iso(m.created_at),
    }
    if m.mode in ("compose_it", "speak_it") and m.role == "assistant":
        try:
            out["data"] = json.loads(m.content)
        except ValueError:
            out["data"] = None
    return out


def flashcard_out(f: Flashcard) -> dict[str, Any]:
    return {
        "id": f.id,
        "session_id": f.session_id,
        "front_it": f.front_it,
        "back_vi": f.back_vi,
        "pos": f.pos,
        "example_it": f.example_it,
        "example_vi": f.example_vi,
        "ease": f.ease,
        "interval_days": f.interval_days,
        "repetitions": f.repetitions,
        "due_at": iso(f.due_at),
        "last_reviewed_at": iso(f.last_reviewed_at),
    }
