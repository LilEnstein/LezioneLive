/**
 * Chuẩn bị buổi học trực tiếp (Flow A, mục 10.1 feature_spec): xin phép → nguồn âm thanh (micro laptop / điện thoại)
 * → phòng học (room_profile) → sound check 30 giây (ồn nền, SNR, đánh giá, gợi ý) → tuỳ chọn → bắt đầu.
 */
import {
  ArrowLeft,
  ArrowRight,
  AudioLines,
  Check,
  CircleStop,
  GraduationCap,
  Hand,
  Hourglass,
  Laptop,
  Leaf,
  Lightbulb,
  Lock,
  Mic,
  Play,
  Plus,
  School,
  Smartphone,
  TriangleAlert,
  VolumeX,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { BatteryStatus, Course, DenoiseLevel, InClassStatus, MicDevice, Rating, RoomProfile, SoundCheck } from "../api/types";
import { ConsentPanel } from "../components/ConsentPanel";
import { CompanionDevices, PairPhoneModal, useCompanionStatus } from "../components/PairPhoneModal";
import { SnrGauge, snrTone } from "../components/SnrGauge";
import { Badge, Button, Card, Field, ProgressBar, inputCls } from "../components/ui";
import { VuMeter } from "../components/VuMeter";
import { useT, type MessageKey } from "../i18n";
import { errorMessage, useApp } from "../store/app";

type Source = "mic_local" | "mic_companion";
const RUNNING = new Set<SoundCheck["state"]>(["starting", "noise", "speech", "analyzing"]);
const ROOM_KEY = "lezionelive.room.";
const RATING_DOT: Record<Rating, string> = { good: "bg-emerald-500", fair: "bg-amber-400", poor: "bg-red-500" };

function Step({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="mb-3 text-base font-semibold">{title}</h2>
      <div className="space-y-3">{children}</div>
    </Card>
  );
}

function fmtDb(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v)}`;
}

export default function SoundCheckPage() {
  const t = useT();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const toast = useApp((s) => s.toast);
  const health = useApp((s) => s.health);
  const settings = useApp((s) => s.settings?.settings);
  const loadHealth = useApp((s) => s.loadHealth);
  const courseId = Number(params.get("course"));
  const [title, setTitle] = useState(params.get("title") ?? "");

  const [course, setCourse] = useState<Course | null>(null);
  const [source, setSource] = useState<Source>("mic_local");
  const [mics, setMics] = useState<MicDevice[]>([]);
  const [micDevice, setMicDevice] = useState<string>("");
  const [rooms, setRooms] = useState<RoomProfile[]>([]);
  const [roomId, setRoomId] = useState<number | null>(null);
  const [newRoom, setNewRoom] = useState("");
  const [check, setCheck] = useState<SoundCheck | null>(null);
  const [denoise, setDenoise] = useState<DenoiseLevel>("light");
  const [batterySaver, setBatterySaver] = useState(false);
  const [battery, setBattery] = useState<BatteryStatus | null>(null);
  const [status, setStatus] = useState<InClassStatus | null>(null);
  const [pairOpen, setPairOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const { status: companion, refresh: refreshCompanion } = useCompanionStatus(source === "mic_companion");

  // ---------------------------------------------------------------- dữ liệu
  useEffect(() => {
    api
      .courses()
      .then((cs) => setCourse(cs.find((c) => c.id === courseId) ?? null))
      .catch((e) => toast("error", errorMessage(e)));
    api
      .audioDevices()
      .then((d) => setMics(d.microphones ?? []))
      .catch(() => undefined);
    api.power().then((p) => setBattery(p.battery)).catch(() => undefined);
    api
      .inclassStatus()
      .then((st) => {
        setStatus(st);
        // Tải sẵn model nhận diện giọng (~26 MB) khi còn mạng, trước khi vào lớp.
        if (!st.speaker_model.cached) void api.prepareSpeakerModel().catch(() => undefined);
      })
      .catch(() => undefined);
    api
      .roomProfiles()
      .then((rs) => {
        setRooms(rs);
        let last: string | null = null;
        try {
          last = localStorage.getItem(ROOM_KEY + courseId);
        } catch {
          /* ignore */
        }
        const found = rs.find((r) => String(r.id) === last);
        if (found) setRoomId(found.id);
      })
      .catch(() => undefined);
  }, [courseId, toast]);

  useEffect(() => {
    if (settings) {
      setMicDevice((prev) => prev || settings.mic_device || "");
      setDenoise(settings.denoise_default);
    }
  }, [settings]);

  const room = useMemo(() => rooms.find((r) => r.id === roomId) ?? null, [rooms, roomId]);

  useEffect(() => {
    if (room) setDenoise(room.recommended_denoise);
    try {
      if (room) localStorage.setItem(ROOM_KEY + courseId, String(room.id));
    } catch {
      /* ignore */
    }
  }, [room, courseId]);

  useEffect(() => {
    if (battery && !battery.plugged && battery.percent < 50) setBatterySaver(true);
  }, [battery]);

  // ------------------------------------------------------------ sound check
  const stopPolling = () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    pollRef.current = null;
  };
  useEffect(() => stopPolling, []);

  const startCheck = async () => {
    stopPolling();
    try {
      const started = await api.startSoundcheck({
        source,
        device_id: source === "mic_local" ? micDevice || null : null,
        room_profile_id: roomId,
      });
      setCheck(started);
      pollRef.current = window.setInterval(async () => {
        try {
          const next = await api.soundcheck(started.id);
          setCheck(next);
          if (!RUNNING.has(next.state)) {
            stopPolling();
            if (next.result) setDenoise(next.result.recommended.recommended_denoise);
            if (next.room_profile) setRooms((prev) => prev.map((r) => (r.id === next.room_profile?.id ? next.room_profile : r)));
          }
        } catch {
          stopPolling();
        }
      }, 400);
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  const createRoom = async () => {
    const name = newRoom.trim();
    if (!name) return;
    try {
      const created = await api.createRoomProfile(name);
      setRooms((prev) => [created, ...prev]);
      setRoomId(created.id);
      setNewRoom("");
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  // ---------------------------------------------------------------- bắt đầu
  const start = useCallback(
    async (confirmAudio = false) => {
      setStarting(true);
      setStartError(null);
      try {
        const res = await api.createSession({
          course_id: courseId,
          title: title.trim() || undefined,
          audio_mode: source,
          room_profile_id: roomId,
          denoise_level: denoise,
          battery_saver: batterySaver,
          mic_device: source === "mic_local" ? micDevice || null : null,
          confirm_save_audio: confirmAudio,
        });
        navigate(`/live/${res.session_id}`);
      } catch (e) {
        if (e instanceof ApiError && e.code === "AUDIO_SAVE_CONFIRM_REQUIRED") {
          if (confirm(t("sc.confirm_audio"))) await start(true);
          return;
        }
        if (e instanceof ApiError && e.code === "STT_NOT_READY") void loadHealth();
        setStartError(errorMessage(e));
      } finally {
        setStarting(false);
      }
    },
    [batterySaver, courseId, denoise, loadHealth, micDevice, navigate, roomId, source, t, title],
  );

  if (!courseId) {
    return (
      <p className="text-slate-500">
        {t("start.inclass_need_course")}{" "}
        <Link to="/" className="text-brand-600 underline">
          {t("nav.home")}
        </Link>
      </p>
    );
  }

  const running = check !== null && RUNNING.has(check.state);
  const result = check?.state === "done" ? check.result : null;
  const phoneMic = companion?.active_mic ?? null;
  const consentOk = Boolean(course?.consent_confirmed_at);
  const sttReady = health?.stt.ready ?? false;
  const canCheck = !running && (source === "mic_local" || phoneMic !== null || (companion?.devices.some((d) => d.mode !== "subtitle") ?? false));

  return (
    <div className="mx-auto max-w-3xl space-y-5 pb-16">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-900 dark:hover:text-white">
          <ArrowLeft className="h-4 w-4" /> {t("common.back")}
        </Link>
        <h1 className="flex flex-1 items-center gap-2 text-2xl font-bold tracking-tight">
          <School className="h-6 w-6 text-brand-600" /> {t("sc.title")}
        </h1>
      </div>
      {course && (
        <p className="-mt-3 text-sm text-slate-500">
          {course.name}
          {course.name_it ? ` · ${course.name_it}` : ""}
          {course.lecturer ? ` · ${course.lecturer}` : ""}
        </p>
      )}

      {course && !consentOk && <ConsentPanel course={course} onChange={setCourse} />}

      <Step title={t("sc.step_source")}>
        <div className="grid gap-2 sm:grid-cols-2">
          {(["mic_local", "mic_companion"] as const).map((s) => (
            <label
              key={s}
              className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${
                source === s ? "border-brand-500 bg-brand-50/60 dark:bg-brand-950/30" : "border-slate-200 dark:border-slate-700"
              }`}
            >
              <input type="radio" name="source" checked={source === s} disabled={running} onChange={() => setSource(s)} />
              <span>
                <span className="flex items-center gap-1.5 text-sm font-medium">
                  {s === "mic_local" ? <Laptop className="h-4 w-4 text-brand-600" /> : <Smartphone className="h-4 w-4 text-brand-600" />}
                  {t(s === "mic_local" ? "sc.src_laptop" : "sc.src_phone")}
                </span>
                <span className="block text-xs text-slate-500">{t(s === "mic_local" ? "sc.src_laptop_hint" : "sc.src_phone_hint")}</span>
              </span>
            </label>
          ))}
        </div>
        {source === "mic_local" ? (
          <Field label={t("sc.device")}>
            <select className={inputCls} value={micDevice} disabled={running} onChange={(e) => setMicDevice(e.target.value)}>
              <option value="">{t("sc.device_default")}</option>
              {mics.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.default ? "★ " : ""}
                  {m.name}
                  {m.virtual ? ` (${t("sc.virtual")})` : ""}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" onClick={() => setPairOpen(true)}>
                <Smartphone className="h-4 w-4" /> {t("sc.pair_phone")}
              </Button>
              {phoneMic ? (
                <Badge tone="green">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> {t("sc.phone_connected", { name: phoneMic.name })}
                </Badge>
              ) : (
                <span className="text-sm text-slate-500">{t("sc.phone_waiting")}</span>
              )}
            </div>
            <CompanionDevices status={companion} onChange={refreshCompanion} />
          </div>
        )}
      </Step>

      <Step title={t("sc.step_room")}>
        <div className="flex flex-wrap gap-2">
          <select
            className={`${inputCls} max-w-xs`}
            value={roomId ?? ""}
            disabled={running}
            onChange={(e) => setRoomId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{t("sc.room_none")}</option>
            {rooms.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <input
            className={`${inputCls} max-w-xs`}
            placeholder={t("sc.room_name_placeholder")}
            value={newRoom}
            onChange={(e) => setNewRoom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void createRoom()}
          />
          <Button disabled={!newRoom.trim()} onClick={createRoom}>
            <Plus className="h-4 w-4" /> {t("sc.room_new")}
          </Button>
        </div>
        {room?.rating && (
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {t("sc.room_last", {
              rating: t(`sc.rating.${room.rating}` as MessageKey),
              snr: fmtDb(room.snr_db),
              noise: fmtDb(room.noise_floor_dbfs),
            })}
          </p>
        )}
        {room?.rating && !result && <p className="text-xs text-emerald-700 dark:text-emerald-400">{t("sc.room_skip_hint")}</p>}
      </Step>

      <Step title={t("sc.step_check")}>
        <div className="flex flex-wrap items-center gap-3">
          {running ? (
            <Button variant="danger" onClick={() => check && void api.cancelSoundcheck(check.id).then(setCheck)}>
              <CircleStop className="h-4 w-4" /> {t("sc.cancel_check")}
            </Button>
          ) : (
            <Button variant="primary" disabled={!canCheck} onClick={startCheck}>
              <Mic className="h-4 w-4" /> {check ? t("sc.recheck") : t("sc.start_check")}
            </Button>
          )}
          {running && check && (
            <span className="flex items-center gap-2 text-sm">
              {t("sc.level")} <VuMeter db={check.level_dbfs} active />
              <span className="font-mono text-xs tabular-nums text-slate-500">{Math.round(check.level_dbfs)} dBFS</span>
            </span>
          )}
        </div>
        {check && running && (
          <div className="space-y-3">
            <div>
              <p className={`mb-1 flex items-center gap-1.5 text-sm ${check.state === "noise" ? "font-semibold" : "text-slate-500"}`}>
                <VolumeX className="h-3.5 w-3.5" /> {t("sc.phase_noise")} {Math.min(check.noise_s, Math.floor(check.elapsed_s))}/{check.noise_s}s
              </p>
              <ProgressBar value={Math.min(1, check.elapsed_s / check.noise_s)} />
            </div>
            <div>
              <p className={`mb-1 flex items-center gap-1.5 text-sm ${check.state === "speech" ? "font-semibold" : "text-slate-500"}`}>
                <AudioLines className="h-3.5 w-3.5" /> {t("sc.phase_speech")} {Math.max(0, Math.floor(check.elapsed_s - check.noise_s))}/{check.speech_s}s
              </p>
              <ProgressBar value={Math.max(0, Math.min(1, (check.elapsed_s - check.noise_s) / check.speech_s))} />
            </div>
            {check.state === "analyzing" && <p className="text-sm text-slate-500">{t("sc.analyzing")}</p>}
          </div>
        )}
        {check?.state === "failed" && (
          <p className="flex items-start gap-1.5 rounded-lg bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/50 dark:text-red-200">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {t("sc.failed", { error: check.error ?? "" })}
          </p>
        )}
        {result && (
          <div
            className={`space-y-3 rounded-xl border p-4 ${
              snrTone(result.snr_db) === "green"
                ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-800 dark:bg-emerald-950/30"
                : snrTone(result.snr_db) === "amber"
                  ? "border-amber-300 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/30"
                  : "border-red-300 bg-red-50/60 dark:border-red-800 dark:bg-red-950/30"
            }`}
          >
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">{t("sc.noise_floor")}</p>
                <p className="font-mono text-lg tabular-nums">{fmtDb(result.noise_floor_dbfs)} dBFS</p>
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">{t("sc.snr")}</p>
                <SnrGauge snr={result.snr_db} />
              </div>
              <div>
                <p className="text-xs uppercase tracking-wide text-slate-500">{t("sc.rating")}</p>
                <p className="flex items-center gap-2 text-lg font-semibold">
                  <span className={`h-2.5 w-2.5 rounded-full ${RATING_DOT[result.rating] ?? "bg-slate-400"}`} />
                  {t(`sc.rating.${result.rating}` as MessageKey)}
                </p>
              </div>
            </div>
            {result.tips.length > 0 && (
              <div>
                <p className="mb-1 flex items-center gap-1.5 text-sm font-semibold">
                  <Lightbulb className="h-4 w-4 text-amber-500" /> {t("sc.tips")}
                </p>
                <ul className="list-disc space-y-0.5 pl-5 text-sm">
                  {result.tips.map((tip) => (
                    <li key={tip.code}>{t(`tip.${tip.code}` as MessageKey) || tip.text}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Step>

      <Step title={t("sc.step_options")}>
        <Field label={t("sc.denoise")}>
          <div className="flex flex-wrap gap-2">
            {(["off", "light", "strong"] as const).map((lv) => (
              <label
                key={lv}
                className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm ${
                  denoise === lv ? "border-brand-500 bg-brand-50 dark:bg-brand-950/40" : "border-slate-200 dark:border-slate-700"
                }`}
              >
                <input type="radio" name="denoise" checked={denoise === lv} onChange={() => setDenoise(lv)} />
                {t(`denoise.${lv}`)}
                {result?.recommended.recommended_denoise === lv && <Badge tone="green">{t("sc.recommended")}</Badge>}
              </label>
            ))}
          </div>
        </Field>
        {status && (
          <p className="flex items-center gap-1 text-xs text-slate-500">
            {status.denoise_engines.rnnoise && <Check className="h-3 w-3" />}
            {status.denoise_engines.rnnoise ? t("settings.rnnoise_ok") : t("settings.rnnoise_missing")}
          </p>
        )}
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-0.5" checked={batterySaver} onChange={(e) => setBatterySaver(e.target.checked)} />
          <span>
            <Leaf className="mr-1 inline h-3.5 w-3.5 text-emerald-600" />
            {t("sc.battery_saver")}
            {battery && (
              <span className="ml-2 text-xs text-slate-500">
                {t("sc.battery", { pct: battery.percent })}
                {battery.plugged ? ` · ${t("sc.plugged")}` : ""}
              </span>
            )}
          </span>
        </label>
        {settings && (
          <ul className="space-y-0.5 text-xs text-slate-500">
            <li className="flex items-center gap-1.5">
              <GraduationCap className="h-3.5 w-3.5" /> {t("sc.speaker_tagging")}: {settings.speaker_tagging ? t("sc.on") : t("sc.off")}
            </li>
            <li className="flex items-center gap-1.5">
              <Hand className="h-3.5 w-3.5" /> {t("sc.discard_other")}: {settings.discard_other_speech ? t("sc.on") : t("sc.off")}
            </li>
            {source === "mic_companion" && (
              <li className="flex items-center gap-1.5">
                <Laptop className="h-3.5 w-3.5" /> {t("sc.fallback")}: {settings.companion_fallback_mic ? t("sc.on") : t("sc.off")}
              </li>
            )}
            <li>
              <Link to="/settings" className="text-brand-600 hover:underline">
                {t("sc.settings_link")} →
              </Link>
            </li>
          </ul>
        )}
        <Field label={`${t("start.session_title")} (${t("common.optional")})`}>
          <input className={inputCls} value={title} placeholder={t("start.session_title_placeholder")} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <p className="flex items-start gap-1.5 text-xs text-slate-500">
          <Lock className="mt-px h-3.5 w-3.5 shrink-0" /> {t("sc.privacy_note")}
        </p>
        {startError && (
          <p className="flex items-start gap-1.5 rounded-lg bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950/50 dark:text-red-200">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {startError}
          </p>
        )}
        {!sttReady && (
          <p className="flex items-center gap-1.5 text-sm text-amber-700 dark:text-amber-300">
            <Hourglass className="h-4 w-4" /> {t("start.stt_not_ready")}
          </p>
        )}
        <div className="flex justify-end">
          <Button
            variant="primary"
            className="px-5 py-2.5 text-base"
            loading={starting}
            disabled={!consentOk || running || !sttReady || (source === "mic_companion" && !phoneMic && !companion?.devices.length)}
            onClick={() => void start()}
          >
            <Play className="h-4 w-4" /> {t("sc.start")} <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </Step>

      <PairPhoneModal open={pairOpen} onClose={() => setPairOpen(false)} />
    </div>
  );
}
