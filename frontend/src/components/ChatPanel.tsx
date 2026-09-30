/**
 * Hỏi đáp với Claude trên transcript (FR-AS-01..04, Flow C), stream qua SSE.
 * Một buổi học có nhiều cuộc trò chuyện (thread) với lịch sử riêng, giao diện kiểu Claude: danh sách "Gần đây"
 * gắn cố định bên trái khi khung đủ rộng, trượt ra dạng ngăn kéo khi khung hẹp (cột phải màn hình học).
 */
import {
  ArrowUp,
  Check,
  Copy,
  ImagePlus,
  Lightbulb,
  ListChecks,
  PanelLeft,
  Paperclip,
  Sparkles,
  Square,
  SquarePen,
  TriangleAlert,
  X,
} from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";

import { api } from "../api/client";
import { postSSE } from "../api/sse";
import type { ChatMessage, ChatThread } from "../api/types";
import { useT } from "../i18n";
import { downscaleImage } from "../lib/image";
import { errorMessage, useApp } from "../store/app";
import { AssistantPicker } from "./AssistantPicker";
import { ChatThreadList } from "./ChatThreadList";
import { Markdown } from "./Markdown";
import { IconButton, Spinner } from "./ui";

export interface ChatPanelHandle {
  focus: () => void;
  prefill: (text: string) => void;
  ask: (text: string) => void;
  newChat: () => void;
  /** Đính kèm ảnh (data URL JPEG đã thu nhỏ, ví dụ khung hình màn hình Teams); gợi ý câu hỏi nếu ô nhập trống. */
  attachImage: (dataUrl: string, question?: string) => void;
}

interface UiImage {
  thumb: string;
  full: string;
}

interface UiMessage {
  key: string;
  role: "user" | "assistant";
  content: string;
  images?: UiImage[];
  streaming?: boolean;
  error?: string;
  summaryMode?: boolean;
  model?: string;
  seconds?: number;
}

/** Khung rộng hơn mức này thì danh sách cuộc trò chuyện gắn cố định bên trái. */
const DOCK_MIN_WIDTH = 640;
/** Khớp HISTORY_MESSAGES của backend: số tin nhắn cũ Claude còn "nhớ" trong một cuộc trò chuyện. */
const HISTORY_MESSAGES = 10;
const NO_MESSAGES: UiMessage[] = [];
/** Khớp MAX_IMAGES của backend (ảnh mỗi câu hỏi). */
const MAX_IMAGES = 4;

function fromHistory(m: ChatMessage): UiMessage {
  return {
    key: `m${m.id}`,
    role: m.role,
    content: m.content,
    images: m.images?.map((a) => ({ thumb: a.thumb_url, full: a.url })),
    model: m.model ?? undefined,
  };
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("FileReader failed"));
    reader.readAsDataURL(blob);
  });
}

function imageFiles(list: FileList | File[] | null | undefined): File[] {
  return Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
}

function draftTitle(question: string): string {
  const first = question.split("\n").find((l) => l.trim()) ?? "";
  const text = first.replace(/\s+/g, " ").trim();
  return text.length <= 60 ? text : `${text.slice(0, 59).trimEnd()}…`;
}

export const ChatPanel = forwardRef<
  ChatPanelHandle,
  { sessionId: number; onTimestamp: (ms: number) => void; onBusyChange?: (busy: boolean) => void }
>(
  function ChatPanel({ sessionId, onTimestamp, onBusyChange }, ref) {
    const t = useT();
    const toast = useApp((s) => s.toast);
    const hasKey = useApp((s) => s.health?.assistant?.ready ?? s.health?.api_key.present ?? false);
    const [threads, setThreads] = useState<ChatThread[]>([]);
    const [activeId, setActiveId] = useState<number | null>(null); // null = cuộc trò chuyện mới chưa gửi
    const [cache, setCache] = useState<Record<number, UiMessage[]>>({});
    const [input, setInput] = useState("");
    const [busy, setBusy] = useState(false);
    const [streamingId, setStreamingId] = useState<number | null>(null);
    const [docked, setDocked] = useState(false);
    const [dockOpen, setDockOpen] = useState(true);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [pending, setPending] = useState<{ key: string; dataUrl: string }[]>([]); // ảnh chờ gửi kèm
    const [dragging, setDragging] = useState(false);
    const [preview, setPreview] = useState<string | null>(null);
    const rootRef = useRef<HTMLDivElement>(null);
    const fileRef = useRef<HTMLInputElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const listRef = useRef<HTMLDivElement>(null);
    const abortRef = useRef<AbortController | null>(null);
    const activeRef = useRef<number | null>(null);
    const busyRef = useRef(false);
    const loadedRef = useRef<Set<number>>(new Set());
    const touchedRef = useRef(false); // người dùng đã tự chọn/gửi → không tự nhảy sang thread gần nhất

    const messages = activeId === null ? NO_MESSAGES : (cache[activeId] ?? NO_MESSAGES);

    const onBusyRef = useRef(onBusyChange);
    onBusyRef.current = onBusyChange;
    useEffect(() => onBusyRef.current?.(busy), [busy]);

    useEffect(() => {
      const el = rootRef.current;
      if (!el) return;
      const ro = new ResizeObserver(([entry]) => setDocked(entry.contentRect.width >= DOCK_MIN_WIDTH));
      ro.observe(el);
      return () => ro.disconnect();
    }, []);

    const refreshThreads = useCallback(async () => {
      try {
        setThreads(await api.chatThreads(sessionId));
      } catch {
        /* giữ danh sách hiện tại */
      }
    }, [sessionId]);

    const select = useCallback(
      (id: number | null) => {
        activeRef.current = id;
        setActiveId(id);
        setDrawerOpen(false);
        if (id === null || loadedRef.current.has(id)) return;
        loadedRef.current.add(id);
        api
          .threadMessages(sessionId, id)
          .then((hist) => setCache((prev) => ({ ...prev, [id]: hist.map(fromHistory) })))
          .catch((e) => {
            loadedRef.current.delete(id);
            toast("error", errorMessage(e));
          });
      },
      [sessionId, toast],
    );

    useEffect(() => {
      let cancelled = false;
      loadedRef.current = new Set();
      touchedRef.current = false;
      activeRef.current = null;
      setActiveId(null);
      setCache({});
      setThreads([]);
      api
        .chatThreads(sessionId)
        .then((list) => {
          if (cancelled) return;
          setThreads(list);
          // Mở lại cuộc trò chuyện gần nhất, như tiếp tục mạch hỏi trong buổi học.
          if (list[0] && !touchedRef.current) select(list[0].id);
        })
        .catch(() => undefined);
      return () => {
        cancelled = true;
        abortRef.current?.abort();
      };
    }, [sessionId, select]);

    useEffect(() => {
      const el = listRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, [messages, activeId]);

    const focusInput = () => window.setTimeout(() => inputRef.current?.focus(), 0);

    const pendingRef = useRef(pending);
    pendingRef.current = pending;

    const addImages = useCallback(
      async (files: File[]) => {
        if (!files.length) return;
        const room = MAX_IMAGES - pendingRef.current.length;
        if (files.length > room) toast("error", t("chat.too_many_images", { n: MAX_IMAGES }));
        for (const file of files.slice(0, Math.max(0, room))) {
          try {
            // Thu nhỏ ≤ 1568 px (JPEG) ngay trên trình duyệt: gửi nhanh, ít token ảnh.
            const dataUrl = await blobToDataUrl(await downscaleImage(file));
            setPending((prev) =>
              prev.length >= MAX_IMAGES ? prev : [...prev, { key: `${Date.now()}-${Math.random()}`, dataUrl }],
            );
          } catch {
            toast("error", t("chat.image_failed", { name: file.name || "clipboard" }));
          }
        }
        focusInput();
      },
      [t, toast],
    );

    useEffect(() => {
      if (!preview) return;
      const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPreview(null);
      window.addEventListener("keydown", onKey);
      return () => window.removeEventListener("keydown", onKey);
    }, [preview]);

    const newChat = useCallback(() => {
      touchedRef.current = true;
      select(null);
      focusInput();
    }, [select]);

    const send = useCallback(
      async (text: string, images: string[] = []) => {
        const question = text.trim() || (images.length ? t("chat.image_default_q") : "");
        if (!question || busyRef.current) return;
        touchedRef.current = true;
        busyRef.current = true;
        setBusy(true);
        setInput("");
        if (images.length) setPending([]);
        let tid = activeRef.current;
        if (tid === null) {
          try {
            const created = await api.createChatThread(sessionId);
            tid = created.id;
            loadedRef.current.add(tid);
            setThreads((prev) => [created, ...prev]);
            setCache((prev) => ({ ...prev, [created.id]: [] }));
            select(tid);
          } catch (e) {
            toast("error", errorMessage(e));
            setInput(text);
            if (images.length) setPending(images.map((dataUrl, i) => ({ key: `r${i}-${Date.now()}`, dataUrl })));
            busyRef.current = false;
            setBusy(false);
            return;
          }
        }
        const threadId = tid;
        const key = `s${Date.now()}`;
        setStreamingId(threadId);
        setCache((prev) => ({
          ...prev,
          [threadId]: [
            ...(prev[threadId] ?? []),
            {
              key: `${key}-u`,
              role: "user",
              content: question,
              images: images.map((dataUrl) => ({ thumb: dataUrl, full: dataUrl })),
            },
            { key, role: "assistant", content: "", streaming: true },
          ],
        }));
        // Đưa cuộc trò chuyện lên đầu ngay (tiêu đề tạm theo câu hỏi đầu, backend đặt tiêu đề thật).
        setThreads((prev) => {
          const cur = prev.find((th) => th.id === threadId);
          if (!cur) return prev;
          const bumped = {
            ...cur,
            title: cur.title || draftTitle(question),
            message_count: cur.message_count + 2,
            updated_at: new Date().toISOString(),
          };
          return [bumped, ...prev.filter((th) => th.id !== threadId)];
        });
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        const started = performance.now();
        const update = (fn: (m: UiMessage) => UiMessage) =>
          setCache((prev) => ({ ...prev, [threadId]: (prev[threadId] ?? []).map((m) => (m.key === key ? fn(m) : m)) }));
        try {
          await postSSE(
            `/sessions/${sessionId}/chat`,
            { message: question, mode: "qa", thread_id: threadId, images },
            ({ event, data }) => {
              const d = data as Record<string, unknown>;
              if (event === "delta") update((m) => ({ ...m, content: m.content + String(d.text ?? "") }));
              else if (event === "done") {
                const msg = d.message as ChatMessage | undefined;
                update((m) => ({
                  ...m,
                  content: msg?.content ?? m.content,
                  streaming: false,
                  summaryMode: Boolean(d.summary_mode),
                  model: typeof d.model === "string" ? d.model : m.model,
                  seconds: Math.round((performance.now() - started) / 100) / 10,
                }));
              } else if (event === "error") update((m) => ({ ...m, streaming: false, error: String(d.message ?? "") }));
            },
            ctrl.signal,
          );
        } catch (e) {
          update((m) => ({ ...m, streaming: false, error: errorMessage(e) }));
        } finally {
          update((m) => ({ ...m, streaming: false }));
          busyRef.current = false;
          setBusy(false);
          setStreamingId(null);
          abortRef.current = null;
          void refreshThreads();
        }
      },
      [sessionId, select, toast, refreshThreads, t],
    );

    const submit = () => void send(input, pending.map((p) => p.dataUrl));

    const rename = useCallback(
      async (id: number, title: string) => {
        try {
          const updated = await api.renameChatThread(id, title);
          setThreads((prev) => prev.map((th) => (th.id === id ? updated : th)));
        } catch (e) {
          toast("error", errorMessage(e));
        }
      },
      [toast],
    );

    const remove = useCallback(
      async (id: number) => {
        if (streamingId === id) abortRef.current?.abort();
        try {
          await api.deleteChatThread(id);
        } catch (e) {
          toast("error", errorMessage(e));
          return;
        }
        loadedRef.current.delete(id);
        setThreads((prev) => prev.filter((th) => th.id !== id));
        setCache((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
        if (activeRef.current === id) select(null);
      },
      [streamingId, toast, select],
    );

    useImperativeHandle(
      ref,
      () => ({
        focus: () => inputRef.current?.focus(),
        prefill: (text: string) => {
          setInput(text);
          window.setTimeout(() => {
            const el = inputRef.current;
            if (el) {
              el.focus();
              el.setSelectionRange(text.length, text.length);
            }
          }, 0);
        },
        ask: (text: string) => void send(text),
        newChat,
        attachImage: (dataUrl: string, question?: string) => {
          if (pendingRef.current.length >= MAX_IMAGES) {
            toast("error", t("chat.too_many_images", { n: MAX_IMAGES }));
            return;
          }
          setPending((prev) => [...prev, { key: `${Date.now()}-${Math.random()}`, dataUrl }]);
          if (question) setInput((cur) => (cur.trim() ? cur : question));
          focusInput();
        },
      }),
      [send, newChat, t, toast],
    );

    const activeThread = threads.find((th) => th.id === activeId);
    const title = activeThread?.title || t("chat.new_chat");
    const sidebarVisible = docked ? dockOpen : drawerOpen;
    const toggleSidebar = () => (docked ? setDockOpen((v) => !v) : setDrawerOpen((v) => !v));
    const busyElsewhere = busy && streamingId !== null && streamingId !== activeId;

    const threadList = (
      <ChatThreadList
        threads={threads}
        activeId={activeId}
        streamingId={streamingId}
        onSelect={(id) => {
          touchedRef.current = true;
          select(id);
        }}
        onNew={newChat}
        onRename={(id, title) => void rename(id, title)}
        onDelete={(id) => void remove(id)}
        onCollapse={toggleSidebar}
      />
    );

    const quickActions = (
      <>
        <QuickChip icon={<Sparkles className="h-3.5 w-3.5" />} disabled={busy} onClick={() => void send(t("chat.q_explain_last"))}>
          {t("chat.quick_explain_last")}
        </QuickChip>
        <QuickChip icon={<ListChecks className="h-3.5 w-3.5" />} disabled={busy} onClick={() => void send(t("chat.q_summary5"))}>
          {t("chat.quick_summary5")}
        </QuickChip>
        <QuickChip
          icon={<Lightbulb className="h-3.5 w-3.5" />}
          disabled={busy}
          onClick={() => {
            setInput(t("chat.q_concept_prefix"));
            focusInput();
          }}
        >
          {t("chat.quick_concept")}
        </QuickChip>
      </>
    );

    return (
      <div ref={rootRef} className="relative flex h-full min-h-0 bg-white dark:bg-slate-900">
        {docked && dockOpen && (
          <aside className="w-60 shrink-0 border-r border-slate-200 dark:border-slate-700">{threadList}</aside>
        )}

        <div
          className="relative flex min-w-0 flex-1 flex-col"
          onDragOver={(e) => {
            if (!hasKey || !Array.from(e.dataTransfer.types).includes("Files")) return;
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
          }}
          onDrop={(e) => {
            if (!dragging) return;
            e.preventDefault();
            setDragging(false);
            void addImages(imageFiles(e.dataTransfer.files));
          }}
        >
          {dragging && (
            <div className="pointer-events-none absolute inset-2 z-10 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-brand-500 bg-brand-50/80 text-sm font-medium text-brand-700 dark:bg-slate-900/80 dark:text-brand-300">
              <ImagePlus className="h-7 w-7" />
              {t("chat.drop_images")}
            </div>
          )}

          {/* Thanh tiêu đề cuộc trò chuyện */}
          <div className="flex items-center gap-1 border-b border-slate-200 px-2 py-1.5 dark:border-slate-700">
            {!sidebarVisible && (
              <IconButton label={t("chat.toggle_threads")} size="sm" onClick={toggleSidebar}>
                <PanelLeft className="h-4 w-4" />
              </IconButton>
            )}
            <button
              type="button"
              className="flex min-w-0 flex-1 items-center gap-1 rounded-md px-1.5 py-1 text-left text-sm font-medium text-slate-800 hover:bg-slate-100 dark:text-slate-100 dark:hover:bg-slate-800"
              title={title}
              onClick={() => !sidebarVisible && toggleSidebar()}
            >
              <span className="truncate">{title}</span>
              {threads.length > 1 && <span className="shrink-0 text-xs font-normal text-slate-400">· {threads.length}</span>}
            </button>
            <IconButton label={t("chat.new_chat_hint")} size="sm" disabled={activeId === null} onClick={newChat}>
              <SquarePen className="h-4 w-4" />
              <span className="hidden text-xs sm:inline">{t("chat.new_chat")}</span>
            </IconButton>
          </div>

          {/* Tin nhắn */}
          <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto">
            {messages.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center px-6 py-8 text-center">
                <img
                  src="/mascot.webp"
                  alt=""
                  className="h-14 w-14 rounded-full shadow-sm ring-1 ring-slate-200 dark:ring-slate-700"
                  aria-hidden
                />
                <p className="mt-3 text-lg font-semibold tracking-tight text-slate-800 dark:text-slate-100">
                  {t("chat.greeting")}
                </p>
                <p className="mt-1.5 max-w-sm text-sm text-slate-500 dark:text-slate-400">
                  {hasKey ? t("chat.empty") : t("chat.no_key")}
                </p>
                {hasKey && <div className="mt-5 flex max-w-md flex-wrap justify-center gap-2">{quickActions}</div>}
                {hasKey && activeId === null && <p className="mt-4 max-w-sm text-xs text-slate-400">{t("chat.new_hint")}</p>}
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-5 px-3 py-4 sm:px-4">
                {messages.map((m) =>
                  m.role === "user" ? (
                    <div key={m.key} className="ml-10 flex flex-col items-end gap-1.5">
                      {!!m.images?.length && (
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {m.images.map((img, i) => (
                            <button
                              key={i}
                              type="button"
                              title={t("chat.open_image")}
                              onClick={() => setPreview(img.full)}
                              className="overflow-hidden rounded-xl border border-slate-200 bg-white hover:opacity-90 dark:border-slate-700"
                            >
                              <img src={img.thumb} alt="" className="h-24 max-w-[16rem] bg-white object-contain" />
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="rounded-2xl rounded-tr-md bg-slate-100 px-3.5 py-2 text-sm text-slate-900 dark:bg-slate-800 dark:text-slate-100">
                        <div className="whitespace-pre-wrap break-words">{m.content}</div>
                      </div>
                    </div>
                  ) : (
                    <div key={m.key} className="group flex gap-2.5">
                      <img
                        src="/mascot.webp"
                        alt=""
                        className="mt-0.5 h-7 w-7 shrink-0 rounded-full ring-1 ring-slate-200 dark:ring-slate-700"
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        {m.content ? (
                          <Markdown text={m.content} onTimestamp={onTimestamp} />
                        ) : m.streaming ? (
                          <span className="flex h-7 items-center gap-2 text-sm text-slate-500">
                            <Spinner className="h-3.5 w-3.5" /> {t("chat.thinking")}
                          </span>
                        ) : null}
                        {m.streaming && m.content && (
                          <span className="ml-0.5 inline-block h-4 w-[3px] animate-blink rounded-sm bg-brand-500 align-text-bottom" />
                        )}
                        {m.summaryMode && <p className="mt-1 text-xs text-slate-400">{t("chat.summary_mode")}</p>}
                        {m.error && (
                          <p className="mt-1.5 flex items-start gap-1.5 rounded-lg bg-red-50 px-2.5 py-1.5 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
                            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {m.error}
                          </p>
                        )}
                        {!m.streaming && m.content && (
                          <div className="mt-1 flex items-center gap-1 text-[0.7rem] text-slate-400">
                            <CopyButton text={m.content} />
                            {m.model && (
                              <span className="opacity-0 transition group-hover:opacity-100">
                                {m.model}
                                {m.seconds ? ` · ${m.seconds}s` : ""}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  ),
                )}
              </div>
            )}
          </div>

          {/* Ô soạn tin kiểu Claude */}
          <div className="px-3 pb-2 pt-1 sm:px-4">
            <div className="mx-auto max-w-3xl">
              {busyElsewhere ? (
                <p className="mb-2 text-xs text-amber-700 dark:text-amber-400">{t("chat.busy_other")}</p>
              ) : (
                messages.length > HISTORY_MESSAGES && (
                  <p className="mb-2 flex flex-wrap items-center gap-x-2 text-xs text-slate-500 dark:text-slate-400">
                    {t("chat.long_thread")}
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 font-medium text-brand-600 hover:underline dark:text-brand-400"
                      onClick={newChat}
                    >
                      <SquarePen className="h-3.5 w-3.5" /> {t("chat.new_chat")}
                    </button>
                  </p>
                )
              )}
              {messages.length > 0 && hasKey && <div className="no-scrollbar mb-2 flex gap-1.5 overflow-x-auto">{quickActions}</div>}
              <div className="rounded-2xl border border-slate-300 bg-white shadow-sm transition focus-within:border-brand-400 focus-within:ring-4 focus-within:ring-brand-500/10 dark:border-slate-600 dark:bg-slate-800">
                {pending.length > 0 && (
                  <div className="flex flex-wrap gap-2 px-3 pt-3">
                    {pending.map((p) => (
                      <div key={p.key} className="group relative">
                        <button type="button" title={t("chat.open_image")} onClick={() => setPreview(p.dataUrl)}>
                          <img
                            src={p.dataUrl}
                            alt=""
                            className="h-16 max-w-[8rem] rounded-lg border border-slate-200 bg-white object-contain dark:border-slate-700"
                          />
                        </button>
                        <button
                          type="button"
                          title={t("chat.remove_image")}
                          aria-label={t("chat.remove_image")}
                          onClick={() => setPending((prev) => prev.filter((x) => x.key !== p.key))}
                          className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-white shadow hover:bg-red-600"
                        >
                          <X className="h-3 w-3" strokeWidth={2.5} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                <textarea
                  ref={inputRef}
                  rows={2}
                  value={input}
                  disabled={!hasKey}
                  placeholder={t("chat.placeholder")}
                  className="block max-h-48 min-h-[3rem] w-full resize-none bg-transparent px-3.5 pb-1 pt-3 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none disabled:cursor-not-allowed dark:text-slate-100"
                  onChange={(e) => setInput(e.target.value)}
                  onPaste={(e) => {
                    const files = imageFiles(e.clipboardData.files);
                    if (files.length) {
                      e.preventDefault();
                      void addImages(files);
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
                <div className="flex items-center gap-1 px-2 pb-2">
                  <input
                    ref={fileRef}
                    type="file"
                    accept="image/*"
                    multiple
                    hidden
                    onChange={(e) => {
                      void addImages(imageFiles(e.target.files));
                      e.target.value = "";
                    }}
                  />
                  <IconButton
                    label={t("chat.attach_image", { n: MAX_IMAGES })}
                    disabled={!hasKey || pending.length >= MAX_IMAGES}
                    onClick={() => fileRef.current?.click()}
                  >
                    <Paperclip className="h-[1.1rem] w-[1.1rem]" />
                  </IconButton>
                  <div className="ml-auto flex min-w-0 items-center gap-1.5">
                    <AssistantPicker disabled={busy} inline />
                    {busy ? (
                      <button
                        type="button"
                        title={t("chat.stop")}
                        aria-label={t("chat.stop")}
                        onClick={() => abortRef.current?.abort()}
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white transition hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
                      >
                        <Square className="h-3 w-3" fill="currentColor" />
                      </button>
                    ) : (
                      <button
                        type="button"
                        title={`${t("chat.send")} (Enter)`}
                        aria-label={t("chat.send")}
                        disabled={(!input.trim() && !pending.length) || !hasKey}
                        onClick={submit}
                        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white shadow-sm transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none dark:disabled:bg-slate-700 dark:disabled:text-slate-500"
                      >
                        <ArrowUp className="h-4 w-4" strokeWidth={2.25} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
              <p className="mt-1.5 text-center text-[0.68rem] text-slate-400">{t("chat.disclaimer")}</p>
            </div>
          </div>
        </div>

        {preview && (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4"
            onClick={() => setPreview(null)}
            role="dialog"
            aria-modal
          >
            <img src={preview} alt="" className="max-h-full max-w-full rounded-lg bg-white shadow-2xl" />
          </div>
        )}

        {!docked && drawerOpen && (
          <>
            <div className="absolute inset-0 z-20 bg-slate-900/30" onClick={() => setDrawerOpen(false)} aria-hidden />
            <aside className="absolute inset-y-0 left-0 z-30 w-[85%] max-w-[18rem] border-r border-slate-200 shadow-xl dark:border-slate-700">
              {threadList}
            </aside>
          </>
        )}
      </div>
    );
  },
);

/** Nút sao chép dưới câu trả lời (đổi thành dấu tích 1,5 s sau khi chép). */
function CopyButton({ text }: { text: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return (
    <IconButton
      size="sm"
      label={copied ? t("chat.copied") : t("chat.copy")}
      className="!h-6 !min-w-6 !px-1 text-slate-400"
      onClick={() => void navigator.clipboard.writeText(text).then(() => setCopied(true))}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
    </IconButton>
  );
}

function QuickChip({
  icon,
  disabled,
  onClick,
  children,
}: {
  icon: ReactNode;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700 dark:hover:text-white"
    >
      <span className="text-brand-600 dark:text-brand-400">{icon}</span>
      {children}
    </button>
  );
}
