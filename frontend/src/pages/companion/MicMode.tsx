/** Chế độ Micro (IC-FR-60, mục 10.4): màn hình gần như đen — chấm đỏ REC, mức âm, SNR. Giữ màn hình sáng (IC-FR-06). */
import { CircleStop, Mic } from "lucide-react";
import { useT } from "../../i18n";
import { useSession } from "../../store/session";

export function MicLevel({ db }: { db: number }) {
  const pct = Math.max(0, Math.min(100, ((db + 60) / 55) * 100));
  return (
    <div className="h-1.5 w-48 overflow-hidden rounded-full bg-slate-800">
      <div
        className={`h-full rounded-full transition-[width] duration-100 ${pct > 90 ? "bg-red-500" : "bg-emerald-500/80"}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function MicMode({
  micOn,
  level,
  rtt,
  error,
  wakeLockOk,
  onStart,
  onStop,
  onChangeMode,
}: {
  micOn: boolean;
  level: number;
  rtt: number | null;
  error: string | null;
  wakeLockOk: boolean;
  onStart: () => void;
  onStop: () => void;
  onChangeMode: () => void;
}) {
  const t = useT();
  const snr = useSession((s) => s.sourceHealth?.snr_db ?? null);
  const live = useSession((s) => s.status?.state === "live");
  const hasSession = useSession((s) => s.sessionId !== null);

  if (!micOn) {
    return (
      <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-6 bg-black p-6 text-center">
        <button
          type="button"
          onClick={onStart}
          className="flex h-40 w-40 items-center justify-center rounded-full bg-red-600 text-lg font-semibold text-white shadow-lg shadow-red-900/40 active:bg-red-700"
        >
          <Mic className="h-16 w-16" strokeWidth={1.5} />
        </button>
        <p className="text-lg">{t("ph.start_mic")}</p>
        {error && <p className="rounded-xl bg-red-950 p-3 text-sm text-red-100">{error}</p>}
        <button type="button" onClick={onChangeMode} className="text-sm text-slate-500 underline">
          {t("ph.change_mode")}
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-5 bg-black p-6 text-center text-slate-400">
      <div className="flex items-center gap-3">
        <span className="h-5 w-5 animate-pulseDot rounded-full bg-red-600" />
        <span className="text-2xl font-bold tracking-widest text-red-500">REC</span>
      </div>
      <MicLevel db={level} />
      <p className="font-mono text-sm tabular-nums">
        SNR {snr === null ? "—" : `${Math.round(snr)} dB`}
        {rtt !== null && ` · ${t("ph.latency", { ms: Math.round(rtt / 2) })}`}
      </p>
      {!hasSession || !live ? <p className="text-sm text-slate-500">{t("ph.waiting_session")}</p> : null}
      {!wakeLockOk && <p className="max-w-xs text-xs text-amber-500">{t("ph.wakelock_off")}</p>}
      <div className="mt-8 flex gap-4 text-sm">
        <button type="button" onClick={onStop} className="inline-flex items-center gap-2 rounded-full border border-slate-700 px-4 py-2 text-slate-300">
          <CircleStop className="h-4 w-4" /> {t("ph.stop_mic")}
        </button>
        <button type="button" onClick={onChangeMode} className="rounded-full border border-slate-800 px-4 py-2 text-slate-500">
          {t("ph.change_mode")}
        </button>
      </div>
    </div>
  );
}
