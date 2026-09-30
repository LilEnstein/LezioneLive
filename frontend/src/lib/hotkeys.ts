import { useEffect, useRef } from "react";

export type HotkeyMap = Record<string, (e: KeyboardEvent) => void>;

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/**
 * Phím tắt: "mod+b" (Ctrl trên Windows/Linux, Cmd trên macOS), "space", "0".."5"...
 * Tổ hợp có mod hoạt động cả khi đang gõ; phím đơn chỉ khi không focus ô nhập.
 */
export function useHotkeys(map: HotkeyMap, enabled = true): void {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    const handler = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key === " " ? "space" : e.key.toLowerCase();
      const combo = (mod ? "mod+" : "") + (e.shiftKey && mod ? "shift+" : "") + key;
      const fn = ref.current[combo];
      if (!fn) return;
      if (!mod && isTyping(e.target)) return;
      e.preventDefault();
      e.stopPropagation();
      fn(e);
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [enabled]);
}
