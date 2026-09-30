"""Điều phối một phiên học: nguồn âm thanh → (tiền xử lý) → VAD → STT → (người nói) → DB → hàng đợi dịch.

Mỗi phiên đang chạy có một ``SessionRunner`` tồn tại độc lập với kết nối WebSocket, nên WebSocket rớt
và kết nối lại (E-WS-01) không làm mất trạng thái.

Nguồn âm thanh đi qua interface chung ``AudioSource`` (feature_spec mục 6.2): tab (WebSocket), loopback,
micro laptop (``mic_local``) hoặc điện thoại (``mic_companion``). Với hai nguồn "lớp học trực tiếp", runner
thêm: tiền xử lý (lọc thông cao → khử nhiễu → AGC), cổng mức âm tối thiểu, SNR thời gian thực, phân biệt
người nói, tự chuyển sang micro laptop khi điện thoại mất kết nối, chế độ tiết kiệm pin.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import func
from sqlmodel import col, select

from app.audio.pcm import WavAppender, rms_dbfs
from app.audio.preprocess import Preprocessor
from app.audio.segmenter import SegEvent, Segmenter, Utterance
from app.audio.soundcheck import LOW_SNR_WARN_DB, SnrTracker
from app.audio.sources import CompanionSource, QueuedSource, SourceError, TabSource, make_source
from app.audio.speaker_tagger import SpeakerTagger, TagResult
from app.audio.vad import make_vad
from app.config import INCLASS_SOURCES
from app.db.models import LectureSession, RoomProfile, Segment, SessionMarker, utcnow
from app.db.session import db_session
from app.services.power import CRITICAL_PERCENT, LOW_PERCENT
from app.services.privacy import redact_segments, redact_session_others
from app.stt.base import STTError

if TYPE_CHECKING:
    from app.context import AppContext

log = logging.getLogger(__name__)

SILENCE_WARN_S = 60.0
STATUS_INTERVAL_S = 2.0
HEALTH_INTERVAL_S = 1.0
ACTIVE_SAVE_MS = 10_000
FALLBACK_AFTER_S = 3.0  # IC-AC-03: mất điện thoại 3 s → micro laptop
LOW_SNR_WARN_AFTER_S = 120.0  # IC-E-04
LOW_SNR_RECOVER_DB = 12.0
BATTERY_CHECK_S = 30.0
RUNTIME_GATE_MARGIN_DB = 4.0


class SessionRunner:
    def __init__(self, ctx: AppContext, session: LectureSession) -> None:
        self.ctx = ctx
        self.session_id: int = session.id  # type: ignore[assignment]
        self.capture_source: str = session.capture_source or session.audio_mode or "tab"
        if self.capture_source == "tab" and session.audio_mode == "loopback":
            self.capture_source = "loopback"  # phiên tạo trước migration 0002
        self.audio_mode = self.capture_source
        self.inclass = self.capture_source in INCLASS_SOURCES
        self.state = "paused" if session.status == "paused" else "live"
        s = ctx.settings.get()
        self.room: RoomProfile | None = None
        with db_session(ctx.engine) as db:
            max_seq, max_end = db.exec(
                select(func.max(Segment.seq), func.max(Segment.t_end_ms)).where(Segment.session_id == self.session_id)
            ).one()
            if session.room_profile_id:
                self.room = db.get(RoomProfile, session.room_profile_id)
        start_offset = max(session.active_ms or 0, max_end or 0)
        self._next_seq = (max_seq or 0) + 1 if max_seq is not None else 1
        if self.inclass:
            threshold = self.room.vad_threshold if self.room else s.inclass_vad_threshold
            self.min_speech_ms = s.inclass_min_speech_ms
        else:
            threshold, self.min_speech_ms = s.vad_threshold, 250
        self.segmenter = Segmenter(
            make_vad(),
            min_ms=s.vad_min_ms,
            max_ms=s.vad_max_ms,
            threshold=threshold,
            min_speech_ms=self.min_speech_ms,
            start_offset_ms=start_offset,
        )
        self.battery_saver = bool(session.battery_saver)
        self.mic_device = session.mic_device or s.mic_device
        self.denoise_level: str | None = None
        self.pre: Preprocessor | None = None
        if self.inclass:
            self.denoise_level = session.denoise_level or (self.room.recommended_denoise if self.room else None) or s.denoise_default
            self.pre = Preprocessor(self.denoise_level, prefer_rnnoise=not self.battery_saver)  # type: ignore[arg-type]
        self.snr = SnrTracker()
        self.tagger: SpeakerTagger | None = None
        self._source: QueuedSource = make_source(self.capture_source, device_id=self.mic_device)
        self._primary: QueuedSource = self._source
        self._consumer: asyncio.Task | None = None
        self.fallback_active = False
        self._fallback_warned = False
        self._saved_active_ms = start_offset
        self._utt_q: asyncio.Queue[tuple[Utterance, float] | None] = asyncio.Queue()
        self._audio_owner: str | None = None
        self._speaking = False
        self._last_speech_wall = time.monotonic()
        self._silence_warned = False
        self._last_level_pub = 0.0
        self._last_health = 0.0
        self._last_status = 0.0
        self._last_battery = 0.0
        self._battery_low_warned = False
        self._battery_critical_done = False
        self._low_snr_since: float | None = None
        self._low_snr_warned = False
        self._frame_errors = 0
        self._watching = False
        self._wav: WavAppender | None = None
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stt_task: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stt_task = asyncio.create_task(self._stt_worker())
        self._tasks = [asyncio.create_task(self._monitor())]
        if self.inclass:
            self._configure_speaker_tagging()
            if self.battery_saver:
                self._apply_stt_saver(True)
            self.ctx.connectivity.watch(True)
            self._watching = True
            if self.state == "live":
                self.ctx.companion.set_target(self.session_id)
        if self.state == "live":
            await self._start_capture()
        requeued = self.ctx.translation.requeue_unfinished(self.session_id)
        if requeued:
            log.info("Phiên %s: đưa lại %d segment vào hàng đợi dịch", self.session_id, requeued)
        self.publish_status()

    def _configure_speaker_tagging(self) -> None:
        enabled = self.inclass and self.ctx.settings.get().speaker_tagging and not self.battery_saver
        if enabled and self.tagger is None:
            self.tagger = SpeakerTagger(self.ctx.speaker_models.embedder())
            if not self.ctx.speaker_models.cached():
                self.ctx.speaker_models.start_prepare()  # tải model ONNX (~26 MB) cho phiên sau
        elif not enabled:
            self.tagger = None

    async def _start_capture(self) -> None:
        settings = self.ctx.settings.get()
        if settings.save_raw_audio and self._wav is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._wav = WavAppender(self.ctx.env.data_dir / "audio" / f"session_{self.session_id}_{stamp}.wav")
        if isinstance(self._primary, CompanionSource):
            self.ctx.companion.attach_sink(self._primary, owner=f"session:{self.session_id}")
            if not self._primary.running:
                await self._primary.start()
        source = self._source
        if not source.running:
            try:
                await source.start()
            except SourceError as e:
                self._source_error(source, str(e))
                return
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._consume(source))

    async def _stop_capture(self) -> None:
        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for src in {id(self._source): self._source, id(self._primary): self._primary}.values():
            try:
                await src.stop()
            except Exception:  # noqa: BLE001
                log.debug("Dừng nguồn âm thanh lỗi", exc_info=True)
        if isinstance(self._primary, CompanionSource):
            self.ctx.companion.detach_sink(self._primary)

    def _source_error(self, source: QueuedSource, message: str) -> None:
        if source.kind == "loopback":
            code = "LOOPBACK_UNAVAILABLE" if "không bắt được" in message.lower() else "LOOPBACK_ERROR"
            self._warn(code, message if code == "LOOPBACK_UNAVAILABLE" else f"Lỗi bắt âm thanh hệ thống: {message}")
        elif source.kind == "mic_local":
            self._warn("MIC_ERROR", message)
        else:
            self._warn("SOURCE_ERROR", message)

    # ----------------------------------------------------------------- audio
    def push_audio(self, pcm: bytes, source: str) -> None:
        """Âm thanh tab từ WebSocket /ws/session (chỉ kết nối gửi đầu tiên được nhận)."""
        if self.state != "live" or not pcm or not isinstance(self._source, TabSource):
            return
        if self._audio_owner is None:
            self._audio_owner = source
        elif source != self._audio_owner:
            return
        self._source.feed(pcm)

    def release_audio(self, source: str) -> None:
        if self._audio_owner == source:
            self._audio_owner = None

    async def _consume(self, source: QueuedSource) -> None:
        async for frame in source.frames():
            if source is not self._source:
                break
            if self.state != "live":
                continue
            try:
                self._process_frame(frame, source)
            except Exception:  # noqa: BLE001 - một khung lỗi không được làm dừng phiên
                self._frame_errors += 1
                if self._frame_errors <= 3:
                    log.exception("Lỗi xử lý khung âm thanh")
        if source.error and source is self._source and self.state == "live":
            self._source_error(source, source.error)

    def _process_frame(self, frame: np.ndarray, source: QueuedSource) -> None:
        if self._wav is not None:
            self._wav.write(frame.astype("<i2").tobytes())
        x = frame.astype(np.float32) / 32768.0
        if source.kind != "tab":
            now = time.monotonic()
            interval = 1.0 if self.battery_saver else 0.1  # IC-FR-70: cập nhật UI 1 lần/giây
            if now - self._last_level_pub >= interval:
                self._last_level_pub = now
                self.publish({"type": "level", "db": round(rms_dbfs(x), 1)})
        if self.pre is not None:
            y = self.pre.process(x)
            level_db = self.pre.last_hpf_db
        else:
            y = x
            level_db = rms_dbfs(x)
        events = self.segmenter.push(y)
        speech = self.segmenter.in_speech or any(e.kind == "speech_start" for e in events)
        self.snr.add(self.segmenter.t_ms, level_db, speech)
        for event in events:
            self._on_seg_event(event)
        if self.pre is not None and self.pre.over_budget():
            self._auto_downgrade()

    def _on_seg_event(self, ev: SegEvent) -> None:
        now = time.monotonic()
        if ev.kind == "speech_start":
            self._speaking = True
            self._last_speech_wall = now
            self._silence_warned = False
            self.publish({"type": "vad.speech_start", "t_ms": ev.t_ms})
        elif ev.kind == "speech_end":
            self._speaking = False
            self._last_speech_wall = now
            self.publish({"type": "vad.speech_end", "t_ms": ev.t_ms})
        elif ev.utterance is not None:
            utt = ev.utterance
            if self.inclass and not self._passes_gate(utt):
                return
            # Thời điểm (đồng hồ thật) câu nói kết thúc = bây giờ - phần audio đã nhận sau t_end.
            lag_s = max(0, self.segmenter.t_ms - utt.t_end_ms) / 1000
            self._utt_q.put_nowait((utt, now - lag_s))

    def _min_level(self) -> float | None:
        """Ngưỡng mức âm tối thiểu (IC-FR-13): theo hồ sơ phòng nếu đang dùng đúng nguồn đã hiệu chỉnh."""
        if self.room is not None and self.room.min_level_dbfs is not None and not self.fallback_active:
            return self.room.min_level_dbfs
        noise = self.snr.noise_floor()
        return None if noise is None else noise + RUNTIME_GATE_MARGIN_DB

    def _passes_gate(self, utt: Utterance) -> bool:
        if utt.duration_ms < self.min_speech_ms:
            return False
        gate = self._min_level()
        level = self.snr.level_between(utt.t_start_ms, utt.t_end_ms)
        if gate is not None and level is not None and level < gate:
            log.debug("Bỏ utterance [%s-%s] mức %.1f < %.1f dBFS", utt.t_start_ms, utt.t_end_ms, level, gate)
            self.publish(
                {"type": "vad.dropped", "t_start_ms": utt.t_start_ms, "t_end_ms": utt.t_end_ms,
                 "level_dbfs": round(level, 1), "gate_dbfs": round(gate, 1)}
            )
            return False
        return True

    def _auto_downgrade(self) -> None:
        """IC-E-05: khử nhiễu mạnh làm chậm quá ngưỡng → tự hạ xuống light."""
        assert self.pre is not None
        log.warning("Khử nhiễu mạnh tốn %.0f ms/100 ms, tự hạ xuống light", self.pre.processing_ms)
        self._set_denoise("light", persist=True)
        self._warn("DENOISE_DOWNGRADED", "Khử nhiễu mạnh làm chậm xử lý nên đã tự chuyển sang khử nhiễu nhẹ.")

    # ------------------------------------------------------------------- STT
    async def _stt_worker(self) -> None:
        while True:
            item = await self._utt_q.get()
            if item is None:
                return
            utt, speech_end_wall = item
            try:
                await self._transcribe(utt, speech_end_wall)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Lỗi xử lý utterance")

    async def _transcribe(self, utt: Utterance, speech_end_wall: float) -> None:
        stt = self.ctx.stt
        if not stt.ready and not await stt.prepare():
            self._warn("STT_NOT_READY", stt.error or "Nhận dạng giọng nói chưa sẵn sàng.")
            return
        prompt, keywords = self.ctx.context.stt_prompt(self.session_id)
        # Embedding người nói chạy song song với STT (không làm chậm phụ đề).
        tagger = self.tagger
        embed_task = asyncio.create_task(asyncio.to_thread(tagger.embed, utt.audio)) if tagger else None
        started = time.perf_counter()
        result = None
        for attempt in range(2):
            try:
                result = await stt.provider.transcribe(utt.audio, language="it", prompt=prompt, keywords=keywords)
                break
            except STTError as e:
                if attempt == 0 and e.retryable:
                    await asyncio.sleep(1.0)
                    continue
                self._warn("STT_ERROR", str(e))
                break
        if result is None or not result.text:
            if embed_task is not None:
                embed_task.cancel()
            return
        stt_ms = int((time.perf_counter() - started) * 1000)
        snr = self.snr.snr() if self.inclass else None
        seg = Segment(
            session_id=self.session_id,
            seq=self._next_seq,
            t_start_ms=utt.t_start_ms,
            t_end_ms=utt.t_end_ms,
            text_it=result.text,
            confidence=round(result.confidence, 4),
            status="transcribed",
            snr_db=None if snr is None else round(snr, 1),
        )
        with db_session(self.ctx.engine) as db:  # ghi ngay (NFR-04)
            db.add(seg)
            db.commit()
            db.refresh(seg)
        self._next_seq += 1
        self.publish(
            {
                "type": "segment.transcribed",
                "id": seg.id,
                "seq": seg.seq,
                "t_start_ms": seg.t_start_ms,
                "t_end_ms": seg.t_end_ms,
                "text_it": seg.text_it,
                "confidence": seg.confidence,
                "speaker": seg.speaker,
                "snr_db": seg.snr_db,
            }
        )
        self.ctx.metrics.on_transcribed(
            segment_id=seg.id,  # type: ignore[arg-type]
            session_id=self.session_id,
            speech_end_wall=speech_end_wall,
            stt_ms=stt_ms,
            audio_ms=utt.duration_ms,
        )
        if embed_task is not None and tagger is not None and tagger is self.tagger:
            try:
                emb = await embed_task
            except Exception:  # noqa: BLE001
                emb = None
            self._apply_tags(tagger.add(seg.id, seg.t_start_ms, utt.duration_ms, emb))  # type: ignore[arg-type]
        queue = self.ctx.translation.queue(self.session_id)
        if queue.enabled:
            queue.enqueue(seg.id)  # type: ignore[arg-type]

    def _apply_tags(self, results: list[TagResult]) -> None:
        if not results:
            return
        by_id = {r.segment_id: r for r in results}
        others: list[int] = []
        with db_session(self.ctx.engine) as db:
            for seg in db.exec(select(Segment).where(col(Segment.id).in_(list(by_id)))):
                r = by_id[seg.id]  # type: ignore[index]
                if seg.speaker == r.speaker and seg.speaker_score == r.score:
                    continue
                seg.speaker, seg.speaker_score = r.speaker, r.score
                db.add(seg)
                if r.speaker == "other" and seg.status == "translated":
                    others.append(seg.id)  # type: ignore[arg-type]
            db.commit()
        for r in results:
            self.publish({"type": "segment.speaker", "id": r.segment_id, "speaker": r.speaker, "score": r.score})
        if others and self.ctx.settings.get().discard_other_speech:
            redact_segments(self.ctx.engine, others)

    def learn_speaker(self, segment_id: int, speaker: str) -> None:
        if self.tagger is not None:
            self.tagger.learn(segment_id, speaker)  # type: ignore[arg-type]

    # --------------------------------------------------------------- monitor
    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(min(STATUS_INTERVAL_S, HEALTH_INTERVAL_S))
            now = time.monotonic()
            try:
                if self.state == "live":
                    self._check_silence(now)
                    if self.segmenter.t_ms - self._saved_active_ms >= ACTIVE_SAVE_MS:
                        self._save_active_ms()
                    if self.inclass:
                        await self._check_source()
                        self._check_snr(now)
                if self.inclass and now - self._last_battery >= BATTERY_CHECK_S:
                    self._last_battery = now
                    self._check_battery()
                if self.capture_source != "tab" and now - self._last_health >= HEALTH_INTERVAL_S:
                    self._last_health = now
                    self.publish(self.health().as_event())
                if now - self._last_status >= STATUS_INTERVAL_S:
                    self._last_status = now
                    self.publish_status()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Lỗi giám sát phiên %s", self.session_id)

    def _check_silence(self, now: float) -> None:
        idle = now - self._last_speech_wall
        if not self._speaking and idle >= SILENCE_WARN_S and not self._silence_warned:
            self._silence_warned = True
            if self.inclass:
                msg = "Không nghe thấy tiếng nói trong 60 giây. Kiểm tra micro / điện thoại và vị trí đặt micro."
            else:
                msg = "Không nhận được tiếng nói trong 60 giây. Kiểm tra nguồn âm thanh (tab Teams, 'Chia sẻ âm thanh tab')."
            self._warn("SILENCE_60S", msg)

    def _check_snr(self, now: float) -> None:
        """IC-E-04: SNR < 8 dB kéo dài 2 phút → cảnh báo (một lần, đặt lại khi SNR ≥ 12 dB)."""
        snr = self.snr.snr()
        if snr is None:
            return
        if snr < LOW_SNR_WARN_DB:
            if self._low_snr_since is None:
                self._low_snr_since = now
            elif now - self._low_snr_since >= LOW_SNR_WARN_AFTER_S and not self._low_snr_warned:
                self._low_snr_warned = True
                self._warn(
                    "LOW_SNR",
                    "Âm thanh đang rất nhiễu, bản dịch có thể sai nhiều. Thử bật khử nhiễu mạnh hoặc đổi vị trí micro.",
                )
        elif snr >= LOW_SNR_RECOVER_DB:
            self._low_snr_since = None
            self._low_snr_warned = False

    async def _check_source(self) -> None:
        """IC-FR-07 / IC-E-03: điện thoại mất kết nối → micro laptop (nếu bật); có lại → quay về điện thoại."""
        primary = self._primary
        if not isinstance(primary, CompanionSource):
            return
        if not self.fallback_active:
            lost = primary.disconnected_for()
            if lost < FALLBACK_AFTER_S:
                self._fallback_warned = False
                return
            if self._fallback_warned:
                return
            self._fallback_warned = True
            if self.ctx.settings.get().companion_fallback_mic:
                await self._switch_source(make_source("mic_local", device_id=self.mic_device), fallback=True)
                self._marker("source_fallback", "Mất kết nối điện thoại — chuyển sang micro laptop")
                self._warn("COMPANION_LOST", "Mất kết nối điện thoại. Đã chuyển sang micro laptop.")
            else:
                self._marker("source_lost", "Mất kết nối điện thoại")
                self._warn("COMPANION_LOST", "Mất kết nối điện thoại. Kiểm tra Wi-Fi/hotspot và mở lại trang trên điện thoại.")
        elif primary.device_connected and primary.receiving:
            fallback = self._source
            await self._switch_source(primary, fallback=False)
            try:
                await fallback.stop()
            except Exception:  # noqa: BLE001
                pass
            self._marker("source_restored", "Điện thoại đã kết nối lại — dùng micro điện thoại")
            self._warn("COMPANION_RESTORED", "Điện thoại đã kết nối lại.")

    async def _switch_source(self, source: QueuedSource, *, fallback: bool) -> None:
        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._flush()
        if self.pre is not None:
            self.pre.reset()
        self.snr.reset()
        self.fallback_active = fallback
        self._source = source
        if isinstance(source, CompanionSource):
            source.drain()  # bỏ âm thanh cũ dồn trong lúc dùng micro laptop
        try:
            await source.start()
        except SourceError as e:
            self._source_error(source, str(e))
            return
        self._consumer = asyncio.create_task(self._consume(source))
        self.publish_status()

    def _check_battery(self) -> None:
        status = self.ctx.power.status()
        if status is None or status["plugged"]:
            return
        percent = status["percent"]
        if percent < CRITICAL_PERCENT and not self._battery_critical_done:
            self._battery_critical_done = True
            self._battery_low_warned = True
            self._save_active_ms()
            self.ctx.checkpoint_db()
            if not self.battery_saver:
                self.set_battery_saver(True, reason="auto")
            self._warn(
                "BATTERY_CRITICAL",
                f"Pin còn {percent:.0f}%: đã tự bật chế độ tiết kiệm pin, transcript đã được lưu xuống đĩa. Hãy cắm sạc.",
            )
        elif percent < LOW_PERCENT and not self._battery_low_warned:
            self._battery_low_warned = True
            self._warn(
                "BATTERY_LOW",
                f"Pin còn {percent:.0f}%. Nên bật chế độ tiết kiệm pin hoặc ngồi gần ổ cắm.",
            )

    def _save_active_ms(self) -> None:
        t = self.segmenter.t_ms
        with db_session(self.ctx.engine) as db:
            sess = db.get(LectureSession, self.session_id)
            if sess is not None:
                sess.active_ms = max(sess.active_ms or 0, t)
                db.add(sess)
                db.commit()
        self._saved_active_ms = t

    # --------------------------------------------------------------- control
    def _set_db_status(self, status: str, **fields: Any) -> None:
        with db_session(self.ctx.engine) as db:
            sess = db.get(LectureSession, self.session_id)
            if sess is None:
                return
            sess.status = status
            sess.active_ms = max(sess.active_ms or 0, self.segmenter.t_ms)
            for k, v in fields.items():
                setattr(sess, k, v)
            db.add(sess)
            db.commit()

    def _flush(self) -> None:
        if self.pre is not None:
            tail = self.pre.flush()
            for event in self.segmenter.push(tail):
                self._on_seg_event(event)
        for event in self.segmenter.flush():
            self._on_seg_event(event)
        self._speaking = False

    async def pause(self, reason: str | None = None) -> None:
        if self.state != "live":
            return
        self.state = "paused"
        await self._stop_capture()
        self._flush()
        if self.pre is not None:
            self.pre.reset()
        self._audio_owner = None
        self._set_db_status("paused")
        if reason == "source_ended":
            self._warn("SOURCE_ENDED", "Đã dừng chia sẻ âm thanh. Bấm Tiếp tục để chọn lại nguồn.")
        self.publish_status()

    async def resume(self) -> None:
        if self.state == "ended":
            raise RuntimeError("Phiên đã kết thúc")
        if self.state == "live":
            return
        self.state = "live"
        self.segmenter.reset()
        self._last_speech_wall = time.monotonic()
        self._silence_warned = False
        if self.fallback_active:  # tiếp tục thì thử lại nguồn chính trước
            self._source, self.fallback_active = self._primary, False
        if self.inclass:
            self.ctx.companion.set_target(self.session_id)
        await self._start_capture()
        self._set_db_status("live")
        self.publish_status()

    async def end(self) -> None:
        if self.state == "ended":
            return
        self.state = "ended"
        await self._stop_capture()
        self._flush()
        await self._utt_q.put(None)
        if self._stt_task is not None:
            try:
                await asyncio.wait_for(self._stt_task, timeout=60)
            except TimeoutError:
                log.warning("STT chưa xử lý xong khi kết thúc phiên %s", self.session_id)
                self._stt_task.cancel()
        for task in self._tasks:
            task.cancel()
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        self._release_shared()
        if self.inclass and self.ctx.settings.get().discard_other_speech:
            # IC-AC-05: khi kết thúc phiên, DB không còn nội dung lời người khác (trước khi tạo tóm tắt).
            # Segment đang dịch dở sẽ không được ghi bản dịch (save_translation bỏ qua segment đã xoá).
            redact_session_others(self.ctx.engine, self.session_id)
        self._set_db_status("ended", ended_at=utcnow())
        self.publish_status()

    async def shutdown(self) -> None:
        """Tắt backend: dừng bắt âm, giữ phiên ở trạng thái paused để tiếp tục sau."""
        if self.state == "live":
            await self.pause()
        await self._utt_q.put(None)
        if self._stt_task is not None:
            try:
                await asyncio.wait_for(self._stt_task, timeout=20)
            except TimeoutError:
                self._stt_task.cancel()
        for task in self._tasks:
            task.cancel()
        if self._wav is not None:
            self._wav.close()
        self._release_shared()

    def _release_shared(self) -> None:
        if self.inclass:
            self.ctx.companion.clear_target(self.session_id)
            self._apply_stt_saver(False)
        if self._watching:
            self._watching = False
            self.ctx.connectivity.watch(False)
        if self.pre is not None:
            self.pre.close()

    # ------------------------------------------------------- tuỳ chọn trong phiên
    def _set_denoise(self, level: str, *, persist: bool) -> None:
        if self.pre is None:
            return
        tail = self.pre.set_level(level, prefer_rnnoise=not self.battery_saver)  # type: ignore[arg-type]
        for event in self.segmenter.push(tail):
            self._on_seg_event(event)
        self.denoise_level = level
        if persist:
            with db_session(self.ctx.engine) as db:
                sess = db.get(LectureSession, self.session_id)
                if sess is not None:
                    sess.denoise_level = level
                    db.add(sess)
                    db.commit()
        self.publish_status()

    def set_denoise(self, level: str) -> None:
        self._set_denoise(level, persist=True)

    def _apply_stt_saver(self, enabled: bool) -> None:
        setter = getattr(self.ctx.stt, "set_power_saver", None)
        if setter is None:
            return
        message = setter(self.session_id, enabled)
        if message:
            self._warn("BATTERY_SAVER_STT", message)

    def set_battery_saver(self, enabled: bool, *, reason: str = "user") -> None:
        """IC-FR-70: Whisper small int8, tắt phân biệt người nói, cập nhật UI 1 lần/giây."""
        if enabled == self.battery_saver:
            return
        self.battery_saver = enabled
        with db_session(self.ctx.engine) as db:
            sess = db.get(LectureSession, self.session_id)
            if sess is not None:
                sess.battery_saver = enabled
                db.add(sess)
                db.commit()
        if self.inclass:
            self._apply_stt_saver(enabled)
            self._configure_speaker_tagging()
            if self.pre is not None and self.denoise_level:
                self._set_denoise(self.denoise_level, persist=False)
        text = "Bật chế độ tiết kiệm pin" if enabled else "Tắt chế độ tiết kiệm pin"
        self._marker("battery_saver", text + (" (tự động do pin yếu)" if reason == "auto" else ""))
        self.publish_status()

    # ---------------------------------------------------------------- events
    def publish(self, event: dict[str, Any]) -> None:
        self.ctx.hub.publish(self.session_id, event)

    def _warn(self, code: str, message: str) -> None:
        self.publish({"type": "warning", "code": code, "message": message})

    def _marker(self, kind: str, message: str) -> None:
        marker = SessionMarker(session_id=self.session_id, t_ms=self.segmenter.t_ms, kind=kind, message=message)
        with db_session(self.ctx.engine) as db:
            db.add(marker)
            db.commit()
            db.refresh(marker)
        self.publish(
            {"type": "marker.created", "marker": {"id": marker.id, "t_ms": marker.t_ms, "kind": kind, "message": message}}
        )

    def health(self):  # type: ignore[no-untyped-def]
        h = self._source.health()
        h.snr_db = self.snr.snr()
        return h

    def status_event(self) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "status",
            "state": self.state,
            "stt_ready": self.ctx.stt.ready,
            "queue_len": self.ctx.translation.queue_len(self.session_id),
            "translation_enabled": self.ctx.translation.enabled(self.session_id),
            "translation_slow": self.ctx.translation.slow,
            "network_online": self.ctx.connectivity.online,
            "api_key_ok": self.ctx.translator.ready and not self.ctx.translation.auth_failed,
            "speaking": self._speaking,
            "t_ms": self.segmenter.t_ms,
            "audio_mode": self.audio_mode,
            "capture_source": self.capture_source,
            "inclass": self.inclass,
        }
        if self.inclass:
            event.update(
                {
                    "active_source": self._source.kind,
                    "fallback_active": self.fallback_active,
                    "denoise_level": self.denoise_level,
                    "denoise_engine": self.pre.engine if self.pre else None,
                    "battery_saver": self.battery_saver,
                    "speaker_tagging": self.tagger is not None,
                    "speaker_ready": bool(self.tagger and self.tagger.ready),
                    "room_profile_id": self.room.id if self.room else None,
                    "source_health": self.health().as_event(),
                    "battery": self.ctx.power.status(),
                }
            )
        return event

    def publish_status(self) -> None:
        self.publish(self.status_event())


class RunnerRegistry:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._runners: dict[int, SessionRunner] = {}
        self._lock = asyncio.Lock()

    def peek(self, session_id: int) -> SessionRunner | None:
        return self._runners.get(session_id)

    async def get(self, session_id: int, *, create: bool = True) -> SessionRunner | None:
        runner = self._runners.get(session_id)
        if runner is not None or not create:
            return runner
        async with self._lock:
            runner = self._runners.get(session_id)
            if runner is not None:
                return runner
            with db_session(self.ctx.engine) as db:
                sess = db.get(LectureSession, session_id)
            if sess is None or sess.status == "ended":
                return None
            runner = SessionRunner(self.ctx, sess)
            self._runners[session_id] = runner
            await runner.start()
            return runner

    async def end(self, session_id: int) -> None:
        runner = self._runners.pop(session_id, None)
        if runner is not None:
            await runner.end()
            return
        with db_session(self.ctx.engine) as db:
            sess = db.get(LectureSession, session_id)
            if sess is not None and sess.status != "ended":
                sess.status = "ended"
                sess.ended_at = utcnow()
                db.add(sess)
                db.commit()
        if self.ctx.settings.get().discard_other_speech:
            redact_session_others(self.ctx.engine, session_id)

    async def shutdown(self) -> None:
        for runner in list(self._runners.values()):
            try:
                await runner.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("Lỗi khi dừng phiên %s", runner.session_id)
        self._runners.clear()
