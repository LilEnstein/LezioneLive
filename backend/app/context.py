"""Container gom các service của app (tạo trong lifespan của FastAPI, truy cập qua ``request.app.state.ctx``)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlmodel import select

from app.audio.soundcheck import SoundCheckManager
from app.audio.speaker_tagger import SpeakerModelManager
from app.companion.hub import CompanionHub
from app.companion.pairing import PairingManager
from app.companion.server import CompanionServer
from app.config import AppSettings, EnvSettings
from app.db.migrations import run_migrations
from app.db.models import LectureSession
from app.db.session import db_session, make_engine
from app.llm.assistant import AssistantService
from app.llm.board_ocr import BoardCaptureService
from app.llm.claude_code import ClaudeCodeClient
from app.llm.client import LLMClient
from app.llm.flashcards import FlashcardService
from app.llm.prompt_library import PromptLibrary
from app.llm.router import AssistantLLM
from app.llm.summarizer import Summarizer
from app.llm.translator import Translator
from app.offline.draft_translator import DraftTranslator
from app.pipeline.connectivity import ConnectivityMonitor
from app.pipeline.context import ContextProvider
from app.pipeline.events import EventHub
from app.pipeline.postprocess import PostProcessor
from app.pipeline.session_runner import RunnerRegistry
from app.pipeline.translation_queue import TranslationService
from app.security import SecretStore
from app.services.cost import UsageRecorder
from app.services.metrics import MetricsStore
from app.services.power import BatteryMonitor
from app.services.settings_store import SettingsStore
from app.services.slides import SlideService
from app.stt.manager import STTManager

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    env: EnvSettings
    engine: Engine
    settings: SettingsStore
    secrets: SecretStore
    hub: EventHub
    usage: UsageRecorder
    llm: LLMClient
    assistant_llm: AssistantLLM  # trợ lý học tập: API hoặc gói Pro qua Claude Code
    prompts: PromptLibrary
    context: ContextProvider
    translator: Translator
    connectivity: ConnectivityMonitor
    metrics: MetricsStore
    translation: TranslationService
    assistant: AssistantService
    summarizer: Summarizer
    flashcards: FlashcardService
    stt: STTManager
    draft: DraftTranslator
    power: BatteryMonitor
    speaker_models: SpeakerModelManager
    pairing: PairingManager
    companion: CompanionHub
    soundchecks: SoundCheckManager = field(default_factory=SoundCheckManager)
    runners: RunnerRegistry = field(init=False)
    post: PostProcessor = field(init=False)
    slides: SlideService = field(init=False)
    boards: BoardCaptureService = field(init=False)
    companion_server: CompanionServer = field(init=False)

    def __post_init__(self) -> None:
        self.runners = RunnerRegistry(self)
        self.post = PostProcessor(self)
        self.slides = SlideService(self)
        self.boards = BoardCaptureService(self)
        self.companion_server = CompanionServer(self)

    def checkpoint_db(self) -> None:
        """Ghi hết WAL vào file DB chính (pin sắp hết, IC-E-07)."""
        try:
            with self.engine.begin() as conn:
                conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001
            log.warning("Không checkpoint được SQLite WAL", exc_info=True)

    async def shutdown(self) -> None:
        await self.soundchecks.shutdown()
        await self.companion_server.stop()
        await self.runners.shutdown()
        await self.post.shutdown()
        await self.translation.shutdown()
        await self.stt.close()
        await self.connectivity.close()
        self.engine.dispose()


def build_context(
    env: EnvSettings,
    *,
    secrets: SecretStore | None = None,
    llm: LLMClient | None = None,
    connectivity: ConnectivityMonitor | None = None,
    stt: STTManager | None = None,
    power: BatteryMonitor | None = None,
    speaker_models: SpeakerModelManager | None = None,
) -> AppContext:
    """Tạo toàn bộ service. Các tham số tuỳ chọn cho phép test thay bằng bản giả."""
    env.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(env.db_path)
    run_migrations(engine)
    settings = SettingsStore(engine)
    secrets = secrets or SecretStore()
    hub = EventHub()
    usage = UsageRecorder(engine, settings, hub)
    if llm is None:
        llm = LLMClient(secrets, usage)
    else:
        llm.usage = usage
    prompts = PromptLibrary()
    context = ContextProvider(engine, settings)
    claude_code = ClaudeCodeClient(env.data_dir / "claude_code")
    draft = DraftTranslator(settings.get().draft_model)  # model NLLB trên máy: dịch nháp hoặc nguồn dịch chính
    translator = Translator(llm, prompts, settings, claude_code, local=draft)
    assistant_llm = AssistantLLM(llm, claude_code, settings)
    connectivity = connectivity or ConnectivityMonitor()
    metrics = MetricsStore(engine)
    translation = TranslationService(
        engine=engine,
        settings=settings,
        translator=translator,
        llm=llm,
        context=context,
        hub=hub,
        connectivity=connectivity,
        metrics=metrics,
        draft=draft,
    )
    stt = stt or STTManager(settings, secrets)
    stt.on_warning = lambda code, message: hub.publish_all({"type": "warning", "code": code, "message": message})

    def _on_settings(old: AppSettings, new: AppSettings) -> None:
        if old.cost_threshold_usd != new.cost_threshold_usd:
            usage.reset_warnings()
        if (old.secondary_language, old.context_segments) != (new.secondary_language, new.context_segments):
            context.invalidate()
        if old.draft_model != new.draft_model:
            draft.set_repo(new.draft_model)
        if (old.translate_provider, old.draft_model) != (new.translate_provider, new.draft_model):
            if new.translate_provider == "local" and not env.disable_stt_autoload:
                try:
                    asyncio.get_running_loop()
                    draft.start_prepare(download=False)  # nạp sẵn model vào RAM để câu đầu tiên không phải chờ
                except RuntimeError:
                    pass  # không có event loop (gọi từ luồng khác): model sẽ nạp khi dịch câu đầu
            translation.on_api_key_changed()  # đổi nguồn dịch: bỏ trạng thái lỗi key, dịch lại câu đang chờ

    settings.add_listener(_on_settings)
    return AppContext(
        env=env,
        engine=engine,
        settings=settings,
        secrets=secrets,
        hub=hub,
        usage=usage,
        llm=llm,
        assistant_llm=assistant_llm,
        prompts=prompts,
        context=context,
        translator=translator,
        connectivity=connectivity,
        metrics=metrics,
        translation=translation,
        assistant=AssistantService(engine, assistant_llm, prompts, settings, context),  # type: ignore[arg-type]
        summarizer=Summarizer(engine, assistant_llm, prompts, settings, context),  # type: ignore[arg-type]
        flashcards=FlashcardService(engine, assistant_llm, prompts, settings, context),  # type: ignore[arg-type]
        stt=stt,
        draft=draft,
        power=power or BatteryMonitor(),
        speaker_models=speaker_models or SpeakerModelManager(),
        pairing=PairingManager(engine),
        companion=CompanionHub(hub),
    )


def recover_sessions(ctx: AppContext) -> int:
    """Sau khi backend khởi động lại (crash/tắt): phiên đang 'live' chuyển sang 'paused' (AC-04)."""
    count = 0
    with db_session(ctx.engine) as s:
        for sess in s.exec(select(LectureSession).where(LectureSession.status == "live")):
            sess.status = "paused"
            s.add(sess)
            count += 1
        s.commit()
    if count:
        log.info("Đã chuyển %d phiên đang dở sang trạng thái tạm dừng", count)
    return count
