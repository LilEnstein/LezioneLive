/** Ôn flashcard SM-2 (Flow F, FR-LE-02): lật thẻ (Space), tự chấm 0–5, nghe phát âm tiếng Ý (R/E). */
import { ExternalLink, Headphones, PartyPopper, Play, Snail, Trash2, TriangleAlert, Volume2 } from "lucide-react";
import { useCallback, useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Flashcard } from "../api/types";
import { Button, Card, IconButton } from "../components/ui";
import { useT, type MessageKey } from "../i18n";
import { useHotkeys } from "../lib/hotkeys";
import {
  loadSpeechPrefs,
  saveSpeechPrefs,
  speakItalian,
  speechSupported,
  stopSpeaking,
  useItalianVoice,
  type SpeechPrefs,
} from "../lib/speech";
import { fmtDateTime } from "../lib/time";
import { errorMessage, useApp } from "../store/app";

const GRADE_TONES = [
  "bg-red-600 hover:bg-red-700",
  "bg-red-500 hover:bg-red-600",
  "bg-orange-500 hover:bg-orange-600",
  "bg-amber-500 hover:bg-amber-600",
  "bg-emerald-600 hover:bg-emerald-700",
  "bg-brand-600 hover:bg-brand-700",
];

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card className="px-4 py-3">
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      <div className="text-xs text-slate-500">{label}</div>
    </Card>
  );
}

function Toggle({ on, onClick, icon, children }: { on: boolean; onClick: () => void; icon: ReactNode; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
        on
          ? "border-brand-300 bg-brand-50 text-brand-700 dark:border-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
          : "border-slate-300 text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

export default function Flashcards() {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const toast = useApp((s) => s.toast);
  const [stats, setStats] = useState({ total: 0, due: 0, new: 0, mature: 0 });
  const [queue, setQueue] = useState<Flashcard[]>([]);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [lastDue, setLastDue] = useState<string | null>(null);
  const [all, setAll] = useState<Flashcard[] | null>(null);
  const [prefs, setPrefs] = useState<SpeechPrefs>(loadSpeechPrefs);
  const { voice, loaded: voicesLoaded } = useItalianVoice();

  const togglePref = (key: keyof SpeechPrefs) =>
    setPrefs((p) => {
      const next = { ...p, [key]: !p[key] };
      saveSpeechPrefs(next);
      return next;
    });

  const say = useCallback(
    (text: string | null | undefined, e?: MouseEvent) => {
      e?.stopPropagation(); // bấm loa không lật thẻ
      if (text) speakItalian(text, { voice, slow: prefs.slow });
    },
    [voice, prefs.slow],
  );

  useEffect(() => stopSpeaking, []);

  const refresh = useCallback(() => {
    api.flashcardStats().then(setStats).catch(() => undefined);
  }, []);

  useEffect(refresh, [refresh]);

  const start = async () => {
    try {
      const due = await api.dueFlashcards(100);
      setQueue(due.cards);
      setIndex(0);
      setFlipped(false);
      setReviewing(true);
      setLastDue(null);
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  const card = reviewing ? queue[index] : undefined;
  const hideItalian = prefs.listening && !flipped;

  // Tự đọc từ khi sang thẻ mới (luôn bật ở chế độ luyện nghe). Khoá theo index vì thẻ sai được xếp lại cuối hàng.
  const autoKey = card ? `${index}:${card.id}` : null;
  useEffect(() => {
    if (autoKey && card && (prefs.autoplay || prefs.listening)) say(card.front_it);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoKey]);

  const grade = useCallback(
    async (g: number) => {
      if (!card || !flipped) return;
      try {
        const updated = await api.review(card.id, g);
        setLastDue(updated.due_at);
        if (g < 3) setQueue((q) => [...q, updated]); // sai → ôn lại trong lượt này
        setIndex((i) => i + 1);
        setFlipped(false);
        refresh();
      } catch (e) {
        toast("error", errorMessage(e));
      }
    },
    [card, flipped, refresh, toast],
  );

  useHotkeys(
    {
      space: () => setFlipped((f) => !f),
      r: () => say(card?.front_it),
      e: () => say(card?.example_it),
      "0": () => void grade(0),
      "1": () => void grade(1),
      "2": () => void grade(2),
      "3": () => void grade(3),
      "4": () => void grade(4),
      "5": () => void grade(5),
    },
    reviewing && !!card,
  );

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">{t("fc.title")}</h1>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label={t("fc.due")} value={stats.due} />
        <Stat label={t("fc.new")} value={stats.new} />
        <Stat label={t("fc.mature")} value={stats.mature} />
        <Stat label={t("fc.total")} value={stats.total} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Toggle on={prefs.autoplay} onClick={() => togglePref("autoplay")} icon={<Volume2 className="h-3.5 w-3.5" />}>
          {t("fc.tts.autoplay")}
        </Toggle>
        <Toggle on={prefs.listening} onClick={() => togglePref("listening")} icon={<Headphones className="h-3.5 w-3.5" />}>
          {t("fc.tts.listening")}
        </Toggle>
        <Toggle on={prefs.slow} onClick={() => togglePref("slow")} icon={<Snail className="h-3.5 w-3.5" />}>
          {t("fc.tts.slow")}
        </Toggle>
        {(!speechSupported || (voicesLoaded && !voice)) && (
          <span className="inline-flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
            {t(speechSupported ? "fc.tts.no_voice" : "fc.tts.unsupported")}
          </span>
        )}
      </div>

      {!reviewing && (
        <div className="flex flex-wrap gap-3">
          <Button variant="primary" className="px-5 py-2.5" disabled={stats.due === 0} onClick={start}>
            <Play className="h-4 w-4" /> {t("fc.start", { n: stats.due })}
          </Button>
          <Button onClick={() => api.flashcards().then(setAll)}>{t("fc.all_cards")}</Button>
          {stats.total === 0 && <p className="self-center text-sm text-slate-500">{t("fc.empty")}</p>}
          {stats.total > 0 && stats.due === 0 && <p className="self-center text-sm text-slate-500">{t("fc.nothing_due")}</p>}
        </div>
      )}

      {reviewing && card && (
        <div className="mx-auto max-w-2xl space-y-4">
          <div className="text-center text-sm text-slate-500">
            {t("fc.progress", { i: index + 1, n: queue.length })} · {t("fc.tts.keys")}
          </div>
          <div
            role="button"
            tabIndex={0}
            onClick={() => setFlipped((f) => !f)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setFlipped((f) => !f);
            }}
            className="block w-full cursor-pointer rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-md transition hover:shadow-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400 dark:border-slate-700 dark:bg-slate-900"
          >
            {hideItalian ? (
              <div className="flex flex-col items-center gap-3">
                <IconButton
                  label={t("fc.tts.play_word")}
                  tone="primary"
                  className="!h-16 !w-16 rounded-full"
                  onClick={(e) => say(card.front_it, e)}
                >
                  <Headphones className="h-7 w-7" />
                </IconButton>
                <p className="text-sm text-slate-500">{t("fc.tts.listen_hint")}</p>
                {card.example_it && (
                  <Button size="sm" variant="ghost" onClick={(e) => say(card.example_it, e)}>
                    <Volume2 className="h-3.5 w-3.5" /> {t("fc.tts.play_example")}
                  </Button>
                )}
              </div>
            ) : (
              <>
                <div className="flex items-center justify-center gap-2">
                  <span className="text-3xl font-bold text-slate-900 dark:text-white">🇮🇹 {card.front_it}</span>
                  <IconButton label={t("fc.tts.play_word")} onClick={(e) => say(card.front_it, e)}>
                    <Volume2 className="h-5 w-5" />
                  </IconButton>
                </div>
                {card.example_it && (
                  <p className="mt-4 text-base italic text-slate-600 dark:text-slate-300">
                    “{card.example_it}”
                    <IconButton
                      size="sm"
                      className="ml-1 align-middle"
                      label={t("fc.tts.play_example")}
                      onClick={(e) => say(card.example_it, e)}
                    >
                      <Volume2 className="h-4 w-4" />
                    </IconButton>
                  </p>
                )}
              </>
            )}
            {flipped ? (
              <div className="mt-6 space-y-2 border-t border-slate-200 pt-6 dark:border-slate-700">
                <div className="text-2xl font-semibold text-brand-700 dark:text-brand-300">🇻🇳 {card.back_vi}</div>
                {card.pos && <div className="text-sm text-slate-500">{card.pos}</div>}
                {card.example_vi && <p className="text-base text-slate-600 dark:text-slate-300">{card.example_vi}</p>}
                {card.session_title && <p className="text-xs text-slate-400">{t("fc.from_session", { title: card.session_title })}</p>}
              </div>
            ) : (
              <div className="mt-6 text-sm text-slate-400">{t("fc.flip")}</div>
            )}
          </div>
          {flipped && (
            <div>
              <p className="mb-2 text-center text-xs text-slate-500">{t("fc.grade_hint")}</p>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                {[0, 1, 2, 3, 4, 5].map((g) => (
                  <button
                    key={g}
                    type="button"
                    onClick={() => void grade(g)}
                    className={`rounded-xl px-2 py-2 text-sm font-medium text-white ${GRADE_TONES[g]}`}
                  >
                    {g} · {t(`fc.grade.${g}` as MessageKey)}
                  </button>
                ))}
              </div>
            </div>
          )}
          {lastDue && <p className="text-center text-xs text-slate-400">{t("fc.next_due", { date: fmtDateTime(lastDue, lang) })}</p>}
        </div>
      )}

      {reviewing && !card && (
        <Card className="mx-auto max-w-xl p-10 text-center">
          <PartyPopper className="mx-auto h-10 w-10 text-violet-500" strokeWidth={1.5} />
          <p className="text-xl font-semibold">{t("fc.done")}</p>
          <Button className="mt-4" onClick={() => setReviewing(false)}>
            {t("common.back")}
          </Button>
        </Card>
      )}

      {!reviewing && all && (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500 dark:bg-slate-800/60">
              <tr>
                <th className="px-3 py-2">🇮🇹</th>
                <th className="px-3 py-2">🇻🇳</th>
                <th className="px-3 py-2">{t("fc.example")}</th>
                <th className="px-3 py-2">SM-2</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {all.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-2 font-medium">
                    <span className="inline-flex items-center gap-1">
                      {c.front_it}
                      <IconButton size="sm" label={t("fc.tts.play_word")} onClick={() => say(c.front_it)}>
                        <Volume2 className="h-3.5 w-3.5" />
                      </IconButton>
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {c.back_vi} {c.pos && <span className="text-xs text-slate-400">({c.pos})</span>}
                  </td>
                  <td className="px-3 py-2 text-xs italic text-slate-500">
                    {c.example_it && (
                      <button
                        type="button"
                        className="text-left hover:text-brand-600"
                        title={t("fc.tts.play_example")}
                        onClick={() => say(c.example_it)}
                      >
                        {c.example_it}
                      </button>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-500">
                    EF {c.ease.toFixed(2)} · {c.interval_days}d · {fmtDateTime(c.due_at, lang)}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {c.session_id && (
                      <Link className="mr-2 inline-flex text-brand-600 hover:text-brand-700" to={`/sessions/${c.session_id}`}>
                        <ExternalLink className="h-3.5 w-3.5" />
                      </Link>
                    )}
                    <button
                      type="button"
                      className="inline-flex align-middle text-slate-400 hover:text-red-600"
                      aria-label={t("common.delete")}
                      title={t("common.delete")}
                      onClick={async () => {
                        await api.deleteFlashcard(c.id);
                        setAll((prev) => prev?.filter((x) => x.id !== c.id) ?? null);
                        refresh();
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
