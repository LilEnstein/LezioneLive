import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from "lucide-react";
import { useT } from "../i18n";
import { useApp } from "../store/app";

const ICON = { info: Info, success: CircleCheck, warning: TriangleAlert, error: CircleAlert };

const TONE = {
  info: "border-brand-200 bg-white text-slate-800 dark:border-brand-800 dark:bg-slate-800 dark:text-slate-100",
  success: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100",
  warning: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100",
  error: "border-red-300 bg-red-50 text-red-900 dark:border-red-700 dark:bg-red-950 dark:text-red-100",
};

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  const dismiss = useApp((s) => s.dismiss);
  const tr = useT();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className={`pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 text-sm shadow-lg ${TONE[t.kind]}`}
        >
          {(() => {
            const Icon = ICON[t.kind];
            return <Icon className="mt-0.5 h-4 w-4 shrink-0" />;
          })()}
          <span className="flex-1 whitespace-pre-line">{t.message}</span>
          {t.action && (
            <button
              type="button"
              className="shrink-0 font-semibold underline"
              onClick={() => {
                t.action?.onClick();
                dismiss(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" className="shrink-0 opacity-60 hover:opacity-100" aria-label={tr("common.close")} onClick={() => dismiss(t.id)}>
            <X className="h-4 w-4" />
          </button>
        </div>
      ))}
    </div>
  );
}
