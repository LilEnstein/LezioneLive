/**
 * Chế độ Phụ đề trên điện thoại (IC-FR-61, mục 10.3): nền tối, chữ ≥ 18 px, vài câu gần nhất; kéo lên xem lịch sử,
 * tự quay về cuối sau 10 giây; 📌 Ghim, 💡 Giải thích (hỏi nhanh Claude), 📷 Chụp bảng, 🙋 Hỏi trong lớp (IC-US-10).
 */
import {
  Battery,
  BatteryCharging,
  Camera,
  GraduationCap,
  Hand,
  Lightbulb,
  Mic,
  Pause,
  Pin,
  Settings,
  Undo2,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { postSSE } from "../../api/sse";
import type { SpeakResult } from "../../api/types";
import { phoneApi } from "../../companion/socket";
import { Markdown } from "../../components/Markdown";
import { useT } from "../../i18n";
import { downscaleImage } from "../../lib/image";
import { fmtClock } from "../../lib/time";
import { useSession } from "../../store/session";

const HISTORY = 60;
const BACK_TO_LATEST_MS = 10_000;

function Clock() {
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
  return <span className="font-mono tabular-nums">{fmtClock(anchor.current.t + (live ? Math.min(3000, now - anchor.current.at) : 0))}</span>;
}

function Row({ id }: { id: number }) {
  const t = useT();
  const seg = useSession((s) => s.segments[id]);
  const partial = useSession((s) => s.partial[id]);
  const pinned = useSession((s) => s.pins.some((p) => p.segment_id === id));
  if (!seg || seg.status === "redacted") return null;
  const other = seg.speaker === "other";
  const vi =
    seg.text_vi ??
    (partial !== undefined ? `${partial}▌` : seg.status === "pending_translation" ? t("live.pending_translation") : "⋯");
  return (
    <div className={`rounded-xl px-3 py-2 ${other ? "ml-6 bg-fuchsia-950/30" : "bg-slate-900/70"} ${pinned ? "ring-1 ring-amber-400/70" : ""}`}>
      <p className="text-[0.95rem] leading-snug text-slate-400">
        {seg.speaker === "lecturer" ? (
          <GraduationCap className="mr-1 inline h-3.5 w-3.5 align-[-0.1em]" />
        ) : other ? (
          <Hand className="mr-1 inline h-3.5 w-3.5 align-[-0.1em]" />
        ) : null}
        {seg.text_it_edited ?? seg.text_it}
      </p>
      <p className={`text-[1.2rem] font-medium leading-snug ${seg.translation_kind === "draft" ? "italic text-slate-300" : "text-slate-50"}`}>
        {seg.translation_kind === "draft" && <span className="mr-1 rounded bg-slate-700 px-1 text-xs not-italic">{t("live.draft")}</span>}
        {vi}
        {pinned && <Pin className="ml-1 inline h-3.5 w-3.5 align-[-0.1em] text-amber-400" fill="currentColor" />}
      </p>
    </div>
  );
}

function Sheet({ title, onClose, children }: { title: React.ReactNode; onClose: () => void; children: React.ReactNode }) {
  const t = useT();
  return (
    <div className="fixed inset-0 z-40 flex flex-col justify-end bg-black/60" onClick={onClose}>
      <div
        className="max-h-[80dvh] overflow-y-auto rounded-t-2xl bg-slate-900 p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-lg font-semibold">{title}</h3>
          <button type="button" className="p-2 text-slate-400" onClick={onClose} aria-label={t("common.close")}>
            <X className="h-5 w-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SubtitleMode({
  token,
  micOn,
  both,
  phoneBattery,
  online,
  onStartMic,
  onChangeMode,
}: {
  token: string;
  micOn: boolean;
  both: boolean;
  phoneBattery: { level: number; charging: boolean } | null;
  online: boolean;
  onStartMic: () => void;
  onChangeMode: () => void;
}) {
  const t = useT();
  const order = useSession((s) => s.order);
  const live = useSession((s) => s.status?.state === "live");
  const hasSession = useSession((s) => s.sessionId !== null);
  const partial = useSession((s) => s.partial);
  const [follow, setFollow] = useState(true);
  const [toast, setToast] = useState<string | null>(null);
  const [sheet, setSheet] = useState<"explain" | "ask" | null>(null);
  const [answer, setAnswer] = useState("");
  const [answerBusy, setAnswerBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [speak, setSpeak] = useState<SpeakResult | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const idleTimer = useRef<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const say = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3000);
  }, []);

  const toBottom = useCallback(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    if (follow) toBottom();
  }, [order.length, partial, follow, toBottom]);

  const onScroll = () => {
    const el = listRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setFollow(atBottom);
    if (idleTimer.current !== null) window.clearTimeout(idleTimer.current);
    if (!atBottom) idleTimer.current = window.setTimeout(() => setFollow(true), BACK_TO_LATEST_MS);
  };

  const pin = async () => {
    try {
      await phoneApi("/pins", token, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      say(t("ph.pinned"));
      if (navigator.vibrate) navigator.vibrate(30);
    } catch (e) {
      say((e as Error).message);
    }
  };

  const explain = async () => {
    setSheet("explain");
    setAnswer("");
    setAnswerBusy(true);
    try {
      await postSSE(
        "/companion/explain",
        {},
        ({ event, data }) => {
          const d = data as { text?: string; content?: string; message?: string };
          if (event === "delta") setAnswer((a) => a + (d.text ?? ""));
          else if (event === "done" && d.content) setAnswer(d.content);
          else if (event === "error") setAnswer(`⚠ ${d.message ?? ""}`);
        },
        undefined,
        { Authorization: `Bearer ${token}` },
      );
    } catch (e) {
      setAnswer(`⚠ ${(e as Error).message}`);
    } finally {
      setAnswerBusy(false);
    }
  };

  const ask = async () => {
    if (!question.trim()) return;
    setAnswerBusy(true);
    try {
      setSpeak(
        await phoneApi<SpeakResult>("/speak", token, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text_vi: question.trim() }),
        }),
      );
    } catch (e) {
      say((e as Error).message);
    } finally {
      setAnswerBusy(false);
    }
  };

  const sendBoard = async (file: File) => {
    try {
      const image = await downscaleImage(file);
      const fd = new FormData();
      fd.append("file", image, "board.jpg");
      say(t("ph.board_sent"));
      await phoneApi("/board-captures", token, { method: "POST", body: fd });
    } catch (e) {
      say((e as Error).message);
    }
  };

  const visible = order.slice(-HISTORY);

  return (
    <div className="flex h-[100dvh] flex-col bg-slate-950 text-slate-100">
      <header className="flex items-center gap-3 border-b border-slate-800 px-3 py-2 pt-[max(0.5rem,env(safe-area-inset-top))] text-sm">
        {live ? (
          <span className="flex items-center gap-1.5 font-bold text-red-500">
            <span className="h-2.5 w-2.5 animate-pulseDot rounded-full bg-red-600" /> REC
          </span>
        ) : (
          <Pause className="h-4 w-4 text-slate-500" />
        )}
        <Clock />
        {both && !micOn && (
          <button type="button" onClick={onStartMic} className="inline-flex items-center gap-1 rounded-full bg-red-600 px-2 py-0.5 text-xs font-semibold">
            <Mic className="h-3 w-3" /> {t("ph.start_mic")}
          </button>
        )}
        {both && micOn && <Mic className="h-4 w-4 text-red-400" />}
        <span className="ml-auto">{online ? <Wifi className="h-4 w-4 text-emerald-400" /> : <WifiOff className="h-4 w-4 text-red-400" />}</span>
        {phoneBattery && (
          <span className="inline-flex items-center gap-1 tabular-nums text-slate-400">
            {phoneBattery.charging ? <BatteryCharging className="h-4 w-4" /> : <Battery className="h-4 w-4" />}
            {Math.round(phoneBattery.level * 100)}%
          </span>
        )}
      </header>

      <div ref={listRef} onScroll={onScroll} className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-3">
        {!hasSession && <p className="py-20 text-center text-slate-500">{t("ph.waiting_session")}</p>}
        {hasSession && visible.length === 0 && <p className="py-20 text-center text-slate-500">{t("ph.empty")}</p>}
        {visible.map((id) => (
          <Row key={id} id={id} />
        ))}
      </div>
      {!follow && (
        <button
          type="button"
          onClick={() => {
            setFollow(true);
            toBottom();
          }}
          className="absolute bottom-24 left-1/2 -translate-x-1/2 rounded-full bg-brand-600 px-4 py-1.5 text-sm font-medium"
        >
          {t("live.jump_latest")}
        </button>
      )}

      <nav className="grid grid-cols-5 border-t border-slate-800 pb-[env(safe-area-inset-bottom)] text-center text-xs">
        {[
          { id: "pin", icon: Pin, label: t("ph.pin"), onClick: pin },
          { id: "explain", icon: Lightbulb, label: t("ph.explain"), onClick: explain },
          { id: "board", icon: Camera, label: t("ph.board"), onClick: () => fileRef.current?.click() },
          { id: "ask", icon: Hand, label: t("ph.ask"), onClick: () => setSheet("ask") },
          { id: "mode", icon: Settings, label: t("ph.change_mode"), onClick: onChangeMode },
        ].map((b) => (
          <button
            key={b.id}
            type="button"
            onClick={b.onClick}
            disabled={!hasSession && b.id !== "mode"}
            className="flex flex-col items-center gap-1 py-2.5 active:bg-slate-800 disabled:opacity-40"
          >
            <b.icon className="h-5 w-5" />
            {b.label}
          </button>
        ))}
      </nav>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) void sendBoard(f);
        }}
      />

      {toast && (
        <div className="fixed left-1/2 top-16 z-50 -translate-x-1/2 rounded-full bg-slate-800 px-4 py-2 text-sm shadow-lg">{toast}</div>
      )}

      {sheet === "explain" && (
        <Sheet
          title={
            <>
              <Lightbulb className="h-5 w-5 text-amber-400" /> {t("ph.explain")}
            </>
          } onClose={() => setSheet(null)}>
          {answer ? (
            <div className="text-[1.05rem]">
              <Markdown text={answer} className="prose-invert" />
            </div>
          ) : (
            answerBusy && <p className="text-slate-400">{t("ph.explaining")}</p>
          )}
        </Sheet>
      )}
      {sheet === "ask" && (
        <Sheet
          title={
            <>
              <Hand className="h-5 w-5 text-brand-300" /> {t("ph.ask")}
            </>
          } onClose={() => setSheet(null)}>
          <div className="space-y-3">
            <textarea
              rows={3}
              value={question}
              placeholder={t("ph.ask_placeholder")}
              onChange={(e) => setQuestion(e.target.value)}
              className="w-full rounded-xl border border-slate-700 bg-slate-950 p-3 text-base"
            />
            <button
              type="button"
              disabled={answerBusy || !question.trim()}
              onClick={ask}
              className="w-full rounded-xl bg-brand-600 py-3 font-semibold disabled:opacity-50"
            >
              {answerBusy ? "…" : t("ph.ask_submit")}
            </button>
            {speak && (
              <div className="space-y-2 rounded-xl bg-slate-950 p-3">
                <p className="text-xl font-semibold text-emerald-300">{speak.question_it}</p>
                <p className="font-mono text-base tracking-wide text-brand-300">{speak.pronunciation}</p>
                <p className="flex items-start gap-1.5 text-sm text-slate-400">
                  <Undo2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {speak.back_translation_vi}
                </p>
                {speak.tip && (
                  <p className="flex items-start gap-1.5 text-xs text-amber-300">
                    <Lightbulb className="h-3.5 w-3.5 shrink-0" /> {speak.tip}
                  </p>
                )}
              </div>
            )}
          </div>
        </Sheet>
      )}
    </div>
  );
}
