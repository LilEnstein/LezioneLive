/** Document Picture-in-Picture (Chrome/Edge 116+): cửa sổ phụ đề nổi đặt đè lên Teams (mục 12.4). */
import { useCallback, useEffect, useState } from "react";

interface DocumentPictureInPicture {
  requestWindow(options?: { width?: number; height?: number }): Promise<Window>;
  window: Window | null;
}

declare global {
  interface Window {
    documentPictureInPicture?: DocumentPictureInPicture;
  }
}

export function pipSupported(): boolean {
  return typeof window !== "undefined" && "documentPictureInPicture" in window;
}

function copyStyles(target: Document): void {
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const css = Array.from(sheet.cssRules)
        .map((r) => r.cssText)
        .join("\n");
      const style = target.createElement("style");
      style.textContent = css;
      target.head.appendChild(style);
    } catch {
      if (sheet.href) {
        const link = target.createElement("link");
        link.rel = "stylesheet";
        link.href = sheet.href;
        target.head.appendChild(link);
      }
    }
  }
  target.documentElement.className = document.documentElement.className;
}

export function useDocumentPip() {
  const [pipWindow, setPipWindow] = useState<Window | null>(null);

  const open = useCallback(async (width = 640, height = 220) => {
    if (!window.documentPictureInPicture) throw new Error("unsupported");
    const win = await window.documentPictureInPicture.requestWindow({ width, height });
    copyStyles(win.document);
    win.document.title = "LezioneLive";
    win.document.body.className = "bg-slate-950 text-slate-100";
    win.addEventListener("pagehide", () => setPipWindow(null));
    setPipWindow(win);
    return win;
  }, []);

  const close = useCallback(() => {
    pipWindow?.close();
    setPipWindow(null);
  }, [pipWindow]);

  useEffect(() => () => pipWindow?.close(), [pipWindow]);

  return { pipWindow, open, close };
}
