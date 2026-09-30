/** Cài đặt (mục 13): API key, model, STT, VAD, chi phí & bảng giá, quyền riêng tư, giao diện. */
import {
  Bot,
  Check,
  CircleCheck,
  CircleDollarSign,
  CircleX,
  Download,
  Globe,
  GraduationCap,
  Hand,
  KeyRound,
  Laptop,
  Lock,
  type LucideIcon,
  Mic,
  Plus,
  RotateCcw,
  Save,
  School,
  Scissors,
  Smartphone,
  TriangleAlert,
  WifiOff,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type {
  AppSettings,
  AssistantEffort,
  AudioDevice,
  MicDevice,
  ModelPricing,
  ModelStatus,
  TranslateProvider,
} from "../api/types";
import { DEFAULT_CC_MODELS, EFFORTS, modelLabel } from "../components/AssistantPicker";
import { CompanionDevices, useCompanionStatus } from "../components/PairPhoneModal";
import { LocalModelList } from "../components/TranslationEngineMenu";
import { Badge, Button, Card, Field, ProgressBar, inputCls } from "../components/ui";
import { useT } from "../i18n";
import type { MessageKey } from "../i18n/vi";
import { errorMessage, useApp } from "../store/app";

const STT_KEYS = [
  "stt_provider",
  "whisper_model",
  "whisper_device",
  "whisper_compute_type",
  "azure_region",
  "deepgram_model",
  "onboarding_done",
] as const;

function Section({ title, icon: Icon, children }: { title: ReactNode; icon?: LucideIcon; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="mb-4 flex items-center gap-2 text-base font-semibold">
        {Icon && (
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-50 text-brand-600 dark:bg-brand-900/40 dark:text-brand-300">
            <Icon className="h-4 w-4" />
          </span>
        )}
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </Card>
  );
}

export function ApiKeyForm({ onSaved }: { onSaved?: () => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const health = useApp((s) => s.health);
  const loadHealth = useApp((s) => s.loadHealth);
  const loadSettings = useApp((s) => s.loadSettings);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const info = health?.api_key;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.putApiKey(key.trim());
      setKey("");
      await Promise.all([loadHealth(), loadSettings()]);
      onSaved?.();
    } catch (e) {
      setError(e instanceof ApiError && e.code === "INVALID_API_KEY" ? t("settings.api_key_invalid") : errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3">
      {info?.present ? (
        <p className="flex flex-wrap items-center gap-1.5 font-medium text-emerald-700 dark:text-emerald-400">
          <CircleCheck className="h-4 w-4" />
          {t("settings.api_key_connected", { last4: info.last4 ?? "" })}
          {info.source === "env" && <span className="ml-2 text-xs font-normal text-slate-500">{t("settings.api_key_env")}</span>}
        </p>
      ) : (
        <p className="text-sm text-slate-500">{t("settings.api_key_missing")}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          className={`${inputCls} min-w-[16rem] flex-1 font-mono`}
          placeholder={t("settings.api_key_placeholder")}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && key.trim() && void save()}
        />
        <Button variant="primary" loading={saving} disabled={!key.trim()} onClick={save}>
          {saving ? t("settings.api_key_checking") : t("settings.api_key_save")}
        </Button>
        {info?.present && info.source === "keychain" && (
          <Button
            variant="ghost"
            onClick={async () => {
              if (!confirm(t("common.confirm_delete"))) return;
              await api.deleteApiKey();
              await loadHealth();
              toast("info", t("settings.api_key_missing"));
            }}
          >
            {t("settings.api_key_delete")}
          </Button>
        )}
      </div>
      {error && (
        <p className="flex items-center gap-1.5 text-sm font-medium text-red-600 dark:text-red-400">
          <CircleX className="h-4 w-4 shrink-0" /> {error}
        </p>
      )}
      <p className="text-xs text-slate-500">{t("settings.api_key_help")}</p>
    </div>
  );
}

export function SttSetup({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const health = useApp((s) => s.health);
  const settings = useApp((s) => s.settings);
  const saveSettings = useApp((s) => s.saveSettings);
  const loadHealth = useApp((s) => s.loadHealth);
  const loadSettings = useApp((s) => s.loadSettings);
  const [sttKey, setSttKey] = useState("");
  if (!settings || !health) return null;
  const s = settings.settings;
  const stt = health.stt;
  const gpu = health.gpu;
  const cloud = s.stt_provider !== "whisper_local";
  const providerName = s.stt_provider === "deepgram" ? "Deepgram" : "Azure Speech";

  const update = async (patch: Partial<AppSettings>) => {
    try {
      await saveSettings(patch);
      await loadHealth();
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  return (
    <div className="space-y-4">
      <p className="text-sm">
        {gpu.available ? (
          <Badge tone="green">{t("settings.gpu", { name: gpu.name ?? "NVIDIA", vram: gpu.vram_mb ?? "?" })}</Badge>
        ) : (
          <span className="text-slate-500">{t("settings.no_gpu")}</span>
        )}
      </p>
      {gpu.available && (gpu.cuda_libs_missing?.length ?? 0) > 0 && !cloud && (
        <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          <p>{t("settings.cuda_missing", { libs: gpu.cuda_libs_missing?.join(", ") ?? "" })}</p>
          <code className="mt-1 block rounded bg-slate-900 px-2 py-1 text-xs text-slate-100">
            backend\.venv\Scripts\pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12&gt;=9,&lt;10"
          </code>
        </div>
      )}
      <Field label={t("settings.stt_provider")}>
        <select className={inputCls} value={s.stt_provider} onChange={(e) => void update({ stt_provider: e.target.value as AppSettings["stt_provider"] })}>
          <option value="whisper_local">{t("settings.stt_whisper")}</option>
          <option value="deepgram">{t("settings.stt_deepgram")}</option>
          <option value="azure">{t("settings.stt_azure")}</option>
        </select>
      </Field>
      {!cloud ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("settings.whisper_model")}>
            <select className={inputCls} value={s.whisper_model} onChange={(e) => void update({ whisper_model: e.target.value })}>
              {settings.choices.whisper_models.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("settings.whisper_device")}>
            <select
              className={inputCls}
              value={s.whisper_device}
              onChange={(e) => void update({ whisper_device: e.target.value as AppSettings["whisper_device"] })}
            >
              <option value="auto">{t("settings.device_auto")}</option>
              <option value="cuda">GPU (CUDA)</option>
              <option value="cpu">CPU</option>
            </select>
          </Field>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {t("settings.cloud_warning", { provider: providerName })}
          </p>
          <Field label={t("settings.stt_key", { provider: providerName })}>
            <div className="flex gap-2">
              <input
                type="password"
                autoComplete="off"
                className={`${inputCls} font-mono`}
                value={sttKey}
                placeholder={settings.stt_keys[s.stt_provider === "deepgram" ? "deepgram" : "azure"] ? "••••••••" : ""}
                onChange={(e) => setSttKey(e.target.value)}
              />
              <Button
                variant="primary"
                disabled={sttKey.trim().length < 8}
                onClick={async () => {
                  try {
                    await api.putSttKey(s.stt_provider === "deepgram" ? "deepgram" : "azure", sttKey.trim());
                    setSttKey("");
                    toast("success", t("settings.stt_key_saved"));
                    await Promise.all([loadSettings(), loadHealth()]);
                  } catch (e) {
                    toast("error", errorMessage(e));
                  }
                }}
              >
                {t("common.save")}
              </Button>
            </div>
          </Field>
          {s.stt_provider === "azure" && (
            <Field label={t("settings.azure_region")}>
              <input className={inputCls} defaultValue={s.azure_region} onBlur={(e) => void update({ azure_region: e.target.value.trim() })} />
            </Field>
          )}
          {s.stt_provider === "deepgram" && !compact && (
            <Field label={t("settings.deepgram_model")}>
              <input className={inputCls} defaultValue={s.deepgram_model} onBlur={(e) => void update({ deepgram_model: e.target.value.trim() })} />
            </Field>
          )}
        </div>
      )}
      <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {stt.ready ? (
            <Badge tone="green">
              <Check className="h-3 w-3" /> {t("status.stt_ready")}
            </Badge>
          ) : stt.state === "loading" ? (
            <Badge tone="amber">{t("status.stt_loading", { pct: Math.round(stt.progress * 100) })}</Badge>
          ) : stt.state === "error" ? (
            <Badge tone="red">{t("status.stt_error")}</Badge>
          ) : (
            <Badge>{t("status.stt_idle")}</Badge>
          )}
          {!cloud && stt.model_cached !== undefined && stt.model_cached !== null && (
            <span className="text-xs text-slate-500">{stt.model_cached ? t("settings.model_cached") : t("settings.model_not_cached")}</span>
          )}
          {[stt.model, stt.device, stt.compute_type].filter(Boolean).length > 0 && (
            <span className="text-xs text-slate-500">{[stt.model, stt.device, stt.compute_type].filter(Boolean).join(" · ")}</span>
          )}
          {!stt.ready && stt.state !== "loading" && (
            <Button size="sm" variant="primary" className="ml-auto" onClick={() => void api.sttPrepare().then(loadHealth)}>
              {t("settings.prepare")}
            </Button>
          )}
        </div>
        {stt.state === "loading" && (
          <>
            <ProgressBar value={stt.progress} />
            <p className="text-xs text-slate-500">{stt.message}</p>
          </>
        )}
        {stt.error && <p className="text-xs text-red-600 dark:text-red-400">{stt.error}</p>}
        {stt.degraded && (
          <p className="flex items-start gap-1 text-xs text-amber-700 dark:text-amber-300">
            <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" /> {stt.degraded}
          </p>
        )}
      </div>
    </div>
  );
}

function ModelTest({ model, effort }: { model: string; effort: AssistantEffort }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const run = () => {
    setBusy(true);
    setResult(null);
    api
      .claudeCodeTest(model, effort)
      .then((r) => {
        setOk(r.ok);
        setResult(
          r.ok
            ? t("settings.cc_test_ok", { model: r.model, s: String(r.seconds ?? "?") })
            : t("settings.cc_test_fail", { error: r.error ?? "?" }),
        );
      })
      .catch((e) => {
        setOk(false);
        setResult(t("settings.cc_test_fail", { error: errorMessage(e) }));
      })
      .finally(() => setBusy(false));
  };
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <Button size="sm" onClick={run} disabled={busy || !model.trim()}>
        {busy ? t("settings.cc_testing") : t("settings.cc_test")}
      </Button>
      {result && (
        <span className={`inline-flex items-center gap-1 ${ok ? "text-green-700 dark:text-green-400" : "text-red-600 dark:text-red-400"}`}>
          {ok ? <CircleCheck className="h-4 w-4 shrink-0" /> : <CircleX className="h-4 w-4 shrink-0" />}
          {result}
        </span>
      )}
    </div>
  );
}

export function ClaudeCodeStatus() {
  const t = useT();
  const [status, setStatus] = useState<Awaited<ReturnType<typeof api.claudeCodeStatus>> | null>(null);
  const [loading, setLoading] = useState(false);
  const check = () => {
    setLoading(true);
    api
      .claudeCodeStatus()
      .then(setStatus)
      .catch(() => setStatus({ installed: false, logged_in: false }))
      .finally(() => setLoading(false));
  };
  useEffect(check, []);

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm dark:border-slate-700 dark:bg-slate-800/50">
      <p className="text-xs text-slate-500 dark:text-slate-400">{t("settings.cc_hint")}</p>
      <div className="flex flex-wrap items-center gap-2">
        {loading || !status ? (
          <span className="text-slate-500">{t("settings.cc_checking")}</span>
        ) : status.logged_in ? (
          <Badge tone="green">
            <Check className="h-3 w-3" /> {t("settings.cc_ok")}
            {status.subscription ? ` (${status.subscription})` : ""}
          </Badge>
        ) : (
          <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400">
            <TriangleAlert className="h-4 w-4 shrink-0" /> {status.installed ? t("settings.cc_login") : t("settings.cc_not_installed")}
          </span>
        )}
        <Button size="sm" onClick={check} disabled={loading}>
          {t("settings.cc_recheck")}
        </Button>
      </div>
    </div>
  );
}

function ModelRow({
  label,
  status,
  onPrepare,
}: {
  label: string;
  status: ModelStatus | null;
  onPrepare: () => void;
}) {
  const t = useT();
  if (!status) return null;
  const busy = status.state === "downloading" || status.state === "loading";
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-slate-600 dark:text-slate-300">{label}:</span>
      {status.cached || status.state === "ready" ? (
        <Badge tone="green">
          <Check className="h-3 w-3" /> {t("settings.model_ready")}
        </Badge>
      ) : busy ? (
        <Badge tone="amber">{t("settings.model_downloading")}</Badge>
      ) : (
        <>
          <Badge>{t("settings.model_missing")}</Badge>
          <Button size="sm" onClick={onPrepare}>
            <Download className="h-3.5 w-3.5" /> {t("settings.model_download")}
          </Button>
        </>
      )}
      {status.error && <span className="text-xs text-red-600">{status.error}</span>}
    </div>
  );
}

/** Mục "Lớp học trực tiếp": micro, khử nhiễu, người nói, quyền riêng tư, điện thoại, dịch nháp ngoại tuyến. */
function InClassSettings({
  form,
  set,
  mics,
}: {
  form: AppSettings;
  set: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => void;
  mics: MicDevice[];
}) {
  const t = useT();
  const health = useApp((s) => s.health);
  const [speaker, setSpeaker] = useState<ModelStatus | null>(null);
  const [draft, setDraft] = useState<ModelStatus | null>(null);
  const { status: companion, refresh } = useCompanionStatus(true, 5000);
  const rnnoise = health?.capabilities.denoise?.rnnoise ?? false;

  const reload = () => {
    api.speakerModel().then(setSpeaker).catch(() => undefined);
    api.draftModel().then(setDraft).catch(() => undefined);
  };
  useEffect(reload, []);
  useEffect(() => {
    const busy = [speaker?.state, draft?.state].some((st) => st === "downloading" || st === "loading");
    if (!busy) return;
    const timer = window.setInterval(reload, 2000);
    return () => window.clearInterval(timer);
  }, [speaker?.state, draft?.state]);

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("settings.mic_device")}>
          <select className={inputCls} value={form.mic_device ?? ""} onChange={(e) => set("mic_device", e.target.value || null)}>
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
        <Field label={t("settings.denoise_default")} hint={
            rnnoise ? (
              <span className="inline-flex items-center gap-1">
                <Check className="h-3 w-3" /> {t("settings.rnnoise_ok")}
              </span>
            ) : (
              t("settings.rnnoise_missing")
            )
          }>
          <select
            className={inputCls}
            value={form.denoise_default}
            onChange={(e) => set("denoise_default", e.target.value as AppSettings["denoise_default"])}
          >
            {(["off", "light", "strong"] as const).map((lv) => (
              <option key={lv} value={lv}>
                {t(`denoise.${lv}`)}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("settings.inclass_vad")}>
          <input
            type="number"
            min={0.1}
            max={0.95}
            step={0.05}
            className={inputCls}
            value={form.inclass_vad_threshold}
            onChange={(e) => set("inclass_vad_threshold", Number(e.target.value))}
          />
        </Field>
        <Field label={t("settings.inclass_min_speech")}>
          <input
            type="number"
            min={200}
            max={3000}
            step={100}
            className={inputCls}
            value={form.inclass_min_speech_ms}
            onChange={(e) => set("inclass_min_speech_ms", Number(e.target.value))}
          />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.speaker_tagging} onChange={(e) => set("speaker_tagging", e.target.checked)} />
        <GraduationCap className="h-4 w-4 text-slate-500" /> {t("settings.speaker_tagging")}
      </label>
      <ModelRow label={t("settings.speaker_model")} status={speaker} onPrepare={() => void api.prepareSpeakerModel().then(setSpeaker)} />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.discard_other_speech} onChange={(e) => set("discard_other_speech", e.target.checked)} />
        <Hand className="h-4 w-4 text-slate-500" /> {t("settings.discard_other")}
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.companion_fallback_mic} onChange={(e) => set("companion_fallback_mic", e.target.checked)} />
        <Laptop className="h-4 w-4 text-slate-500" /> {t("settings.fallback_mic")}
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.browser_audio_processing}
          onChange={(e) => set("browser_audio_processing", e.target.checked)}
        />
        <Smartphone className="h-4 w-4 text-slate-500" /> {t("settings.browser_processing")}
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.offline_draft} onChange={(e) => set("offline_draft", e.target.checked)} />
        <WifiOff className="h-4 w-4 text-slate-500" /> {t("settings.offline_draft")}
      </label>
      {form.offline_draft && (
        <ModelRow label={t("settings.offline_draft")} status={draft} onPrepare={() => void api.prepareDraftModel().then(setDraft)} />
      )}
      <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
        <p className="mb-2 flex items-center gap-1.5 text-sm font-medium">
          <Smartphone className="h-4 w-4" /> {t("settings.companion")}
        </p>
        <CompanionDevices status={companion} onChange={refresh} />
      </div>
    </>
  );
}

function PricingTable({
  pricing,
  defaults,
  onChange,
}: {
  pricing: Record<string, ModelPricing>;
  defaults: Record<string, ModelPricing>;
  onChange: (p: Record<string, ModelPricing>) => void;
}) {
  const t = useT();
  const cols: { key: keyof ModelPricing; label: string }[] = [
    { key: "input", label: t("settings.pricing_input") },
    { key: "output", label: t("settings.pricing_output") },
    { key: "cache_write_5m", label: t("settings.pricing_cw5") },
    { key: "cache_write_1h", label: t("settings.pricing_cw1h") },
    { key: "cache_read", label: t("settings.pricing_cr") },
  ];
  const [newModel, setNewModel] = useState("");
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-1 pr-2">{t("settings.pricing_model")}</th>
              {cols.map((c) => (
                <th key={c.key} className="px-1">
                  {c.label}
                </th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {Object.entries(pricing).map(([model, price]) => (
              <tr key={model}>
                <td className="py-1 pr-2 font-mono text-xs">{model}</td>
                {cols.map((c) => (
                  <td key={c.key} className="px-1">
                    <input
                      type="number"
                      min={0}
                      step="0.01"
                      className={`${inputCls} w-24 py-1 text-right tabular-nums`}
                      value={price[c.key]}
                      onChange={(e) => onChange({ ...pricing, [model]: { ...price, [c.key]: Number(e.target.value) } })}
                    />
                  </td>
                ))}
                <td>
                  <button
                    type="button"
                    className="px-2 text-slate-400 hover:text-red-600"
                    title={t("common.delete")}
                    aria-label={t("common.delete")}
                    onClick={() => {
                      const next = { ...pricing };
                      delete next[model];
                      onChange(next);
                    }}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-2">
        <input className={`${inputCls} max-w-xs font-mono`} placeholder="claude-…" value={newModel} onChange={(e) => setNewModel(e.target.value)} />
        <Button
          size="sm"
          disabled={!newModel.trim()}
          onClick={() => {
            onChange({ ...pricing, [newModel.trim()]: { input: 0, output: 0, cache_write_5m: 0, cache_write_1h: 0, cache_read: 0 } });
            setNewModel("");
          }}
        >
          <Plus className="h-3.5 w-3.5" /> {t("settings.pricing_add")}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onChange({ ...defaults })}>
          <RotateCcw className="h-3.5 w-3.5" /> {t("settings.pricing_reset")}
        </Button>
      </div>
    </div>
  );
}

export default function Settings() {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const settingsResp = useApp((s) => s.settings);
  const health = useApp((s) => s.health);
  const saveSettings = useApp((s) => s.saveSettings);
  const loadSettings = useApp((s) => s.loadSettings);
  const [form, setForm] = useState<AppSettings | null>(null);
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [mics, setMics] = useState<MicDevice[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void loadSettings();
    api
      .audioDevices()
      .then((d) => {
        setDevices(d.devices);
        setMics(d.microphones ?? []);
      })
      .catch(() => undefined);
  }, [loadSettings]);

  useEffect(() => {
    if (settingsResp) setForm(settingsResp.settings);
  }, [settingsResp]);

  if (!form || !settingsResp) return <p className="text-slate-500">{t("common.loading")}</p>;
  const set = <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => setForm({ ...form, [k]: v });

  const save = async () => {
    setSaving(true);
    try {
      // Cài đặt STT được lưu ngay trong SttSetup; không ghi đè bằng giá trị cũ của form.
      const patch: Partial<AppSettings> = { ...form };
      for (const k of STT_KEYS) delete patch[k];
      await saveSettings(patch);
      toast("success", t("settings.saved"));
    } catch (e) {
      toast("error", errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const modelSelect = (
    key: "translate_model" | "assistant_model" | "assistant_cc_model",
    choices: string[],
    label: (m: string) => string = (m) => m,
  ) => (
    <div className="flex gap-2">
      <select className={inputCls} value={choices.includes(form[key]) ? form[key] : "__custom"} onChange={(e) => e.target.value !== "__custom" && set(key, e.target.value)}>
        {choices.map((m) => (
          <option key={m} value={m}>
            {label(m)}
          </option>
        ))}
        <option value="__custom">…</option>
      </select>
      <input className={`${inputCls} font-mono`} value={form[key]} onChange={(e) => set(key, e.target.value)} />
    </div>
  );

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <h1 className="text-2xl font-bold tracking-tight">{t("settings.title")}</h1>

      <Section icon={KeyRound} title={t("settings.api_key")}>
        <ApiKeyForm />
      </Section>

      <Section icon={Bot} title={t("settings.models")}>
        <Field label={t("settings.translate_provider")}>
          <select
            className={inputCls}
            value={form.translate_provider}
            onChange={(e) => set("translate_provider", e.target.value as TranslateProvider)}
          >
            <option value="api">{t("settings.provider_api")}</option>
            <option value="claude_code">{t("settings.provider_claude_code")}</option>
            <option value="local">{t("settings.provider_local")}</option>
          </select>
        </Field>
        {form.translate_provider === "claude_code" && <ClaudeCodeStatus />}
        {form.translate_provider === "local" ? (
          <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
            <p className="text-xs text-slate-500 dark:text-slate-400">{t("settings.local_hint")}</p>
            <LocalModelList activeRepo={form.draft_model} onActivate={(repo) => set("draft_model", repo)} />
          </div>
        ) : (
          <Field label={t("settings.translate_model")}>{modelSelect("translate_model", settingsResp.choices.translate_models)}</Field>
        )}
        <Field label={t("settings.secondary_language")}>
          <select className={inputCls} value={form.secondary_language} onChange={(e) => set("secondary_language", e.target.value as "none" | "en")}>
            <option value="none">{t("settings.secondary_none")}</option>
            <option value="en">{t("settings.secondary_en")}</option>
          </select>
        </Field>
        <Field label={t("settings.slides_mode")}>
          <select className={inputCls} value={form.slides_mode} onChange={(e) => set("slides_mode", e.target.value as "document" | "text")}>
            <option value="document">{t("settings.slides_document")}</option>
            <option value="text">{t("settings.slides_text")}</option>
          </select>
        </Field>
      </Section>

      <Section icon={GraduationCap} title={t("settings.assistant_section")}>
        <p className="text-xs text-slate-500 dark:text-slate-400">{t("settings.assistant_hint")}</p>
        <Field label={t("settings.assistant_provider")}>
          <select
            className={inputCls}
            value={form.assistant_provider}
            onChange={(e) => set("assistant_provider", e.target.value as "api" | "claude_code")}
          >
            <option value="api">{t("settings.provider_api")}</option>
            <option value="claude_code">{t("settings.provider_claude_code")}</option>
          </select>
        </Field>
        {form.assistant_provider === "claude_code" ? (
          <>
            {form.translate_provider !== "claude_code" && <ClaudeCodeStatus />}
            <Field label={t("settings.assistant_cc_model")}>
              {modelSelect("assistant_cc_model", settingsResp.choices.claude_code_models ?? DEFAULT_CC_MODELS, (m) =>
                modelLabel(t, m),
              )}
            </Field>
            <Field label={t("settings.assistant_effort")} hint={t("settings.effort_hint")}>
              <select
                className={inputCls}
                value={form.assistant_effort}
                onChange={(e) => set("assistant_effort", e.target.value as AssistantEffort)}
              >
                {EFFORTS.map((ef) => (
                  <option key={ef} value={ef}>
                    {t(`effort.${ef}` as MessageKey)}
                  </option>
                ))}
              </select>
            </Field>
            <ModelTest model={form.assistant_cc_model} effort={form.assistant_effort} />
          </>
        ) : (
          <Field label={t("settings.assistant_model")}>{modelSelect("assistant_model", settingsResp.choices.assistant_models)}</Field>
        )}
      </Section>

      <Section icon={Mic} title={t("settings.stt")}>
        <SttSetup />
        {health?.capabilities.loopback && (
          <Field label={t("settings.loopback_device")}>
            <select className={inputCls} value={form.loopback_device ?? ""} onChange={(e) => set("loopback_device", e.target.value || null)}>
              <option value="">{t("settings.loopback_default")}</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                  {d.loopback ? " · loopback" : ""}
                </option>
              ))}
            </select>
          </Field>
        )}
      </Section>

      <Section icon={Scissors} title={t("settings.vad")}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("settings.vad_min")}>
            <input type="number" min={500} max={5000} step={100} className={inputCls} value={form.vad_min_ms} onChange={(e) => set("vad_min_ms", Number(e.target.value))} />
          </Field>
          <Field label={t("settings.vad_max")}>
            <input type="number" min={3000} max={30000} step={500} className={inputCls} value={form.vad_max_ms} onChange={(e) => set("vad_max_ms", Number(e.target.value))} />
          </Field>
          <Field label={t("settings.vad_threshold")}>
            <input type="number" min={0.1} max={0.95} step={0.05} className={inputCls} value={form.vad_threshold} onChange={(e) => set("vad_threshold", Number(e.target.value))} />
          </Field>
          <Field label={t("settings.translate_concurrency")}>
            <input type="number" min={1} max={8} className={inputCls} value={form.translate_concurrency} onChange={(e) => set("translate_concurrency", Number(e.target.value))} />
          </Field>
          <Field label={t("settings.context_segments")}>
            <input type="number" min={0} max={10} className={inputCls} value={form.context_segments} onChange={(e) => set("context_segments", Number(e.target.value))} />
          </Field>
        </div>
      </Section>

      <Section icon={CircleDollarSign} title={t("settings.cost")}>
        <Field label={t("settings.cost_threshold")} hint={t("settings.cost_threshold_hint")}>
          <input
            type="number"
            min={0}
            step={0.1}
            className={`${inputCls} max-w-[12rem]`}
            value={form.cost_threshold_usd ?? ""}
            onChange={(e) => set("cost_threshold_usd", e.target.value === "" ? null : Number(e.target.value))}
          />
        </Field>
        <div>
          <p className="mb-2 text-sm font-medium">{t("settings.pricing")}</p>
          <PricingTable pricing={form.pricing} defaults={settingsResp.default_pricing} onChange={(p) => set("pricing", p)} />
        </div>
      </Section>

      <Section icon={School} title={t("settings.inclass")}>
        <InClassSettings form={form} set={set} mics={mics} />
      </Section>

      <Section icon={Lock} title={t("settings.privacy")}>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.save_raw_audio}
            onChange={(e) => {
              // IC-FR-83: bật lưu âm thanh cần xác nhận thêm.
              if (e.target.checked && !confirm(t("settings.save_audio_confirm"))) return;
              set("save_raw_audio", e.target.checked);
            }}
          />
          {t("settings.save_raw_audio")}
        </label>
      </Section>

      <Section icon={Globe} title={t("settings.ui")}>
        <Field label={t("settings.ui_language")}>
          <select className={`${inputCls} max-w-xs`} value={form.ui_language} onChange={(e) => set("ui_language", e.target.value as "vi" | "en")}>
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </Field>
        <Link to="/onboarding" className="text-sm text-brand-600 hover:underline">
          {t("settings.rerun_onboarding")} →
        </Link>
        {health && (
          <p className="text-xs text-slate-400">
            {t("settings.about", { version: health.version, keyring: health.capabilities.keyring_backend })}
          </p>
        )}
      </Section>

      {/* Dính đáy vùng cuộn nội dung (không đè thanh bên). */}
      <div className="sticky bottom-0 z-20 -mx-4 -mb-6 border-t border-slate-200 bg-white/95 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-950/95 md:-mx-8 md:-mb-8">
        <div className="flex justify-end px-4 md:px-8">
          <Button variant="primary" loading={saving} onClick={save}>
            <Save className="h-4 w-4" /> {t("settings.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}
