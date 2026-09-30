/** Ảnh bảng xen kẽ trong transcript (IC-FR-43) + cửa sổ chi tiết: ảnh, chữ trích xuất, LaTeX, thuật ngữ (IC-FR-41/42). */
import { BookA, Camera, Check, Plus, RotateCw, Sigma, Trash2, TriangleAlert } from "lucide-react";
import { memo, useEffect, useState } from "react";

import { api } from "../api/client";
import type { BoardCapture, GlossarySuggestion } from "../api/types";
import { useT } from "../i18n";
import { fmtTs } from "../lib/time";
import { errorMessage, useApp } from "../store/app";
import { useSession } from "../store/session";
import { Markdown } from "./Markdown";
import { Badge, Button, Modal, Spinner } from "./ui";

function StatusBadge({ capture }: { capture: BoardCapture }) {
  const t = useT();
  if (capture.status === "processing")
    return (
      <Badge tone="amber">
        <Spinner className="h-3 w-3" /> {t("board.processing")}
      </Badge>
    );
  if (capture.status === "unreadable") return <Badge tone="red">{t("board.unreadable")}</Badge>;
  if (capture.status === "failed") return <Badge tone="red">{t("board.failed")}</Badge>;
  return null;
}

export const BoardCaptureCard = memo(function BoardCaptureCard({ id }: { id: number }) {
  const t = useT();
  const capture = useSession((s) => s.boards[id]);
  const [open, setOpen] = useState(false);
  if (!capture) return null;
  const preview = (capture.extracted_it ?? "").split("\n").filter(Boolean).slice(0, 2).join(" · ");
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full items-center gap-3 border-b border-slate-100 bg-indigo-50/60 px-3 py-2 text-left hover:bg-indigo-100/70 dark:border-slate-800 dark:bg-indigo-950/30 dark:hover:bg-indigo-950/50"
      >
        <span className="w-[4.2rem] shrink-0 font-mono text-[0.78em] tabular-nums text-slate-400">[{fmtTs(capture.t_ms)}]</span>
        <img
          src={capture.thumb_url}
          alt={t("board.label")}
          loading="lazy"
          className="h-14 w-20 shrink-0 rounded-md border border-slate-200 object-cover dark:border-slate-700"
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-indigo-900 dark:text-indigo-200">
            <Camera className="h-4 w-4" /> {t("board.label")}
            <StatusBadge capture={capture} />
            {capture.terms.length > 0 && <Badge tone="violet">{t("board.terms")}: {capture.terms.length}</Badge>}
          </span>
          {preview && <span className="block truncate text-xs text-slate-600 dark:text-slate-300">{preview}</span>}
          {capture.error && capture.status !== "processing" && (
            <span className="block truncate text-xs text-red-600 dark:text-red-400">{capture.error}</span>
          )}
        </span>
      </button>
      {open && <BoardCaptureModal capture={capture} onClose={() => setOpen(false)} />}
    </>
  );
});

export function BoardCaptureModal({ capture, onClose }: { capture: BoardCapture; onClose: () => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const [suggestions, setSuggestions] = useState<GlossarySuggestion[]>([]);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .suggestions(capture.session_id)
      .then((all) => setSuggestions(all.filter((s) => s.capture_id === capture.id)))
      .catch(() => undefined);
  }, [capture.id, capture.session_id, capture.status]);

  const addTerm = async (termIt: string) => {
    const sug = suggestions.find((s) => s.term_it.toLowerCase() === termIt.toLowerCase());
    if (!sug) return;
    try {
      await api.acceptSuggestion(sug.id);
      setAdded((prev) => new Set(prev).add(termIt));
      setSuggestions((prev) => prev.filter((s) => s.id !== sug.id));
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  const latexMd = capture.latex.map((f) => `$$${f}$$`).join("\n\n");

  return (
    <Modal
      open
      wide
      title={
        <>
          <Camera className="h-5 w-5 text-brand-600" /> {t("board.title")} [{fmtTs(capture.t_ms)}]
        </>
      }
      onClose={onClose}
      footer={
        <>
          <Button
            variant="ghost"
            onClick={async () => {
              if (!confirm(t("common.confirm_delete"))) return;
              await api.deleteBoardCapture(capture.id);
              onClose();
            }}
          >
            <Trash2 className="h-4 w-4" /> {t("board.delete")}
          </Button>
          {capture.status !== "done" && capture.status !== "processing" && (
            <Button
              loading={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  useSession.getState().upsertBoard(await api.retryBoardCapture(capture.id));
                } catch (e) {
                  toast("error", errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              <RotateCw className="h-4 w-4" /> {t("board.retry")}
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>
            {t("common.close")}
          </Button>
        </>
      }
    >
      <div className="space-y-4 text-sm">
        <a href={capture.image_url} target="_blank" rel="noreferrer">
          <img src={capture.image_url} alt={t("board.label")} className="max-h-[45vh] w-full rounded-lg object-contain" />
        </a>
        <StatusBadge capture={capture} />
        {capture.error && <p className="flex items-start gap-1.5 rounded-lg bg-red-50 p-2 text-red-800 dark:bg-red-950/50 dark:text-red-200">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {capture.error}
          </p>}
        {capture.unreadable && <p className="text-xs text-slate-500">({capture.unreadable})</p>}
        {capture.extracted_it && (
          <section>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">🇮🇹 {t("board.text_it")}</h4>
            <p className="whitespace-pre-wrap rounded-lg bg-slate-50 p-2 dark:bg-slate-800">{capture.extracted_it}</p>
          </section>
        )}
        {capture.latex.length > 0 && (
          <section>
            <h4 className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <Sigma className="h-3.5 w-3.5" /> {t("board.latex")}
            </h4>
            <Markdown text={latexMd} />
          </section>
        )}
        {capture.translation_vi && (
          <section>
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">🇻🇳 {t("board.translation")}</h4>
            <p className="whitespace-pre-wrap">{capture.translation_vi}</p>
          </section>
        )}
        {capture.terms.length > 0 && (
          <section>
            <h4 className="mb-1 flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              <BookA className="h-3.5 w-3.5" /> {t("board.terms")}
            </h4>
            <ul className="space-y-1">
              {capture.terms.map((term) => {
                const pending = suggestions.some((s) => s.term_it.toLowerCase() === term.it.toLowerCase());
                return (
                  <li key={term.it} className="flex items-center gap-2 rounded-lg bg-slate-50 px-2 py-1 dark:bg-slate-800">
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{term.it}</span> → {term.vi}
                      {term.en && <span className="text-slate-500"> · {term.en}</span>}
                    </span>
                    {added.has(term.it) ? (
                      <Badge tone="green">
                        <Check className="h-3 w-3" /> {t("board.added")}
                      </Badge>
                    ) : (
                      pending && (
                        <Button size="sm" variant="success" title={t("board.add_term")} onClick={() => void addTerm(term.it)}>
                          <Plus className="h-3.5 w-3.5" /> {t("board.add_term")}
                        </Button>
                      )
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>
    </Modal>
  );
}
