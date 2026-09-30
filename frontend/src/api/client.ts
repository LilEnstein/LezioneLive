import type {
  AudioDevice,
  BatteryStatus,
  BoardCapture,
  CaptureSource,
  ChatMessage,
  ChatThread,
  CompanionStatus,
  CostBreakdown,
  Course,
  DenoiseLevel,
  Flashcard,
  GlossarySuggestion,
  GlossaryTerm,
  Health,
  InClassStatus,
  LicenseStatus,
  LocalModelInfo,
  Metrics,
  MicDevice,
  ModelStatus,
  PairedDevice,
  PairInfo,
  Pin,
  PinKind,
  RoomProfile,
  SearchResult,
  Segment,
  Session,
  SessionDetail,
  SessionMarker,
  SettingsResponse,
  SlideDeck,
  SoundCheck,
  Speaker,
  SttStatus,
} from "./types";

export interface CreateSessionBody {
  course_id: number | null;
  title?: string;
  audio_mode: CaptureSource;
  room_profile_id?: number | null;
  denoise_level?: DenoiseLevel | null;
  battery_saver?: boolean;
  mic_device?: string | null;
  confirm_save_audio?: boolean;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let payload: BodyInit | undefined;
  if (body instanceof FormData || body instanceof ArrayBuffer || body instanceof Blob) {
    payload = body;
    if (body instanceof ArrayBuffer) headers["Content-Type"] = "application/octet-stream";
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: payload, ...init });
  } catch {
    throw new ApiError(0, "BACKEND_UNREACHABLE", "Không kết nối được backend");
  }
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    let message = res.statusText || "Lỗi không xác định";
    try {
      const data = await res.json();
      const detail = data?.detail;
      if (detail && typeof detail === "object") {
        code = detail.code ?? code;
        message = detail.message ?? message;
      } else if (typeof detail === "string") {
        message = detail;
      }
    } catch {
      /* không phải JSON */
    }
    throw new ApiError(res.status, code, message);
  }
  if (res.status === 204) return undefined as T;
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const put = <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {});
const patch = <T>(path: string, body?: unknown) => request<T>("PATCH", path, body ?? {});
const del = <T>(path: string) => request<T>("DELETE", path);

function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const str = s.toString();
  return str ? `?${str}` : "";
}

export const api = {
  health: () => get<Health>("/health"),
  licenseStatus: () => get<LicenseStatus>("/license/status"),
  licenseActivate: (email: string, code: string) => post<LicenseStatus>("/license/activate", { email, code }),
  settings: () => get<SettingsResponse>("/settings"),
  putSettings: (patchBody: Partial<SettingsResponse["settings"]>) => put<SettingsResponse>("/settings", patchBody),
  putApiKey: (api_key: string) =>
    put<{ valid: boolean; last4: string; source: string }>("/settings/api-key", { api_key }),
  deleteApiKey: () => del<{ deleted: boolean }>("/settings/api-key"),
  claudeCodeTest: (model: string, effort: string) =>
    post<{ ok: boolean; model: string; reply?: string; seconds?: number; error?: string }>("/settings/claude-code/test", {
      model,
      effort,
    }),
  claudeCodeStatus: () =>
    get<{ installed: boolean; logged_in: boolean; subscription?: string | null; error?: string }>("/settings/claude-code"),
  putSttKey: (provider: "deepgram" | "azure", api_key: string) =>
    put<{ saved: boolean; last4: string }>("/settings/stt-key", { provider, api_key }),
  deleteSttKey: (provider: "deepgram" | "azure") => del<{ deleted: boolean }>(`/settings/stt-key/${provider}`),
  sttStatus: () => get<SttStatus>("/stt/status"),
  sttPrepare: () => post<SttStatus>("/stt/prepare"),
  sttTest: (pcm: ArrayBuffer) =>
    request<{ text: string; confidence: number; elapsed_ms: number; audio_ms: number; peak: number }>(
      "POST",
      "/stt/test",
      pcm,
    ),
  audioDevices: () =>
    get<{ available: boolean; devices: AudioDevice[]; microphones: MicDevice[]; error?: string }>("/audio/devices"),

  courses: () => get<Course[]>("/courses"),
  createCourse: (body: { name: string; name_it?: string | null; lecturer?: string | null }) =>
    post<Course>("/courses", body),
  updateCourse: (id: number, body: { name: string; name_it?: string | null; lecturer?: string | null }) =>
    put<Course>(`/courses/${id}`, body),
  deleteCourse: (id: number) => del<{ deleted: boolean }>(`/courses/${id}`),

  sessions: (params: { course_id?: number; status?: string } = {}) => get<Session[]>(`/sessions${qs(params)}`),
  createSession: (body: CreateSessionBody) => post<{ session_id: number; session: Session }>("/sessions", body),
  session: (id: number, afterSeq = -1) => get<SessionDetail>(`/sessions/${id}${qs({ after_seq: afterSeq })}`),
  patchSession: (
    id: number,
    body: { title?: string; course_id?: number | null; denoise_level?: DenoiseLevel; battery_saver?: boolean },
  ) => patch<Session>(`/sessions/${id}`, body),
  deleteSession: (id: number) => del<{ deleted: boolean }>(`/sessions/${id}`),
  pause: (id: number) => post<{ status: string }>(`/sessions/${id}/pause`),
  resume: (id: number) => post<{ status: string }>(`/sessions/${id}/resume`),
  end: (id: number) => post<{ status: string; post_status: string }>(`/sessions/${id}/end`),
  setTranslation: (id: number, enabled: boolean) =>
    post<{ translation_enabled: boolean }>(`/sessions/${id}/translation`, { enabled }),
  retryFailed: (id: number) => post<{ requeued: number }>(`/sessions/${id}/retry-failed`),
  /** Dịch lại bằng Claude các câu đang là bản dịch máy (nháp / model cục bộ). */
  retranslate: (id: number) => post<{ requeued: number }>(`/sessions/${id}/retranslate`),

  editSegment: (id: number, text_it_edited: string) => patch<Segment>(`/segments/${id}`, { text_it_edited }),
  retrySegment: (id: number) => post<{ queued: boolean }>(`/segments/${id}/retry`),

  pins: (sessionId: number) => get<Pin[]>(`/sessions/${sessionId}/pins`),
  createPin: (sessionId: number, body: { segment_id?: number | null; kind?: PinKind; note?: string | null }) =>
    post<Pin>(`/sessions/${sessionId}/pins`, body),
  updatePin: (id: number, body: { kind?: PinKind; note?: string | null }) => patch<Pin>(`/pins/${id}`, body),
  deletePin: (id: number) => del<{ deleted: boolean }>(`/pins/${id}`),

  slides: (sessionId: number) => get<SlideDeck[]>(`/sessions/${sessionId}/slides`),
  uploadSlides: (sessionId: number, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<SlideDeck>("POST", `/sessions/${sessionId}/slides`, fd);
  },
  deleteSlides: (deckId: number) => del<{ deleted: boolean }>(`/slides/${deckId}`),
  slideFileUrl: (deckId: number) => `/api/slides/${deckId}/file`,
  suggestions: (sessionId: number) => get<GlossarySuggestion[]>(`/sessions/${sessionId}/glossary-suggestions`),
  acceptSuggestion: (id: number, term_vi?: string) =>
    post<GlossaryTerm>(`/glossary-suggestions/${id}/accept`, term_vi ? { term_vi } : {}),
  rejectSuggestion: (id: number) => post<{ rejected: boolean }>(`/glossary-suggestions/${id}/reject`),

  chatHistory: (sessionId: number, mode?: "qa" | "compose_it" | "speak_it") =>
    get<ChatMessage[]>(`/sessions/${sessionId}/chat${qs({ mode })}`),
  threadMessages: (sessionId: number, threadId: number) =>
    get<ChatMessage[]>(`/sessions/${sessionId}/chat${qs({ thread_id: threadId })}`),
  chatThreads: (sessionId: number) => get<ChatThread[]>(`/sessions/${sessionId}/chat/threads`),
  createChatThread: (sessionId: number, title?: string) =>
    post<ChatThread>(`/sessions/${sessionId}/chat/threads`, title ? { title } : {}),
  renameChatThread: (threadId: number, title: string) => patch<ChatThread>(`/chat/threads/${threadId}`, { title }),
  deleteChatThread: (threadId: number) => del<{ deleted: boolean }>(`/chat/threads/${threadId}`),

  summary: (sessionId: number) =>
    get<{
      status: string;
      error: string | null;
      running: boolean;
      summary: { content_md: string; model: string | null; created_at: string } | null;
    }>(`/sessions/${sessionId}/summary`),
  regenerateSummary: (sessionId: number) => post<{ status: string }>(`/sessions/${sessionId}/summary/regenerate`),
  sessionFlashcards: (sessionId: number) => get<Flashcard[]>(`/sessions/${sessionId}/flashcards`),
  regenerateFlashcards: (sessionId: number) =>
    post<{ status: string }>(`/sessions/${sessionId}/flashcards/regenerate`),
  cost: (sessionId: number) => get<CostBreakdown>(`/sessions/${sessionId}/cost`),
  metrics: (sessionId: number) => get<Metrics>(`/sessions/${sessionId}/metrics`),
  exportUrl: (sessionId: number, format: "md" | "html" | "pdf") => `/api/sessions/${sessionId}/export?format=${format}`,

  glossary: (params: { course_id?: string; q?: string } = {}) => get<GlossaryTerm[]>(`/glossary${qs(params)}`),
  createTerm: (body: Omit<GlossaryTerm, "id" | "source" | "updated_at">) => post<GlossaryTerm>("/glossary", body),
  updateTerm: (id: number, body: Omit<GlossaryTerm, "id" | "source" | "updated_at">) =>
    put<GlossaryTerm>(`/glossary/${id}`, body),
  deleteTerm: (id: number) => del<{ deleted: boolean }>(`/glossary/${id}`),
  importGlossary: (file: File, courseId: string) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ imported: number; updated: number; skipped: number }>(
      "POST",
      `/glossary/import${qs({ course_id: courseId })}`,
      fd,
    );
  },
  glossaryExportUrl: (courseId: string) => `/api/glossary/export${qs({ course_id: courseId })}`,

  flashcards: (sessionId?: number) => get<Flashcard[]>(`/flashcards${qs({ session_id: sessionId })}`),
  dueFlashcards: (limit = 50) => get<{ cards: Flashcard[]; total_due: number }>(`/flashcards/due${qs({ limit })}`),
  flashcardStats: () => get<{ total: number; due: number; new: number; mature: number }>("/flashcards/stats"),
  review: (id: number, grade: number) => post<Flashcard>(`/flashcards/${id}/review`, { grade }),
  updateFlashcard: (id: number, body: Partial<Flashcard>) => put<Flashcard>(`/flashcards/${id}`, body),
  deleteFlashcard: (id: number) => del<{ deleted: boolean }>(`/flashcards/${id}`),

  search: (q: string, courseId?: number) =>
    get<{ query: string; highlight: [string, string]; results: SearchResult[] }>(
      `/search${qs({ q, course_id: courseId })}`,
    ),

  // --- Lớp học trực tiếp (feature_spec mục 7.2) ---
  confirmConsent: (courseId: number, confirmed = true) => post<Course>(`/courses/${courseId}/consent`, { confirmed }),
  consentMessage: (courseId: number, body: { student_name: string; lecturer?: string | null }) =>
    post<{ subject_it: string; email_it: string; back_translation_vi: string }>(
      `/courses/${courseId}/consent-message`,
      body,
    ),
  roomProfiles: () => get<RoomProfile[]>("/room-profiles"),
  createRoomProfile: (name: string) => post<RoomProfile>("/room-profiles", { name }),
  updateRoomProfile: (id: number, body: Partial<RoomProfile> & { name: string }) =>
    put<RoomProfile>(`/room-profiles/${id}`, body),
  deleteRoomProfile: (id: number) => del<{ deleted: boolean }>(`/room-profiles/${id}`),
  startSoundcheck: (body: { source: "mic_local" | "mic_companion"; device_id?: string | null; room_profile_id?: number | null }) =>
    post<SoundCheck>("/soundcheck", body),
  soundcheck: (id: string) => get<SoundCheck>(`/soundcheck/${id}`),
  cancelSoundcheck: (id: string) => post<SoundCheck>(`/soundcheck/${id}/cancel`),
  boardCaptures: (sessionId: number) => get<BoardCapture[]>(`/sessions/${sessionId}/board-captures`),
  uploadBoardCapture: (sessionId: number, image: Blob, tMs?: number) => {
    const fd = new FormData();
    fd.append("file", image, "board.jpg");
    if (tMs !== undefined) fd.append("t_ms", String(Math.round(tMs)));
    return request<BoardCapture>("POST", `/sessions/${sessionId}/board-captures`, fd);
  },
  retryBoardCapture: (id: number) => post<BoardCapture>(`/board-captures/${id}/retry`),
  deleteBoardCapture: (id: number) => del<{ deleted: boolean }>(`/board-captures/${id}`),
  setSpeaker: (segmentId: number, speaker: Speaker) => patch<Segment>(`/segments/${segmentId}/speaker`, { speaker }),
  markers: (sessionId: number) => get<SessionMarker[]>(`/sessions/${sessionId}/markers`),
  companionPair: (body: { ip?: string | null; session_id?: number | null } = {}) =>
    post<PairInfo>("/companion/pair", body),
  companionStatus: () => get<CompanionStatus>("/companion/status"),
  companionStop: () => post<CompanionStatus>("/companion/stop"),
  companionDevices: () => get<PairedDevice[]>("/companion/devices"),
  revokeDevice: (id: number) => del<{ revoked: boolean }>(`/companion/devices/${id}`),
  speakerModel: () => get<ModelStatus>("/speaker-model"),
  prepareSpeakerModel: () => post<ModelStatus>("/speaker-model/prepare"),
  draftModel: () => get<LocalModelInfo>("/offline/draft"),
  /** Không truyền repo (hoặc repo đang dùng): tải + nạp. Repo khác: chỉ tải về, model đang dùng vẫn chạy. */
  prepareDraftModel: (repo?: string) => post<LocalModelInfo>("/offline/draft/prepare", repo ? { repo } : {}),
  power: () => get<{ battery: BatteryStatus | null }>("/power"),
  inclassStatus: () => get<InClassStatus>("/inclass/status"),
  whisperModelCached: (model: string) => get<{ model: string; cached: boolean | null }>(`/whisper-models/${model}`),
};
