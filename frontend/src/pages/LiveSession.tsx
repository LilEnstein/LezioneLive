/** Màn hình học trực tiếp (mục 12.2, Flow B). */
import {
  AArrowDown,
  AArrowUp,
  ArrowLeft,
  Camera,
  CircleDollarSign,
  CircleStop,
  ExternalLink,
  Eye,
  EyeOff,
  Headphones,
  Hourglass,
  Keyboard,
  KeyRound,
  Laptop,
  MonitorUp,
  Pause,
  PictureInPicture2,
  Pin as PinIcon,
  Play,
  RotateCw,
  Smartphone,
  Turtle,
  WifiOff,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Pin, Segment, ServerEvent } from "../api/types";
import { SessionSocket } from "../api/ws";
import { CaptureError, TabAudioCapture } from "../audio/capture";
import { AssistantDock } from "../components/AssistantDock";
import { AssistantPanel, type AssistantHandle } from "../components/AssistantPanel";
import { AudioSourcePicker } from "../components/AudioSourcePicker";
import { BoardCaptureDialog } from "../components/BoardCaptureDialog";
import { CostBadge } from "../components/CostBadge";
import {
  BatteryBadge,
  BatterySaverToggle,
  DenoiseSelect,
  LiveSnr,
  NetworkBadge,
  SourceBadge,
} from "../components/InClassBar";
import { PairPhoneModal } from "../components/PairPhoneModal";
import { PipSubtitles } from "../components/PipSubtitles";
import { ScreenViewer } from "../components/ScreenViewer";
import { SubtitlePane, type SegmentAction, type SubtitlePaneHandle } from "../components/SubtitlePane";
import { TranslationEngineMenu, fmtSize } from "../components/TranslationEngineMenu";
import { Badge, Button, IconButton, Modal } from "../components/ui";
import { VuMeter } from "../components/VuMeter";
import { useT, type MessageKey } from "../i18n";
import { DisplayCaptureError, displaySurface, requestScreenView } from "../lib/displayCapture";
import { useHotkeys } from "../lib/hotkeys";
import { pipSupported, useDocumentPip } from "../lib/pip";
import { fmtClock, fmtTs } from "../lib/time";
import { errorMessage, useApp } from "../store/app";
import { pinnedIds, useSession } from "../store/session";

const WARNING_KEYS: Record<string, MessageKey> = {
  SOURCE_ENDED: "live.source_ended",
};

function LiveTimer() {
  const status = useSession((s) => s.status);
  const [now, setNow] = useState(() => Date.now());
  const anchor = useRef({ t: 0, at: Date.now() });
  useEffect(() => {
    if (status) anchor.current = { t: status.t_ms, at: Date.now() };
  }, [status]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const live = status?.state === "live";
  const ms = anchor.current.t + (live ? Math.min(3000, now - anchor.current.at) : 0);
  return <span className="font-mono tabular-nums">{fmtClock(ms)}</span>;
}

function LiveVu({ capturing }: { capturing: boolean }) {
  const db = useSession((s) => s.levelDb);
  const live = useSession((s) => s.status?.state === "live");
  // Loopback / micro laptop / điện thoại: backend đo mức âm và gửi sự kiện "level".
  const backendSource = useSession((s) => (s.session?.capture_source ?? s.session?.audio_mode ?? "tab") !== "tab");
  return <VuMeter db={db} active={live && (capturing || backendSource)} />;
}

/** Chế độ tiết kiệm pin (IC-FR-70): gom sự kiện, cập nhật giao diện 1 lần/giây. */
const EVENT_FLUSH_MS = 1000;

type ScreenLayout = "side" | "stack";
/** Hình đang xem trong khung màn hình; ``fromAudio``: lấy chung từ tab đang bắt âm thanh. */
interface ScreenSource {
  stream: MediaStream;
  label: string;
  fromAudio: boolean;
}

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* localStorage không dùng được */
  }
}

/** Thanh kéo đổi tỉ lệ khung màn hình / phụ đề. */
function SplitHandle({
  layout,
  containerRef,
  onRatio,
  title,
}: {
  layout: ScreenLayout;
  containerRef: RefObject<HTMLDivElement>;
  onRatio: (ratio: number) => void;
  title: string;
}) {
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const r = layout === "side" ? (ev.clientX - rect.left) / rect.width : (ev.clientY - rect.top) / rect.height;
      onRatio(Math.min(0.85, Math.max(0.15, r)));
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  };
  return (
    <div
      role="separator"
      aria-orientation={layout === "side" ? "vertical" : "horizontal"}
      title={title}
      onPointerDown={onPointerDown}
      className={`${
        layout === "side" ? "w-1.5 cursor-col-resize" : "h-1.5 cursor-row-resize"
      } shrink-0 touch-none bg-slate-200 transition-colors hover:bg-brand-400 dark:bg-slate-800 dark:hover:bg-brand-600`}
    />
  );
}

export default function LiveSession() {
  const { id } = useParams();
  const sessionId = Number(id);
  const t = useT();
  const navigate = useNavigate();
  const toast = useApp((s) => s.toast);
  const settings = useApp((s) => s.settings?.settings);
  const health = useApp((s) => s.health);
  const lang = useApp((s) => s.lang);
  const loadHealth = useApp((s) => s.loadHealth);
  const saveSettings = useApp((s) => s.saveSettings);

  const session = useSession((s) => s.session);
  const course = useSession((s) => s.course);
  const status = useSession((s) => s.status);
  const cost = useSession((s) => s.cost);
  const threshold = useSession((s) => s.threshold);
  const pinCount = useSession((s) => s.pins.length);
  const networkOnline = useSession((s) => s.networkOnline);
  const translationSlow = useSession((s) => s.translationSlow);
  const hasEn = useSession((s) => s.order.some((i) => s.segments[i]?.text_en));

  const [autoScroll, setAutoScroll] = useState(true);
  const [hideVi, setHideVi] = useState(false);
  const [fontScale, setFontScale] = useState(() => Number(localStorage.getItem("lezionelive.font") || "1"));
  const [capturing, setCapturing] = useState(false);
  const [sourceLabel, setSourceLabel] = useState("");
  const [costModal, setCostModal] = useState(false);
  const [authProblem, setAuthProblem] = useState(false);
  const [wsDown, setWsDown] = useState(false);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [ending, setEnding] = useState(false);
  const [boardOpen, setBoardOpen] = useState(false);
  const [pairOpen, setPairOpen] = useState(false);
  const [localProblem, setLocalProblem] = useState(false);
  // Trợ lý: nút logo nổi, bấm mới mở khung hỏi đáp (không chiếm cột cố định).
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantWide, setAssistantWide] = useState(() => stored("lezionelive.assistant.wide") === "1");
  const [assistantBusy, setAssistantBusy] = useState(false);
  const [assistantUnread, setAssistantUnread] = useState(false);
  // Khung xem màn hình Teams / tab ngay trong màn hình học.
  const [showScreen, setShowScreen] = useState(() => stored("lezionelive.screen") === "1");
  const [screen, setScreen] = useState<ScreenSource | null>(null);
  const [screenLayout, setScreenLayout] = useState<ScreenLayout>(() => {
    const saved = stored("lezionelive.screen.layout");
    return saved === "side" || saved === "stack" ? saved : window.innerWidth >= 1100 ? "side" : "stack";
  });
  const [screenRatio, setScreenRatio] = useState(() => {
    const saved = Number(stored("lezionelive.screen.ratio"));
    return saved >= 0.15 && saved <= 0.85 ? saved : 0.58;
  });

  const socketRef = useRef<SessionSocket | null>(null);
  const captureRef = useRef<TabAudioCapture | null>(null);
  const paneRef = useRef<SubtitlePaneHandle>(null);
  const assistantRef = useRef<AssistantHandle>(null);
  const assistantOpenRef = useRef(assistantOpen);
  assistantOpenRef.current = assistantOpen;
  const screenRef = useRef<ScreenSource | null>(null);
  const showScreenRef = useRef(showScreen);
  showScreenRef.current = showScreen;
  const mainRef = useRef<HTMLDivElement>(null);
  const tRef = useRef(t);
  tRef.current = t;
  const { pipWindow, open: openPip, close: closePip } = useDocumentPip();

  const showEn = settings?.secondary_language === "en" || hasEn;
  const state = status?.state ?? session?.status ?? "live";
  const audioMode = session?.capture_source ?? session?.audio_mode ?? "tab";
  const inclass = session?.inclass ?? (audioMode === "mic_local" || audioMode === "mic_companion");
  const pendingTranslations = useSession((s) => s.pendingTranslations);

  // ------------------------------------------------------------ WebSocket
  useEffect(() => {
    const store = useSession.getState();
    store.reset();
    let socket: SessionSocket | null = null;
    let cancelled = false;

    let buffer: ServerEvent[] = [];
    const flush = () => {
      const events = buffer;
      buffer = [];
      for (const ev of events) useSession.getState().apply(ev);
    };
    const flushTimer = window.setInterval(flush, EVENT_FLUSH_MS);

    const onEvent = (ev: ServerEvent) => {
      const saver = useSession.getState().status?.battery_saver;
      if (saver && (ev.type === "segment.translation.delta" || ev.type === "level" || ev.type === "source.health")) {
        buffer.push(ev); // tiết kiệm pin: dồn cập nhật liên tục, vẽ lại mỗi giây
      } else {
        if (buffer.length) flush();
        useSession.getState().apply(ev);
      }
      const tt = tRef.current;
      if (ev.type === "companion.connected") toast("info", tt("live.phone_connected", { name: ev.device.name }));
      else if (ev.type === "companion.disconnected") toast("warning", tt("live.phone_disconnected", { name: ev.device.name }));
      else if (ev.type === "board.captured") {
        if (ev.status === "done") toast("success", tt("live.board_done", { n: ev.suggestions }));
        else toast("warning", ev.capture.error ?? tt("board.failed"));
      }
      if (ev.type === "segment.translated") {
        setAuthProblem(false); // Claude dịch được lại (key mới / đăng nhập lại) hoặc model máy đã chạy
        setLocalProblem(false);
      }
      if (ev.type !== "warning") return;
      switch (ev.code) {
        case "LOCAL_MODEL_MISSING":
          setLocalProblem(true);
          break;
        case "COST_THRESHOLD":
          setCostModal(true);
          break;
        case "API_KEY_INVALID":
        case "API_KEY_MISSING":
          setAuthProblem(true);
          break;
        case "NETWORK_DOWN":
          break; // banner theo trạng thái mạng
        case "BATTERY_LOW":
          toast("warning", ev.message, {
            ttl: 20000,
            action: {
              label: tt("live.enable_saver"),
              onClick: () => void api.patchSession(sessionId, { battery_saver: true }).catch(() => undefined),
            },
          });
          break;
        case "BATTERY_CRITICAL":
        case "MIC_ERROR":
          toast("error", ev.message, { ttl: 15000 });
          break;
        case "COMPANION_RESTORED":
          toast("success", ev.message);
          break;
        default: {
          const key = WARNING_KEYS[ev.code];
          toast("warning", key ? tt(key) : ev.message, { ttl: ev.code === "LOW_SNR" || ev.code === "COMPANION_LOST" ? 12000 : undefined });
        }
      }
    };

    store
      .load(sessionId)
      .then((detail) => {
        if (cancelled) return;
        if (detail.session.status === "ended") {
          navigate(`/sessions/${sessionId}`, { replace: true });
          return;
        }
        socket = new SessionSocket(sessionId, {
          onEvent,
          onOpen: (reconnected) => {
            setWsDown(false);
            if (reconnected) {
              void useSession.getState().loadMissed();
              toast("info", tRef.current("live.reconnected"));
            }
          },
          onClose: (willRetry) => willRetry && setWsDown(true),
        });
        socket.connect();
        socketRef.current = socket;
      })
      .catch((e) => toast("error", errorMessage(e)));

    return () => {
      cancelled = true;
      window.clearInterval(flushTimer);
      socket?.close();
      socketRef.current = null;
      captureRef.current?.stop();
      captureRef.current = null;
      screenRef.current?.stream.getVideoTracks().forEach((tr) => tr.stop());
      screenRef.current = null;
      useSession.getState().reset();
    };
  }, [sessionId, navigate, toast]);

  useEffect(() => {
    localStorage.setItem("lezionelive.font", String(fontScale));
  }, [fontScale]);

  useEffect(() => store("lezionelive.assistant.wide", assistantWide ? "1" : "0"), [assistantWide]);
  useEffect(() => store("lezionelive.screen", showScreen ? "1" : "0"), [showScreen]);
  useEffect(() => store("lezionelive.screen.layout", screenLayout), [screenLayout]);
  useEffect(() => store("lezionelive.screen.ratio", String(screenRatio)), [screenRatio]);

  useEffect(() => {
    if (!health) return;
    if (health.api_key.present && !health.translation.auth_failed) setAuthProblem(false);
    if (health.translation.ready !== false && !health.translation.auth_failed) setLocalProblem(false);
  }, [health]);

  // ---------------------------------------------------------- màn hình
  const surfaceLabel = useCallback(
    (track: MediaStreamTrack | null | undefined) => {
      const surface = displaySurface(track);
      return surface ? t(`screen.surface_${surface}` as MessageKey) : "";
    },
    [t],
  );
  /** Đổi hình đang xem; chỉ dừng track hình của nguồn cũ (âm thanh tab nếu có vẫn chạy). */
  const setScreenSource = useCallback((next: ScreenSource | null) => {
    const prev = screenRef.current;
    if (prev && prev.stream !== next?.stream) prev.stream.getVideoTracks().forEach((tr) => tr.stop());
    screenRef.current = next;
    setScreen(next);
    if (!next) return;
    // Người dùng bấm "Dừng chia sẻ" trên thanh của Chrome.
    next.stream.getVideoTracks()[0]?.addEventListener("ended", () => {
      if (screenRef.current?.stream === next.stream) {
        screenRef.current = null;
        setScreen(null);
      }
    });
  }, []);

  const pickScreen = useCallback(async () => {
    try {
      const stream = await requestScreenView();
      setScreenSource({ stream, label: surfaceLabel(stream.getVideoTracks()[0]), fromAudio: false });
      setShowScreen(true);
    } catch (e) {
      if (e instanceof DisplayCaptureError && e.code === "DENIED") return;
      toast(
        "error",
        e instanceof DisplayCaptureError && e.code === "UNSUPPORTED"
          ? t("screen.unsupported")
          : t("screen.error", { error: errorMessage(e) }),
      );
    }
  }, [setScreenSource, surfaceLabel, t, toast]);

  // ---------------------------------------------------------------- audio
  const pickSource = useCallback(async () => {
    if (screenRef.current?.fromAudio) setScreenSource(null); // hình lấy chung với nguồn âm thanh cũ
    captureRef.current?.stop();
    const cap = new TabAudioCapture({
      onFrame: (pcm) => {
        if (useSession.getState().status?.state === "live") socketRef.current?.sendAudio(pcm);
      },
      onLevel: (db) => useSession.setState({ levelDb: db }),
      onEnded: () => {
        setCapturing(false);
        socketRef.current?.sendControl("source_ended");
      },
    }, { keepVideo: showScreenRef.current });
    try {
      await cap.start();
    } catch (e) {
      if (e instanceof CaptureError) {
        if (e.code === "NO_AUDIO") toast("error", t("live.no_audio_track"), { ttl: 10000 });
        else if (e.code !== "DENIED") toast("error", t("live.capture_error", { error: e.message }));
      } else {
        toast("error", t("live.capture_error", { error: errorMessage(e) }));
      }
      return;
    }
    captureRef.current = cap;
    setCapturing(true);
    setSourceLabel(cap.label);
    // Khung màn hình đang bật và chưa có hình riêng: hiện luôn hình tab vừa chọn (thường là tab Teams).
    if (cap.videoTrack && (!screenRef.current || screenRef.current.fromAudio)) {
      setScreenSource({ stream: new MediaStream([cap.videoTrack]), label: surfaceLabel(cap.videoTrack), fromAudio: true });
    } else {
      cap.videoTrack?.stop();
    }
    if (useSession.getState().status?.state === "paused") await api.resume(sessionId).catch(() => undefined);
  }, [sessionId, setScreenSource, surfaceLabel, t, toast]);

  const togglePause = useCallback(async () => {
    const current = useSession.getState().status?.state;
    try {
      if (current === "live") {
        await api.pause(sessionId);
      } else if (current === "paused") {
        if (audioMode === "tab" && !captureRef.current?.active) {
          await pickSource();
          return;
        }
        await api.resume(sessionId);
      }
    } catch (e) {
      toast("error", errorMessage(e));
    }
  }, [audioMode, pickSource, sessionId, toast]);

  const endSession = async () => {
    setEnding(true);
    try {
      await api.end(sessionId);
      captureRef.current?.stop();
      captureRef.current = null;
      navigate(`/sessions/${sessionId}`);
    } catch (e) {
      toast("error", errorMessage(e));
      setEnding(false);
    }
  };

  // ------------------------------------------------------------- actions
  const pinSegment = useCallback(
    async (segId?: number) => {
      const st = useSession.getState();
      const target = segId ?? st.selectedId ?? st.order[st.order.length - 1];
      if (!target) return;
      if (pinnedIds(st.pins).has(target)) return;
      const optimistic: Pin = {
        id: -Date.now(),
        session_id: sessionId,
        segment_id: target,
        kind: "confused",
        note: null,
        created_at: new Date().toISOString(),
      };
      st.addPin(optimistic); // hiện 📌 ngay (AC-07: ≤ 200 ms)
      try {
        const pin = await api.createPin(sessionId, { segment_id: target, kind: "confused" });
        useSession.getState().removePin(optimistic.id);
        useSession.getState().addPin(pin);
      } catch (e) {
        useSession.getState().removePin(optimistic.id);
        toast("error", errorMessage(e));
      }
    },
    [sessionId, toast],
  );

  const openAssistant = useCallback(() => {
    setAssistantOpen(true);
    setAssistantUnread(false);
  }, []);

  const askAbout = useCallback(
    (seg: Segment) => {
      const st = useSession.getState();
      const context = st.order
        .map((i) => st.segments[i])
        .filter((s) => s.id !== seg.id && Math.abs(s.t_start_ms - seg.t_start_ms) <= 30_000)
        .map((s) => `[${fmtTs(s.t_start_ms)}] ${s.text_it_edited ?? s.text_it}`)
        .join("\n");
      const text = t("live.ask_segment", {
        ts: fmtTs(seg.t_start_ms),
        it: seg.text_it_edited ?? seg.text_it,
        vi: seg.text_vi ? `\n(${seg.text_vi})` : "",
        context: context || "—",
      });
      openAssistant();
      window.setTimeout(() => assistantRef.current?.prefill(text), 0);
    },
    [openAssistant, t],
  );

  const onAction = useCallback(
    async (action: SegmentAction, seg: Segment) => {
      const st = useSession.getState();
      try {
        if (action === "pin") await pinSegment(seg.id);
        else if (action === "unpin") {
          const pin = st.pins.find((p) => p.segment_id === seg.id);
          if (pin) {
            st.removePin(pin.id);
            await api.deletePin(pin.id);
          }
        } else if (action === "ask") askAbout(seg);
        else if (action === "copy") {
          await navigator.clipboard.writeText([seg.text_it_edited ?? seg.text_it, seg.text_vi].filter(Boolean).join("\n"));
          toast("success", t("common.copied"));
        } else if (action === "retry") await api.retrySegment(seg.id);
        else if (action === "speaker") {
          // Sửa nhãn người nói (IC-FR-31): hệ thống học lại từ sửa đổi.
          const next = seg.speaker === "other" ? "lecturer" : "other";
          useSession.getState().upsertSegments([await api.setSpeaker(seg.id, next)]);
        }
      } catch (e) {
        toast("error", errorMessage(e));
      }
    },
    [askAbout, pinSegment, t, toast],
  );

  const saveEdit = useCallback(async (seg: Segment, text: string) => {
    const updated = await api.editSegment(seg.id, text);
    useSession.getState().upsertSegments([updated]);
  }, []);

  const withAssistant = (fn: (a: AssistantHandle) => void) => {
    openAssistant();
    window.setTimeout(() => assistantRef.current && fn(assistantRef.current), 0);
  };

  const assistantBusyRef = useRef(false);
  const onAssistantBusy = useCallback((busy: boolean) => {
    const finished = assistantBusyRef.current && !busy;
    assistantBusyRef.current = busy;
    setAssistantBusy(busy);
    if (finished && !assistantOpenRef.current) setAssistantUnread(true); // trả lời xong khi khung đang đóng
  }, []);

  useHotkeys({
    "mod+b": () => void pinSegment(),
    "mod+k": () => withAssistant((a) => a.focusChat()),
    "mod+shift+o": () => withAssistant((a) => a.newChat()),
    "mod+h": () => setHideVi((v) => !v),
    "mod+e": () => {
      const st = useSession.getState();
      const seg = st.segments[st.selectedId ?? st.order[st.order.length - 1]];
      if (seg)
        withAssistant((a) =>
          a.ask(t("live.explain_segment", { ts: fmtTs(seg.t_start_ms), text: seg.text_it_edited ?? seg.text_it })),
        );
    },
    space: () => void togglePause(),
  });

  // ----------------------------------------------------------------- UI
  if (!session) {
    return <div className="flex h-screen items-center justify-center text-slate-500">{t("common.loading")}</div>;
  }
  const translationOff = status ? !status.translation_enabled : !session.translation_enabled;
  const localEngine = (settings?.translate_provider ?? health?.translation.provider) === "local";
  const keyProblem =
    !localEngine && (authProblem || (health ? !(health.translation.ready ?? health.api_key.present) : false));
  const localModel = health?.translation.local_model;
  const localMissing = localEngine && (localProblem || health?.translation.ready === false);
  const useLocalEngine = () =>
    void saveSettings({ translate_provider: "local" })
      .then(() => toast("success", t("engine.switched", { name: t("engine.local_short") })))
      .catch((e) => toast("error", errorMessage(e)));

  return (
    <div className="flex h-screen flex-col bg-slate-50 dark:bg-slate-950">
      {/* Header kiểu thanh cuộc họp Teams: trạng thái bên trái, nhóm công cụ bên phải, nút Kết thúc đỏ. */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
        <Link
          to="/"
          title={t("nav.home")}
          aria-label={t("nav.home")}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900 dark:hover:bg-slate-800 dark:hover:text-white"
        >
          <ArrowLeft className="h-[1.1rem] w-[1.1rem]" />
        </Link>
        <div className="flex items-center gap-2 text-sm font-semibold">
          {state === "live" ? (
            <span
              className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-1 text-red-600 dark:bg-red-950/50 dark:text-red-400"
              title={inclass ? t("live.recording_indicator") : undefined}
            >
              <span className="h-2 w-2 animate-pulseDot rounded-full bg-red-600" /> {inclass ? t("live.rec") : t("live.live")}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400">
              <Pause className="h-3.5 w-3.5" /> {t("live.paused")}
            </span>
          )}
          <span className="font-mono text-slate-600 dark:text-slate-300">
            <LiveTimer />
          </span>
        </div>
        <div className="min-w-0 flex-1 truncate">
          <span className="font-semibold">{course?.name_it || course?.name || session.title}</span>
          {course && <span className="ml-2 text-sm text-slate-500">{session.title}</span>}
        </div>
        <LiveVu capturing={capturing} />
        {inclass && (
          <>
            <SourceBadge />
            <LiveSnr />
            <NetworkBadge />
            <BatteryBadge />
          </>
        )}
        <CostBadge cost={cost} threshold={threshold ?? settings?.cost_threshold_usd ?? null} />
        <TranslationEngineMenu sessionId={sessionId} translationEnabled={!translationOff} />
        {(audioMode === "tab" || audioMode === "loopback") && (
          <AudioSourcePicker mode={audioMode} capturing={capturing} label={sourceLabel} onPick={pickSource} />
        )}

        <div className="flex items-center gap-0.5 rounded-xl border border-slate-200 bg-slate-50 p-0.5 dark:border-slate-700 dark:bg-slate-800/60">
          <IconButton label={t("screen.toggle")} active={showScreen} onClick={() => setShowScreen((v) => !v)}>
            <MonitorUp className="h-[1.1rem] w-[1.1rem]" />
            <span className="hidden text-xs xl:inline">{t("screen.toggle")}</span>
          </IconButton>
          {inclass && (
            <>
              <IconButton label={t("live.board")} onClick={() => setBoardOpen(true)}>
                <Camera className="h-[1.1rem] w-[1.1rem]" />
                <span className="hidden text-xs xl:inline">{t("live.board")}</span>
              </IconButton>
              <IconButton label={t("live.pair_phone")} onClick={() => setPairOpen(true)}>
                <Smartphone className="h-[1.1rem] w-[1.1rem]" />
              </IconButton>
            </>
          )}
          <span className="mx-0.5 h-5 w-px bg-slate-200 dark:bg-slate-700" aria-hidden />
          <IconButton label={t("live.listening_mode")} active={hideVi} onClick={() => setHideVi((v) => !v)}>
            {hideVi ? <EyeOff className="h-[1.1rem] w-[1.1rem]" /> : <Eye className="h-[1.1rem] w-[1.1rem]" />}
          </IconButton>
          <IconButton
            label={pipSupported() ? t("live.pip") : t("live.pip_unsupported")}
            active={Boolean(pipWindow)}
            disabled={!pipSupported()}
            onClick={() => (pipWindow ? closePip() : void openPip().catch(() => toast("error", t("live.pip_unsupported"))))}
          >
            <PictureInPicture2 className="h-[1.1rem] w-[1.1rem]" />
          </IconButton>
          <IconButton label={`${t("live.font")} −`} onClick={() => setFontScale((f) => Math.max(0.8, +(f - 0.1).toFixed(1)))}>
            <AArrowDown className="h-[1.1rem] w-[1.1rem]" />
          </IconButton>
          <IconButton label={`${t("live.font")} +`} onClick={() => setFontScale((f) => Math.min(1.8, +(f + 0.1).toFixed(1)))}>
            <AArrowUp className="h-[1.1rem] w-[1.1rem]" />
          </IconButton>
          <span className="mx-0.5 h-5 w-px bg-slate-200 dark:bg-slate-700" aria-hidden />
          <IconButton label={state === "live" ? t("live.pause") : t("live.resume")} onClick={togglePause}>
            {state === "live" ? <Pause className="h-[1.1rem] w-[1.1rem]" /> : <Play className="h-[1.1rem] w-[1.1rem]" />}
          </IconButton>
        </div>
        <Button size="sm" variant="danger" className="h-8" onClick={() => setConfirmEnd(true)} title={t("live.end")}>
          <CircleStop className="h-4 w-4" />
          <span className="hidden sm:inline">{t("live.end")}</span>
        </Button>
      </header>

      {/* Banners */}
      <div className="space-y-px">
        {wsDown && <Banner tone="amber">{t("live.reconnecting")}</Banner>}
        {!networkOnline && (
          <Banner tone="red">
            <WifiOff className="h-4 w-4 shrink-0" /> {inclass ? t("live.offline_pending", { n: pendingTranslations }) : t("live.offline")}
          </Banner>
        )}
        {keyProblem && (
          <Banner tone="red">
            <KeyRound className="h-4 w-4 shrink-0" /> {t("live.no_key")}{" "}
            <span className="ml-1 inline-flex flex-wrap gap-3">
              <button type="button" className="inline-flex items-center gap-1 font-semibold underline" onClick={useLocalEngine}>
                <Laptop className="h-3.5 w-3.5" /> {t("live.use_local")}
              </button>
              <button
                type="button"
                className="font-semibold underline"
                onClick={() =>
                  void api
                    .retryFailed(sessionId)
                    .then(() => loadHealth())
                    .catch((e) => toast("error", errorMessage(e)))
                }
              >
                <RotateCw className="mr-1 inline h-3.5 w-3.5" />
                {t("engine.retry_claude")}
              </button>
              {/* Tab mới: rời màn hình học sẽ dừng bắt âm thanh. */}
              <a className="font-semibold underline" href="/settings" target="_blank" rel="noreferrer">
                {t("live.go_settings")} <ExternalLink className="inline h-3.5 w-3.5" />
              </a>
            </span>
          </Banner>
        )}
        {localMissing && (
          <Banner tone="red">
            <Laptop className="h-4 w-4 shrink-0" /> {localModel?.error || t("live.local_missing")}{" "}
            {localModel && !localModel.cached && (
              <button
                type="button"
                className="font-semibold underline"
                disabled={localModel.state === "downloading"}
                onClick={() =>
                  void api
                    .prepareDraftModel()
                    .then(() => loadHealth())
                    .catch((e) => toast("error", errorMessage(e)))
                }
              >
                {localModel.state === "downloading"
                  ? t("engine.downloading", {
                      pct: localModel.progress != null ? `${Math.round(localModel.progress * 100)}%` : "",
                    })
                  : t("engine.download", { size: fmtSize(localModel.size_mb, lang) })}
              </button>
            )}
          </Banner>
        )}
        {translationOff && (
          <Banner tone="amber">
            {t("live.translation_off")}{" "}
            <button
              type="button"
              className="font-semibold underline"
              onClick={() => void api.setTranslation(sessionId, true)}
            >
              {t("live.turn_on_translation")}
            </button>
          </Banner>
        )}
        {status && !status.stt_ready && <Banner tone="amber">
            <Hourglass className="h-4 w-4 shrink-0" /> {t("live.stt_loading")}
          </Banner>}
        {audioMode === "tab" && !capturing && <Banner tone="blue">
            <Headphones className="h-4 w-4 shrink-0" /> {t("live.source_hint")}
          </Banner>}
        {hideVi && <Banner tone="violet">
            <EyeOff className="h-4 w-4 shrink-0" /> {t("live.listening_on")}
          </Banner>}
      </div>

      {/* Main: [khung màn hình Teams/tab] | phụ đề — kéo thanh giữa để đổi tỉ lệ */}
      <div ref={mainRef} className={`flex min-h-0 flex-1 ${screenLayout === "stack" ? "flex-col" : ""}`}>
        {showScreen && (
          <>
            <section className="min-h-0 min-w-0 shrink-0" style={{ flexBasis: `${screenRatio * 100}%` }}>
              <ScreenViewer
                stream={screen?.stream ?? null}
                label={screen?.label ?? ""}
                layout={screenLayout}
                onPick={() => void pickScreen()}
                onStop={() => setScreenSource(null)}
                onHide={() => setShowScreen(false)}
                onToggleLayout={() => setScreenLayout((l) => (l === "side" ? "stack" : "side"))}
                onSnapshot={(dataUrl) => withAssistant((a) => a.attachImage(dataUrl, t("screen.ask_prompt")))}
              />
            </section>
            <SplitHandle layout={screenLayout} containerRef={mainRef} onRatio={setScreenRatio} title={t("screen.resize")} />
          </>
        )}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-white dark:bg-slate-900">
          <SubtitlePane
            ref={paneRef}
            live
            showEn={showEn}
            hideVi={hideVi}
            fontScale={fontScale}
            autoScroll={autoScroll}
            onAutoScrollChange={setAutoScroll}
            onAction={onAction}
            onSaveEdit={saveEdit}
            inclass={inclass}
            emptyText={
              inclass ? t("live.inclass_waiting") : capturing || audioMode === "loopback" ? t("live.waiting") : t("live.empty")
            }
          />
        </section>
      </div>

      <AssistantDock
        open={assistantOpen}
        wide={assistantWide}
        busy={assistantBusy}
        unread={assistantUnread}
        onToggle={() => (assistantOpen ? setAssistantOpen(false) : openAssistant())}
        onToggleWide={() => setAssistantWide((v) => !v)}
      >
        <AssistantPanel
          ref={assistantRef}
          sessionId={sessionId}
          onTimestamp={(ms) => paneRef.current?.scrollToMs(ms)}
          onAskPin={(_pin, seg) => askAbout(seg)}
          onJump={(seg) => paneRef.current?.scrollToSegment(seg.id)}
          onBusyChange={onAssistantBusy}
        />
      </AssistantDock>

      {/* Footer */}
      <footer className="flex flex-wrap items-center gap-3 border-t border-slate-200 bg-white px-4 py-1.5 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
        <button
          type="button"
          className="inline-flex items-center gap-1 font-medium hover:text-slate-900 dark:hover:text-white"
          onClick={() => withAssistant((a) => a.showTab("pins"))}
        >
          <PinIcon className="h-3.5 w-3.5" /> {t("live.pins_count", { n: pinCount })}
        </button>
        <span className="hidden items-center gap-1 md:inline-flex">
          <Keyboard className="h-3.5 w-3.5" /> {t("live.shortcuts")}
        </span>
        {inclass && (
          <span className="flex items-center gap-2">
            <DenoiseSelect sessionId={sessionId} />
            <BatterySaverToggle sessionId={sessionId} />
          </span>
        )}
        <label className="ml-auto flex items-center gap-1.5">
          <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
          {t("live.autoscroll")}
        </label>
        {status && status.queue_len > 0 && <Badge>{t("live.queue", { n: status.queue_len })}</Badge>}
        {translationSlow && (
          <Badge tone="amber">
            <Turtle className="h-3 w-3" /> {t("live.translation_slow")}
          </Badge>
        )}
      </footer>

      <Modal
        open={confirmEnd}
        title={t("live.end")}
        onClose={() => setConfirmEnd(false)}
        footer={
          <>
            <Button onClick={() => setConfirmEnd(false)}>{t("common.cancel")}</Button>
            <Button variant="danger" loading={ending} onClick={endSession}>
              <CircleStop className="h-4 w-4" /> {t("live.end")}
            </Button>
          </>
        }
      >
        <p className="text-sm">{t("live.end_confirm")}</p>
      </Modal>

      <Modal
        open={costModal}
        title={
          <>
            <CircleDollarSign className="h-5 w-5 text-amber-500" /> {t("live.cost_title")}
          </>
        }
        onClose={() => setCostModal(false)}
        footer={
          <>
            <Button
              onClick={async () => {
                setCostModal(false);
                await api.setTranslation(sessionId, false);
              }}
            >
              {t("live.cost_italian_only")}
            </Button>
            <Button variant="primary" onClick={() => setCostModal(false)}>
              {t("live.cost_continue")}
            </Button>
          </>
        }
      >
        <p className="text-sm">{t("live.cost_body")}</p>
        <p className="mt-2 text-center">
          <CostBadge cost={cost} threshold={threshold} />
        </p>
      </Modal>

      {pipWindow && createPortal(<PipSubtitles hideVi={hideVi} />, pipWindow.document.body)}
      {inclass && <BoardCaptureDialog sessionId={sessionId} open={boardOpen} onClose={() => setBoardOpen(false)} />}
      {inclass && <PairPhoneModal open={pairOpen} onClose={() => setPairOpen(false)} sessionId={sessionId} />}
    </div>
  );
}

function Banner({ tone, children }: { tone: "red" | "amber" | "blue" | "violet"; children: ReactNode }) {
  const tones = {
    red: "bg-red-50 text-red-800 dark:bg-red-950/60 dark:text-red-200",
    amber: "bg-amber-50 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200",
    blue: "bg-brand-50 text-brand-800 dark:bg-brand-950/60 dark:text-brand-200",
    violet: "bg-violet-50 text-violet-800 dark:bg-violet-950/60 dark:text-violet-200",
  };
  return <div className={`flex flex-wrap items-center gap-x-1.5 gap-y-1 px-4 py-1.5 text-sm ${tones[tone]}`}>{children}</div>;
}
