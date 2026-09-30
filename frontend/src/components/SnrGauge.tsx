/** Chỉ số SNR ước tính (IC-FR-05): 10 vạch, xanh ≥ 15 dB, vàng 10–15 dB, đỏ < 10 dB. */
import { useT } from "../i18n";

export function snrTone(snr: number | null | undefined): "green" | "amber" | "red" | "slate" {
  if (snr === null || snr === undefined) return "slate";
  if (snr >= 15) return "green";
  if (snr >= 10) return "amber";
  return "red";
}

const COLORS = {
  green: "bg-emerald-500",
  amber: "bg-amber-400",
  red: "bg-red-500",
  slate: "bg-slate-400",
};

export function SnrGauge({ snr, compact = false }: { snr: number | null | undefined; compact?: boolean }) {
  const t = useT();
  const tone = snrTone(snr);
  const lit = snr === null || snr === undefined ? 0 : Math.max(1, Math.min(10, Math.round(snr / 3)));
  return (
    <span className="inline-flex items-center gap-1.5" title={t("live.snr_title")}>
      <span className="text-xs font-semibold text-slate-500">SNR</span>
      <span className="flex items-end gap-px" aria-hidden>
        {Array.from({ length: compact ? 5 : 10 }, (_, i) => {
          const on = compact ? i < Math.ceil(lit / 2) : i < lit;
          return (
            <span
              key={i}
              className={`w-1 rounded-sm ${on ? COLORS[tone] : "bg-slate-300 dark:bg-slate-600"}`}
              style={{ height: `${6 + i * (compact ? 2 : 1)}px` }}
            />
          );
        })}
      </span>
      <span className="font-mono text-xs tabular-nums">{snr === null || snr === undefined ? "—" : `${Math.round(snr)} dB`}</span>
    </span>
  );
}
