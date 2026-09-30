/** Nút "📷 Chụp bảng" trên laptop (IC-FR-40): webcam hoặc chọn ảnh → thu nhỏ → gửi, gắn mốc thời gian hiện tại. */
import { Aperture, Camera, ImageUp, TriangleAlert, Webcam } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import { useT } from "../i18n";
import { captureVideoFrame, downscaleImage } from "../lib/image";
import { errorMessage, useApp } from "../store/app";
import { useSession } from "../store/session";
import { Button, Modal } from "./ui";

export function BoardCaptureDialog({ sessionId, open, onClose }: { sessionId: number; open: boolean; onClose: () => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const [webcam, setWebcam] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((tr) => tr.stop());
    streamRef.current = null;
  };

  useEffect(() => {
    if (!open) {
      stopCamera();
      setWebcam(false);
      setError(null);
    }
    return stopCamera;
  }, [open]);

  const send = (image: Blob) => {
    // Mốc thời gian lấy ngay lúc chụp; Claude đọc ảnh chạy nền, kết quả hiện trong transcript.
    const tMs = useSession.getState().status?.t_ms;
    onClose();
    api
      .uploadBoardCapture(sessionId, image, tMs)
      .then((capture) => useSession.getState().upsertBoard(capture))
      .catch((e) => toast("error", errorMessage(e)));
  };

  const startWebcam = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
      streamRef.current = stream;
      setWebcam(true);
      window.setTimeout(() => {
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          void videoRef.current.play();
        }
      }, 0);
    } catch (e) {
      setError(t("board.webcam_error", { error: (e as Error).message }));
    }
  };

  return (
    <Modal open={open} title={
        <>
          <Camera className="h-5 w-5 text-brand-600" /> {t("board.title")}
        </>
      } onClose={onClose} wide={webcam}>
      <div className="space-y-3 text-sm">
        {webcam ? (
          <div className="space-y-2">
            <video ref={videoRef} playsInline muted className="max-h-[50vh] w-full rounded-lg bg-black object-contain" />
            <div className="flex gap-2">
              <Button
                variant="primary"
                className="flex-1"
                onClick={async () => {
                  if (!videoRef.current) return;
                  const blob = await captureVideoFrame(videoRef.current);
                  stopCamera();
                  send(blob);
                }}
              >
                <Aperture className="h-4 w-4" /> {t("board.capture")}
              </Button>
              <Button
                onClick={() => {
                  stopCamera();
                  setWebcam(false);
                }}
              >
                {t("common.cancel")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            <Button className="py-6 text-base" onClick={() => void startWebcam()}>
              <Webcam className="h-4 w-4" /> {t("board.take_webcam")}
            </Button>
            <Button className="py-6 text-base" onClick={() => fileRef.current?.click()}>
              <ImageUp className="h-4 w-4" /> {t("board.pick_file")}
            </Button>
          </div>
        )}
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            try {
              send(await downscaleImage(file));
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        />
        {error && (
          <p className="flex items-start gap-1.5 text-red-600 dark:text-red-400">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </p>
        )}
        <p className="text-xs text-slate-500">{t("board.hint")}</p>
      </div>
    </Modal>
  );
}
