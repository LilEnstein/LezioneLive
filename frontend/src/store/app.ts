import { create } from "zustand";

import { ApiError, api } from "../api/client";
import type { AppSettings, Health, SettingsResponse } from "../api/types";

export type ToastKind = "info" | "success" | "warning" | "error";

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  action?: { label: string; onClick: () => void };
}

interface AppState {
  health: Health | null;
  healthError: boolean;
  settings: SettingsResponse | null;
  lang: "vi" | "en";
  toasts: Toast[];
  loadHealth: () => Promise<Health | null>;
  loadSettings: () => Promise<SettingsResponse | null>;
  saveSettings: (patch: Partial<AppSettings>) => Promise<SettingsResponse>;
  setLang: (lang: "vi" | "en") => void;
  toast: (kind: ToastKind, message: string, opts?: { action?: Toast["action"]; ttl?: number }) => number;
  dismiss: (id: number) => void;
}

let toastSeq = 1;

function initialLang(): "vi" | "en" {
  try {
    const saved = localStorage.getItem("lezionelive.lang");
    if (saved === "vi" || saved === "en") return saved;
  } catch {
    /* localStorage không dùng được */
  }
  return "vi";
}

export const useApp = create<AppState>((set, get) => ({
  health: null,
  healthError: false,
  settings: null,
  lang: initialLang(),
  toasts: [],

  loadHealth: async () => {
    try {
      const health = await api.health();
      set({ health, healthError: false });
      return health;
    } catch {
      set({ healthError: true });
      return null;
    }
  },

  loadSettings: async () => {
    try {
      const settings = await api.settings();
      set({ settings });
      get().setLang(settings.settings.ui_language);
      return settings;
    } catch {
      return null;
    }
  },

  saveSettings: async (patch) => {
    const settings = await api.putSettings(patch);
    set({ settings });
    void get().loadHealth(); // trạng thái trợ lý/dịch (sẵn sàng, model) phụ thuộc cài đặt
    if (patch.ui_language) get().setLang(patch.ui_language);
    return settings;
  },

  setLang: (lang) => {
    try {
      localStorage.setItem("lezionelive.lang", lang);
    } catch {
      /* ignore */
    }
    document.documentElement.lang = lang;
    set({ lang });
  },

  toast: (kind, message, opts) => {
    const id = toastSeq++;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { id, kind, message, action: opts?.action }] }));
    const ttl = opts?.ttl ?? (kind === "error" ? 8000 : 4500);
    if (ttl > 0) window.setTimeout(() => get().dismiss(id), ttl);
    return id;
  },

  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
