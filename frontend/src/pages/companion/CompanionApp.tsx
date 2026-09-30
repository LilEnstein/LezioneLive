/**
 * Ứng dụng web đồng hành trên điện thoại (IC-FR-02, IC-FR-60..62): ghép nối bằng QR → chọn chế độ
 * Micro / Phụ đề / Cả hai. Phụ đề dùng chung store phiên với giao diện laptop (cùng sự kiện WebSocket).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { Pin, RunnerStatus, Segment, Session } from "../../api/types";
import { MicError, MicStreamer } from "../../companion/mic";
import { CompanionSocket, type CompanionMessage, type ConnState, type PhoneMode } from "../../companion/socket";
import { useT } from "../../i18n";
import { useSession } from "../../store/session";
import { MicMode } from "./MicMode";
import { PairScreen } from "./Pair";
import { SubtitleMode } from "./SubtitleMode";

const MODE_KEY = "lezionelive.phoneMode";

interface Snapshot {
  type: "snapshot";
  settings?: { browser_audio_processing?: boolean };
  session: Session | null;
  course?: { id: number; name: string; name_it: string | null } | null;
  segments: Segment[];
  pins: Pick<Pin, "id" | "segment_id" | "kind">[];
  runner: RunnerStatus | null;
}

function applySnapshot(snap: Snapshot): void {
  const store = useSession.getState();
  store.reset();
  if (!snap.session) return;
  useSession.setState({
    sessionId: snap.session.id,
    session: snap.session,
    course: snap.course ? { ...snap.course, lecturer: null } : null,
    status: snap.runner,
    pins: snap.pins.map((p) => ({ ...p, session_id: snap.session?.id ?? 0, note: null, created_at: "" })),
    networkOnline: snap.runner?.network_online ?? true,
    sourceHealth: snap.runner?.source_health ?? null,
  });
  store.upsertSegments(snap.segments);
}

function savedMode(): PhoneMode | null {
  try {
    const m = localStorage.getItem(MODE_KEY);
    return m === "mic" || m === "subtitle" || m === "both" ? m : null;
  } catch {
    return null;
  }
}

export default function CompanionApp() {
  const t = useT();
  const [conn, setConn] = useState<ConnState>("connecting");
  const [fatal, setFatal] = useState<{ code: string; message: string } | null>(null);
  const [slow, setSlow] = useState(false);
  const [mode, setMode] = useState<PhoneMode | null>(null);
  const [micOn, setMicOn] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const [level, setLevel] = useState(-120);
  const [rtt, setRtt] = useState<number | null>(null);
  const [wakeLockOk, setWakeLockOk] = useState(true);
  const [processing, setProcessing] = useState(true);
  const [, setTick] = useState(0);
  const socketRef = useRef<CompanionSocket | null>(null);
  const micRef = useRef<MicStreamer | null>(null);
  const online = useSession((s) => s.networkOnline);

  // ------------------------------------------------------------ kết nối
  useEffect(() => {
    const url = new URL(location.href);
    const pairing = url.searchParams.get("t");
    const socket = new CompanionSocket(pairing, {
      onMessage: (msg: CompanionMessage) => {
        if (msg.type === "paired" || msg.type === "welcome") {
          if (url.searchParams.has("t")) {
            url.searchParams.delete("t"); // token ghép nối chỉ dùng một lần: xoá khỏi thanh địa chỉ
            history.replaceState(null, "", url.pathname + url.search);
          }
          setMode((m) => m ?? savedMode());
          return;
        }
        if (msg.type === "snapshot") {
          const snap = msg as unknown as Snapshot;
          if (snap.settings?.browser_audio_processing !== undefined) setProcessing(snap.settings.browser_audio_processing);
          applySnapshot(snap);
          return;
        }
        if (msg.type === "error") return;
        useSession.getState().apply(msg);
      },
      onState: (state) => {
        setConn((prev) => {
          if (prev === "open" && state !== "open" && micRef.current?.active && navigator.vibrate) {
            navigator.vibrate([200, 100, 200]); // IC-E-03: báo mất kết nối bằng rung
          }
          return state;
        });
      },
      onFatal: (code, message) => setFatal({ code, message }),
      onRtt: (ms) => setRtt(ms),
    });
    socketRef.current = socket;
    socket.connect();
    const slowTimer = window.setTimeout(() => setSlow(true), 10_000); // IC-E-02
    const batteryTimer = window.setInterval(() => setTick((x) => x + 1), 30_000);
    return () => {
      window.clearTimeout(slowTimer);
      window.clearInterval(batteryTimer);
      socket.close();
      micRef.current?.stop();
    };
  }, []);

  useEffect(() => {
    if (conn === "open") setSlow(false);
  }, [conn]);

  useEffect(() => {
    if (!mode) return;
    socketRef.current?.setMode(mode);
    try {
      localStorage.setItem(MODE_KEY, mode);
    } catch {
      /* ignore */
    }
  }, [mode]);

  // ---------------------------------------------------------------- micro
  const startMic = useCallback(async () => {
    setMicError(null);
    micRef.current?.stop();
    const mic = new MicStreamer(
      (pcm) => socketRef.current?.sendAudio(pcm),
      (db) => setLevel(db),
    );
    try {
      await mic.start(processing);
    } catch (e) {
      if (e instanceof MicError) setMicError(e.code === "DENIED" ? t("ph.mic_denied") : e.code === "UNSUPPORTED" ? t("ph.mic_unsupported") : e.message);
      else setMicError((e as Error).message);
      return;
    }
    micRef.current = mic;
    setWakeLockOk(mic.wakeLockOk);
    setMicOn(true);
  }, [processing, t]);

  const stopMic = useCallback(() => {
    micRef.current?.stop();
    micRef.current = null;
    setMicOn(false);
  }, []);

  const changeMode = useCallback(() => {
    stopMic();
    setMode(null);
  }, [stopMic]);

  if (fatal || conn !== "open" || !mode) {
    return (
      <PairScreen
        conn={conn}
        fatal={fatal}
        slow={slow}
        onPick={(m) => {
          setMode(m);
          if (m !== "subtitle") void startMic();
        }}
      />
    );
  }

  if (mode === "mic") {
    return (
      <MicMode
        micOn={micOn}
        level={level}
        rtt={rtt}
        error={micError}
        wakeLockOk={wakeLockOk}
        onStart={() => void startMic()}
        onStop={stopMic}
        onChangeMode={changeMode}
      />
    );
  }

  return (
    <SubtitleMode
      token={socketRef.current?.token ?? ""}
      micOn={micOn}
      both={mode === "both"}
      phoneBattery={socketRef.current?.phoneBattery ?? null}
      online={online}
      onStartMic={() => void startMic()}
      onChangeMode={changeMode}
    />
  );
}
