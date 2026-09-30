/**
 * WebSocket /ws/session/{id}: gửi PCM, nhận sự kiện. Tự kết nối lại với backoff (E-WS-01);
 * audio phát sinh khi đang mất kết nối được giữ tạm (tối đa 30 s) rồi gửi bù.
 */
import type { ServerEvent } from "./types";

const MAX_BUFFERED_FRAMES = 300;

export interface SocketHandlers {
  onEvent: (ev: ServerEvent) => void;
  onOpen?: (reconnected: boolean) => void;
  onClose?: (willRetry: boolean) => void;
}

export class SessionSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private attempts = 0;
  private everOpened = false;
  private pending: ArrayBuffer[] = [];
  private timer: number | null = null;

  constructor(
    private sessionId: number,
    private handlers: SocketHandlers,
  ) {}

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  connect(): void {
    this.closed = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/session/${this.sessionId}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      const reconnected = this.everOpened;
      this.everOpened = true;
      this.attempts = 0;
      for (const buf of this.pending) ws.send(buf);
      this.pending = [];
      this.handlers.onOpen?.(reconnected);
    };
    ws.onmessage = (msg) => {
      if (typeof msg.data !== "string") return;
      try {
        this.handlers.onEvent(JSON.parse(msg.data) as ServerEvent);
      } catch {
        /* bỏ qua */
      }
    };
    ws.onclose = (ev) => {
      this.ws = null;
      const fatal = ev.code === 4403 || ev.code === 4404;
      const willRetry = !this.closed && !fatal;
      this.handlers.onClose?.(willRetry);
      if (willRetry) {
        const delay = Math.min(10_000, 500 * 2 ** this.attempts++);
        this.timer = window.setTimeout(() => this.connect(), delay);
      }
    };
  }

  sendAudio(pcm: ArrayBuffer): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(pcm);
    } else {
      this.pending.push(pcm);
      if (this.pending.length > MAX_BUFFERED_FRAMES) this.pending.shift();
    }
  }

  sendControl(action: "pause" | "resume" | "source_ended"): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "control", action }));
  }

  close(): void {
    this.closed = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.pending = [];
    this.ws?.close();
    this.ws = null;
  }
}
