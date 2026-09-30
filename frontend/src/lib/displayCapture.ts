/**
 * Chia sẻ màn hình (getDisplayMedia) mà vẫn ở lại LezioneLive: Chrome/Edge mặc định nhảy sang tab/cửa sổ vừa chọn,
 * CaptureController.setFocusBehavior("no-focus-change") giữ người dùng ở màn hình học.
 */

interface CaptureControllerLike {
  setFocusBehavior?: (behavior: "focus-captured-surface" | "no-focus-change") => void;
}

declare global {
  interface Window {
    CaptureController?: new () => CaptureControllerLike;
  }
}

export class DisplayCaptureError extends Error {
  constructor(
    public code: "DENIED" | "UNSUPPORTED" | "FAILED",
    message: string,
  ) {
    super(message);
  }
}

export function displayCaptureSupported(): boolean {
  return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getDisplayMedia;
}

function newController(): CaptureControllerLike | undefined {
  const Ctor = window.CaptureController;
  if (!Ctor) return undefined;
  const controller = new Ctor();
  try {
    controller.setFocusBehavior?.("no-focus-change"); // Chrome 124+: đặt được trước khi chọn nguồn
  } catch {
    /* bản cũ hơn: đặt lại ngay sau khi chọn */
  }
  return controller;
}

function keepFocus(controller: CaptureControllerLike | undefined, stream: MediaStream): void {
  const surface = (stream.getVideoTracks()[0]?.getSettings() as { displaySurface?: string } | undefined)
    ?.displaySurface;
  if (!controller?.setFocusBehavior || !surface || surface === "monitor") return;
  try {
    controller.setFocusBehavior("no-focus-change");
  } catch {
    /* đã đặt trước khi chọn nguồn */
  }
}

/** Gọi getDisplayMedia với các tuỳ chọn chung (không tự chuyển tab, không chia sẻ chính LezioneLive). */
export async function requestDisplay(options: DisplayMediaStreamOptions): Promise<MediaStream> {
  if (!displayCaptureSupported()) throw new DisplayCaptureError("UNSUPPORTED", "getDisplayMedia unsupported");
  const controller = newController();
  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({
      selfBrowserSurface: "exclude",
      surfaceSwitching: "include",
      ...options,
      ...(controller ? { controller } : {}),
    } as DisplayMediaStreamOptions);
  } catch (e) {
    if ((e as DOMException).name === "NotAllowedError") throw new DisplayCaptureError("DENIED", "denied");
    throw new DisplayCaptureError("FAILED", (e as Error).message);
  }
  keepFocus(controller, stream);
  return stream;
}

/** Loại nguồn hình (Chrome chỉ đặt nhãn track dạng "window:1234:0" nên dùng displaySurface để hiển thị). */
export function displaySurface(track: MediaStreamTrack | null | undefined): "browser" | "window" | "monitor" | null {
  const surface = (track?.getSettings() as { displaySurface?: string } | undefined)?.displaySurface;
  return surface === "browser" || surface === "window" || surface === "monitor" ? surface : null;
}

/** Chỉ lấy hình (xem song song với phụ đề): cửa sổ Teams, tab trình duyệt hoặc cả màn hình. */
export function requestScreenView(): Promise<MediaStream> {
  return requestDisplay({
    video: { frameRate: { ideal: 15, max: 30 } },
    audio: false,
  } as DisplayMediaStreamOptions);
}
