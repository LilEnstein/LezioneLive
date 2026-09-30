/**
 * Bắt âm thanh một tab trình duyệt qua getDisplayMedia (chế độ Tab, mục 6.4) và chuyển thành
 * PCM Int16 16 kHz mono qua AudioWorklet (public/audio-worklet.js), mỗi frame 100 ms.
 * Tuỳ chọn giữ lại hình của tab đó để xem ngay trong màn hình học (không phải chuyển sang tab Teams).
 */
import { DisplayCaptureError, requestDisplay } from "../lib/displayCapture";

export class CaptureError extends Error {
  constructor(
    public code: "NO_AUDIO" | "DENIED" | "UNSUPPORTED" | "FAILED",
    message: string,
  ) {
    super(message);
  }
}

export interface CaptureCallbacks {
  onFrame: (pcm: ArrayBuffer) => void;
  onLevel?: (db: number) => void;
  onEnded?: () => void;
}

export class TabAudioCapture {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private stopped = false;
  label = "";
  /** Hình của tab/màn hình vừa chọn (khi ``keepVideo``), để hiện trong khung màn hình. */
  videoTrack: MediaStreamTrack | null = null;

  constructor(
    private cb: CaptureCallbacks,
    private opts: { keepVideo?: boolean } = {},
  ) {}

  get active(): boolean {
    return !!this.stream && this.stream.getAudioTracks().some((t) => t.readyState === "live");
  }

  async start(): Promise<void> {
    let stream: MediaStream;
    try {
      // Chrome yêu cầu video khi chia sẻ tab; track video được dừng ngay nếu không cần xem hình.
      stream = await requestDisplay({
        video: this.opts.keepVideo ? { frameRate: { ideal: 15, max: 30 } } : true,
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          suppressLocalAudioPlayback: false,
        },
        preferCurrentTab: false,
        systemAudio: "include",
      } as DisplayMediaStreamOptions);
    } catch (e) {
      if (e instanceof DisplayCaptureError) {
        if (e.code === "UNSUPPORTED")
          throw new CaptureError("UNSUPPORTED", "Trình duyệt không hỗ trợ chia sẻ âm thanh tab (cần Chrome/Edge).");
        if (e.code === "DENIED") throw new CaptureError("DENIED", "Bạn đã huỷ chọn nguồn âm thanh.");
      }
      throw new CaptureError("FAILED", (e as Error).message);
    }
    const audio = stream.getAudioTracks();
    if (audio.length === 0) {
      stream.getTracks().forEach((t) => t.stop());
      throw new CaptureError("NO_AUDIO", "NO_AUDIO"); // E-AUD-01
    }
    const video = stream.getVideoTracks();
    this.label = video[0]?.label || audio[0].label || "";
    if (this.opts.keepVideo && video[0]) this.videoTrack = video[0];
    else video.forEach((t) => t.stop());
    this.stream = stream;
    this.stopped = false;
    audio[0].addEventListener("ended", () => {
      if (!this.stopped) this.cb.onEnded?.(); // E-AUD-02: người dùng bấm "Dừng chia sẻ"
    });

    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/audio-worklet.js");
    const source = ctx.createMediaStreamSource(new MediaStream([audio[0]]));
    const node = new AudioWorkletNode(ctx, "pcm-capture", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCountMode: "explicit",
      channelCount: 2,
      processorOptions: { targetRate: 16000, frameSamples: 1600 },
    });
    node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; db: number }>) => {
      this.cb.onFrame(e.data.pcm);
      this.cb.onLevel?.(e.data.db);
    };
    // Nối tới đích qua gain = 0 để graph luôn được xử lý mà không phát lại âm thanh (tab vẫn tự phát).
    const mute = ctx.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    this.node = node;
    if (ctx.state === "suspended") await ctx.resume();
  }

  stop(): void {
    this.stopped = true;
    this.node?.port.postMessage("stop");
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.node = null;
    this.stream = null;
    this.ctx = null;
    this.videoTrack = null;
  }
}
