/**
 * Kết nối điện thoại ↔ laptop qua WSS /ws/companion/{token} (IC-FR-02/03).
 * - Token ghép nối (QR, dùng một lần) → nhận token thiết bị, lưu trong trình duyệt để kết nối lại.
 * - Tự kết nối lại (backoff); âm thanh phát sinh khi mất kết nối được giữ tạm ≤ 30 s rồi gửi bù.
 * - Ping mỗi 5 s để đo độ trễ (IC-NFR-04) và gửi kèm mức pin điện thoại.
 */
import type { ServerEvent } from "../api/types";

const DEVICE_KEY = "lezionelive.deviceToken";
const MAX_BUFFERED_FRAMES = 300;

export type PhoneMode = "mic" | "subtitle" | "both";

export type ConnState = "connecting" | "open" | "reconnecting" | "failed";

export type CompanionMessage =
  | ServerEvent
  | { type: "paired"; device_id: number; device_token: string }
  | { type: "welcome"; device_id: number }
  | { type: "error"; code: "TOKEN_EXPIRED" | "TOKEN_INVALID"; message: string }
  | { type: "snapshot"; [key: string]: unknown };

export function storedDeviceToken(): string | null {
  try {
    return localStorage.getItem(DEVICE_KEY);
  } catch {
    return null;
  }
}

function storeDeviceToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(DEVICE_KEY, token);
    else localStorage.removeItem(DEVICE_KEY);
  } catch {
    /* chế độ ẩn danh: chỉ nhớ trong phiên */
  }
}

export function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  const android = /Android[^;]*;\s*([^;)]+?)(?:\s+Build|\))/.exec(ua);
  if (android) return android[1].trim().slice(0, 40);
  if (/Android/.test(ua)) return "Android";
  return "Trình duyệt";
}

interface Handlers {
  onMessage: (msg: CompanionMessage) => void;
  onState: (state: ConnState) => void;
  onFatal: (code: string, message: string) => void;
  onRtt?: (ms: number) => void;
}

export class CompanionSocket {
  private ws: WebSocket | null = null;
  private closed = false;
  private attempts = 0;
  private pending: ArrayBuffer[] = [];
  private retryTimer: number | null = null;
  private pingTimer: number | null = null;
  private battery: { level: number; charging: boolean } | null = null;
  mode: PhoneMode = "subtitle";
  rtt: number | null = null;
  token: string;
  deviceToken: string | null;

  constructor(
    pairingToken: string | null,
    private handlers: Handlers,
  ) {
    this.deviceToken = storedDeviceToken();
    this.token = pairingToken ?? this.deviceToken ?? "";
    const nav = navigator as Navigator & {
      getBattery?: () => Promise<{ level: number; charging: boolean; addEventListener: (e: string, f: () => void) => void }>;
    };
    nav
      .getBattery?.()
      .then((b) => {
        const update = () => (this.battery = { level: b.level, charging: b.charging });
        update();
        b.addEventListener("levelchange", update);
        b.addEventListener("chargingchange", update);
      })
      .catch(() => undefined);
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get phoneBattery(): { level: number; charging: boolean } | null {
    return this.battery;
  }

  connect(): void {
    if (!this.token) {
      this.handlers.onFatal("NO_TOKEN", "");
      return;
    }
    this.closed = false;
    this.handlers.onState(this.attempts === 0 ? "connecting" : "reconnecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/companion/${encodeURIComponent(this.token)}`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    let fatal = false;
    ws.onopen = () => {
      this.attempts = 0;
      this.handlers.onState("open");
      ws.send(JSON.stringify({ type: "hello", name: deviceName(), mode: this.mode }));
      for (const buf of this.pending) ws.send(buf);
      this.pending = [];
      this.startPing();
    };
    ws.onmessage = (msg) => {
      if (typeof msg.data !== "string") return;
      let data: CompanionMessage;
      try {
        data = JSON.parse(msg.data) as CompanionMessage;
      } catch {
        return;
      }
      if (data.type === "paired") {
        this.deviceToken = data.device_token;
        this.token = data.device_token; // kết nối lại bằng token thiết bị
        storeDeviceToken(data.device_token);
      } else if (data.type === "error") {
        fatal = true;
        const usedPairing = this.token.startsWith("p_");
        if (usedPairing && this.deviceToken && this.deviceToken !== this.token) {
          // QR cũ nhưng điện thoại đã ghép nối trước đó: dùng token thiết bị.
          fatal = false;
          this.token = this.deviceToken;
        } else {
          if (!usedPairing) storeDeviceToken(null);
          this.handlers.onFatal(data.code, data.message);
        }
        return;
      } else if (data.type === "pong") {
        const sent = Number((data as { t?: number }).t);
        if (Number.isFinite(sent)) {
          this.rtt = performance.now() - sent;
          this.handlers.onRtt?.(this.rtt);
        }
      }
      this.handlers.onMessage(data);
    };
    ws.onclose = () => {
      this.ws = null;
      this.stopPing();
      if (this.closed || fatal) return;
      const delay = Math.min(10_000, 500 * 2 ** this.attempts++);
      this.handlers.onState(this.attempts > 6 ? "failed" : "reconnecting");
      this.retryTimer = window.setTimeout(() => this.connect(), delay);
    };
  }

  private startPing(): void {
    this.stopPing();
    const tick = () => {
      if (!this.connected) return;
      this.ws?.send(JSON.stringify({ type: "ping", t: performance.now() }));
      this.ws?.send(JSON.stringify({ type: "stats", rtt_ms: this.rtt, battery: this.battery }));
    };
    tick();
    this.pingTimer = window.setInterval(tick, 5000);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) window.clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  setMode(mode: PhoneMode): void {
    this.mode = mode;
    if (this.connected) this.ws?.send(JSON.stringify({ type: "mode", mode }));
  }

  sendAudio(pcm: ArrayBuffer): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(pcm);
    } else {
      this.pending.push(pcm);
      if (this.pending.length > MAX_BUFFERED_FRAMES) this.pending.shift();
    }
  }

  close(): void {
    this.closed = true;
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer);
    this.stopPing();
    this.pending = [];
    this.ws?.close();
    this.ws = null;
  }
}

/** Gọi API /api/companion/* bằng token thiết bị. */
export async function phoneApi<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/companion${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = data?.detail?.message ?? message;
    } catch {
      /* không phải JSON */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}
