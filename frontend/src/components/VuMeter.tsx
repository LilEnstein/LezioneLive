/** Đồng hồ mức âm 5 vạch (FR-AC-03). */
import { Mic } from "lucide-react";

const STEPS = [-55, -45, -35, -25, -15];

export function VuMeter({ db, active }: { db: number; active: boolean }) {
  const lit = active ? STEPS.filter((s) => db >= s).length : 0;
  return (
    <div className="flex items-end gap-0.5" title={active ? `${db.toFixed(0)} dBFS` : undefined} aria-label="VU">
      <Mic className="mr-1 h-4 w-4 self-center text-slate-500" aria-hidden />
      {STEPS.map((_, i) => (
        <span
          key={i}
          className={`w-1.5 rounded-sm transition-colors duration-75 ${
            i < lit ? (i >= 4 ? "bg-red-500" : i >= 3 ? "bg-amber-400" : "bg-emerald-500") : "bg-slate-300 dark:bg-slate-600"
          }`}
          style={{ height: `${8 + i * 3}px` }}
        />
      ))}
    </div>
  );
}
