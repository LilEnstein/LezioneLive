"""Hàng đợi dịch theo phiên (mục 10.2 "Quy tắc xử lý hàng đợi dịch").

- Tối đa ``translate_concurrency`` (mặc định 3) request đồng thời, FIFO; UI hiển thị theo ``seq``.
- Hàng đợi > 10 (mạng chậm) hoặc bị rate limit kéo dài > 2 phút → gộp các segment liền kề vào 1 request.
- 429/529/5xx: backoff luỹ thừa 1, 2, 4… tối đa 30 s, segment giữ trạng thái ``translating``.
- Lỗi mạng: đánh dấu ``failed`` rồi tự dịch bù khi có mạng trở lại.
- Key sai (401): dừng dịch, giữ transcript; dịch lại khi người dùng cập nhật key.

Chế độ ngoại tuyến (feature_spec Flow E, IC-FR-50/51):
- Segment tới khi mất mạng được lưu trạng thái ``pending_translation`` và vào hàng "tồn đọng"; nếu bật dịch
  nháp thì có bản nháp từ model cục bộ (``translation_kind = draft``).
- Có mạng lại: dịch bù tồn đọng theo thứ tự thời gian, gộp tối đa 5 segment mỗi request; segment mới đang diễn
  ra luôn được ưu tiên dịch trước phần tồn đọng.

Dịch bằng model trên máy (``translate_provider = local``): không cần mạng nên không có tồn đọng/backoff; các segment
đang chờ được dịch gộp một lượt, bản dịch lưu ``translation_kind = local`` (có thể dịch lại bằng Claude sau).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from sqlmodel import col, select

from app.db.models import LectureSession, Segment
from app.db.session import db_session
from app.llm.client import LLMError
from app.llm.translator import Translator
from app.pipeline.connectivity import ConnectivityMonitor
from app.pipeline.context import ContextProvider
from app.pipeline.events import EventHub
from app.services.privacy import redact_segments
from app.services.settings_store import SettingsStore

if TYPE_CHECKING:
    from app.llm.client import LLMClient
    from app.offline.draft_translator import DraftTranslator
    from app.services.metrics import MetricsStore

log = logging.getLogger(__name__)

BATCH_TRIGGER = 10
BATCH_SIZE = 6
BACKLOG_BATCH_SIZE = 5  # Flow E: gộp tối đa 5 segment tồn đọng mỗi request
SLOW_BATCH_AFTER_S = 120.0
SLOW_CLEAR_AFTER_S = 30.0
MAX_BACKOFF_S = 30.0
PENDING_STATUS = "pending_translation"
MACHINE_KINDS = ("draft", "local")  # bản dịch do model trên máy tạo (chất lượng thấp hơn Claude)


class TranslationService:
    def __init__(
        self,
        *,
        engine: Engine,
        settings: SettingsStore,
        translator: Translator,
        llm: LLMClient,
        context: ContextProvider,
        hub: EventHub,
        connectivity: ConnectivityMonitor,
        metrics: MetricsStore | None = None,
        draft: DraftTranslator | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.translator = translator
        self.llm = llm
        self.context = context
        self.hub = hub
        self.connectivity = connectivity
        self.metrics = metrics
        self.draft = draft
        self._queues: dict[int, SessionTranslationQueue] = {}
        self._backoff_until = 0.0
        self._backoff_attempt = 0
        self._slow_since: float | None = None
        self._last_rate_limit = 0.0
        self.auth_failed = False
        self._draft_q: asyncio.Queue[tuple[int, int]] | None = None
        self._draft_task: asyncio.Task | None = None
        connectivity.add_listener(self._on_connectivity)
        if draft is not None and hasattr(draft, "add_ready_listener"):
            draft.add_ready_listener(self._on_local_ready)

    @property
    def local_engine(self) -> bool:
        """Đang dịch bằng model trên máy (không cần mạng, không phụ thuộc Claude)."""
        return self.translator.engine == "local"

    def _on_local_ready(self) -> None:
        # Vừa tải/nạp xong model: dịch các câu của phiên đang mở đã lỗi vì "chưa có model".
        if self.local_engine:
            self.on_api_key_changed()

    # ------------------------------------------------------------- queues
    def queue(self, session_id: int) -> SessionTranslationQueue:
        q = self._queues.get(session_id)
        if q is None:
            with db_session(self.engine) as s:
                sess = s.get(LectureSession, session_id)
            enabled = bool(sess.translation_enabled) if sess else True
            q = SessionTranslationQueue(self, session_id, enabled=enabled)
            self._queues[session_id] = q
        return q

    def enqueue(self, session_id: int, segment_id: int, *, front: bool = False, backlog: bool = False) -> None:
        self.queue(session_id).enqueue(segment_id, front=front, backlog=backlog)

    def queue_len(self, session_id: int) -> int:
        q = self._queues.get(session_id)
        return (len(q.pending) + len(q.backlog) + len(q.in_flight)) if q else 0

    def network_state(self, session_id: int) -> dict:
        return {"type": "network.state", "online": self.connectivity.online, "pending_translations": self.queue_len(session_id)}

    def publish_network_state(self, session_id: int | None = None) -> None:
        for sid in [session_id] if session_id is not None else list(self._queues):
            self.hub.publish(sid, self.network_state(sid))

    # ---------------------------------------------------- ngoại tuyến
    def mark_pending(self, segment_ids: list[int], session_id: int) -> None:
        """Mất mạng: segment chờ dịch lưu trạng thái ``pending_translation`` (IC-FR-50)."""
        changed: list[int] = []
        with db_session(self.engine) as s:
            for seg in s.exec(select(Segment).where(col(Segment.id).in_(segment_ids))):
                if seg.status == "transcribed":
                    seg.status = PENDING_STATUS
                    s.add(seg)
                    changed.append(seg.id)  # type: ignore[arg-type]
            s.commit()
        for sid in changed:
            self.hub.publish(session_id, {"type": "segment.pending", "id": sid})
            self._schedule_draft(session_id, sid)
        if changed:
            self.publish_network_state(session_id)

    def key_blocked(self) -> bool:
        """Không dịch được bằng Claude vì thiếu/sai API key."""
        return self.auth_failed or not self.translator.ready

    def _schedule_draft(self, session_id: int, segment_id: int) -> None:
        # Thiếu/sai key: luôn dùng model cục bộ nếu đã tải sẵn, để vẫn có phụ đề Việt.
        # Đang dịch chính bằng model cục bộ thì không cần bản nháp.
        if self.draft is None or self.local_engine or not (self.settings.get().offline_draft or self.key_blocked()):
            return
        if not self.draft.ready:
            self.draft.start_prepare(download=False)  # chỉ nạp nếu model đã có sẵn (đang mất mạng)
        if self._draft_q is None:
            self._draft_q = asyncio.Queue()
        if self._draft_task is None or self._draft_task.done():
            self._draft_task = asyncio.create_task(self._draft_worker())
        self._draft_q.put_nowait((session_id, segment_id))

    async def _draft_worker(self) -> None:
        assert self._draft_q is not None
        while True:
            session_id, segment_id = await self._draft_q.get()
            draft = self.draft
            if draft is None:
                continue
            if not draft.ready:
                task = draft.start_prepare(download=False)
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=120)
                except (TimeoutError, Exception):  # noqa: BLE001
                    pass
                if not draft.ready:
                    continue
            found = self.load([segment_id])
            if not found or found[0].status not in (PENDING_STATUS, "transcribed", "failed"):
                continue
            seg = found[0]
            try:
                glossary = [(g.term_it, g.term_vi) for g in self.context.translation_context(seg).glossary]
                text = await draft.translate(seg.source_text, glossary=glossary)
            except Exception:  # noqa: BLE001
                log.exception("Dịch nháp lỗi")
                continue
            if text and self.save_draft(seg, text):
                self.hub.publish(session_id, {"type": "segment.draft", "id": seg.id, "text_vi": text})

    def enabled(self, session_id: int) -> bool:
        """Dịch có đang bật cho phiên không (không tạo hàng đợi mới)."""
        q = self._queues.get(session_id)
        if q is not None:
            return q.enabled
        with db_session(self.engine) as s:
            sess = s.get(LectureSession, session_id)
        return bool(sess.translation_enabled) if sess else True

    async def drain(self, session_id: int, timeout: float) -> bool:
        q = self._queues.get(session_id)
        if q is None:
            return True
        return await q.wait_idle(timeout)

    def set_enabled(self, session_id: int, enabled: bool) -> None:
        with db_session(self.engine) as s:
            sess = s.get(LectureSession, session_id)
            if sess is not None:
                sess.translation_enabled = enabled
                s.add(sess)
                s.commit()
        q = self.queue(session_id)
        q.enabled = enabled
        if enabled:
            self.requeue(session_id, statuses=("transcribed",))
        q.wake()

    def requeue(
        self,
        session_id: int,
        *,
        statuses: Iterable[str],
        errors: Iterable[str] | None = None,
        backlog: bool = False,
    ) -> int:
        """Đưa lại vào hàng đợi các segment ở trạng thái cho trước (theo thứ tự seq)."""
        with db_session(self.engine) as s:
            stmt = select(Segment).where(Segment.session_id == session_id, col(Segment.status).in_(list(statuses)))
            rows = list(s.exec(stmt.order_by(Segment.seq)))
        errs = set(errors) if errors is not None else None
        count = 0
        for seg in rows:
            if errs is not None and (seg.error or "") not in errs:
                continue
            self.enqueue(session_id, seg.id, backlog=backlog)  # type: ignore[arg-type]
            count += 1
        return count

    def retranslate_machine(self, session_id: int) -> int:
        """Dịch lại bằng Claude các câu đang là bản dịch máy (nháp/model cục bộ) — ví dụ khi Claude dùng được lại."""
        if self.local_engine:
            return 0
        self.auth_failed = False
        with db_session(self.engine) as s:
            stmt = select(Segment.id).where(
                Segment.session_id == session_id,
                col(Segment.translation_kind).in_(MACHINE_KINDS),
                Segment.status != "redacted",
            )
            ids = list(s.exec(stmt.order_by(Segment.seq)))
        for sid in ids:
            self.enqueue(session_id, sid, backlog=True)  # câu mới đang diễn ra vẫn được dịch trước
        self.wake_all()
        return len(ids)

    def requeue_unfinished(self, session_id: int) -> int:
        return (
            self.requeue(session_id, statuses=("transcribed", "translating"))
            + self.requeue(session_id, statuses=(PENDING_STATUS,), backlog=True)
            + self.requeue(session_id, statuses=("failed",), errors=("network", "auth", "no_key", "rate_limit"))
        )

    def wake_all(self) -> None:
        for q in self._queues.values():
            q.wake()

    async def shutdown(self) -> None:
        for q in self._queues.values():
            await q.stop()

    # ------------------------------------------------------ global state
    def blocked(self) -> bool:
        if self.auth_failed or not self.translator.ready:
            return True
        if self.local_engine:  # model trên máy: không cần mạng, không bị giới hạn tốc độ
            return False
        return not self.connectivity.online or time.monotonic() < self._backoff_until

    def offline_blocked(self) -> bool:
        return not self.local_engine and not self.connectivity.online

    def backoff_remaining(self) -> float:
        return max(0.0, self._backoff_until - time.monotonic())

    @property
    def slow(self) -> bool:
        return self._slow_since is not None

    @property
    def batch_mode(self) -> bool:
        return self._slow_since is not None and time.monotonic() - self._slow_since > SLOW_BATCH_AFTER_S

    def note_backoff(self, err: LLMError) -> float:
        now = time.monotonic()
        self._backoff_attempt += 1
        delay = err.retry_after or min(MAX_BACKOFF_S, 2.0 ** (self._backoff_attempt - 1))
        delay = min(MAX_BACKOFF_S, max(1.0, delay))
        self._backoff_until = max(self._backoff_until, now + delay)
        self._last_rate_limit = now
        if self._slow_since is None:
            self._slow_since = now
            self.hub.publish_all(
                {"type": "warning", "code": "API_RATE_LIMIT", "message": "Dịch đang chậm (Claude API giới hạn tốc độ)."}
            )
        return delay

    def note_success(self) -> None:
        self._backoff_attempt = 0
        if self._slow_since is not None and time.monotonic() - self._last_rate_limit > SLOW_CLEAR_AFTER_S:
            self._slow_since = None
            self.hub.publish_all({"type": "translation.slow", "slow": False})

    def note_network_down(self) -> None:
        was_online = self.connectivity.online
        self.connectivity.mark_offline()
        if was_online:
            self.hub.publish_all(
                {
                    "type": "warning",
                    "code": "NETWORK_DOWN",
                    "message": "Mất kết nối, bản dịch sẽ bù khi có mạng.",
                }
            )

    def note_auth_failed(self, err: LLMError) -> None:
        if not self.auth_failed:
            self.auth_failed = True
            if self.local_engine:  # chưa tải / nạp lỗi model trên máy
                self.hub.publish_all({"type": "warning", "code": "LOCAL_MODEL_MISSING", "message": err.message})
                return
            code = "API_KEY_MISSING" if err.code == "no_key" else "API_KEY_INVALID"
            if self.settings.get().translate_provider == "claude_code" or err.status == 400:  # Claude Code / hết credit
                msg = f"{err.message} Đang dịch nháp bằng model cục bộ."
            elif err.code == "no_key":
                msg = "Chưa có API key Claude, vào Cài đặt để nhập."
            else:
                msg = "API key không hợp lệ, vào Cài đặt để cập nhật."
            self.hub.publish_all({"type": "warning", "code": code, "message": msg})

    def retry_auth_failures(self) -> int:
        """Đăng nhập lại / có key: bỏ trạng thái lỗi key và dịch lại các câu đã lỗi vì key (mọi phiên)."""
        self.auth_failed = False
        with db_session(self.engine) as s:
            stmt = select(Segment.session_id).where(Segment.status == "failed", col(Segment.error).in_(["auth", "no_key"]))
            sessions = set(s.exec(stmt.distinct()))
        n = sum(self.requeue(sid, statuses=("failed",), errors=("auth", "no_key")) for sid in sessions)
        self.wake_all()
        return n

    def on_api_key_changed(self) -> None:
        self.auth_failed = False
        for session_id in list(self._queues):
            self.requeue(session_id, statuses=("failed",), errors=("auth", "no_key"))
            self.requeue(session_id, statuses=("transcribed",))
        self.wake_all()

    def _on_connectivity(self, online: bool) -> None:
        self.hub.publish_all({"type": "network", "online": online})
        if online:
            for session_id in list(self._queues):
                self.requeue(session_id, statuses=("failed",), errors=("network",), backlog=True)
            self.wake_all()
        elif not self.local_engine:  # dịch bằng model trên máy thì mất mạng vẫn dịch bình thường
            for session_id, q in list(self._queues.items()):
                # Segment đang chờ trong hàng → tồn đọng, đánh dấu pending_translation (+ dịch nháp).
                q.move_to_backlog()
                self.mark_pending(list(q.backlog), session_id)
        self.publish_network_state()

    # --------------------------------------------------------- DB helpers
    def load(self, ids: list[int]) -> list[Segment]:
        with db_session(self.engine) as s:
            rows = list(s.exec(select(Segment).where(col(Segment.id).in_(ids)).order_by(Segment.seq)))
        return rows

    def set_status(self, seg: Segment, status: str, error: str | None = None) -> None:
        with db_session(self.engine) as s:
            row = s.get(Segment, seg.id)
            if row is None:
                return
            row.status = status
            row.error = error
            s.add(row)
            s.commit()
        seg.status, seg.error = status, error

    def save_translation(self, seg: Segment, vi: str, en: str | None, kind: str = "claude") -> bool:
        """Lưu bản dịch nếu text nguồn chưa bị sửa trong lúc dịch. Trả về False nếu bản dịch đã lỗi thời."""
        with db_session(self.engine) as s:
            row = s.get(Segment, seg.id)
            if row is None or row.source_text != seg.source_text or row.status == "redacted":
                return False
            row.text_vi = vi
            row.text_en = en
            row.status = "translated"
            row.error = None
            row.translation_kind = kind
            s.add(row)
            s.commit()
            speaker = row.speaker
        seg.speaker = speaker
        return True

    def save_draft(self, seg: Segment, vi: str) -> bool:
        """Bản nháp chỉ ghi khi segment vẫn chưa có bản dịch Claude (hay bản dịch chính bằng model trên máy)."""
        with db_session(self.engine) as s:
            row = s.get(Segment, seg.id)
            if row is None or row.source_text != seg.source_text or row.translation_kind in ("claude", "local"):
                return False
            if row.status not in (PENDING_STATUS, "transcribed", "failed"):
                return False
            row.text_vi = vi
            row.translation_kind = "draft"
            s.add(row)
            s.commit()
        return True

    def after_translated(self, session_id: int, seg: Segment) -> None:
        """Mục 11: lời người khác chỉ hiển thị trong lúc học → xoá nội dung khỏi DB sau khi dịch xong."""
        if seg.speaker == "other" and self.settings.get().discard_other_speech:
            redact_segments(self.engine, [seg.id])  # type: ignore[list-item]


class SessionTranslationQueue:
    def __init__(self, service: TranslationService, session_id: int, *, enabled: bool = True) -> None:
        self.service = service
        self.session_id = session_id
        self.enabled = enabled
        self.pending: deque[int] = deque()  # segment mới, dịch trước (stream)
        self.backlog: list[int] = []  # tồn đọng do mất mạng, dịch bù theo thứ tự thời gian
        self.in_flight: set[int] = set()
        self._from_backlog: set[int] = set()
        self._single_only: set[int] = set()
        self._event = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._workers: list[asyncio.Task] = []
        self._ensure_workers()

    # --------------------------------------------------------------- API
    def _ensure_workers(self) -> None:
        target = self.service.settings.get().translate_concurrency
        self._workers = [w for w in self._workers if not w.done()]
        while len(self._workers) < target:
            self._workers.append(asyncio.create_task(self._worker(len(self._workers))))

    def enqueue(self, segment_id: int, *, front: bool = False, backlog: bool = False) -> None:
        if segment_id in self.in_flight:
            # Đang dịch bản cũ (ví dụ vừa sửa text Ý): dịch lại sau khi xong.
            self._single_only.add(segment_id)
        if segment_id in self.pending or segment_id in self.backlog:
            return
        offline = self.service.offline_blocked()
        if (backlog or offline) and not front:
            self.backlog.append(segment_id)
            self.backlog.sort()
            if offline:
                self.service.mark_pending([segment_id], self.session_id)
        elif front:
            self.pending.appendleft(segment_id)
        else:
            self.pending.append(segment_id)
        if not offline and self.enabled and self.service.key_blocked():
            self.service._schedule_draft(self.session_id, segment_id)
        self._idle.clear()
        self._ensure_workers()
        self.wake()

    def move_to_backlog(self) -> None:
        while self.pending:
            sid = self.pending.popleft()
            if sid not in self.backlog:
                self.backlog.append(sid)
        self.backlog.sort()

    def wake(self) -> None:
        self._event.set()

    async def wait_idle(self, timeout: float) -> bool:
        self._check_idle()
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    # --------------------------------------------------------- internals
    def _check_idle(self) -> None:
        if not self.in_flight and ((not self.pending and not self.backlog) or not self.enabled):
            self._idle.set()

    def _take(self) -> list[int] | None:
        svc = self.service
        if not self.enabled or (not self.pending and not self.backlog) or svc.blocked():
            return None
        # Segment mới đang diễn ra được ưu tiên trước phần tồn đọng (Flow E).
        if self.pending:
            source: deque[int] | list[int] = self.pending
            # Model trên máy dịch cả lô gần như nhanh bằng một câu: gộp ngay khi có hơn một câu chờ.
            many = len(self.pending) > (1 if svc.local_engine else BATCH_TRIGGER)
            size = BATCH_SIZE if (many or svc.batch_mode) else 1
        else:
            source = self.backlog
            size = BACKLOG_BATCH_SIZE
        batch: list[int] = []
        while source and len(batch) < size:
            sid = source[0]
            if sid in self.in_flight:  # đợi lượt dịch cũ xong
                break
            if sid in self._single_only and batch:
                break
            source.pop(0) if isinstance(source, list) else source.popleft()
            batch.append(sid)
            if sid in self._single_only:
                self._single_only.discard(sid)
                break
        if not batch:
            return None
        self.in_flight.update(batch)
        if source is self.backlog:
            self._from_backlog.update(batch)
        return batch

    def _requeue_front(self, segs: list[Segment]) -> None:
        """Lỗi tạm thời: trả segment về đầu hàng (giữ thứ tự), đúng hàng mà nó được lấy ra."""
        for seg in reversed(segs):
            sid: int = seg.id  # type: ignore[assignment]
            if sid in self._from_backlog:
                if sid not in self.backlog:
                    self.backlog.append(sid)
            elif sid not in self.pending:
                self.pending.appendleft(sid)
        self.backlog.sort()

    async def _worker(self, index: int) -> None:
        while True:
            if index >= self.service.settings.get().translate_concurrency:
                return
            batch = self._take()
            if batch is None:
                self._check_idle()
                self._event.clear()
                timeout = self.service.backoff_remaining() or None
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=timeout)
                except TimeoutError:
                    pass
                continue
            try:
                if len(batch) == 1:
                    await self._run_single(batch[0])
                else:
                    await self._run_batch(batch)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Lỗi không mong đợi khi dịch %s", batch)
                for sid in batch:
                    seg = self.service.load([sid])
                    if seg:
                        self._fail(seg[0], "unknown", "Lỗi nội bộ khi dịch")
            finally:
                for sid in batch:
                    self.in_flight.discard(sid)
                    self._from_backlog.discard(sid)
                self._check_idle()
                self._event.set()

    def _publish(self, event: dict) -> None:
        self.service.hub.publish(self.session_id, event)

    def _fail(self, seg: Segment, code: str, message: str) -> None:
        self.service.set_status(seg, "failed", code)
        self._publish({"type": "segment.failed", "id": seg.id, "stage": "translate", "error": message, "code": code})

    def _handle_error(self, segs: list[Segment], err: LLMError) -> None:
        svc = self.service
        if err.retryable:
            delay = svc.note_backoff(err)
            log.info("Dịch bị %s, thử lại sau %.0fs", err.code, delay)
            self._requeue_front(segs)  # giữ thứ tự, segment vẫn ở trạng thái translating
            return
        if err.code == "network":
            svc.note_network_down()
            for seg in segs:
                self._fail(seg, "network", err.message)
            return
        if err.code in ("auth", "no_key"):
            svc.note_auth_failed(err)
            for seg in segs:
                self._fail(seg, err.code, err.message)
                svc._schedule_draft(self.session_id, seg.id)  # type: ignore[arg-type]
            for sid in [*self.pending, *self.backlog]:  # câu đang chờ sẽ bị chặn → dịch nháp luôn
                svc._schedule_draft(self.session_id, sid)
            return
        if len(segs) > 1:
            log.warning("Dịch gộp thất bại (%s), chuyển sang dịch từng câu", err.message)
            for seg in segs:
                self._single_only.add(seg.id)  # type: ignore[arg-type]
            self._requeue_front(segs)
            return
        for seg in segs:
            self._fail(seg, err.code, err.message)

    async def _run_single(self, segment_id: int) -> None:
        svc = self.service
        found = svc.load([segment_id])
        if not found or found[0].status == "redacted":
            return
        seg = found[0]
        ctx = svc.context.translation_context(seg)
        svc.set_status(seg, "translating")
        self._publish({"type": "segment.translating", "id": seg.id})

        def on_delta(delta: str) -> None:
            self._publish({"type": "segment.translation.delta", "id": seg.id, "delta": delta})

        try:
            out = await svc.translator.translate_stream(seg.source_text, ctx, session_id=self.session_id, on_delta=on_delta)
        except LLMError as err:
            self._handle_error([seg], err)
            return
        svc.note_success()
        if not out.vi:
            self._fail(seg, "empty", "Bản dịch rỗng")
            return
        self._saved(seg, out.vi, out.en, out.kind)

    def _saved(self, seg: Segment, vi: str, en: str | None, kind: str = "claude") -> None:
        svc = self.service
        if not svc.save_translation(seg, vi, en, kind):
            return
        event = {"type": "segment.translated", "id": seg.id, "text_vi": vi, "translation_kind": kind}
        if en:
            event["text_en"] = en
        self._publish(event)
        if svc.metrics is not None:
            svc.metrics.on_translated(seg.id)  # type: ignore[arg-type]
        svc.after_translated(self.session_id, seg)

    async def _run_batch(self, ids: list[int]) -> None:
        svc = self.service
        segs = [s for s in svc.load(ids) if s.status != "redacted"]
        if not segs:
            return
        ctx = svc.context.translation_context(segs[0])
        for seg in segs:
            svc.set_status(seg, "translating")
            self._publish({"type": "segment.translating", "id": seg.id})
        try:
            outs = await svc.translator.translate_batch(
                [s.source_text for s in segs],
                ctx,
                session_id=self.session_id,
                speakers=[s.speaker or "unknown" for s in segs],
            )
        except LLMError as err:
            self._handle_error(segs, err)
            return
        svc.note_success()
        for seg, out in zip(segs, outs, strict=True):
            if out.vi:
                self._saved(seg, out.vi, out.en, out.kind)
            else:
                self._fail(seg, "empty", "Bản dịch rỗng")
        if svc.connectivity.online and self.backlog:
            svc.publish_network_state(self.session_id)
