/** Upload slide PDF và duyệt thuật ngữ Claude đề xuất (FR-SL-01/02). */
import { ExternalLink, FileUp, Plus, Trash2, TriangleAlert, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { GlossarySuggestion } from "../api/types";
import { useT } from "../i18n";
import { errorMessage, useApp } from "../store/app";
import { useSession } from "../store/session";
import { Badge, Button, Spinner } from "./ui";

export function SlidesPanel({ sessionId }: { sessionId: number }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const slides = useSession((s) => s.slides);
  const setSlides = useSession((s) => s.setSlides);
  const version = useSession((s) => s.slidesVersion);
  const [suggestions, setSuggestions] = useState<GlossarySuggestion[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.slides(sessionId).then(setSlides).catch(() => undefined);
    api.suggestions(sessionId).then(setSuggestions).catch(() => undefined);
  }, [sessionId, version, setSlides]);

  // Còn slide đang phân tích → thăm dò định kỳ (phòng khi lỡ sự kiện WebSocket).
  useEffect(() => {
    if (!slides.some((d) => d.status === "processing")) return;
    const timer = window.setInterval(() => {
      api.slides(sessionId).then(setSlides).catch(() => undefined);
      api.suggestions(sessionId).then(setSuggestions).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [slides, sessionId, setSlides]);

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const deck = await api.uploadSlides(sessionId, file);
      setSlides([...useSession.getState().slides, deck]);
    } catch (e) {
      toast("error", errorMessage(e));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className="h-full space-y-4 overflow-y-auto p-3 text-sm">
      <div>
        <input
          ref={fileRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void upload(f);
          }}
        />
        <Button variant="primary" className="w-full" loading={uploading} onClick={() => fileRef.current?.click()}>
          <FileUp className="h-4 w-4" /> {uploading ? t("slides.uploading") : t("slides.upload")}
        </Button>
        <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{t("slides.hint")}</p>
      </div>

      {slides.length === 0 && <p className="text-slate-500">{t("slides.empty")}</p>}
      {slides.map((deck) => (
        <div key={deck.id} className="space-y-1.5 rounded-xl border border-slate-200 p-3 dark:border-slate-700">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate font-medium" title={deck.filename}>
              {deck.filename}
            </span>
            {deck.status === "processing" ? (
              <Badge tone="amber">
                <Spinner className="h-3 w-3" /> {t("slides.processing")}
              </Badge>
            ) : deck.status === "ready" ? (
              <Badge tone="green">{t("slides.ready")}</Badge>
            ) : (
              <Badge tone="red">{t("slides.failed")}</Badge>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>{t("slides.pages", { n: deck.page_count })}</span>
            <span>· {(deck.size_bytes / 1_048_576).toFixed(1)} MB</span>
            {deck.uploaded_to_claude && <Badge tone="violet">{t("slides.claude_file")}</Badge>}
            <a className="ml-auto inline-flex items-center gap-1 text-brand-600 hover:underline" href={api.slideFileUrl(deck.id)} target="_blank" rel="noreferrer">
              {t("slides.open")} <ExternalLink className="h-3 w-3" />
            </a>
            <button
              type="button"
              className="inline-flex items-center gap-1 text-red-600 hover:underline"
              onClick={async () => {
                if (!confirm(t("common.confirm_delete"))) return;
                await api.deleteSlides(deck.id);
                setSlides(useSession.getState().slides.filter((d) => d.id !== deck.id));
              }}
            >
              <Trash2 className="h-3 w-3" /> {t("common.delete")}
            </button>
          </div>
          {deck.error && (
            <p className="flex items-start gap-1 text-xs text-amber-700 dark:text-amber-300">
              <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" /> {deck.error}
            </p>
          )}
          {deck.summary && (
            <p className="whitespace-pre-wrap rounded-lg bg-slate-50 p-2 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              {deck.summary}
            </p>
          )}
        </div>
      ))}

      {slides.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{t("slides.suggestions")}</h4>
          {suggestions.length === 0 && <p className="text-xs text-slate-500">{t("slides.no_suggestions")}</p>}
          <ul className="space-y-1.5">
            {suggestions.map((s) => (
              <li key={s.id} className="flex items-start gap-2 rounded-lg bg-slate-50 p-2 dark:bg-slate-800">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{s.term_it}</div>
                  <div className="text-xs text-slate-600 dark:text-slate-300">
                    {s.term_vi}
                    {s.term_en ? ` · ${s.term_en}` : ""}
                  </div>
                  {s.note && <div className="text-xs text-slate-400">{s.note}</div>}
                </div>
                <Button
                  size="sm"
                  variant="success"
                  title={t("slides.accept")}
                  onClick={async () => {
                    await api.acceptSuggestion(s.id);
                    setSuggestions((prev) => prev.filter((x) => x.id !== s.id));
                  }}
                >
                  <Plus className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  title={t("slides.reject")}
                  onClick={async () => {
                    await api.rejectSuggestion(s.id);
                    setSuggestions((prev) => prev.filter((x) => x.id !== s.id));
                  }}
                >
                  <X className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
