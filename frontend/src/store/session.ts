/** Trạng thái một phiên đang mở (LiveSession hoặc SessionReview): segment, stream bản dịch, ghim, chi phí. */
import { create } from "zustand";

import { api } from "../api/client";
import type {
  BatteryStatus,
  BoardCapture,
  Pin,
  PostStatus,
  RunnerStatus,
  Segment,
  ServerEvent,
  Session,
  SessionDetail,
  SessionMarker,
  SlideDeck,
  SourceHealth,
} from "../api/types";

interface SessionState {
  sessionId: number | null;
  session: Session | null;
  course: SessionDetail["course"];
  segments: Record<number, Segment>;
  order: number[];
  partial: Record<number, string>;
  pins: Pin[];
  slides: SlideDeck[];
  status: RunnerStatus | null;
  speaking: boolean;
  levelDb: number;
  cost: number;
  threshold: number | null;
  postStatus: PostStatus;
  postError: string | null;
  selectedId: number | null;
  networkOnline: boolean;
  translationSlow: boolean;
  slidesVersion: number;
  summaryVersion: number;
  // Lớp học trực tiếp
  boards: Record<number, BoardCapture>;
  markers: SessionMarker[];
  sourceHealth: SourceHealth | null;
  battery: BatteryStatus | null;
  pendingTranslations: number;

  load: (id: number) => Promise<SessionDetail>;
  loadMissed: () => Promise<void>;
  apply: (ev: ServerEvent) => void;
  select: (id: number | null) => void;
  upsertSegments: (segs: Segment[]) => void;
  setPins: (pins: Pin[]) => void;
  addPin: (pin: Pin) => void;
  removePin: (id: number) => void;
  setSlides: (slides: SlideDeck[]) => void;
  upsertBoard: (capture: BoardCapture) => void;
  reset: () => void;
}

const EMPTY = {
  sessionId: null,
  session: null,
  course: null,
  segments: {},
  order: [],
  partial: {},
  pins: [],
  slides: [],
  status: null,
  speaking: false,
  levelDb: -120,
  cost: 0,
  threshold: null,
  postStatus: "none" as PostStatus,
  postError: null,
  selectedId: null,
  networkOnline: true,
  translationSlow: false,
  slidesVersion: 0,
  summaryVersion: 0,
  boards: {},
  markers: [],
  sourceHealth: null,
  battery: null,
  pendingTranslations: 0,
};

function insertOrdered(order: number[], segments: Record<number, Segment>, id: number): number[] {
  if (order.includes(id)) return order;
  const seq = segments[id].seq;
  let i = order.length;
  while (i > 0 && segments[order[i - 1]].seq > seq) i--;
  return [...order.slice(0, i), id, ...order.slice(i)];
}

export const useSession = create<SessionState>((set, get) => ({
  ...EMPTY,

  reset: () => set({ ...EMPTY }),

  load: async (id) => {
    const detail = await api.session(id);
    const segments: Record<number, Segment> = {};
    for (const s of detail.segments) segments[s.id] = s;
    set({
      ...EMPTY,
      sessionId: id,
      session: detail.session,
      course: detail.course,
      segments,
      order: detail.segments.map((s) => s.id),
      pins: detail.pins,
      slides: detail.slides,
      status: detail.runner,
      cost: detail.session.cost_usd_est,
      postStatus: detail.session.post_status,
      postError: detail.session.post_error,
      networkOnline: detail.runner?.network_online ?? true,
      translationSlow: detail.runner?.translation_slow ?? false,
      boards: Object.fromEntries((detail.board_captures ?? []).map((b) => [b.id, b])),
      markers: detail.markers ?? [],
      sourceHealth: detail.runner?.source_health ?? null,
      battery: detail.runner?.battery ?? null,
      pendingTranslations: detail.segments.filter((sg) => sg.status === "pending_translation").length,
    });
    return detail;
  },

  loadMissed: async () => {
    const { sessionId, order, segments } = get();
    if (sessionId === null) return;
    const lastSeq = order.length ? segments[order[order.length - 1]].seq : -1;
    // Tải lại cả vài đoạn cuối để nhận bản dịch đã hoàn tất trong lúc mất kết nối.
    const detail = await api.session(sessionId, Math.max(-1, lastSeq - 20));
    get().upsertSegments(detail.segments);
    set({
      pins: detail.pins,
      session: detail.session,
      cost: detail.session.cost_usd_est,
      boards: Object.fromEntries((detail.board_captures ?? []).map((b) => [b.id, b])),
      markers: detail.markers ?? get().markers,
    });
  },

  upsertSegments: (segs) => {
    set((state) => {
      const segments = { ...state.segments };
      let order = state.order;
      const partial = { ...state.partial };
      for (const s of segs) {
        segments[s.id] = s;
        order = insertOrdered(order, segments, s.id);
        if (s.status !== "translating") delete partial[s.id];
      }
      return { segments, order, partial };
    });
  },

  select: (id) => set({ selectedId: id }),
  setPins: (pins) => set({ pins }),
  addPin: (pin) =>
    set((s) => ({
      pins: s.pins.some((p) => p.id === pin.id) ? s.pins.map((p) => (p.id === pin.id ? pin : p)) : [...s.pins, pin],
    })),
  removePin: (id) => set((s) => ({ pins: s.pins.filter((p) => p.id !== id) })),
  setSlides: (slides) => set({ slides }),
  upsertBoard: (capture) => set((s) => ({ boards: { ...s.boards, [capture.id]: capture } })),

  apply: (ev) => {
    const state = get();
    switch (ev.type) {
      case "status":
        set({
          status: ev,
          speaking: ev.speaking,
          networkOnline: ev.network_online,
          translationSlow: ev.translation_slow,
          session: state.session
            ? {
                ...state.session,
                status: ev.state,
                translation_enabled: ev.translation_enabled,
                battery_saver: ev.battery_saver ?? state.session.battery_saver,
                denoise_level: ev.denoise_level ?? state.session.denoise_level,
              }
            : null,
          postStatus: ev.post_status ?? state.postStatus,
          sourceHealth: ev.source_health ?? state.sourceHealth,
          battery: ev.battery !== undefined ? ev.battery : state.battery,
        });
        break;
      case "vad.speech_start":
        set({ speaking: true });
        break;
      case "vad.speech_end":
        set({ speaking: false });
        break;
      case "segment.transcribed": {
        const seg: Segment = {
          id: ev.id,
          seq: ev.seq,
          t_start_ms: ev.t_start_ms,
          t_end_ms: ev.t_end_ms,
          text_it: ev.text_it,
          text_it_edited: null,
          confidence: ev.confidence,
          text_vi: null,
          text_en: null,
          status: "transcribed",
          error: null,
          speaker: ev.speaker ?? "unknown",
          snr_db: ev.snr_db ?? null,
          translation_kind: null,
        };
        const segments = { ...state.segments, [seg.id]: seg };
        set({ segments, order: insertOrdered(state.order, segments, seg.id) });
        break;
      }
      case "segment.translating": {
        const seg = state.segments[ev.id];
        if (!seg) return;
        set({
          segments: { ...state.segments, [ev.id]: { ...seg, status: "translating", error: null } },
          partial: { ...state.partial, [ev.id]: "" },
        });
        break;
      }
      case "segment.translation.delta":
        if (!state.segments[ev.id]) return;
        set({ partial: { ...state.partial, [ev.id]: (state.partial[ev.id] ?? "") + ev.delta } });
        break;
      case "segment.translated": {
        const seg = state.segments[ev.id];
        if (!seg) return;
        const partial = { ...state.partial };
        delete partial[ev.id];
        set({
          segments: {
            ...state.segments,
            [ev.id]: {
              ...seg,
              status: "translated",
              text_vi: ev.text_vi,
              text_en: ev.text_en ?? seg.text_en,
              error: null,
              translation_kind: ev.translation_kind ?? "claude",
            },
          },
          partial,
        });
        break;
      }
      case "segment.pending": {
        const seg = state.segments[ev.id];
        if (!seg) return;
        set({ segments: { ...state.segments, [ev.id]: { ...seg, status: "pending_translation" } } });
        break;
      }
      case "segment.draft": {
        const seg = state.segments[ev.id];
        if (!seg || seg.translation_kind === "claude" || seg.translation_kind === "local") return;
        set({ segments: { ...state.segments, [ev.id]: { ...seg, text_vi: ev.text_vi, translation_kind: "draft" } } });
        break;
      }
      case "segment.speaker": {
        const seg = state.segments[ev.id];
        if (!seg) return;
        set({ segments: { ...state.segments, [ev.id]: { ...seg, speaker: ev.speaker, speaker_score: ev.score } } });
        break;
      }
      case "board.processing":
        get().upsertBoard(ev.capture);
        break;
      case "board.captured":
        get().upsertBoard(ev.capture);
        break;
      case "board.deleted": {
        const boards = { ...state.boards };
        delete boards[ev.id];
        set({ boards });
        break;
      }
      case "marker.created":
        if (!state.markers.some((m) => m.id === ev.marker.id)) set({ markers: [...state.markers, ev.marker] });
        break;
      case "source.health":
        set({ sourceHealth: ev });
        break;
      case "network.state":
        set({ networkOnline: ev.online, pendingTranslations: ev.pending_translations });
        break;
      case "segment.failed": {
        const seg = state.segments[ev.id];
        if (!seg) return;
        const partial = { ...state.partial };
        delete partial[ev.id];
        set({ segments: { ...state.segments, [ev.id]: { ...seg, status: "failed", error: ev.error } }, partial });
        break;
      }
      case "segment.updated":
        get().upsertSegments([ev.segment]);
        break;
      case "cost.update":
        set({ cost: ev.session_cost_usd, threshold: ev.threshold_usd });
        break;
      case "level":
        set({ levelDb: ev.db });
        break;
      case "pin.created":
      case "pin.updated":
        get().addPin(ev.pin);
        break;
      case "pin.deleted":
        get().removePin(ev.id);
        break;
      case "slides.updated":
        set({ slidesVersion: state.slidesVersion + 1 });
        break;
      case "post.status":
        set({ postStatus: ev.status, postError: ev.error ?? null });
        break;
      case "summary.ready":
      case "flashcards.ready":
        set({ summaryVersion: state.summaryVersion + 1 });
        break;
      case "network":
        set({ networkOnline: ev.online });
        break;
      case "translation.slow":
        set({ translationSlow: ev.slow });
        break;
      default:
        break;
    }
  },
}));

/** Các segment_id đã ghim (để tô vàng và hiện 📌). */
export function pinnedIds(pins: Pin[]): Set<number> {
  return new Set(pins.map((p) => p.segment_id).filter((x): x is number => x !== null));
}
