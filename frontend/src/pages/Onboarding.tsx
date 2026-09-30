/** Cài đặt lần đầu (Flow A): API key → STT → môn học → test âm thanh 10 giây → hoàn tất. */
import { ArrowLeft, CircleCheck, Headphones, Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { CaptureError, TabAudioCapture } from "../audio/capture";
import { Button, Card, Field, inputCls } from "../components/ui";
import { VuMeter } from "../components/VuMeter";
import { useT, type MessageKey } from "../i18n";
import { errorMessage, useApp } from "../store/app";
import { ApiKeyForm, ClaudeCodeStatus, SttSetup } from "./Settings";

const STEPS = ["welcome", "key", "stt", "course", "test", "done"] as const;
type Step = (typeof STEPS)[number];
const TEST_SECONDS = 10;

function AudioTest() {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const [phase, setPhase] = useState<"idle" | "recording" | "processing" | "done">("idle");
  const [seconds, setSeconds] = useState(0);
  const [db, setDb] = useState(-120);
  const [result, setResult] = useState<{ text: string; confidence: number; elapsed_ms: number } | null>(null);
  const capRef = useRef<TabAudioCapture | null>(null);

  useEffect(() => () => capRef.current?.stop(), []);

  const run = async () => {
    const frames: ArrayBuffer[] = [];
    const cap = new TabAudioCapture({ onFrame: (pcm) => frames.push(pcm), onLevel: setDb });
    try {
      await cap.start();
    } catch (e) {
      if (e instanceof CaptureError && e.code === "NO_AUDIO") toast("error", t("live.no_audio_track"));
      else if (!(e instanceof CaptureError && e.code === "DENIED")) toast("error", errorMessage(e));
      return;
    }
    capRef.current = cap;
    setResult(null);
    setPhase("recording");
    for (let s = 0; s < TEST_SECONDS; s++) {
      setSeconds(TEST_SECONDS - s);
      await new Promise((r) => window.setTimeout(r, 1000));
    }
    cap.stop();
    capRef.current = null;
    setPhase("processing");
    const total = frames.reduce((n, f) => n + f.byteLength, 0);
    const pcm = new Uint8Array(total);
    let offset = 0;
    for (const f of frames) {
      pcm.set(new Uint8Array(f), offset);
      offset += f.byteLength;
    }
    try {
      setResult(await api.sttTest(pcm.buffer));
    } catch (e) {
      toast("error", errorMessage(e));
    }
    setPhase("done");
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-600 dark:text-slate-300">{t("ob.test_body")}</p>
      <div className="flex items-center gap-3">
        <Button variant="primary" loading={phase === "recording" || phase === "processing"} onClick={run}>
          <Headphones className="h-4 w-4" /> {t("ob.test_start")}
        </Button>
        {phase === "recording" && (
          <>
            <VuMeter db={db} active />
            <span className="text-sm">{t("ob.test_recording", { s: seconds })}</span>
          </>
        )}
        {phase === "processing" && <span className="text-sm">{t("ob.test_processing")}</span>}
      </div>
      {result && (
        <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
          <p className="text-xs font-semibold uppercase text-slate-500">
            {t("ob.test_result")} · {Math.round(result.confidence * 100)}% · {(result.elapsed_ms / 1000).toFixed(1)}s
          </p>
          <p className="mt-1 text-base">🇮🇹 {result.text || <span className="text-slate-500">{t("ob.test_empty")}</span>}</p>
        </div>
      )}
    </div>
  );
}

export default function Onboarding() {
  const t = useT();
  const navigate = useNavigate();
  const toast = useApp((s) => s.toast);
  const health = useApp((s) => s.health);
  const settings = useApp((s) => s.settings);
  const saveSettings = useApp((s) => s.saveSettings);
  const loadHealth = useApp((s) => s.loadHealth);
  const [step, setStep] = useState<Step>("welcome");
  const [course, setCourse] = useState({ name: "", name_it: "", lecturer: "" });
  const [threshold, setThreshold] = useState<string>("3.00");

  useEffect(() => {
    if (settings?.settings.cost_threshold_usd != null) setThreshold(String(settings.settings.cost_threshold_usd));
  }, [settings]);

  const idx = STEPS.indexOf(step);
  const usingPro =
    settings?.settings.translate_provider === "claude_code" && settings?.settings.assistant_provider === "claude_code";
  const keyStepOk = Boolean(health?.api_key.present || (usingPro && health?.translation.ready));
  const next = () => setStep(STEPS[Math.min(STEPS.length - 1, idx + 1)]);
  const back = () => setStep(STEPS[Math.max(0, idx - 1)]);

  const finish = async () => {
    try {
      await saveSettings({
        onboarding_done: true,
        cost_threshold_usd: threshold.trim() === "" ? null : Number(threshold),
      });
      await loadHealth();
      navigate("/");
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <h1 className="text-2xl font-bold tracking-tight">{t("ob.title")}</h1>
      <ol className="flex flex-wrap gap-2 text-xs">
        {STEPS.map((s, i) => (
          <li
            key={s}
            className={`rounded-full px-3 py-1 font-medium ${
              i === idx
                ? "bg-brand-600 text-white"
                : i < idx
                  ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200"
                  : "bg-slate-100 text-slate-500 dark:bg-slate-800"
            }`}
          >
            {i + 1}. {t(`ob.step.${s}` as MessageKey)}
          </li>
        ))}
      </ol>

      <Card className="space-y-4 p-6">
        {step === "welcome" && (
          <>
            <h2 className="text-xl font-semibold">{t("ob.welcome_title")}</h2>
            <p className="text-slate-600 dark:text-slate-300">{t("ob.welcome_body")}</p>
            <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <Lock className="mt-0.5 h-4 w-4 shrink-0" /> {t("ob.welcome_privacy")}
            </p>
          </>
        )}
        {step === "key" && (
          <>
            <h2 className="text-lg font-semibold">{t("ob.key_title")}</h2>
            <ApiKeyForm />
            <div className="space-y-2 border-t border-slate-200 pt-4 dark:border-slate-700">
              <h3 className="font-semibold">{t("ob.pro_title")}</h3>
              <p className="text-sm text-slate-600 dark:text-slate-300">{t("ob.pro_body")}</p>
              {usingPro ? (
                <ClaudeCodeStatus />
              ) : (
                <Button
                  onClick={() =>
                    void saveSettings({ translate_provider: "claude_code", assistant_provider: "claude_code" }).catch((e) =>
                      toast("error", errorMessage(e)),
                    )
                  }
                >
                  {t("ob.pro_use")}
                </Button>
              )}
            </div>
          </>
        )}
        {step === "stt" && (
          <>
            <h2 className="text-lg font-semibold">{t("ob.stt_title")}</h2>
            <p className="text-sm text-slate-600 dark:text-slate-300">
              {health?.gpu.available ? t("ob.stt_recommend_gpu") : t("ob.stt_recommend_cpu")}
            </p>
            {!health?.gpu.available && settings?.settings.whisper_model === "large-v3-turbo" && (
              <Button size="sm" onClick={() => void saveSettings({ whisper_model: "small" }).then(loadHealth)}>
                Whisper small (CPU)
              </Button>
            )}
            <SttSetup compact />
          </>
        )}
        {step === "course" && (
          <>
            <h2 className="text-lg font-semibold">{t("ob.course_title")}</h2>
            <Field label={t("course.name")}>
              <input className={inputCls} value={course.name} placeholder={t("course.name_placeholder")} onChange={(e) => setCourse({ ...course, name: e.target.value })} />
            </Field>
            <Field label={t("course.name_it")}>
              <input className={inputCls} value={course.name_it} placeholder={t("course.name_it_placeholder")} onChange={(e) => setCourse({ ...course, name_it: e.target.value })} />
            </Field>
            <Field label={t("course.lecturer")}>
              <input className={inputCls} value={course.lecturer} placeholder={t("course.lecturer_placeholder")} onChange={(e) => setCourse({ ...course, lecturer: e.target.value })} />
            </Field>
          </>
        )}
        {step === "test" && (
          <>
            <h2 className="text-lg font-semibold">{t("ob.test_title")}</h2>
            <AudioTest />
          </>
        )}
        {step === "done" && (
          <>
            <h2 className="flex items-center gap-2 text-xl font-semibold">
              <CircleCheck className="h-6 w-6 text-emerald-600" /> {t("ob.done_title")}
            </h2>
            <Field label={`${t("ob.budget_title")}: ${t("settings.cost_threshold")}`} hint={t("settings.cost_threshold_hint")}>
              <input type="number" min={0} step={0.1} className={`${inputCls} max-w-[10rem]`} value={threshold} onChange={(e) => setThreshold(e.target.value)} />
            </Field>
          </>
        )}

        <div className="flex justify-between border-t border-slate-200 pt-4 dark:border-slate-700">
          <Button variant="ghost" disabled={idx === 0} onClick={back}>
            <ArrowLeft className="h-4 w-4" /> {t("common.back")}
          </Button>
          <div className="flex gap-2">
            {step !== "done" && step !== "welcome" && (
              <Button variant="ghost" onClick={next}>
                {t("common.skip")}
              </Button>
            )}
            {step === "course" ? (
              <Button
                variant="primary"
                disabled={!course.name.trim()}
                onClick={async () => {
                  try {
                    await api.createCourse({
                      name: course.name.trim(),
                      name_it: course.name_it.trim() || null,
                      lecturer: course.lecturer.trim() || null,
                    });
                    next();
                  } catch (e) {
                    toast("error", errorMessage(e));
                  }
                }}
              >
                {t("common.next")} →
              </Button>
            ) : step === "done" ? (
              <Button variant="primary" onClick={finish}>
                {t("ob.finish")}
              </Button>
            ) : (
              <Button variant="primary" onClick={next} disabled={step === "key" && !keyStepOk}>
                {t("common.next")} →
              </Button>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
