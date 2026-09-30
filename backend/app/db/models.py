"""Các bảng SQLite (mục 8 của spec). Thời gian lưu và trả về dạng UTC có tzinfo (SQLModel ``UTCDateTime``)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    return datetime.now(UTC)


class Course(SQLModel, table=True):
    __tablename__ = "course"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    name_it: str | None = None
    lecturer: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    consent_confirmed_at: datetime | None = None  # IC-FR-80: đã xin phép giảng viên


class RoomProfile(SQLModel, table=True):
    """Hồ sơ âm thanh của một phòng học (IC-FR-23), hiệu chỉnh bằng sound check."""

    __tablename__ = "room_profile"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    noise_floor_dbfs: float | None = None
    speech_level_dbfs: float | None = None
    snr_db: float | None = None
    rating: str | None = None  # good | fair | poor
    vad_threshold: float = 0.6
    min_level_dbfs: float | None = None  # IC-FR-13: utterance nhỏ hơn mức này không đưa vào STT
    recommended_denoise: str = "light"  # off | light | strong
    updated_at: datetime = Field(default_factory=utcnow)


class LectureSession(SQLModel, table=True):
    """Một buổi học (bảng ``session``)."""

    __tablename__ = "session"

    id: int | None = Field(default=None, primary_key=True)
    course_id: int | None = Field(default=None, foreign_key="course.id", ondelete="SET NULL", index=True)
    title: str
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    status: str = "live"  # live | paused | ended
    audio_mode: str = "tab"  # tab | loopback | mic_local | mic_companion (giữ để tương thích)
    capture_source: str = "tab"  # tab | loopback | mic_local | mic_companion
    room_profile_id: int | None = Field(default=None, foreign_key="room_profile.id", ondelete="SET NULL")
    denoise_level: str | None = None  # off | light | strong (chỉ chế độ lớp học)
    battery_saver: bool = False
    mic_device: str | None = None
    stt_provider: str = "whisper_local"
    cost_usd_est: float = 0.0
    cost_warned: bool = False
    translation_enabled: bool = True
    active_ms: int = 0
    post_status: str = "none"  # none | pending | summary | flashcards | done | failed
    post_error: str | None = None


class Segment(SQLModel, table=True):
    __tablename__ = "segment"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_segment_session_seq"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    seq: int
    t_start_ms: int
    t_end_ms: int
    text_it: str
    text_it_edited: str | None = None
    confidence: float = 1.0
    text_vi: str | None = None
    text_en: str | None = None
    # transcribed | pending_translation (ngoại tuyến) | translating | translated | failed | redacted
    status: str = "transcribed"
    error: str | None = None
    speaker: str = "unknown"  # lecturer | other | unknown (IC-FR-30)
    speaker_score: float | None = None
    snr_db: float | None = None
    translation_kind: str | None = None  # claude | draft (IC-FR-51)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def source_text(self) -> str:
        return self.text_it_edited or self.text_it

    @property
    def redacted(self) -> bool:
        """Lời người khác đã bị xoá nội dung ("Không lưu lời người khác", mục 11)."""
        return self.status == "redacted"


class SegmentMetric(SQLModel, table=True):
    """Độ trễ từng stage của một segment (phục vụ đo NFR-01/02)."""

    __tablename__ = "segment_metric"

    segment_id: int = Field(primary_key=True, foreign_key="segment.id", ondelete="CASCADE")
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    audio_ms: int = 0
    stt_ms: int | None = None
    it_latency_ms: int | None = None
    vi_latency_ms: int | None = None


class SlideDeck(SQLModel, table=True):
    __tablename__ = "slide_deck"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    filename: str
    path: str
    page_count: int = 0
    size_bytes: int = 0
    extracted_text: str = ""
    summary: str | None = None
    file_id: str | None = None  # id trên Files API của Anthropic (nếu đã upload)
    status: str = "processing"  # processing | ready | failed
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class GlossaryTerm(SQLModel, table=True):
    __tablename__ = "glossary_term"

    id: int | None = Field(default=None, primary_key=True)
    course_id: int | None = Field(default=None, foreign_key="course.id", ondelete="CASCADE", index=True)
    term_it: str
    term_vi: str
    term_en: str | None = None
    note: str | None = None
    source: str = "manual"  # manual | csv | slides
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class GlossarySuggestion(SQLModel, table=True):
    """Thuật ngữ Claude đề xuất từ slide, chờ người dùng duyệt (FR-SL-02)."""

    __tablename__ = "glossary_suggestion"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    deck_id: int | None = Field(default=None, foreign_key="slide_deck.id", ondelete="CASCADE")
    capture_id: int | None = Field(default=None, foreign_key="board_capture.id", ondelete="CASCADE")
    course_id: int | None = None
    term_it: str
    term_vi: str
    term_en: str | None = None
    note: str | None = None
    status: str = "pending"  # pending | accepted | rejected
    created_at: datetime = Field(default_factory=utcnow)


class Pin(SQLModel, table=True):
    __tablename__ = "pin"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    segment_id: int | None = Field(default=None, foreign_key="segment.id", ondelete="SET NULL")
    kind: str = "confused"  # confused | important | todo
    note: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ChatThread(SQLModel, table=True):
    """Một cuộc trò chuyện hỏi đáp trong buổi học; mỗi thread có lịch sử riêng (ngữ cảnh chat tách biệt)."""

    __tablename__ = "chat_thread"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    title: str = ""  # rỗng → đặt theo câu hỏi đầu tiên
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    thread_id: int | None = Field(default=None, foreign_key="chat_thread.id", ondelete="CASCADE", index=True)
    role: str  # user | assistant
    content: str
    mode: str = "qa"  # qa | compose_it | speak_it (chỉ "qa" thuộc thread)
    model: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ChatAttachment(SQLModel, table=True):
    """Ảnh học viên gửi kèm câu hỏi (đã xoay EXIF, bỏ metadata, thu nhỏ ≤ 1568 px, JPEG)."""

    __tablename__ = "chat_attachment"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    message_id: int | None = Field(default=None, foreign_key="chat_message.id", ondelete="CASCADE", index=True)
    image_path: str
    thumb_path: str
    width: int
    height: int
    created_at: datetime = Field(default_factory=utcnow)


class Summary(SQLModel, table=True):
    __tablename__ = "summary"

    session_id: int = Field(primary_key=True, foreign_key="session.id", ondelete="CASCADE")
    content_md: str
    model: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Flashcard(SQLModel, table=True):
    __tablename__ = "flashcard"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="session.id", ondelete="SET NULL", index=True)
    front_it: str
    back_vi: str
    pos: str | None = None
    example_it: str | None = None
    example_vi: str | None = None
    ease: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    due_at: datetime = Field(default_factory=utcnow, index=True)
    last_reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class LlmUsage(SQLModel, table=True):
    __tablename__ = "llm_usage"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="session.id", ondelete="SET NULL", index=True)
    purpose: str  # translate | chat | compose | summary | flashcard | slides | rolling_summary | validate
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # tổng token ghi cache (5 phút + 1 giờ)
    cache_write_1h_tokens: int = 0  # phần ghi cache TTL 1 giờ (giá khác)
    cost_usd_est: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)


class TranscriptBlockSummary(SQLModel, table=True):
    """Rolling summary cho từng khối 30 phút của transcript (FR-AS-06)."""

    __tablename__ = "transcript_block_summary"
    __table_args__ = (UniqueConstraint("session_id", "block_index", name="uq_block_summary"),)

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    block_index: int
    t_start_ms: int
    t_end_ms: int
    last_seq: int
    content: str
    model: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class BoardCapture(SQLModel, table=True):
    """Ảnh chụp bảng / màn chiếu gắn với mốc thời gian của phiên (IC-FR-40..44)."""

    __tablename__ = "board_capture"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    t_ms: int
    image_path: str
    thumb_path: str | None = None
    width: int = 0
    height: int = 0
    status: str = "processing"  # processing | done | unreadable | failed
    extracted_it: str | None = None
    extracted_latex: str | None = None  # JSON list
    terms_json: str | None = None  # JSON list [{it, vi, en}]
    translation_vi: str | None = None
    unreadable: str | None = None
    error: str | None = None
    source: str = "laptop"  # laptop | phone
    created_at: datetime = Field(default_factory=utcnow)


class SessionMarker(SQLModel, table=True):
    """Mốc sự kiện hiển thị xen kẽ trong transcript (ví dụ gián đoạn nguồn âm thanh, IC-E-03)."""

    __tablename__ = "session_marker"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", ondelete="CASCADE", index=True)
    t_ms: int
    kind: str  # source_lost | source_fallback | source_restored | battery_saver | denoise_changed
    message: str
    created_at: datetime = Field(default_factory=utcnow)


class CompanionDevice(SQLModel, table=True):
    """Điện thoại đã ghép nối (IC-FR-03). Chỉ lưu hash của token thiết bị."""

    __tablename__ = "companion_device"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    token_hash: str = Field(index=True)
    paired_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime | None = None
    revoked: bool = False


class AppSettingRow(SQLModel, table=True):
    __tablename__ = "app_setting"

    key: str = Field(primary_key=True)
    value: str
    updated_at: datetime = Field(default_factory=utcnow)
