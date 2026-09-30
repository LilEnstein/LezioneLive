/** Chọn model + effort cho trợ lý học tập (API hoặc gói Pro qua Claude Code). Lưu ngay vào cài đặt. */
import { useEffect } from "react";

import type { AppSettings, AssistantEffort } from "../api/types";
import { useT } from "../i18n";
import type { MessageKey } from "../i18n/vi";
import { errorMessage, useApp } from "../store/app";

export const EFFORTS: AssistantEffort[] = ["auto", "low", "medium", "high", "xhigh", "max"];
const KNOWN_CC_MODELS = new Set(["sonnet", "opus", "haiku", "fable"]);
export const DEFAULT_CC_MODELS = ["sonnet", "opus", "haiku", "fable"];

export function modelLabel(t: ReturnType<typeof useT>, model: string): string {
  return KNOWN_CC_MODELS.has(model) ? t(`model.${model}` as MessageKey) : model;
}

const selectCls =
  "min-w-0 rounded-md border border-slate-300 bg-white px-1.5 py-1 text-xs text-slate-800 focus:border-brand-500 focus:outline-none dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";

const inlineSelectCls =
  "min-w-0 max-w-[9rem] cursor-pointer truncate rounded-md border-0 bg-transparent py-1 pl-1.5 pr-1 text-xs font-medium text-slate-600 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-brand-500/30 disabled:cursor-not-allowed disabled:opacity-50 dark:text-slate-300 dark:hover:bg-slate-700";

/**
 * Chọn model trợ lý. Mặc định là thanh riêng; ``inline`` là bản gọn đặt trong ô soạn tin (như bộ chọn model của Claude).
 */
export function AssistantPicker({ disabled = false, inline = false }: { disabled?: boolean; inline?: boolean }) {
  const t = useT();
  const settingsResp = useApp((s) => s.settings);
  const loadSettings = useApp((s) => s.loadSettings);
  const saveSettings = useApp((s) => s.saveSettings);
  const toast = useApp((s) => s.toast);

  useEffect(() => {
    if (!settingsResp) void loadSettings();
  }, [settingsResp, loadSettings]);

  if (!settingsResp) return null;
  const st = settingsResp.settings;
  const viaPro = st.assistant_provider === "claude_code";
  const save = (patch: Partial<AppSettings>) => void saveSettings(patch).catch((e) => toast("error", errorMessage(e)));

  const ccModels = settingsResp.choices.claude_code_models ?? DEFAULT_CC_MODELS;
  const models = viaPro ? ccModels : settingsResp.choices.assistant_models;
  const current = viaPro ? st.assistant_cc_model : st.assistant_model;
  const options = models.includes(current) ? models : [current, ...models];

  if (inline) {
    return (
      <div className="flex min-w-0 items-center" title={viaPro ? t("chat.via_pro") : t("chat.via_api")}>
        <select
          className={inlineSelectCls}
          disabled={disabled}
          value={current}
          aria-label={t("chat.picker")}
          onChange={(e) => save(viaPro ? { assistant_cc_model: e.target.value } : { assistant_model: e.target.value })}
        >
          {options.map((m) => (
            <option key={m} value={m}>
              {viaPro ? modelLabel(t, m) : m}
            </option>
          ))}
        </select>
        {viaPro && (
          <select
            className={inlineSelectCls}
            disabled={disabled}
            value={st.assistant_effort}
            aria-label={t("settings.assistant_effort")}
            onChange={(e) => save({ assistant_effort: e.target.value as AssistantEffort })}
          >
            {EFFORTS.map((ef) => (
              <option key={ef} value={ef}>
                {t(`effort.${ef}` as MessageKey)}
              </option>
            ))}
          </select>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 px-3 py-1.5 text-xs dark:border-slate-700">
      <span className="text-slate-500 dark:text-slate-400">{t("chat.picker")}</span>
      <span
        className={`rounded px-1.5 py-0.5 font-semibold ${viaPro ? "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300" : "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300"}`}
      >
        {viaPro ? t("chat.via_pro") : t("chat.via_api")}
      </span>
      <select
        className={selectCls}
        disabled={disabled}
        value={current}
        title={t("settings.assistant_cc_model")}
        onChange={(e) => save(viaPro ? { assistant_cc_model: e.target.value } : { assistant_model: e.target.value })}
      >
        {options.map((m) => (
          <option key={m} value={m}>
            {viaPro ? modelLabel(t, m) : m}
          </option>
        ))}
      </select>
      {viaPro && (
        <select
          className={selectCls}
          disabled={disabled}
          value={st.assistant_effort}
          title={t("settings.assistant_effort")}
          onChange={(e) => save({ assistant_effort: e.target.value as AssistantEffort })}
        >
          {EFFORTS.map((ef) => (
            <option key={ef} value={ef}>
              effort: {t(`effort.${ef}` as MessageKey)}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
