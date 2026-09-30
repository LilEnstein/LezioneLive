/**
 * Trợ lý gọi bằng nút mascot ở góc màn hình (thay cột/tab trợ lý cố định):
 * - ``AssistantDock``: màn hình học — bấm mascot mở khung hỏi đáp nổi phía trên nút.
 * - ``MascotButton`` + ``AssistantHeader``: dùng lại ở trang xem lại buổi học, nơi khung trợ lý mở thành cột cạnh
 *   transcript.
 * Khung chỉ bị ẩn chứ không unmount, nên câu trả lời đang stream không bị huỷ.
 */
import { Maximize2, Minimize2, X } from "lucide-react";
import { useCallback, useRef, useState, type ReactNode } from "react";

import { useT } from "../i18n";
import { IconButton } from "./ui";


/** Trạng thái chung: mở/đóng, đang trả lời, có câu trả lời mới lúc khung đang đóng. */
export function useAssistantToggle(initialOpen = false) {
  const [open, setOpenState] = useState(initialOpen);
  const [busy, setBusy] = useState(false);
  const [unread, setUnread] = useState(false);
  const openRef = useRef(open);
  const busyRef = useRef(false);

  const setOpen = useCallback((value: boolean) => {
    openRef.current = value;
    setOpenState(value);
    if (value) setUnread(false);
  }, []);

  const onBusyChange = useCallback((value: boolean) => {
    const finished = busyRef.current && !value;
    busyRef.current = value;
    setBusy(value);
    if (finished && !openRef.current) setUnread(true);
  }, []);

  return { open, busy, unread, setOpen, toggle: () => setOpen(!openRef.current), onBusyChange };
}

export function AssistantHeader({ onClose, children }: { onClose: () => void; children?: ReactNode }) {
  const t = useT();
  return (
    <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900">
      <img src="/mascot.webp" alt="" className="h-7 w-7 rounded-full ring-1 ring-slate-200 dark:ring-slate-700" />
      <span className="min-w-0 flex-1 truncate text-sm font-semibold">{t("assistant.title")}</span>
      {children}
      <IconButton label={t("common.close")} size="sm" onClick={onClose}>
        <X className="h-4 w-4" />
      </IconButton>
    </div>
  );
}

export function MascotButton({
  open,
  busy,
  unread,
  onClick,
  className = "fixed bottom-11 right-4 z-40",
}: {
  open: boolean;
  busy: boolean;
  unread: boolean;
  onClick: () => void;
  className?: string;
}) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      title={t("assistant.open")}
      aria-label={t("assistant.open")}
      aria-expanded={open}
      className={`${className} h-14 w-14 rounded-full bg-white shadow-lg ring-2 transition hover:scale-105 focus:outline-none focus-visible:ring-4 ${
        open ? "ring-brand-500" : "ring-white dark:ring-slate-700"
      }`}
    >
      <img src="/mascot.webp" alt="" className="h-full w-full rounded-full object-cover" draggable={false} />
      {busy && (
        <span
          className="pointer-events-none absolute -inset-1 animate-spin rounded-full border-2 border-transparent border-t-brand-500"
          aria-hidden
        />
      )}
      {unread && !open && (
        <span
          className="absolute right-0 top-0 h-3.5 w-3.5 rounded-full bg-red-500 ring-2 ring-white dark:ring-slate-900"
          title={t("assistant.new_answer")}
        />
      )}
    </button>
  );
}

export function AssistantDock({
  open,
  wide,
  busy,
  unread,
  onToggle,
  onToggleWide,
  children,
}: {
  open: boolean;
  wide: boolean;
  busy: boolean;
  unread: boolean;
  onToggle: () => void;
  onToggleWide: () => void;
  children: ReactNode;
}) {
  const t = useT();
  return (
    <>
      <div
        role="dialog"
        aria-label={t("assistant.title")}
        className={`${open ? "flex" : "hidden"} fixed bottom-[6.5rem] right-4 z-40 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900 ${
          wide
            ? "h-[calc(100vh-8.5rem)] w-[min(56rem,calc(100vw-2rem))]"
            : "h-[min(42rem,calc(100vh-8.5rem))] w-[min(27rem,calc(100vw-2rem))]"
        }`}
      >
        <AssistantHeader onClose={onToggle}>
          <IconButton label={wide ? t("assistant.shrink") : t("assistant.expand")} size="sm" onClick={onToggleWide}>
            {wide ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </IconButton>
        </AssistantHeader>
        <div className="min-h-0 flex-1">{children}</div>
      </div>
      <MascotButton open={open} busy={busy} unread={unread} onClick={onToggle} />
    </>
  );
}
