/**
 * Phụ đề song ngữ (mục 12.2–12.4): cột Ý | Việt (| Anh), trạng thái segment, tự cuộn,
 * nút "Về mới nhất", chế độ luyện nghe (làm mờ bản dịch, di chuột để xem), menu thao tác.
 */
import {
  ArrowDown,
  ArrowLeftRight,
  Copy,
  Hand,
  MessagesSquare,
  Pencil,
  Pin,
  PinOff,
  TriangleAlert,
  Zap,
} from "lucide-react";
import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { Segment } from "../api/types";
import { useT } from "../i18n";
import { fmtTs } from "../lib/time";
import { pinnedIds, useSession } from "../store/session";
import { BoardCaptureCard } from "./BoardCaptureCard";
import { SpeakerBadge } from "./SpeakerBadge";
import { Button, inputCls } from "./ui";

export type SegmentAction = "pin" | "unpin" | "edit" | "ask" | "copy" | "retry" | "speaker";

/** Dòng trong transcript: segment, ảnh bảng hoặc mốc sự kiện (xen kẽ theo thời gian, IC-FR-43). */
type PaneItem = { kind: "seg"; id: number } | { kind: "board"; id: number } | { kind: "marker"; id: number };

export interface SubtitlePaneHandle {
  scrollToMs: (ms: number) => void;
  scrollToSegment: (id: number) => void;
  scrollToBottom: () => void;
}

interface Props {
  live: boolean;
  showEn: boolean;
  hideVi: boolean;
  fontScale: number;
  autoScroll: boolean;
  onAutoScrollChange: (value: boolean) => void;
  onAction: (action: SegmentAction, seg: Segment) => void;
  onSaveEdit: (seg: Segment, text: string) => Promise<void>;
  emptyText: string;
  inclass?: boolean;
}

const GRID_2 = "grid-cols-[4.2rem_minmax(0,1fr)_minmax(0,1fr)]";
const GRID_3 = "grid-cols-[4.2rem_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.8fr)]";

const SegmentRow = memo(function SegmentRow({
  id,
  showEn,
  hideVi,
  selected,
  pinned,
  editing,
  inclass,
  onSelect,
  onAction,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
}: {
  id: number;
  showEn: boolean;
  hideVi: boolean;
  selected: boolean;
  pinned: boolean;
  editing: boolean;
  inclass: boolean;
  onSelect: (id: number | null) => void;
  onAction: (action: SegmentAction, seg: Segment) => void;
  onStartEdit: (id: number) => void;
  onCancelEdit: () => void;
  onSaveEdit: (seg: Segment, text: string) => Promise<void>;
}) {
  const t = useT();
  const seg = useSession((s) => s.segments[id]);
  const partial = useSession((s) => s.partial[id]);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (editing && seg) setDraft(seg.text_it_edited ?? seg.text_it);
  }, [editing, seg]);

  if (!seg) return null;
  const textIt = seg.text_it_edited ?? seg.text_it;
  const low = seg.confidence < 0.5;
  const other = inclass && seg.speaker === "other";

  if (seg.status === "redacted") {
    return (
      <div
        data-seg-id={id}
        className={`grid ${showEn ? GRID_3 : GRID_2} gap-3 border-b border-slate-100 px-3 py-1.5 text-sm italic text-slate-400 dark:border-slate-800`}
      >
        <div className="font-mono text-[0.78em] tabular-nums">[{fmtTs(seg.t_start_ms)}]</div>
        <div className="flex items-center gap-1.5 pl-6">
          <Hand className="h-[1em] w-[1em]" /> {t("live.redacted")}
        </div>
        <div />
      </div>
    );
  }

  let vi: ReactNode;
  if (seg.text_vi && seg.translation_kind === "local") {
    // Dịch bằng model trên máy (NLLB): hiện bình thường, kèm nhãn nhỏ để biết chất lượng thấp hơn Claude.
    vi = (
      <span title={t("live.local_title")}>
        <span className="mr-1 rounded bg-sky-100 px-1 align-[0.1em] text-[0.65em] font-semibold text-sky-700 dark:bg-sky-900/50 dark:text-sky-300">
          {t("live.local")}
        </span>
        {seg.text_vi}
        {seg.status === "translating" && <span className="ml-1 inline-block animate-blink text-brand-500">▌</span>}
      </span>
    );
  } else if (seg.status === "translated" && seg.text_vi) {
    vi = seg.text_vi;
  } else if (seg.text_vi && seg.translation_kind === "draft") {
    vi = (
      <span className="italic text-slate-500 dark:text-slate-400" title={t("live.draft_title")}>
        <span className="mr-1 rounded bg-slate-200 px-1 text-[0.7em] font-semibold not-italic text-slate-600 dark:bg-slate-700 dark:text-slate-300">
          {t("live.draft")}
        </span>
        {seg.text_vi}
      </span>
    );
  } else if (seg.status === "pending_translation") {
    vi = <span className="text-sm text-slate-400">{t("live.pending_translation")}</span>;
  } else if (seg.status === "translating" && partial !== undefined) {
    vi = (
      <>
        {partial}
        <span className="ml-0.5 inline-block animate-blink text-brand-500">▌</span>
      </>
    );
  } else if (seg.status === "failed") {
    vi = (
      <span className="inline-flex flex-wrap items-center gap-2 text-red-600 dark:text-red-400">
        <span className="inline-flex items-center gap-1" title={seg.error ?? ""}>
          <TriangleAlert className="h-[1em] w-[1em]" /> {t("live.failed")}
        </span>
        <Button
          size="sm"
          onClick={(e) => {
            e.stopPropagation();
            onAction("retry", seg);
          }}
        >
          {t("common.retry")}
        </Button>
      </span>
    );
  } else {
    vi = <span className="text-slate-400">⋯</span>;
  }

  return (
    <div
      data-seg-id={id}
      onClick={() => onSelect(selected ? null : id)}
      className={`group relative grid ${showEn ? GRID_3 : GRID_2} cursor-pointer gap-3 border-b border-slate-100 px-3 py-2 transition-colors dark:border-slate-800 ${
        pinned ? "bg-amber-50 dark:bg-amber-900/20" : "hover:bg-slate-50 dark:hover:bg-slate-800/50"
      } ${selected ? "ring-2 ring-inset ring-brand-400" : ""} ${other ? "text-[0.94em] opacity-80" : ""}`}
    >
      <div className="pt-0.5 font-mono text-[0.78em] tabular-nums text-slate-400">[{fmtTs(seg.t_start_ms)}]</div>
      <div className={`leading-snug ${other ? "pl-6 text-slate-600 dark:text-slate-300" : "text-slate-900 dark:text-slate-100"}`}>
        {editing ? (
          <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
            <textarea
              className={`${inputCls} min-h-[4rem]`}
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") onCancelEdit();
              }}
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="primary"
                loading={saving}
                onClick={async () => {
                  setSaving(true);
                  try {
                    await onSaveEdit(seg, draft);
                  } finally {
                    setSaving(false);
                  }
                }}
              >
                {t("common.save")}
              </Button>
              <Button size="sm" onClick={onCancelEdit}>
                {t("common.cancel")}
              </Button>
            </div>
          </div>
        ) : (
          <>
            {inclass && <SpeakerBadge speaker={seg.speaker} score={seg.speaker_score} compact={!other} />}
            <span
              className={low ? "underline decoration-orange-500 decoration-dotted decoration-2 underline-offset-4" : ""}
              title={low ? t("live.low_conf") : undefined}
            >
              {textIt}
            </span>
            {seg.text_it_edited && (
              <span className="ml-1 inline-flex align-middle text-slate-400" title={t("live.edited")}>
                <Pencil className="h-3 w-3" />
              </span>
            )}
            {pinned && (
              <span className="ml-1 inline-flex align-middle text-amber-500" title={t("live.pinned")}>
                <Pin className="h-3.5 w-3.5" fill="currentColor" />
              </span>
            )}
          </>
        )}
      </div>
      <div
        className={`leading-snug text-slate-700 transition dark:text-slate-300 ${other ? "pl-6" : ""} ${
          hideVi ? "select-none blur-[5px] group-hover:select-auto group-hover:blur-none" : ""
        }`}
      >
        {vi}
      </div>
      {showEn && (
        <div
          className={`leading-snug text-slate-500 dark:text-slate-400 ${hideVi ? "blur-[5px] group-hover:blur-none" : ""}`}
        >
          {seg.text_en ?? ""}
        </div>
      )}
      {selected && !editing && (
        <div
          className="absolute right-2 top-1 z-10 flex gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-lg dark:border-slate-700 dark:bg-slate-800"
          onClick={(e) => e.stopPropagation()}
        >
          <Button size="sm" variant="ghost" onClick={() => onAction(pinned ? "unpin" : "pin", seg)}>
            {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
            {pinned ? t("live.menu_unpin") : t("live.menu_pin")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onStartEdit(seg.id)}>
            <Pencil className="h-3.5 w-3.5" /> {t("live.menu_edit")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onAction("ask", seg)}>
            <MessagesSquare className="h-3.5 w-3.5" /> {t("live.menu_ask")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => onAction("copy", seg)}>
            <Copy className="h-3.5 w-3.5" /> {t("live.menu_copy")}
          </Button>
          {inclass && (
            <Button size="sm" variant="ghost" onClick={() => onAction("speaker", seg)}>
              <ArrowLeftRight className="h-3.5 w-3.5" /> {t("live.menu_speaker")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
});

function MarkerRow({ id }: { id: number }) {
  const marker = useSession((s) => s.markers.find((m) => m.id === id));
  if (!marker) return null;
  return (
    <div className="flex items-center gap-3 border-b border-slate-100 bg-slate-50 px-3 py-1 text-xs text-slate-500 dark:border-slate-800 dark:bg-slate-900/60">
      <span className="w-[4.2rem] shrink-0 font-mono tabular-nums">[{fmtTs(marker.t_ms)}]</span>
      <span className="inline-flex items-center gap-1">
        <Zap className="h-3 w-3 text-amber-500" /> {marker.message}
      </span>
    </div>
  );
}

export const SubtitlePane = forwardRef<SubtitlePaneHandle, Props>(function SubtitlePane(
  { live, showEn, hideVi, fontScale, autoScroll, onAutoScrollChange, onAction, onSaveEdit, emptyText, inclass = false },
  ref,
) {
  const t = useT();
  const order = useSession((s) => s.order);
  const boards = useSession((s) => s.boards);
  const markers = useSession((s) => s.markers);
  const pins = useSession((s) => s.pins);
  const selectedId = useSession((s) => s.selectedId);
  const select = useSession((s) => s.select);
  const speaking = useSession((s) => s.speaking);
  const status = useSession((s) => s.status);
  const [editingId, setEditingId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const autoRef = useRef(autoScroll);
  autoRef.current = autoScroll;
  const pinned = pinnedIds(pins);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  const flash = (el: Element) => {
    el.classList.add("flash-highlight");
    window.setTimeout(() => el.classList.remove("flash-highlight"), 1600);
  };

  const scrollToSegment = useCallback(
    (id: number) => {
      const el = scrollRef.current?.querySelector(`[data-seg-id="${id}"]`);
      if (!el) return;
      onAutoScrollChange(false);
      el.scrollIntoView({ block: "center" });
      flash(el);
      select(id);
    },
    [onAutoScrollChange, select],
  );

  const scrollToMs = useCallback(
    (ms: number) => {
      const { segments, order: ids } = useSession.getState();
      if (!ids.length) return;
      let best = ids[0];
      for (const id of ids) {
        if (segments[id].t_start_ms <= ms + 500) best = id;
        else break;
      }
      scrollToSegment(best);
    },
    [scrollToSegment],
  );

  useImperativeHandle(ref, () => ({ scrollToMs, scrollToSegment, scrollToBottom }), [
    scrollToMs,
    scrollToSegment,
    scrollToBottom,
  ]);

  // Nội dung cao lên (segment mới, bản dịch stream) → cuộn xuống nếu đang bật tự cuộn.
  useLayoutEffect(() => {
    const content = contentRef.current;
    if (!content) return;
    const observer = new ResizeObserver(() => {
      if (autoRef.current) scrollToBottom();
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [scrollToBottom]);

  useEffect(() => {
    if (autoScroll) scrollToBottom();
  }, [autoScroll, scrollToBottom]);

  // Ảnh bảng và mốc sự kiện chèn trước segment đầu tiên bắt đầu sau mốc thời gian của chúng.
  const items = useMemo<PaneItem[]>(() => {
    const extras = [
      ...Object.values(boards).map((b) => ({ t: b.t_ms, rank: 0, item: { kind: "board", id: b.id } as PaneItem })),
      ...markers.map((m) => ({ t: m.t_ms, rank: 1, item: { kind: "marker", id: m.id } as PaneItem })),
    ].sort((a, b) => a.t - b.t || a.rank - b.rank);
    if (!extras.length) return order.map((id) => ({ kind: "seg", id }) as PaneItem);
    const segs = useSession.getState().segments;
    const out: PaneItem[] = [];
    let k = 0;
    for (const id of order) {
      const start = segs[id]?.t_start_ms ?? 0;
      while (k < extras.length && extras[k].t <= start) out.push(extras[k++].item);
      out.push({ kind: "seg", id });
    }
    while (k < extras.length) out.push(extras[k++].item);
    return out;
  }, [order, boards, markers]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    if (atBottom !== autoRef.current) onAutoScrollChange(atBottom);
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col" style={{ fontSize: `${fontScale}rem` }}>
      <div
        className={`grid ${showEn ? GRID_3 : GRID_2} gap-3 border-b border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400`}
        style={{ fontSize: "0.72rem" }}
      >
        <span />
        <span>{t("live.italian")}</span>
        <span>{t("live.vietnamese")}</span>
        {showEn && <span>{t("live.english")}</span>}
      </div>
      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        {/* Màn hình học: chừa chỗ dưới cùng cho nút logo trợ lý nổi, không che dòng mới nhất. */}
        <div ref={contentRef} className={live ? "pb-20" : undefined}>
          {items.length === 0 && !speaking && (
            <div className="px-6 py-16 text-center text-sm text-slate-500 dark:text-slate-400">{emptyText}</div>
          )}
          {items.map((item) =>
            item.kind === "board" ? (
              <BoardCaptureCard key={`b${item.id}`} id={item.id} />
            ) : item.kind === "marker" ? (
              <MarkerRow key={`m${item.id}`} id={item.id} />
            ) : (
              <SegmentRow
                key={item.id}
                id={item.id}
                showEn={showEn}
                hideVi={hideVi}
                selected={selectedId === item.id}
                pinned={pinned.has(item.id)}
                editing={editingId === item.id}
                inclass={inclass}
                onSelect={select}
                onAction={onAction}
                onStartEdit={setEditingId}
                onCancelEdit={() => setEditingId(null)}
                onSaveEdit={async (seg, text) => {
                  await onSaveEdit(seg, text);
                  setEditingId(null);
                }}
              />
            ),
          )}
          {live && speaking && (
            <div className={`grid ${showEn ? GRID_3 : GRID_2} gap-3 px-3 py-2 text-slate-500`}>
              <div className="font-mono text-[0.78em] text-slate-400">[{fmtTs(status?.t_ms ?? 0)}]</div>
              <div>
                <span className="inline-block animate-blink text-brand-500">▌</span>
                <span className="ml-1 italic">{t("live.speaking")}</span>
              </div>
              <div>⋯</div>
            </div>
          )}
        </div>
      </div>
      {!autoScroll && order.length > 0 && (
        <button
          type="button"
          onClick={() => {
            onAutoScrollChange(true);
            scrollToBottom();
          }}
          className="absolute bottom-4 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-brand-600 px-4 py-1.5 text-sm font-medium text-white shadow-lg hover:bg-brand-700"
        >
          <ArrowDown className="h-4 w-4" /> {t("live.jump_latest")}
        </button>
      )}
    </div>
  );
});
