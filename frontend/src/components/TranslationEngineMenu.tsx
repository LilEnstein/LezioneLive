/**
 * Chọn nguồn dịch ngay trên màn hình học: gói Claude Pro/Max, Claude API, hoặc model NLLB trên máy (dự phòng miễn phí,
 * không cần mạng). Đổi ở đây là đổi cài đặt chung — buổi sau vẫn giữ lựa chọn.
 */
import {
  BookA,
  Check,
  ChevronDown,
  Download,
  ExternalLink,
  KeyRound,
  Laptop,
  LifeBuoy,
  type LucideIcon,
  RefreshCw,
  RotateCw,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { LocalModelEntry, ModelStatus, TranslateProvider } from "../api/types";
import { useT, type MessageKey } from "../i18n";
import { errorMessage, useApp } from "../store/app";
import { useSession } from "../store/session";
import { Button, Spinner } from "./ui";

const ENGINES: { id: TranslateProvider; icon: LucideIcon; name: MessageKey; desc: MessageKey }[] = [
  { id: "claude_code", icon: Sparkles, name: "engine.claude_code", desc: "engine.claude_code_desc" },
  { id: "api", icon: KeyRound, name: "engine.api", desc: "engine.api_desc" },
  { id: "local", icon: Laptop, name: "engine.local", desc: "engine.local_desc" },
];

export function fmtSize(mb: number | null | undefined, lang: string): string {
  if (!mb) return "";
  if (mb < 1000) return `${mb} MB`;
  return `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(mb / 1000)} GB`;
}

/** Danh sách model NLLB: tải về (có tiến độ), chọn model đang dùng. Model chưa tải thì phải tải trước khi chọn. */
export function LocalModelList({ activeRepo, onActivate }: { activeRepo: string; onActivate: (repo: string) => void }) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const toast = useApp((s) => s.toast);
  const loadHealth = useApp((s) => s.loadHealth);
  const [models, setModels] = useState<LocalModelEntry[] | null>(null);
  const [active, setActive] = useState<ModelStatus | null>(null);

  const refresh = useCallback(async () => {
    try {
      const info = await api.draftModel();
      setModels(info.models);
      setActive(info);
    } catch {
      /* giữ trạng thái cũ */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, activeRepo]);

  const busy = !!models?.some((m) => m.downloading) || active?.state === "loading" || active?.state === "downloading";
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => void refresh(), 1500);
    return () => {
      window.clearInterval(timer);
      void loadHealth(); // tải/nạp xong: cập nhật trạng thái dịch trên màn hình học
    };
  }, [busy, refresh, loadHealth]);

  const download = async (repo: string) => {
    try {
      const info = await api.prepareDraftModel(repo === activeRepo ? undefined : repo);
      setModels(info.models);
      setActive(info);
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  if (!models) return <Spinner className="h-4 w-4 text-slate-400" />;
  return (
    <div className="space-y-1.5">
      {models.map((m) => {
        const inUse = m.repo === activeRepo;
        const loading = inUse && active?.state === "loading";
        return (
          <label
            key={m.repo}
            className={`flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${
              inUse
                ? "border-brand-400 bg-brand-50 dark:border-brand-600 dark:bg-brand-950/40"
                : "border-slate-200 dark:border-slate-700"
            } ${m.cached && !inUse ? "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800" : ""}`}
          >
            <input
              type="radio"
              name="local-model"
              checked={inUse}
              disabled={!m.cached && !inUse}
              onChange={() => onActivate(m.repo)}
            />
            <span className="min-w-0 flex-1">
              <span className="block font-medium text-slate-800 dark:text-slate-100">
                {m.label}
                {inUse && <span className="ml-1 font-normal text-brand-600 dark:text-brand-300">· {t("engine.in_use")}</span>}
              </span>
              <span className="block text-slate-500 dark:text-slate-400">
                {t(`engine.tier_${m.tier}` as MessageKey)}
                {m.size_mb ? ` · ${fmtSize(m.size_mb, lang)}` : ""}
              </span>
              {m.download_error && <span className="block text-red-600 dark:text-red-400">{m.download_error}</span>}
              {inUse && active?.error && !m.download_error && (
                <span className="block text-red-600 dark:text-red-400">{active.error}</span>
              )}
            </span>
            {m.downloading ? (
              <span className="flex shrink-0 items-center gap-1 text-amber-700 dark:text-amber-400">
                <Spinner className="h-3 w-3" />
                {t("engine.downloading", { pct: m.progress != null ? `${Math.round(m.progress * 100)}%` : "" })}
              </span>
            ) : loading ? (
              <span className="flex shrink-0 items-center gap-1 text-slate-500">
                <Spinner className="h-3 w-3" /> {t("engine.loading")}
              </span>
            ) : m.cached ? (
              <span className="inline-flex shrink-0 items-center gap-1 text-emerald-700 dark:text-emerald-400">
                <Check className="h-3.5 w-3.5" /> {t("engine.cached")}
              </span>
            ) : (
              <Button size="sm" onClick={() => void download(m.repo)}>
                <Download className="h-3.5 w-3.5" /> {t("engine.download", { size: fmtSize(m.size_mb, lang) })}
              </Button>
            )}
          </label>
        );
      })}
    </div>
  );
}

export function TranslationEngineMenu({ sessionId, translationEnabled }: { sessionId: number; translationEnabled: boolean }) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const settings = useApp((s) => s.settings?.settings);
  const health = useApp((s) => s.health);
  const saveSettings = useApp((s) => s.saveSettings);
  const loadHealth = useApp((s) => s.loadHealth);
  const toast = useApp((s) => s.toast);
  const machineCount = useSession((s) =>
    s.order.reduce((n, id) => {
      const kind = s.segments[id]?.translation_kind;
      return kind === "draft" || kind === "local" ? n + 1 : n;
    }, 0),
  );
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!settings) return null;
  const engine = settings.translate_provider;
  const local = engine === "local";
  const current = ENGINES.find((e) => e.id === engine) ?? ENGINES[0];
  const failing = !local && (health?.translation.auth_failed ?? false);
  const notReady = health?.translation.ready === false;
  const localModel = health?.translation.local_model;
  const shortName = (id: TranslateProvider) =>
    id === "local" ? t("engine.local_short") : t(ENGINES.find((e) => e.id === id)!.name);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast("error", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const choose = (id: TranslateProvider) =>
    run(async () => {
      if (id === engine) return;
      await saveSettings({ translate_provider: id });
      toast("success", t("engine.switched", { name: shortName(id) }));
    });

  return (
    <div ref={rootRef} className="relative">
      <Button
        size="sm"
        variant={open ? "primary" : "secondary"}
        onClick={() => setOpen((v) => !v)}
        title={t("engine.button_title")}
        aria-expanded={open}
      >
        <span className={`h-2 w-2 shrink-0 rounded-full ${failing || notReady ? "bg-amber-500" : "bg-emerald-500"}`} />
        <current.icon className="h-4 w-4" /> <span className="hidden md:inline">{shortName(engine)}</span>
        <ChevronDown className={`h-3.5 w-3.5 opacity-60 transition ${open ? "rotate-180" : ""}`} />
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1.5 w-[23rem] max-w-[calc(100vw-1rem)] space-y-3 rounded-xl border border-slate-200 bg-white p-3 text-slate-800 shadow-xl dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{t("engine.title")}</p>
          <div className="space-y-1">
            {ENGINES.map((e) => (
              <label
                key={e.id}
                className={`flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 ${
                  e.id === engine
                    ? "bg-brand-50 ring-1 ring-brand-300 dark:bg-brand-950/40 dark:ring-brand-700"
                    : "hover:bg-slate-50 dark:hover:bg-slate-800"
                }`}
              >
                <input
                  type="radio"
                  name="translate-engine"
                  className="mt-1"
                  checked={e.id === engine}
                  disabled={busy}
                  onChange={() => void choose(e.id)}
                />
                <e.icon className="mt-0.5 h-4 w-4 shrink-0 text-brand-600 dark:text-brand-400" aria-hidden />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{t(e.name)}</span>
                  <span className="block text-xs text-slate-500 dark:text-slate-400">{t(e.desc)}</span>
                </span>
              </label>
            ))}
          </div>

          {local ? (
            <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
              <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{t("engine.models")}</p>
              <LocalModelList
                activeRepo={settings.draft_model}
                onActivate={(repo) => void run(() => saveSettings({ draft_model: repo }))}
              />
              <p className="flex items-start gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                <BookA className="mt-px h-3.5 w-3.5 shrink-0" /> {t("engine.glossary_hint")}
              </p>
            </div>
          ) : (
            <div className="space-y-2 border-t border-slate-200 pt-3 text-xs dark:border-slate-700">
              {failing ? (
                <div className="rounded-lg bg-amber-50 p-2.5 text-amber-900 dark:bg-amber-950/50 dark:text-amber-200">
                  <TriangleAlert className="mr-1 inline h-4 w-4 align-text-bottom" />
                  {t("engine.claude_failing")}
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <Button size="sm" disabled={busy} onClick={() => void run(async () => {
                      await api.retryFailed(sessionId);
                      await loadHealth();
                    })}>
                      <RotateCw className="h-3.5 w-3.5" /> {t("engine.retry_claude")}
                    </Button>
                    <Button size="sm" variant="primary" disabled={busy} onClick={() => void choose("local")}>
                      <Laptop className="h-3.5 w-3.5" /> {t("live.use_local")}
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="flex items-start gap-1.5 text-slate-500 dark:text-slate-400">
                  <LifeBuoy className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {localModel?.cached ? t("engine.fallback_note") : t("engine.fallback_need_model")}
                </p>
              )}
              {localModel && !localModel.cached && (
                <Button
                  size="sm"
                  disabled={busy || localModel.state === "downloading"}
                  onClick={() => void run(async () => {
                    await api.prepareDraftModel();
                    await loadHealth();
                  })}
                >
                  {localModel.state === "downloading" ? (
                    <>
                      <Spinner className="h-3 w-3" />
                      {t("engine.downloading", {
                        pct: localModel.progress != null ? `${Math.round(localModel.progress * 100)}%` : "",
                      })}
                    </>
                  ) : (
                    <>
                      <Download className="h-3.5 w-3.5" /> {t("engine.download", { size: fmtSize(localModel.size_mb, lang) })}
                    </>
                  )}
                </Button>
              )}
              {machineCount > 0 && (
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => void run(async () => {
                    const r = await api.retranslate(sessionId);
                    toast("info", t("engine.retranslate_done", { n: r.requeued }));
                    await loadHealth();
                  })}
                >
                  <RefreshCw className="h-3.5 w-3.5" /> {t("engine.retranslate", { n: machineCount })}
                </Button>
              )}
            </div>
          )}

          <div className="flex items-center justify-between gap-2 border-t border-slate-200 pt-2.5 text-xs dark:border-slate-700">
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={translationEnabled}
                onChange={(e) => void run(() => api.setTranslation(sessionId, e.target.checked))}
              />
              {t("engine.translation_on")}
            </label>
            {/* Mở tab mới: rời màn hình học sẽ dừng bắt âm thanh. */}
            <a href="/settings" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-brand-600 hover:underline dark:text-brand-300">
              {t("engine.more_settings")} <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
