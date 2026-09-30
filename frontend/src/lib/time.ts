/** [mm:ss] tính từ đầu buổi (phút có thể > 59, khớp với transcript gửi cho Claude). */
export function fmtTs(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function fmtClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((x) => String(x).padStart(2, "0")).join(":");
}

export function fmtDateTime(iso: string | null | undefined, lang: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(lang === "vi" ? "vi-VN" : "en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtDate(iso: string | null | undefined, lang: string): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(lang === "vi" ? "vi-VN" : "en-GB");
}

export function fmtUsd(v: number): string {
  if (v > 0 && v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

/** Tìm mốc [mm:ss] hoặc [h:mm:ss] → ms. */
export function parseTs(text: string): number | null {
  const m = /^\[?(\d{1,3}):(\d{2})(?::(\d{2}))?\]?$/.exec(text.trim());
  if (!m) return null;
  if (m[3] !== undefined) return (Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3])) * 1000;
  return (Number(m[1]) * 60 + Number(m[2])) * 1000;
}
