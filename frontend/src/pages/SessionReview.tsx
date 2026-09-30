/**
 * Xem lại buổi học: tóm tắt, transcript, ghim, flashcard, chi phí; xuất MD/PDF (Flow D, FR-POST).
 * Trợ lý gọi bằng nút mascot ở góc màn hình và mở thành cột cạnh nội dung — vừa đọc transcript vừa hỏi.
 */
import {
  CircleDollarSign,
  Download,
  EyeOff,
  FileDown,
  Layers,
  NotebookText,
  Pin,
  Play,
  RefreshCw,
  RotateCw,
  ScrollText,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { CostBreakdown, Flashcard, Metrics, Segment, ServerEvent } from "../api/types";
import { SessionSocket } from "../api/ws";
import { AssistantHeader, MascotButton, useAssistantToggle } from "../components/AssistantDock";
import { AssistantPanel, type AssistantHandle } from "../components/AssistantPanel";
import { Markdown } from "../components/Markdown";
import { PinList } from "../components/PinList";
import { SubtitlePane, type SegmentAction, type SubtitlePaneHandle } from "../components/SubtitlePane";
import { Badge, Button, Card, Spinner, Tabs } from "../components/ui";
import { useT, type MessageKey } from "../i18n";
import { useHotkeys } from "../lib/hotkeys";
import { fmtClock, fmtDateTime, fmtTs, fmtUsd } from "../lib/time";
import { errorMessage, useApp } from "../store/app";
import { useSession } from "../store/session";

type Tab = "summary" | "transcript" | "pins" | "flashcards" | "cost";

const ASSISTANT_KEY = "lezionelive.review.assistant";

function storedOpen(): boolean {
  try {
    return localStorage.getItem(ASSISTANT_KEY) === "1";
  } catch {
    return false;
  }
}

interface SummaryState {
  status: string;
  error: string | null;
  running: boolean;
  summary: { content_md: string; model: string | null; created_at: string } | null;
}

export default function SessionReview() {
  const { id } = useParams();
  const sessionId = Number(id);
  const [params] = useSearchParams();
  const t = useT();
  const lang = useApp((s) => s.lang);
  const toast = useApp((s) => s.toast);
  const health = useApp((s) => s.health);
  const session = useSession((s) => s.session);
  const course = useSession((s) => s.course);
  const postStatus = useSession((s) => s.postStatus);
  const postError = useSession((s) => s.postError);
  const summaryVersion = useSession((s) => s.summaryVersion);
  const order = useSession((s) => s.order);
  const failedCount = useSession((s) => s.order.filter((i) => s.segments[i]?.status === "failed").length);
  // Câu dịch bằng model trên máy (nháp / NLLB): dịch lại bằng Claude sau giờ học khi Claude dùng được.
  const machineCount = useSession(
    (s) => s.order.filter((i) => ["draft", "local"].includes(s.segments[i]?.translation_kind ?? "")).length,
  );
  const localEngine = useApp((s) => s.settings?.settings.translate_provider === "local");
  const hasEn = useSession((s) => s.order.some((i) => s.segments[i]?.text_en));
  const lastEnd = useSession((s) => (s.order.length ? s.segments[s.order[s.order.length - 1]].t_end_ms : 0));

  const [tab, setTab] = useState<Tab>("summary");
  const [summary, setSummary] = useState<SummaryState | null>(null);
  const [cards, setCards] = useState<Flashcard[]>([]);
  const [cost, setCost] = useState<CostBreakdown | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [autoScroll, setAutoScroll] = useState(false);
  const [hideVi, setHideVi] = useState(false);
  const paneRef = useRef<SubtitlePaneHandle>(null);
  const assistantRef = useRef<AssistantHandle>(null);
  const assistant = useAssistantToggle(storedOpen());
  const pendingJump = useRef<number | null>(params.get("t") ? Number(params.get("t")) : null);

  // Tải phiên + nghe sự kiện (tóm tắt xong, dịch lại, ghim…)
  useEffect(() => {
    const store = useSession.getState();
    store.reset();
    let socket: SessionSocket | null = null;
    store
      .load(sessionId)
      .then(() => {
        socket = new SessionSocket(sessionId, {
          onEvent: (ev: ServerEvent) => useSession.getState().apply(ev),
        });
        socket.connect();
        if (pendingJump.current !== null) setTab("transcript");
      })
      .catch((e) => toast("error", errorMessage(e)));
    return () => {
      socket?.close();
      useSession.getState().reset();
    };
  }, [sessionId, toast]);

  const refreshSummary = useCallback(() => {
    api.summary(sessionId).then(setSummary).catch(() => undefined);
    api.sessionFlashcards(sessionId).then(setCards).catch(() => undefined);
  }, [sessionId]);

  useEffect(refreshSummary, [refreshSummary, summaryVersion, postStatus]);

  useEffect(() => {
    if (tab !== "cost") return;
    api.cost(sessionId).then(setCost).catch(() => undefined);
    api.metrics(sessionId).then(setMetrics).catch(() => undefined);
  }, [tab, sessionId]);

  // Nhảy tới mốc thời gian (từ trang tìm kiếm ?t=ms)
  useEffect(() => {
    if (tab === "transcript" && pendingJump.current !== null && order.length) {
      const ms = pendingJump.current;
      pendingJump.current = null;
      window.setTimeout(() => paneRef.current?.scrollToMs(ms), 50);
    }
  }, [tab, order.length]);

  useEffect(() => {
    try {
      localStorage.setItem(ASSISTANT_KEY, assistant.open ? "1" : "0");
    } catch {
      /* localStorage không dùng được */
    }
  }, [assistant.open]);

  const { setOpen: setAssistantOpen } = assistant;
  const withAssistant = useCallback(
    (fn: (a: AssistantHandle) => void) => {
      setAssistantOpen(true);
      window.setTimeout(() => assistantRef.current && fn(assistantRef.current), 50);
    },
    [setAssistantOpen],
  );

  const jumpTo = useCallback((ms: number) => {
    setTab("transcript");
    window.setTimeout(() => paneRef.current?.scrollToMs(ms), 50);
  }, []);

  const askAbout = useCallback(
    (seg: Segment) => {
      const st = useSession.getState();
      const context = st.order
        .map((i) => st.segments[i])
        .filter((s) => s.id !== seg.id && Math.abs(s.t_start_ms - seg.t_start_ms) <= 30_000)
        .map((s) => `[${fmtTs(s.t_start_ms)}] ${s.text_it_edited ?? s.text_it}`)
        .join("\n");
      withAssistant((a) =>
        a.prefill(
          t("live.ask_segment", {
            ts: fmtTs(seg.t_start_ms),
            it: seg.text_it_edited ?? seg.text_it,
            vi: seg.text_vi ? `\n(${seg.text_vi})` : "",
            context: context || "—",
          }),
        ),
      );
    },
    [t, withAssistant],
  );

  const onAction = useCallback(
    async (action: SegmentAction, seg: Segment) => {
      const st = useSession.getState();
      try {
        if (action === "pin") st.addPin(await api.createPin(sessionId, { segment_id: seg.id }));
        else if (action === "unpin") {
          const pin = st.pins.find((p) => p.segment_id === seg.id);
          if (pin) {
            await api.deletePin(pin.id);
            st.removePin(pin.id);
          }
        } else if (action === "ask") askAbout(seg);
        else if (action === "copy") {
          await navigator.clipboard.writeText([seg.text_it_edited ?? seg.text_it, seg.text_vi].filter(Boolean).join("\n"));
          toast("success", t("common.copied"));
        } else if (action === "retry") await api.retrySegment(seg.id);
        else if (action === "speaker") {
          const next = seg.speaker === "other" ? "lecturer" : "other";
          st.upsertSegments([await api.setSpeaker(seg.id, next)]);
        }
      } catch (e) {
        toast("error", errorMessage(e));
      }
    },
    [askAbout, sessionId, t, toast],
  );

  useHotkeys({ "mod+h": () => setHideVi((v) => !v) }, tab === "transcript");
  useHotkeys({ "mod+k": () => withAssistant((a) => a.focusChat()) });

  if (!session) return <div className="py-20 text-center text-slate-500">{t("common.loading")}</div>;

  const running = ["pending", "summary", "flashcards"].includes(postStatus) || summary?.running;
  const duration = Math.max(session.active_ms, lastEnd);
  const pdfOk = health?.capabilities.pdf_export ?? false;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-slate-500">
            {course ? `${course.name}${course.name_it ? ` · ${course.name_it}` : ""}` : ""}
          </p>
          <h1 className="text-2xl font-bold tracking-tight">{session.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-slate-500">
            <span>{fmtDateTime(session.started_at, lang)}</span>
            <span>
              · {t("review.duration")}: {fmtClock(duration)}
            </span>
            <span>· {t("common.segments", { n: order.length })}</span>
            <span>· {fmtUsd(session.cost_usd_est)}</span>
            <Badge tone={session.status === "ended" ? "slate" : session.status === "live" ? "red" : "amber"}>
              {t(`home.status.${session.status}` as MessageKey)}
            </Badge>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {session.status !== "ended" && (
            <Link to={`/live/${sessionId}`}>
              <Button variant="primary">
                <Play className="h-4 w-4" /> {t("review.continue")}
              </Button>
            </Link>
          )}
          {failedCount > 0 && (
            <Button
              onClick={async () => {
                const res = await api.retryFailed(sessionId);
                toast("info", t("review.requeued", { n: res.requeued }));
              }}
            >
              <RotateCw className="h-4 w-4" /> {t("review.retry_failed")} ({failedCount})
            </Button>
          )}
          {machineCount > 0 && !localEngine && (
            <Button
              onClick={() =>
                void api
                  .retranslate(sessionId)
                  .then((res) => toast("info", t("engine.retranslate_done", { n: res.requeued })))
                  .catch((e) => toast("error", errorMessage(e)))
              }
            >
              <RefreshCw className="h-4 w-4" /> {t("engine.retranslate", { n: machineCount })}
            </Button>
          )}
          <span className="text-sm text-slate-500">{t("review.export")}:</span>
          <a href={api.exportUrl(sessionId, "md")} download>
            <Button size="sm">
              <FileDown className="h-3.5 w-3.5" /> {t("review.export_md")}
            </Button>
          </a>
          {pdfOk ? (
            <a href={api.exportUrl(sessionId, "pdf")} download>
              <Button size="sm">
                <FileDown className="h-3.5 w-3.5" /> {t("review.export_pdf")}
              </Button>
            </a>
          ) : (
            <a href={api.exportUrl(sessionId, "html")} download title={t("review.pdf_unavailable")}>
              <Button size="sm">
                <FileDown className="h-3.5 w-3.5" /> {t("review.export_html")}
              </Button>
            </a>
          )}
        </div>
      </div>

      <div className={`grid gap-4 ${assistant.open ? "lg:grid-cols-[minmax(0,1fr)_minmax(22rem,28rem)]" : ""}`}>
        <div className="min-w-0 space-y-4">
          <Tabs
            className="max-w-4xl"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "summary", label: <><NotebookText className="h-3.5 w-3.5" /> {t("review.tab_summary")}</> },
              { id: "transcript", label: <><ScrollText className="h-3.5 w-3.5" /> {t("review.tab_transcript")}</> },
              { id: "pins", label: <><Pin className="h-3.5 w-3.5" /> {t("review.tab_pins")}</> },
              { id: "flashcards", label: <><Layers className="h-3.5 w-3.5" /> {t("review.tab_flashcards")}</> },
              { id: "cost", label: <><CircleDollarSign className="h-3.5 w-3.5" /> {t("review.tab_cost")}</> },
            ]}
          />

          {tab === "summary" && (
            <Card className="p-6">
              {running && (
                <div className="mb-4 flex items-center gap-2 rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-800 dark:bg-brand-950/50 dark:text-brand-200">
                  <Spinner />
                  {t(`review.post.${postStatus === "none" ? "pending" : postStatus}` as MessageKey)}
                </div>
              )}
              {postStatus === "failed" && (
                <div className="mb-4 flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950/50 dark:text-red-200">
                  <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {t("review.post.failed", { error: postError ?? summary?.error ?? "" })}
                </div>
              )}
              {summary?.summary ? (
                <>
                  <Markdown text={summary.summary.content_md} onTimestamp={jumpTo} className="prose-base" />
                  <div className="mt-6 flex items-center gap-3 border-t border-slate-200 pt-3 text-xs text-slate-400 dark:border-slate-700">
                    <span>
                      {summary.summary.model} · {fmtDateTime(summary.summary.created_at, lang)}
                    </span>
                    <Button
                      size="sm"
                      disabled={running}
                      onClick={async () => {
                        try {
                          await api.regenerateSummary(sessionId);
                          useSession.setState({ postStatus: "pending" });
                        } catch (e) {
                          toast("error", errorMessage(e));
                        }
                      }}
                    >
                      <RefreshCw className="h-3.5 w-3.5" /> {t("review.regenerate")}
                    </Button>
                  </div>
                </>
              ) : (
                !running && (
                  <div className="py-10 text-center">
                    <p className="mb-3 text-slate-500">{t("review.no_summary")}</p>
                    <Button
                      variant="primary"
                      onClick={async () => {
                        try {
                          await api.regenerateSummary(sessionId);
                          useSession.setState({ postStatus: "pending" });
                        } catch (e) {
                          toast("error", errorMessage(e));
                        }
                      }}
                    >
                      {t("review.generate")}
                    </Button>
                  </div>
                )
              )}
            </Card>
          )}

          {tab === "transcript" && (
            <Card className="h-[72vh] overflow-hidden">
              <div className="flex items-center gap-3 border-b border-slate-200 px-3 py-2 text-sm dark:border-slate-700">
                <Button size="sm" variant={hideVi ? "primary" : "secondary"} onClick={() => setHideVi((v) => !v)}>
                  <EyeOff className="h-3.5 w-3.5" /> {t("live.listening_mode")}
                </Button>
                <span className="text-xs text-slate-500">Ctrl+H</span>
              </div>
              <div className="h-[calc(72vh-3rem)]">
                <SubtitlePane
                  ref={paneRef}
                  live={false}
                  showEn={hasEn}
                  hideVi={hideVi}
                  fontScale={1}
                  autoScroll={autoScroll}
                  onAutoScrollChange={setAutoScroll}
                  onAction={onAction}
                  onSaveEdit={async (seg, text) => {
                    useSession.getState().upsertSegments([await api.editSegment(seg.id, text)]);
                  }}
                  inclass={session.inclass ?? false}
                  emptyText={t("common.none")}
                />
              </div>
            </Card>
          )}

          {tab === "pins" && (
            <Card className="max-w-3xl">
              <PinList onAsk={(_p, seg) => askAbout(seg)} onJump={(seg) => jumpTo(seg.t_start_ms)} />
            </Card>
          )}

          {tab === "flashcards" && (
            <Card className="p-4">
              <div className="mb-3 flex items-center gap-3">
                <span className="text-sm text-slate-500">{cards.length} flashcard</span>
                <Button
                  size="sm"
                  disabled={running}
                  onClick={async () => {
                    try {
                      await api.regenerateFlashcards(sessionId);
                      useSession.setState({ postStatus: "pending" });
                    } catch (e) {
                      toast("error", errorMessage(e));
                    }
                  }}
                >
                  <RefreshCw className="h-3.5 w-3.5" /> {cards.length ? t("review.regenerate") : t("review.flashcards_generate")}
                </Button>
                <Link to="/flashcards" className="ml-auto text-sm text-brand-600 hover:underline">
                  {t("nav.flashcards")} →
                </Link>
              </div>
              {cards.length === 0 ? (
                <p className="py-6 text-center text-slate-500">{t("review.flashcards_empty")}</p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {cards.map((c) => (
                    <div key={c.id} className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700">
                      <div className="flex items-baseline gap-2">
                        <span className="font-semibold text-slate-900 dark:text-white">{c.front_it}</span>
                        {c.pos && <span className="text-xs text-slate-400">{c.pos}</span>}
                      </div>
                      <div className="text-slate-700 dark:text-slate-300">{c.back_vi}</div>
                      {c.example_it && <div className="mt-1 text-xs italic text-slate-500">“{c.example_it}”</div>}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}

          {tab === "cost" && (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card className="p-4">
                <h3 className="mb-2 font-semibold">
                  {t("review.cost_total")}: {cost ? fmtUsd(cost.total_usd) : "…"}
                  {cost?.threshold_usd != null && (
                    <span className="ml-2 text-sm font-normal text-slate-500">
                      ({t("review.cost_threshold")}: {fmtUsd(cost.threshold_usd)})
                    </span>
                  )}
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="text-left text-xs text-slate-500">
                      <tr>
                        <th className="py-1 pr-2" />
                        <th className="px-2">{t("review.requests")}</th>
                        <th className="px-2">{t("review.tokens_in")}</th>
                        <th className="px-2">{t("review.tokens_out")}</th>
                        <th className="px-2">{t("review.cache_read")}</th>
                        <th className="px-2">{t("review.cache_write")}</th>
                        <th className="px-2 text-right">USD</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums">
                      {cost?.by_purpose.map((p) => (
                        <tr key={p.purpose} className="border-t border-slate-100 dark:border-slate-800" title={p.models.join(", ")}>
                          <td className="py-1 pr-2 font-medium">{t(`review.purpose.${p.purpose}` as MessageKey)}</td>
                          <td className="px-2">{p.requests}</td>
                          <td className="px-2">{p.input_tokens.toLocaleString()}</td>
                          <td className="px-2">{p.output_tokens.toLocaleString()}</td>
                          <td className="px-2">{p.cache_read_tokens.toLocaleString()}</td>
                          <td className="px-2">{p.cache_write_tokens.toLocaleString()}</td>
                          <td className="px-2 text-right">{fmtUsd(p.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
              <Card className="p-4">
                <h3 className="mb-2 font-semibold">{t("review.latency")}</h3>
                {metrics && (
                  <table className="w-full text-sm tabular-nums">
                    <thead className="text-left text-xs text-slate-500">
                      <tr>
                        <th />
                        <th className="px-2">p50</th>
                        <th className="px-2">p95</th>
                        <th className="px-2">max</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(
                        [
                          ["review.latency_it", metrics.it_latency_ms, metrics.targets.it_p50, metrics.targets.it_p95],
                          ["review.latency_vi", metrics.vi_latency_ms, metrics.targets.vi_p50, metrics.targets.vi_p95],
                          ["review.latency_stt", metrics.stt_ms, null, null],
                        ] as const
                      ).map(([key, stats, p50t, p95t]) => (
                        <tr key={key} className="border-t border-slate-100 dark:border-slate-800">
                          <td className="py-1 pr-2 font-medium">{t(key)}</td>
                          <td className={`px-2 ${p50t && stats.p50 !== null && stats.p50 > p50t ? "text-red-600" : ""}`}>
                            {stats.p50 !== null ? `${(stats.p50 / 1000).toFixed(2)}s` : "—"}
                          </td>
                          <td className={`px-2 ${p95t && stats.p95 !== null && stats.p95 > p95t ? "text-red-600" : ""}`}>
                            {stats.p95 !== null ? `${(stats.p95 / 1000).toFixed(2)}s` : "—"}
                          </td>
                          <td className="px-2">{stats.max !== null ? `${(stats.max / 1000).toFixed(2)}s` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <a className="mt-3 inline-flex items-center gap-1 text-sm text-brand-600 hover:underline" href={`/api/sessions/${sessionId}/metrics?format=csv`}>
                  <Download className="h-3.5 w-3.5" /> {t("review.latency_csv")}
                </a>
              </Card>
            </div>
          )}
        </div>

        {/* Màn hình rộng: cột dính cạnh nội dung; màn hình hẹp: khung nổi phía trên nút mascot. */}
        <aside
          aria-label={t("assistant.title")}
          className={
            assistant.open
              ? "fixed bottom-24 right-4 z-40 flex h-[min(40rem,calc(100vh-8rem))] w-[min(27rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900 lg:sticky lg:bottom-auto lg:right-auto lg:top-20 lg:z-auto lg:h-[calc(100vh-7rem)] lg:w-auto lg:self-start lg:shadow-sm"
              : "hidden"
          }
        >
          <AssistantHeader onClose={() => assistant.setOpen(false)} />
          <div className="min-h-0 flex-1">
            <AssistantPanel
              ref={assistantRef}
              sessionId={sessionId}
              onTimestamp={jumpTo}
              onAskPin={(_p, seg) => askAbout(seg)}
              onJump={(seg) => jumpTo(seg.t_start_ms)}
              tabs={["qa", "compose", "slides"]}
              onBusyChange={assistant.onBusyChange}
            />
          </div>
        </aside>
      </div>

      <MascotButton
        open={assistant.open}
        busy={assistant.busy}
        unread={assistant.unread}
        onClick={assistant.toggle}
        className={`fixed bottom-6 right-6 z-40 ${assistant.open ? "lg:hidden" : ""}`}
      />
    </div>
  );
}
