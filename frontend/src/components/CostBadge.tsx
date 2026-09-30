/** Chi phí API của phiên (FR-COST-01, AC-11): khớp tổng llm_usage × bảng giá cấu hình. */
import { fmtUsd } from "../lib/time";

export function CostBadge({ cost, threshold }: { cost: number; threshold: number | null }) {
  const over = threshold !== null && cost >= threshold;
  const near = threshold !== null && !over && cost >= threshold * 0.8;
  const tone = over
    ? "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-200"
    : near
      ? "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200"
      : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200";
  return (
    <span
      className={`rounded-md px-2 py-0.5 font-mono text-sm font-semibold tabular-nums ${tone}`}
      title={threshold !== null ? `${fmtUsd(cost)} / ${fmtUsd(threshold)}` : fmtUsd(cost)}
    >
      {fmtUsd(cost)}
    </span>
  );
}
