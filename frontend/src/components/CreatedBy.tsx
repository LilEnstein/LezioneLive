import { CREATED_BY } from "../lib/branding";

/** Dòng "Created by Turin" cố định ở góc dưới mọi trang (không chặn thao tác bên dưới). */
export function CreatedBy() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed bottom-1 right-2 z-[60] select-none text-[10px] font-medium tracking-wide text-slate-400/80 dark:text-slate-500/80"
    >
      {CREATED_BY}
    </div>
  );
}
