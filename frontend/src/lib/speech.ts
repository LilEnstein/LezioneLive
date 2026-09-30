/**
 * Đọc tiếng Ý bằng Web Speech API của trình duyệt — chạy cục bộ, không tốn API.
 * Chất lượng phụ thuộc giọng cài trên máy: Edge có "Microsoft … Online (Natural)",
 * Chrome có "Google italiano"; Windows cần gói giọng Italiano cho giọng offline.
 */
import { useEffect, useState } from "react";

export type SpeechPrefs = { autoplay: boolean; listening: boolean; slow: boolean };

const PREFS_KEY = "lezionelive.fc.speech";
const DEFAULT_PREFS: SpeechPrefs = { autoplay: true, listening: false, slow: false };

export const speechSupported = typeof window !== "undefined" && "speechSynthesis" in window;

function isItalian(v: SpeechSynthesisVoice): boolean {
  return v.lang.toLowerCase().replace("_", "-").startsWith("it");
}

/** Ưu tiên giọng neural (Natural/Online), rồi Google, rồi it-IT bất kỳ. */
function voiceScore(v: SpeechSynthesisVoice): number {
  let s = 0;
  if (/natural/i.test(v.name)) s += 4;
  if (/online|google/i.test(v.name)) s += 2;
  if (v.lang.toLowerCase().replace("_", "-") === "it-it") s += 1;
  return s;
}

function pickItalianVoice(): SpeechSynthesisVoice | null {
  if (!speechSupported) return null;
  const voices = window.speechSynthesis.getVoices().filter(isItalian);
  voices.sort((a, b) => voiceScore(b) - voiceScore(a));
  return voices[0] ?? null;
}

/** Giọng Ý tốt nhất; danh sách giọng nạp bất đồng bộ nên theo dõi `voiceschanged`. */
export function useItalianVoice(): { voice: SpeechSynthesisVoice | null; loaded: boolean } {
  const [state, setState] = useState(() => ({
    voice: pickItalianVoice(),
    loaded: speechSupported && window.speechSynthesis.getVoices().length > 0,
  }));
  useEffect(() => {
    if (!speechSupported) return;
    const update = () =>
      setState({ voice: pickItalianVoice(), loaded: window.speechSynthesis.getVoices().length > 0 });
    window.speechSynthesis.addEventListener("voiceschanged", update);
    update();
    return () => window.speechSynthesis.removeEventListener("voiceschanged", update);
  }, []);
  return state;
}

export function speakItalian(text: string, opts: { voice: SpeechSynthesisVoice | null; slow?: boolean }): void {
  if (!speechSupported || !text.trim()) return;
  const synth = window.speechSynthesis;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = opts.voice?.lang ?? "it-IT";
  if (opts.voice) u.voice = opts.voice;
  u.rate = opts.slow ? 0.6 : 0.95;
  synth.speak(u);
}

export function stopSpeaking(): void {
  if (speechSupported) window.speechSynthesis.cancel();
}

export function loadSpeechPrefs(): SpeechPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (raw) return { ...DEFAULT_PREFS, ...(JSON.parse(raw) as Partial<SpeechPrefs>) };
  } catch {
    /* localStorage không dùng được */
  }
  return DEFAULT_PREFS;
}

export function saveSpeechPrefs(prefs: SpeechPrefs): void {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage không dùng được */
  }
}
