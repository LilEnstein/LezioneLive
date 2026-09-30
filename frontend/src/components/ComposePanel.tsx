/**
 * Chế độ "Hỏi giảng viên" (FR-AS-05, Flow E): tiếng Việt → câu hỏi tiếng Ý trang trọng + dịch ngược.
 * Chế độ "Hỏi trực tiếp trong lớp" (IC-US-10): câu tiếng Ý ngắn (≤ 25 từ) để nói, kèm phiên âm nhấn âm.
 */
import { AudioLines, Copy, Hand, Languages, Lightbulb, Mail, TriangleAlert, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { postSSE } from "../api/sse";
import type { ChatMessage, ComposeResult, SpeakResult } from "../api/types";
import { useT } from "../i18n";
import { errorMessage, useApp } from "../store/app";
import { Button, Tabs, inputCls } from "./ui";

type ComposeMode = "compose_it" | "speak_it";

interface Item {
  question: string;
  result: ComposeResult & Partial<SpeakResult>;
}

function pairs(history: ChatMessage[]): Item[] {
  const out: Item[] = [];
  let pending: string | null = null;
  for (const m of history) {
    if (m.role === "user") pending = m.content;
    else if (m.data && pending !== null) {
      out.push({ question: pending, result: m.data });
      pending = null;
    }
  }
  return out;
}

function SpeakCard({ item }: { item: Item }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const words = item.result.word_count ?? item.result.question_it.split(/\s+/).filter(Boolean).length;
  return (
    <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-800">
      <p className="text-xs text-slate-500 dark:text-slate-400">🇻🇳 {item.question}</p>
      <div className="flex items-start gap-2">
        <p className="flex flex-1 items-start gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-lg font-medium text-slate-900 dark:bg-emerald-950/40 dark:text-slate-100">
          <Hand className="mt-1.5 h-4 w-4 shrink-0 text-emerald-600" /> {item.result.question_it}
        </p>
        <Button
          size="sm"
          onClick={async () => {
            await navigator.clipboard.writeText(item.result.question_it);
            toast("success", t("common.copied"));
          }}
        >
          <Copy className="h-4 w-4" />
        </Button>
      </div>
      {item.result.pronunciation && (
        <div>
          <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <AudioLines className="h-3.5 w-3.5" /> {t("compose.pronunciation")}
          </span>
          <p className="font-mono text-[0.95rem] tracking-wide text-brand-700 dark:text-brand-300">{item.result.pronunciation}</p>
        </div>
      )}
      <div>
        <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <Undo2 className="h-3.5 w-3.5" /> {t("compose.back")}
        </span>
        <p className="text-slate-700 dark:text-slate-300">{item.result.back_translation_vi}</p>
      </div>
      <p className="text-xs text-slate-400">{t("compose.words", { n: words })}</p>
      {item.result.tip && (
        <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <Lightbulb className="h-3.5 w-3.5 shrink-0" /> {t("compose.tip")}: {item.result.tip}
        </p>
      )}
    </div>
  );
}

function ResultCard({ item }: { item: Item }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  return (
    <div className="space-y-2 rounded-xl border border-slate-200 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-800">
      <p className="text-xs text-slate-500 dark:text-slate-400">🇻🇳 {item.question}</p>
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">🇮🇹 {t("compose.italian")}</span>
          <Button
            size="sm"
            variant="primary"
            onClick={async () => {
              await navigator.clipboard.writeText(item.result.question_it);
              toast("success", t("common.copied"));
            }}
          >
            <Copy className="h-3.5 w-3.5" /> {t("compose.copy_it")}
          </Button>
        </div>
        <p className="whitespace-pre-wrap rounded-lg bg-emerald-50 px-3 py-2 text-[0.95rem] text-slate-900 dark:bg-emerald-950/40 dark:text-slate-100">
          {item.result.question_it}
        </p>
      </div>
      <div>
        <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <Undo2 className="h-3.5 w-3.5" /> {t("compose.back")}
        </span>
        <p className="whitespace-pre-wrap text-slate-700 dark:text-slate-300">{item.result.back_translation_vi}</p>
      </div>
      {item.result.notes && (
        <p className="flex items-start gap-1.5 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <Lightbulb className="h-3.5 w-3.5 shrink-0" /> {t("compose.notes")}: {item.result.notes}
        </p>
      )}
    </div>
  );
}

export function ComposePanel({ sessionId }: { sessionId: number }) {
  const t = useT();
  const hasKey = useApp((s) => s.health?.assistant?.ready ?? s.health?.api_key.present ?? false);
  const [mode, setMode] = useState<ComposeMode>("compose_it");
  const [items, setItems] = useState<Item[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setItems([]);
    api
      .chatHistory(sessionId, mode)
      .then((h) => setItems(pairs(h).reverse()))
      .catch(() => undefined);
  }, [sessionId, mode]);

  const submit = async () => {
    const question = text.trim();
    if (!question) return;
    setBusy(true);
    setError(null);
    try {
      let result: ComposeResult | null = null;
      await postSSE(`/sessions/${sessionId}/chat`, { message: question, mode }, ({ event, data }) => {
        if (event === "result") result = data as ComposeResult;
        if (event === "error") setError(String((data as { message?: string }).message ?? ""));
      });
      if (result) {
        setItems((prev) => [{ question, result: result as ComposeResult }, ...prev]);
        setText("");
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 border-b border-slate-200 p-3 dark:border-slate-700">
        <Tabs
          value={mode}
          onChange={setMode}
          tabs={[
            { id: "compose_it", label: <><Mail className="h-3.5 w-3.5" /> {t("compose.mode_write")}</> },
            { id: "speak_it", label: <><Hand className="h-3.5 w-3.5" /> {t("compose.mode_speak")}</> },
          ]}
        />
        <p className="text-xs text-slate-500 dark:text-slate-400">{mode === "speak_it" ? t("compose.speak_hint") : t("compose.hint")}</p>
        <textarea
          rows={3}
          value={text}
          disabled={!hasKey}
          placeholder={t("compose.placeholder")}
          className={`${inputCls} resize-none`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) void submit();
          }}
        />
        <Button variant="primary" className="w-full" loading={busy} disabled={!text.trim() || !hasKey} onClick={submit}>
          {mode === "speak_it" ? <Hand className="h-4 w-4" /> : <Languages className="h-4 w-4" />}
          {mode === "speak_it" ? t("compose.speak_submit") : t("compose.submit")}
        </Button>
        {error && (
          <p className="flex items-start gap-1.5 text-sm text-red-600 dark:text-red-400">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {error}
          </p>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {items.length > 0 && (
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("compose.history")}</p>
        )}
        {items.map((item, i) => (mode === "speak_it" ? <SpeakCard key={i} item={item} /> : <ResultCard key={i} item={item} />))}
      </div>
    </div>
  );
}
