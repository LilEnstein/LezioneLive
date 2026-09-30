/** Danh sách cuộc trò chuyện hỏi đáp của buổi học (kiểu sidebar "Recents" của Claude). */
import { MessageSquare, PanelLeftClose, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import type { ChatThread } from "../api/types";
import { useT } from "../i18n";
import { useApp } from "../store/app";
import { Spinner, inputCls } from "./ui";

function timeLabel(iso: string, lang: string): string {
  const d = new Date(iso);
  const locale = lang === "vi" ? "vi-VN" : "en-GB";
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit" });
}

const iconBtn =
  "rounded-md p-1 text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-slate-700 dark:hover:text-slate-200";

export function ChatThreadList({
  threads,
  activeId,
  streamingId,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onCollapse,
}: {
  threads: ChatThread[];
  activeId: number | null;
  streamingId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onRename: (id: number, title: string) => void;
  onDelete: (id: number) => void;
  onCollapse: () => void;
}) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [confirmId, setConfirmId] = useState<number | null>(null);

  useEffect(() => {
    if (confirmId === null) return;
    const timer = window.setTimeout(() => setConfirmId(null), 3000);
    return () => window.clearTimeout(timer);
  }, [confirmId]);

  const q = query.trim().toLowerCase();
  const visible = q ? threads.filter((th) => (th.title || t("chat.new_chat")).toLowerCase().includes(q)) : threads;

  const commitRename = (id: number) => {
    const title = draftTitle.trim();
    const current = threads.find((th) => th.id === id)?.title ?? "";
    setEditingId(null);
    if (title && title !== current) onRename(id, title);
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50 dark:bg-slate-900">
      <div className="flex items-center gap-1 px-2 pt-2">
        <button type="button" className={iconBtn} title={t("chat.toggle_threads")} onClick={onCollapse}>
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>
      <div className="px-2 pb-2 pt-1">
        <button
          type="button"
          onClick={onNew}
          title={t("chat.new_chat_hint")}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm font-medium text-slate-700 hover:bg-slate-200/70 dark:text-slate-200 dark:hover:bg-slate-800"
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-600 text-white">
            <Plus className="h-3.5 w-3.5" strokeWidth={2.5} />
          </span>
          {t("chat.new_chat")}
        </button>
        {threads.length > 5 && (
          <div className="relative mt-2">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("chat.search_threads")}
              className={`${inputCls} py-1.5 pl-8 text-xs`}
            />
          </div>
        )}
      </div>
      <p className="px-4 pb-1 text-[0.7rem] font-medium uppercase tracking-wide text-slate-400">{t("chat.recents")}</p>
      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {visible.length === 0 && (
          <p className="px-2.5 py-3 text-xs text-slate-400">
            {threads.length ? t("chat.threads_no_match") : t("chat.threads_empty")}
          </p>
        )}
        {visible.map((th) => {
          const active = th.id === activeId;
          const title = th.title || t("chat.new_chat");
          if (editingId === th.id) {
            return (
              <input
                key={th.id}
                autoFocus
                value={draftTitle}
                maxLength={200}
                className={`${inputCls} py-1.5 text-sm`}
                onChange={(e) => setDraftTitle(e.target.value)}
                onBlur={() => commitRename(th.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename(th.id);
                  else if (e.key === "Escape") setEditingId(null);
                }}
              />
            );
          }
          return (
            <div
              key={th.id}
              className={`group flex items-center rounded-lg ${
                active
                  ? "bg-slate-200/80 dark:bg-slate-700/70"
                  : "hover:bg-slate-200/50 dark:hover:bg-slate-800"
              }`}
            >
              <button
                type="button"
                className="flex min-w-0 flex-1 items-start gap-2 px-2.5 py-1.5 text-left"
                onClick={() => onSelect(th.id)}
                onDoubleClick={() => {
                  setDraftTitle(th.title);
                  setEditingId(th.id);
                }}
                title={title}
              >
                <MessageSquare className={`mt-0.5 h-3.5 w-3.5 shrink-0 ${active ? "text-brand-600 dark:text-brand-400" : "text-slate-400"}`} />
                <span className="min-w-0 flex-1">
                <span
                  className={`block truncate text-sm ${active ? "font-medium text-slate-900 dark:text-white" : "text-slate-700 dark:text-slate-300"}`}
                >
                  {title}
                </span>
                <span className="block text-[0.7rem] text-slate-400">
                  {th.id === streamingId ? (
                    <span className="inline-flex items-center gap-1 text-brand-600 dark:text-brand-400">
                      <Spinner className="h-2.5 w-2.5" /> {t("chat.answering")}
                    </span>
                  ) : (
                    `${timeLabel(th.updated_at, lang)} · ${t("chat.msg_count", { n: th.message_count })}`
                  )}
                </span>
                </span>
              </button>
              <div
                className={`flex shrink-0 items-center pr-1 ${confirmId === th.id ? "" : "invisible group-focus-within:visible group-hover:visible"}`}
              >
                {confirmId === th.id ? (
                  <button
                    type="button"
                    className="rounded-md bg-red-600 px-1.5 py-0.5 text-[0.7rem] font-medium text-white hover:bg-red-700"
                    title={t("chat.delete_confirm")}
                    onClick={() => {
                      setConfirmId(null);
                      onDelete(th.id);
                    }}
                  >
                    {t("chat.delete")}?
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      className={iconBtn}
                      title={t("chat.rename")}
                      onClick={() => {
                        setDraftTitle(th.title);
                        setEditingId(th.id);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" className={`${iconBtn} hover:!text-red-600`} title={t("chat.delete")} onClick={() => setConfirmId(th.id)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
