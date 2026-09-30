/** Thu nhỏ ảnh trên trình duyệt trước khi upload (IC-FR-44: cạnh dài ≤ 1568 px, JPEG) — tiết kiệm dữ liệu điện thoại. */

export const MAX_EDGE = 1568;

function canvasToBlob(canvas: HTMLCanvasElement, quality = 0.85): Promise<Blob> {
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), "image/jpeg", quality),
  );
}

export function drawScaled(source: CanvasImageSource, width: number, height: number, maxEdge = MAX_EDGE): HTMLCanvasElement {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D không khả dụng");
  // JPEG không có kênh alpha: lót nền trắng để ảnh PNG nền trong suốt (công thức, chữ đen) không bị đen kịt.
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas;
}

/** File ảnh (kể cả ảnh chụp từ camera điện thoại) → JPEG đã thu nhỏ, xoay đúng chiều theo EXIF. */
export async function downscaleImage(file: Blob, maxEdge = MAX_EDGE): Promise<Blob> {
  if (typeof createImageBitmap === "function") {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" } as ImageBitmapOptions);
      const canvas = drawScaled(bmp, bmp.width, bmp.height, maxEdge);
      bmp.close();
      return await canvasToBlob(canvas);
    } catch {
      /* Safari cũ: dùng thẻ <img> */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("Không đọc được ảnh"));
      el.src = url;
    });
    return await canvasToBlob(drawScaled(img, img.naturalWidth, img.naturalHeight, maxEdge));
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Chụp một khung hình từ <video> (webcam). */
export async function captureVideoFrame(video: HTMLVideoElement, maxEdge = MAX_EDGE): Promise<Blob> {
  return canvasToBlob(drawScaled(video, video.videoWidth, video.videoHeight, maxEdge));
}
