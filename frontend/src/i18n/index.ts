import { useCallback } from "react";

import { useApp } from "../store/app";
import { en } from "./en";
import { vi, type MessageKey } from "./vi";

export type { MessageKey };
export type Lang = "vi" | "en";

const DICTS: Record<Lang, Record<MessageKey, string>> = { vi, en };

export function translate(lang: Lang, key: MessageKey, vars?: Record<string, string | number>): string {
  const template = DICTS[lang]?.[key] ?? vi[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m));
}

export type TFn = (key: MessageKey, vars?: Record<string, string | number>) => string;

export function useT(): TFn {
  const lang = useApp((s) => s.lang);
  return useCallback((key, vars) => translate(lang, key, vars), [lang]);
}
