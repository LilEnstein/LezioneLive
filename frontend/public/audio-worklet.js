/**
 * AudioWorklet: trộn về mono, lọc chống răng cưa, resample về 16 kHz, đóng gói PCM Int16 LE.
 * Mỗi 100 ms (1600 mẫu) gửi 1 frame về main thread kèm mức âm (dBFS) cho VU meter.
 * (FR-AC-02, FR-AC-03; mục 6.2 "AudioWorklet")
 */

function designLowpass(numTaps, cutoffHz, fs) {
  const fc = cutoffHz / fs;
  const m = numTaps - 1;
  const h = new Float32Array(numTaps);
  let sum = 0;
  for (let i = 0; i < numTaps; i++) {
    const x = i - m / 2;
    const sinc = x === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * x) / (Math.PI * x);
    const w = 0.54 - 0.46 * Math.cos((2 * Math.PI * i) / m); // Hamming
    h[i] = sinc * w;
    sum += h[i];
  }
  for (let i = 0; i < numTaps; i++) h[i] /= sum;
  return h;
}

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    this.frameSamples = opts.frameSamples || 1600;
    this.ratio = sampleRate / this.targetRate;
    this.filter = this.ratio > 1 ? designLowpass(33, 0.45 * this.targetRate, sampleRate) : null;
    this.history = new Float32Array(this.filter ? this.filter.length : 1);
    this.histPos = 0;
    this.prev = 0; // mẫu đã lọc trước đó (để nội suy tuyến tính)
    this.inIndex = 0; // chỉ số mẫu đầu vào hiện tại (tương đối)
    this.nextOut = 0; // vị trí (theo mẫu đầu vào) của mẫu đầu ra kế tiếp
    this.buf = new Int16Array(this.frameSamples);
    this.n = 0;
    this.sumSq = 0;
    this.active = true;
    this.port.onmessage = (e) => {
      if (e.data === "stop") this.active = false;
    };
  }

  filtered(x) {
    if (!this.filter) return x;
    const taps = this.filter;
    const len = taps.length;
    this.history[this.histPos] = x;
    let acc = 0;
    let idx = this.histPos;
    for (let k = 0; k < len; k++) {
      acc += taps[k] * this.history[idx];
      idx = idx === 0 ? len - 1 : idx - 1;
    }
    this.histPos = (this.histPos + 1) % len;
    return acc;
  }

  emit(v) {
    const s = v < -1 ? -1 : v > 1 ? 1 : v;
    this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    this.sumSq += s * s;
    if (this.n === this.frameSamples) {
      const rms = Math.sqrt(this.sumSq / this.n);
      const db = rms > 0 ? 20 * Math.log10(rms) : -120;
      this.port.postMessage({ pcm: this.buf.buffer, db }, [this.buf.buffer]);
      this.buf = new Int16Array(this.frameSamples);
      this.n = 0;
      this.sumSq = 0;
    }
  }

  process(inputs) {
    if (!this.active) return false;
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) return true;
    const ch0 = input[0];
    const ch1 = input.length > 1 ? input[1] : null;
    for (let i = 0; i < ch0.length; i++) {
      const x = ch1 ? 0.5 * (ch0[i] + ch1[i]) : ch0[i];
      const y = this.filtered(x);
      // Mẫu đầu ra nằm trong khoảng (inIndex-1, inIndex]: nội suy giữa prev và y.
      while (this.nextOut <= this.inIndex) {
        const frac = 1 - (this.inIndex - this.nextOut);
        this.emit(this.prev + (y - this.prev) * frac);
        this.nextOut += this.ratio;
      }
      this.prev = y;
      this.inIndex += 1;
      // Giữ số nhỏ để tránh mất chính xác số thực khi chạy nhiều giờ.
      if (this.inIndex > 1e6) {
        this.inIndex -= 1e6;
        this.nextOut -= 1e6;
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
