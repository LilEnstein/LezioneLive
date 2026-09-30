/** Màn hình ghép nối / chọn chế độ của điện thoại đồng hành (IC-FR-03, IC-FR-60, IC-E-02, IC-E-08). */
import { Captions, Mic, MonitorSmartphone, TriangleAlert, WifiOff } from "lucide-react";
import { useT } from "../../i18n";
import type { ConnState, PhoneMode } from "../../companion/socket";

export function PairScreen({
  conn,
  fatal,
  slow,
  onPick,
}: {
  conn: ConnState;
  fatal: { code: string; message: string } | null;
  slow: boolean;
  onPick: (mode: PhoneMode) => void;
}) {
  const t = useT();
  if (fatal) {
    const text =
      fatal.code === "TOKEN_EXPIRED" ? t("ph.token_expired") : fatal.code === "NO_TOKEN" ? t("ph.no_token") : t("ph.token_invalid");
    return (
      <Shell>
        <TriangleAlert className="h-12 w-12 text-amber-400" strokeWidth={1.5} />
        <p className="text-lg font-medium">{text}</p>
      </Shell>
    );
  }
  if (conn !== "open") {
    return (
      <Shell>
        <div className="h-10 w-10 animate-spin rounded-full border-4 border-slate-600 border-t-brand-400" />
        <p className="text-lg">{conn === "connecting" ? t("ph.connecting") : t("ph.reconnecting")}</p>
        {(slow || conn === "failed") && (
          <p className="flex items-start gap-2 rounded-xl bg-amber-900/40 p-3 text-sm text-amber-100">
            <WifiOff className="mt-0.5 h-4 w-4 shrink-0" /> {t("ph.cant_connect")}
          </p>
        )}
      </Shell>
    );
  }
  return (
    <Shell>
      <p className="text-lg font-semibold">{t("ph.choose_mode")}</p>
      <div className="w-full space-y-3">
        {(["mic", "subtitle", "both"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => onPick(m)}
            className="flex w-full items-center gap-4 rounded-2xl border border-slate-700 bg-slate-900 p-4 text-left active:bg-slate-800"
          >
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-600/20 text-brand-300">
              {m === "mic" ? <Mic className="h-6 w-6" /> : m === "subtitle" ? <Captions className="h-6 w-6" /> : <MonitorSmartphone className="h-6 w-6" />}
            </span>
            <span className="min-w-0">
              <span className="block text-xl font-semibold">{t(`ph.mode_${m}`)}</span>
              <span className="block text-sm text-slate-400">{t(`ph.mode_${m}_hint`)}</span>
            </span>
          </button>
        ))}
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const t = useT();
  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-5 p-6 text-center">
      <p className="text-sm font-semibold uppercase tracking-widest text-slate-500">{t("ph.title")}</p>
      {children}
    </div>
  );
}
