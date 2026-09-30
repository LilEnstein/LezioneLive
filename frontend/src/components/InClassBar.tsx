/** Thanh trạng thái buổi học trực tiếp (mục 10.2 feature_spec): nguồn (laptop/điện thoại), SNR, mạng, pin, khử nhiễu, tiết kiệm pin. */
import {
  AudioLines,
  Battery,
  BatteryCharging,
  BatteryLow,
  Laptop,
  Leaf,
  Smartphone,
  Wifi,
  WifiOff,
} from "lucide-react";
import { api } from "../api/client";
import type { DenoiseLevel } from "../api/types";
import { useT } from "../i18n";
import { errorMessage, useApp } from "../store/app";
import { useSession } from "../store/session";
import { SnrGauge } from "./SnrGauge";

export function SourceBadge() {
  const t = useT();
  const status = useSession((s) => s.status);
  const health = useSession((s) => s.sourceHealth);
  const active = status?.active_source ?? status?.capture_source ?? "mic_local";
  const fallback = status?.fallback_active ?? false;
  const connected = health?.connected ?? false;
  const Icon = active === "mic_companion" ? Smartphone : Laptop;
  const label = fallback
    ? t("live.src_fallback")
    : active === "mic_companion"
      ? t("live.src_mic_companion")
      : t("live.src_mic_local");
  const live = status?.state === "live";
  return (
    <span
      className={`inline-flex max-w-[16rem] items-center gap-1 truncate rounded-md px-2 py-1 text-xs font-medium ${
        fallback
          ? "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
          : live && !connected
            ? "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200"
            : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200"
      }`}
      title={[health?.device, health?.latency_ms != null ? `${health.latency_ms} ms` : null, health?.error]
        .filter(Boolean)
        .join(" · ")}
    >
      <Icon className="h-3.5 w-3.5 shrink-0" /> {label}
      {live && !connected && <span>· {t("live.src_disconnected")}</span>}
    </span>
  );
}

export function LiveSnr() {
  const snr = useSession((s) => s.sourceHealth?.snr_db ?? null);
  return <SnrGauge snr={snr} compact />;
}

export function NetworkBadge() {
  const online = useSession((s) => s.networkOnline);
  const pending = useSession((s) => s.pendingTranslations);
  return (
    <span
      className={`inline-flex items-center gap-1 text-xs font-medium ${online ? "text-emerald-600 dark:text-emerald-400" : "text-red-600"}`}
      title={online ? "Online" : `Offline · ${pending}`}
    >
      {online ? <Wifi className="h-4 w-4" /> : <WifiOff className="h-4 w-4" />}
      {!online && pending}
    </span>
  );
}

export function BatteryBadge() {
  const t = useT();
  const battery = useSession((s) => s.battery);
  if (!battery) return null;
  const tone = battery.critical ? "text-red-600" : battery.low ? "text-amber-600" : "text-slate-600 dark:text-slate-300";
  const left = battery.minutes_left !== null ? ` · ${t("live.battery_left", { min: battery.minutes_left })}` : "";
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium tabular-nums ${tone}`} title={t("live.battery", { pct: battery.percent }) + left}>
      {battery.plugged ? (
        <BatteryCharging className="h-4 w-4" />
      ) : battery.low || battery.critical ? (
        <BatteryLow className="h-4 w-4" />
      ) : (
        <Battery className="h-4 w-4" />
      )}
      {battery.percent}%
    </span>
  );
}

export function DenoiseSelect({ sessionId }: { sessionId: number }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const level = useSession((s) => s.status?.denoise_level ?? s.session?.denoise_level ?? "light");
  const engine = useSession((s) => s.status?.denoise_engine);
  return (
    <label className="flex items-center gap-1 text-xs" title={`${t("live.denoise")}${engine ? ` (${engine})` : ""}`}>
      <AudioLines className="h-3.5 w-3.5" aria-hidden />
      <select
        className="rounded-md border border-slate-300 bg-white px-1 py-0.5 text-xs dark:border-slate-600 dark:bg-slate-800"
        value={level}
        onChange={async (e) => {
          try {
            await api.patchSession(sessionId, { denoise_level: e.target.value as DenoiseLevel });
          } catch (err) {
            toast("error", errorMessage(err));
          }
        }}
      >
        {(["off", "light", "strong"] as const).map((lv) => (
          <option key={lv} value={lv}>
            {t(`denoise.${lv}`)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function BatterySaverToggle({ sessionId }: { sessionId: number }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const on = useSession((s) => s.status?.battery_saver ?? s.session?.battery_saver ?? false);
  return (
    <button
      type="button"
      title={t("live.battery_saver")}
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${
        on ? "bg-emerald-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-200"
      }`}
      onClick={async () => {
        try {
          await api.patchSession(sessionId, { battery_saver: !on });
        } catch (e) {
          toast("error", errorMessage(e));
        }
      }}
    >
      <Leaf className="h-3.5 w-3.5" /> {t("live.battery_saver")}
    </button>
  );
}
