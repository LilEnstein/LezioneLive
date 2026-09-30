/**
 * Micro điện thoại (IC-FR-02/04/06): getUserMedia → AudioWorklet (public/audio-worklet.js) → PCM Int16 16 kHz,
 * khung 100 ms. Tắt echoCancellation; noiseSuppression + autoGainControl của trình duyệt là lớp xử lý đầu tiên
 * (tắt được trong Cài đặt để so sánh). Giữ màn hình sáng bằng Screen Wake Lock khi đang thu.
 */

export class MicError extends Error {
  constructor(
    public code: "DENIED" | "UNSUPPORTED" | "FAILED",
    message: string,
  ) {
    super(message);
  }
}

interface WakeLockSentinelLike {
  released: boolean;
  release: () => Promise<void>;
}

export class MicStreamer {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private wakeLock: WakeLockSentinelLike | null = null;
  private onVisibility = () => {
    if (document.visibilityState === "visible" && this.stream) void this.acquireWakeLock();
  };
  wakeLockOk = false;

  constructor(
    private onFrame: (pcm: ArrayBuffer) => void,
    private onLevel: (db: number) => void,
  ) {}

  get active(): boolean {
    return !!this.stream && this.stream.getAudioTracks().some((t) => t.readyState === "live");
  }

  async start(browserProcessing: boolean): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
      throw new MicError("UNSUPPORTED", "getUserMedia unavailable");
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: browserProcessing,
          autoGainControl: browserProcessing,
        },
        video: false,
      });
    } catch (e) {
      const name = (e as DOMException).name;
      if (name === "NotAllowedError" || name === "SecurityError") throw new MicError("DENIED", name);
      throw new MicError("FAILED", (e as Error).message);
    }
    this.stream = stream;
    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/audio-worklet.js");
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "pcm-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCountMode: "explicit",
      channelCount: 1,
      processorOptions: { targetRate: 16000, frameSamples: 1600 },
    });
    node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; db: number }>) => {
      this.onFrame(e.data.pcm);
      this.onLevel(e.data.db);
    };
    const mute = ctx.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    this.node = node;
    if (ctx.state === "suspended") await ctx.resume();
    await this.acquireWakeLock();
    document.addEventListener("visibilitychange", this.onVisibility);
  }

  private async acquireWakeLock(): Promise<void> {
    const wl = (navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<WakeLockSentinelLike> } }).wakeLock;
    if (!wl) {
      this.wakeLockOk = false;
      return;
    }
    try {
      if (!this.wakeLock || this.wakeLock.released) this.wakeLock = await wl.request("screen");
      this.wakeLockOk = true;
    } catch {
      this.wakeLockOk = false;
    }
  }

  stop(): void {
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.node?.port.postMessage("stop");
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    void this.wakeLock?.release().catch(() => undefined);
    this.node = null;
    this.stream = null;
    this.ctx = null;
    this.wakeLock = null;
  }
}
