/**
 * Khung xem màn hình MS Teams / tab trình duyệt / cửa sổ bất kỳ ngay trong màn hình học (không phải mở nhiều tab).
 * - Cắt vùng: kéo chuột chọn phần muốn xem (ví dụ khung slide); phóng vừa khung bằng CSS nên không tốn CPU vẽ lại.
 * - Chụp khung hình (đúng vùng đã cắt) gửi cho trợ lý để hỏi về slide/bảng.
 */
import {
  Aperture,
  CircleStop,
  Columns2,
  Crop,
  Lightbulb,
  Monitor,
  MonitorUp,
  RefreshCw,
  Rows2,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { useT } from "../i18n";
import { MAX_EDGE } from "../lib/image";
import { Button } from "./ui";

/** Vùng cắt theo tỉ lệ 0..1 của khung hình gốc. */
interface CropRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const FULL: CropRect = { x: 0, y: 0, w: 1, h: 1 };
const CROP_KEY = "lezionelive.screen.crop";
const MIN_CROP = 0.04;

function loadCrop(): CropRect | null {
  try {
    const c = JSON.parse(localStorage.getItem(CROP_KEY) ?? "null") as CropRect | null;
    if (!c || ![c.x, c.y, c.w, c.h].every((v) => typeof v === "number" && v >= 0 && v <= 1)) return null;
    return c.w >= MIN_CROP && c.h >= MIN_CROP ? c : null;
  } catch {
    return null;
  }
}

function saveCrop(c: CropRect | null): void {
  try {
    if (c) localStorage.setItem(CROP_KEY, JSON.stringify(c));
    else localStorage.removeItem(CROP_KEY);
  } catch {
    /* localStorage không dùng được */
  }
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function CropOverlay({ onDone, hint }: { onDone: (rect: CropRect | null) => void; hint: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);

  const point = (e: ReactPointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return { x: clamp01((e.clientX - r.left) / r.width), y: clamp01((e.clientY - r.top) / r.height) };
  };
  const rect = drag
    ? {
        x: Math.min(drag.x0, drag.x1),
        y: Math.min(drag.y0, drag.y1),
        w: Math.abs(drag.x1 - drag.x0),
        h: Math.abs(drag.y1 - drag.y0),
      }
    : null;

  return (
    <div
      ref={ref}
      className="absolute inset-0 z-10 cursor-crosshair touch-none select-none"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        const p = point(e);
        setDrag({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
      }}
      onPointerMove={(e) => {
        if (!drag) return;
        const p = point(e);
        setDrag({ ...drag, x1: p.x, y1: p.y });
      }}
      onPointerUp={() => {
        setDrag(null);
        onDone(rect && rect.w >= MIN_CROP && rect.h >= MIN_CROP ? rect : null);
      }}
    >
      {rect ? (
        <div
          className="absolute border-2 border-brand-400"
          style={{
            left: `${rect.x * 100}%`,
            top: `${rect.y * 100}%`,
            width: `${rect.w * 100}%`,
            height: `${rect.h * 100}%`,
            boxShadow: "0 0 0 9999px rgb(2 6 23 / 0.55)",
          }}
        />
      ) : (
        <div className="absolute inset-0 bg-slate-950/40" />
      )}
      <div className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 whitespace-nowrap rounded-full bg-slate-900/90 px-3 py-1 text-xs text-white">
        <Crop className="mr-1 inline h-3.5 w-3.5" />
        {hint}
      </div>
    </div>
  );
}

export function ScreenViewer({
  stream,
  label,
  layout,
  onPick,
  onStop,
  onHide,
  onToggleLayout,
  onSnapshot,
}: {
  stream: MediaStream | null;
  label: string;
  layout: "side" | "stack";
  onPick: () => void;
  onStop: () => void;
  onHide: () => void;
  onToggleLayout: () => void;
  onSnapshot: (dataUrl: string) => void;
}) {
  const t = useT();
  const areaRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [area, setArea] = useState({ w: 0, h: 0 });
  const [dims, setDims] = useState({ w: 16, h: 9 });
  const [crop, setCrop] = useState<CropRect | null>(loadCrop);
  const [cropping, setCropping] = useState(false);

  useLayoutEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setArea({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const update = () => video.videoWidth && setDims({ w: video.videoWidth, h: video.videoHeight });
    video.addEventListener("loadedmetadata", update);
    video.addEventListener("resize", update); // cửa sổ được chia sẻ đổi kích thước
    return () => {
      video.removeEventListener("loadedmetadata", update);
      video.removeEventListener("resize", update);
    };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = stream;
    if (stream) void video.play().catch(() => undefined);
  }, [stream]);

  useEffect(() => {
    if (!cropping) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setCropping(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cropping]);

  const view = cropping ? FULL : (crop ?? FULL);
  const aspect = (view.w * dims.w) / (view.h * dims.h);
  const availW = Math.max(0, area.w - 16);
  const availH = Math.max(0, area.h - 16);
  let boxW = availW;
  let boxH = availW / aspect;
  if (boxH > availH) {
    boxH = availH;
    boxW = availH * aspect;
  }
  const videoW = boxW / view.w;
  const videoH = boxH / view.h;

  const applyCrop = (rect: CropRect | null) => {
    setCropping(false);
    if (!rect) return;
    setCrop(rect);
    saveCrop(rect);
  };

  const snapshot = () => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return;
    const c = crop ?? FULL;
    const sx = c.x * video.videoWidth;
    const sy = c.y * video.videoHeight;
    const sw = c.w * video.videoWidth;
    const sh = c.h * video.videoHeight;
    const scale = Math.min(1, MAX_EDGE / Math.max(sw, sh));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(sw * scale));
    canvas.height = Math.max(1, Math.round(sh * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    onSnapshot(canvas.toDataURL("image/jpeg", 0.85));
  };

  const iconBtn =
    "inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-xs font-medium text-slate-300 hover:bg-slate-700 hover:text-white disabled:opacity-40";

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-slate-900 text-slate-100">
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-700 px-2 py-1">
        <span className="flex min-w-0 flex-1 items-center gap-1.5 truncate px-1 text-xs text-slate-400" title={label}>
          <Monitor className="h-3.5 w-3.5 shrink-0" /> {label || t("screen.toggle")}
        </span>
        {stream && (
          <>
            <button
              type="button"
              className={`${iconBtn} ${cropping ? "bg-brand-600 text-white" : ""}`}
              title={t("screen.crop_hint")}
              onClick={() => setCropping((v) => !v)}
            >
              <Crop className="h-4 w-4" /> <span className="hidden xl:inline">{t("screen.crop")}</span>
            </button>
            {crop && !cropping && (
              <button
                type="button"
                className={iconBtn}
                title={t("screen.uncrop")}
                onClick={() => {
                  setCrop(null);
                  saveCrop(null);
                }}
              >
                <Undo2 className="h-4 w-4" /> <span className="hidden xl:inline">{t("screen.uncrop")}</span>
              </button>
            )}
            <button type="button" className={iconBtn} title={t("screen.ask")} onClick={snapshot}>
              <Aperture className="h-4 w-4" /> <span className="hidden xl:inline">{t("screen.ask")}</span>
            </button>
          </>
        )}
        <button
          type="button"
          className={iconBtn}
          title={layout === "side" ? t("screen.layout_stack") : t("screen.layout_side")}
          onClick={onToggleLayout}
        >
          {layout === "side" ? <Rows2 className="h-4 w-4" /> : <Columns2 className="h-4 w-4" />}
        </button>
        {stream && (
          <>
            <button type="button" className={iconBtn} title={t("screen.change")} onClick={onPick}>
              <RefreshCw className="h-4 w-4" />
            </button>
            <button type="button" className={iconBtn} title={t("screen.stop")} onClick={onStop}>
              <CircleStop className="h-4 w-4" />
            </button>
          </>
        )}
        <button type="button" className={iconBtn} title={t("screen.hide")} onClick={onHide}>
          <X className="h-4 w-4" />
        </button>
      </div>

      <div ref={areaRef} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden">
        <div
          className={`relative overflow-hidden rounded-md bg-black shadow-lg ${stream ? "" : "hidden"}`}
          style={{ width: boxW, height: boxH }}
        >
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className="absolute max-w-none"
            style={{ width: videoW, height: videoH, left: -view.x * videoW, top: -view.y * videoH }}
          />
          {cropping && <CropOverlay onDone={applyCrop} hint={t("screen.crop_hint")} />}
        </div>
        {!stream && (
          <div className="max-w-md space-y-3 px-6 text-center">
            <MonitorUp className="mx-auto h-12 w-12 text-slate-500" strokeWidth={1.25} aria-hidden />
            <p className="text-base font-semibold">{t("screen.empty_title")}</p>
            <p className="text-sm text-slate-400">{t("screen.empty_body")}</p>
            <Button variant="primary" onClick={onPick}>
              <MonitorUp className="h-4 w-4" /> {t("screen.pick")}
            </Button>
            <p className="flex items-start justify-center gap-1.5 text-xs text-slate-500">
              <Lightbulb className="mt-px h-3.5 w-3.5 shrink-0" /> {t("screen.empty_tip")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
