export type SegmentStatus =
  | "transcribed"
  | "pending_translation"
  | "translating"
  | "translated"
  | "failed"
  | "redacted";
export type Speaker = "lecturer" | "other" | "unknown";
export type CaptureSource = "tab" | "loopback" | "mic_local" | "mic_companion";
export type DenoiseLevel = "off" | "light" | "strong";
export type SessionStatus = "live" | "paused" | "ended";
export type PinKind = "confused" | "important" | "todo";
export type PostStatus = "none" | "pending" | "summary" | "flashcards" | "done" | "failed";
/** claude | draft (bản nháp tạm khi mất mạng/hết hạn mức) | local (dịch chính bằng model trên máy). */
export type TranslationKind = "claude" | "draft" | "local";
export type TranslateProvider = "api" | "claude_code" | "local";

export interface Segment {
  id: number;
  seq: number;
  t_start_ms: number;
  t_end_ms: number;
  text_it: string;
  text_it_edited: string | null;
  confidence: number;
  text_vi: string | null;
  text_en: string | null;
  status: SegmentStatus;
  error: string | null;
  speaker?: Speaker;
  speaker_score?: number | null;
  snr_db?: number | null;
  translation_kind?: TranslationKind | null;
}

export interface Course {
  id: number;
  name: string;
  name_it: string | null;
  lecturer: string | null;
  created_at: string;
  consent_confirmed_at?: string | null;
  session_count?: number;
}

export interface Session {
  id: number;
  course_id: number | null;
  course_name: string | null;
  title: string;
  started_at: string;
  ended_at: string | null;
  status: SessionStatus;
  audio_mode: CaptureSource;
  capture_source?: CaptureSource;
  inclass?: boolean;
  room_profile_id?: number | null;
  denoise_level?: DenoiseLevel | null;
  battery_saver?: boolean;
  stt_provider: string;
  cost_usd_est: number;
  cost_warned: boolean;
  translation_enabled: boolean;
  active_ms: number;
  post_status: PostStatus;
  post_error: string | null;
  segment_count?: number;
  pin_count?: number;
  has_summary?: boolean;
}

export interface Pin {
  id: number;
  session_id: number;
  segment_id: number | null;
  kind: PinKind;
  note: string | null;
  created_at: string;
}

export interface SlideDeck {
  id: number;
  session_id: number;
  filename: string;
  page_count: number;
  size_bytes: number;
  summary: string | null;
  has_text: boolean;
  uploaded_to_claude: boolean;
  status: "processing" | "ready" | "failed";
  error: string | null;
  created_at: string;
}

export interface ComposeResult {
  question_it: string;
  back_translation_vi: string;
  notes: string;
}

export interface ChatThread {
  id: number;
  session_id: number;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatAttachment {
  id: number;
  url: string;
  thumb_url: string;
  width: number;
  height: number;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  mode: "qa" | "compose_it" | "speak_it";
  thread_id: number | null;
  images?: ChatAttachment[];
  model: string | null;
  created_at: string;
  data?: (ComposeResult & Partial<SpeakResult>) | null;
}

export interface SpeakResult {
  question_it: string;
  pronunciation: string;
  back_translation_vi: string;
  tip: string;
  word_count?: number;
}

export interface GlossaryTerm {
  id: number;
  course_id: number | null;
  term_it: string;
  term_vi: string;
  term_en: string | null;
  note: string | null;
  source: string;
  updated_at: string;
}

export interface GlossarySuggestion {
  id: number;
  session_id: number;
  deck_id: number | null;
  course_id: number | null;
  capture_id?: number | null;
  term_it: string;
  term_vi: string;
  term_en: string | null;
  note: string | null;
  status: string;
}

export interface Flashcard {
  id: number;
  session_id: number | null;
  front_it: string;
  back_vi: string;
  pos: string | null;
  example_it: string | null;
  example_vi: string | null;
  ease: number;
  interval_days: number;
  repetitions: number;
  due_at: string;
  last_reviewed_at: string | null;
  session_title?: string | null;
}

export interface SttStatus {
  provider: string;
  cloud: boolean;
  ready: boolean;
  state: "idle" | "loading" | "ready" | "error";
  progress: number;
  message: string;
  error: string | null;
  model?: string;
  device?: string | null;
  compute_type?: string | null;
  degraded?: string | null;
  model_cached?: boolean | null;
}

export interface GpuInfo {
  available: boolean;
  name: string | null;
  vram_mb: number | null;
  cuda_libs_missing?: string[];
}

export interface KeyInfo {
  present: boolean;
  last4: string | null;
  source: "env" | "keychain" | null;
}

export interface LicenseStatus {
  active: boolean;
  email: string | null;
  author: string;
  contact: string;
}

export interface Health {
  status: string;
  version: string;
  stt: SttStatus;
  gpu: GpuInfo;
  api_key: KeyInfo;
  network: { online: boolean };
  translation: {
    slow: boolean;
    auth_failed: boolean;
    ready?: boolean;
    provider?: TranslateProvider;
    local_model?: ModelStatus;
  };
  assistant?: AssistantInfo;
  capabilities: {
    loopback: boolean;
    microphone?: boolean;
    pdf_export: boolean;
    pdf_engine: string | null;
    keyring_backend: string;
    platform: string;
    denoise?: { rnnoise: boolean; spectral: boolean };
    speaker_model?: boolean;
    draft_model?: string;
    companion_running?: boolean;
  };
  onboarding_done: boolean;
}

export interface ModelPricing {
  input: number;
  output: number;
  cache_write_5m: number;
  cache_write_1h: number;
  cache_read: number;
}

export type AssistantEffort = "auto" | "low" | "medium" | "high" | "xhigh" | "max";

export interface AssistantInfo {
  ready: boolean;
  provider: "api" | "claude_code";
  model: string;
  effort: AssistantEffort;
}

export interface AppSettings {
  translate_model: string;
  translate_provider: TranslateProvider;
  assistant_provider: "api" | "claude_code";
  assistant_cc_model: string;
  assistant_effort: AssistantEffort;
  assistant_model: string;
  secondary_language: "none" | "en";
  stt_provider: "whisper_local" | "deepgram" | "azure";
  whisper_model: string;
  whisper_device: "auto" | "cuda" | "cpu";
  whisper_compute_type: string;
  deepgram_model: string;
  azure_region: string;
  vad_min_ms: number;
  vad_max_ms: number;
  vad_threshold: number;
  translate_concurrency: number;
  context_segments: number;
  cost_threshold_usd: number | null;
  pricing: Record<string, ModelPricing>;
  save_raw_audio: boolean;
  ui_language: "vi" | "en";
  slides_mode: "document" | "text";
  loopback_device: string | null;
  onboarding_done: boolean;
  mic_device: string | null;
  denoise_default: DenoiseLevel;
  inclass_vad_threshold: number;
  inclass_min_speech_ms: number;
  speaker_tagging: boolean;
  discard_other_speech: boolean;
  companion_fallback_mic: boolean;
  browser_audio_processing: boolean;
  offline_draft: boolean;
  draft_model: string;
}

export interface SettingsResponse {
  settings: AppSettings;
  api_key: KeyInfo;
  stt_keys: { deepgram: boolean; azure: boolean };
  choices: {
    translate_models: string[];
    assistant_models: string[];
    whisper_models: string[];
    claude_code_models?: string[];
    local_models?: LocalModelChoice[];
  };
  default_pricing: Record<string, ModelPricing>;
}

export interface RunnerStatus {
  type: "status";
  state: SessionStatus;
  stt_ready: boolean;
  queue_len: number;
  translation_enabled: boolean;
  translation_slow: boolean;
  network_online: boolean;
  api_key_ok: boolean;
  speaking: boolean;
  t_ms: number;
  audio_mode: CaptureSource;
  capture_source?: CaptureSource;
  inclass?: boolean;
  post_status?: PostStatus;
  active_source?: CaptureSource;
  fallback_active?: boolean;
  denoise_level?: DenoiseLevel | null;
  denoise_engine?: string | null;
  battery_saver?: boolean;
  speaker_tagging?: boolean;
  speaker_ready?: boolean;
  room_profile_id?: number | null;
  source_health?: SourceHealth;
  battery?: BatteryStatus | null;
}

export interface SourceHealth {
  type: "source.health";
  source: CaptureSource;
  connected: boolean;
  level_dbfs: number | null;
  snr_db: number | null;
  device: string | null;
  latency_ms: number | null;
  error: string | null;
}

export interface BatteryStatus {
  percent: number;
  plugged: boolean;
  minutes_left: number | null;
  low: boolean;
  critical: boolean;
}

export type Rating = "good" | "fair" | "poor";

export interface RoomProfile {
  id: number;
  name: string;
  noise_floor_dbfs: number | null;
  speech_level_dbfs: number | null;
  snr_db: number | null;
  rating: Rating | null;
  vad_threshold: number;
  min_level_dbfs: number | null;
  recommended_denoise: DenoiseLevel;
  updated_at: string;
}

export interface SoundCheckResult {
  noise_floor_dbfs: number;
  speech_level_dbfs: number | null;
  snr_db: number | null;
  rating: Rating;
  rating_label: string;
  speech_seconds: number;
  clipped: boolean;
  tips: { code: string; text: string }[];
  recommended: { vad_threshold: number; recommended_denoise: DenoiseLevel; min_level_dbfs: number };
}

export interface SoundCheck {
  id: string;
  state: "starting" | "noise" | "speech" | "analyzing" | "done" | "failed" | "cancelled";
  source: CaptureSource;
  room_profile_id: number | null;
  elapsed_s: number;
  total_s: number;
  noise_s: number;
  speech_s: number;
  phase_elapsed_s: number;
  progress: number;
  level_dbfs: number;
  error: string | null;
  result: SoundCheckResult | null;
  room_profile?: RoomProfile;
}

export interface BoardTerm {
  it: string;
  vi: string;
  en: string;
}

export interface BoardCapture {
  id: number;
  session_id: number;
  t_ms: number;
  status: "processing" | "done" | "unreadable" | "failed";
  source: "laptop" | "phone";
  width: number;
  height: number;
  image_url: string;
  thumb_url: string;
  extracted_it: string | null;
  latex: string[];
  terms: BoardTerm[];
  translation_vi: string | null;
  unreadable: string | null;
  error: string | null;
  created_at: string;
}

export interface SessionMarker {
  id: number;
  t_ms: number;
  kind: string;
  message: string;
  created_at?: string;
}

export interface MicDevice {
  id: string;
  name: string;
  default: boolean;
  virtual: boolean;
  channels: number;
}

export interface LanAddress {
  ip: string;
  interface: string;
  primary: boolean;
  hotspot: boolean;
}

export interface CompanionConn {
  conn_id: string;
  device_id: number;
  name: string;
  mode: "mic" | "subtitle" | "both";
  active_mic: boolean;
  streaming: boolean;
  rtt_ms: number | null;
  battery: { level?: number; charging?: boolean } | null;
  connected_s: number;
}

export interface PairedDevice {
  id: number;
  name: string;
  paired_at: string;
  last_seen_at: string | null;
  revoked: boolean;
  online?: boolean;
}

export interface CompanionStatus {
  running: boolean;
  port: number;
  error: string | null;
  addresses: LanAddress[];
  ca_fingerprint: string | null;
  devices: CompanionConn[];
  active_mic: CompanionConn | null;
  target_session_id: number | null;
  sink: string | null;
  paired_devices: PairedDevice[];
}

export interface PairInfo {
  token: string;
  url: string;
  ip: string;
  port: number;
  expires_at: number;
  expires_in: number;
  qr_svg: string;
  ca_url: string;
  local_url: string;
  addresses: LanAddress[];
  server_time: number;
}

export interface ModelStatus {
  model: string;
  label?: string;
  cached: boolean;
  state: "idle" | "downloading" | "loading" | "ready" | "error";
  error: string | null;
  size_mb: number;
  progress?: number | null;
}

export interface LocalModelChoice {
  repo: string;
  label: string;
  size_mb: number | null;
  tier: "fast" | "better" | "custom";
}

/** Model dịch cục bộ trong danh sách chọn (GET /offline/draft). */
export interface LocalModelEntry extends LocalModelChoice {
  cached: boolean;
  active: boolean;
  downloading: boolean;
  progress: number | null;
  download_error: string | null;
}

export interface LocalModelInfo extends ModelStatus {
  models: LocalModelEntry[];
}

export interface InClassStatus {
  denoise_engines: { rnnoise: boolean; spectral: boolean };
  speaker_model: ModelStatus;
  draft_model: ModelStatus;
  battery: BatteryStatus | null;
  companion: CompanionStatus;
}

export interface SessionDetail {
  session: Session;
  course: { id: number; name: string; name_it: string | null; lecturer: string | null } | null;
  segments: Segment[];
  has_more: boolean;
  pins: Pin[];
  slides: SlideDeck[];
  board_captures?: BoardCapture[];
  markers?: SessionMarker[];
  room_profile?: RoomProfile | null;
  runner: RunnerStatus | null;
}

export interface CostBreakdown {
  session_id: number;
  total_usd: number;
  threshold_usd: number | null;
  by_purpose: {
    purpose: string;
    requests: number;
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    cost_usd: number;
    models: string[];
  }[];
}

export interface LatencyStats {
  p50: number | null;
  p95: number | null;
  max: number | null;
}

export interface Metrics {
  segments: number;
  stt_ms: LatencyStats;
  it_latency_ms: LatencyStats;
  vi_latency_ms: LatencyStats;
  targets: { it_p50: number; it_p95: number; vi_p50: number; vi_p95: number };
}

export interface SearchResult {
  segment_id: number;
  session_id: number;
  seq: number;
  t_start_ms: number;
  text_it: string;
  text_vi: string;
  session_title: string;
  session_started_at: string | null;
  course_name: string | null;
  course_id: number | null;
}

export interface AudioDevice {
  id: string;
  name: string;
  loopback: boolean;
  recommended: boolean;
  default: boolean;
}

export type ServerEvent =
  | RunnerStatus
  | { type: "vad.speech_start" | "vad.speech_end"; t_ms: number }
  | {
      type: "segment.transcribed";
      id: number;
      seq: number;
      t_start_ms: number;
      t_end_ms: number;
      text_it: string;
      confidence: number;
      speaker?: Speaker;
      snr_db?: number | null;
    }
  | { type: "segment.translating"; id: number }
  | { type: "segment.translation.delta"; id: number; delta: string }
  | { type: "segment.translated"; id: number; text_vi: string; text_en?: string; translation_kind?: "claude" | "local" }
  | { type: "segment.pending"; id: number }
  | { type: "segment.draft"; id: number; text_vi: string }
  | { type: "segment.speaker"; id: number; speaker: Speaker; score: number | null }
  | { type: "board.processing"; capture: BoardCapture }
  | {
      type: "board.captured";
      id: number;
      t_ms: number;
      thumb_url: string;
      extracted_it: string | null;
      terms: BoardTerm[];
      status: BoardCapture["status"];
      suggestions: number;
      capture: BoardCapture;
    }
  | { type: "board.deleted"; id: number }
  | { type: "marker.created"; marker: SessionMarker }
  | SourceHealth
  | { type: "network.state"; online: boolean; pending_translations: number }
  | {
      type: "companion.connected" | "companion.disconnected" | "companion.mode" | "companion.mic";
      device: CompanionConn;
    }
  | { type: "vad.dropped"; t_start_ms: number; t_end_ms: number; level_dbfs: number; gate_dbfs: number }
  | { type: "segment.failed"; id: number; stage: string; error: string; code?: string }
  | { type: "segment.updated"; segment: Segment }
  | { type: "cost.update"; session_cost_usd: number; threshold_usd: number | null }
  | { type: "warning"; code: string; message: string }
  | { type: "level"; db: number }
  | { type: "pin.created" | "pin.updated"; pin: Pin }
  | { type: "pin.deleted"; id: number }
  | { type: "slides.updated"; deck_id: number; suggestions: number }
  | { type: "post.status"; status: PostStatus; error?: string }
  | { type: "summary.ready" }
  | { type: "flashcards.ready"; count: number }
  | { type: "network"; online: boolean }
  | { type: "translation.slow"; slow: boolean }
  | { type: "pong" };
