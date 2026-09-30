/**
 * Khung ứng dụng kiểu Claude / Microsoft Teams: thanh bên trái (thu gọn được thành dải icon) gồm nút
 * "Buổi học mới", điều hướng chính, danh sách buổi học gần đây và trạng thái hệ thống; nội dung trang cuộn riêng.
 * Màn hình hẹp (< md): thanh bên thành ngăn kéo mở bằng nút menu trên thanh đầu trang.
 */
import {
  BookA,
  House,
  Layers,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Radio,
  Search,
  Settings,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Session } from "../api/types";
import { useT, type MessageKey } from "../i18n";
import { useApp } from "../store/app";
import { Badge, IconButton } from "./ui";

export function StatusPills() {
  const t = useT();
  const health = useApp((s) => s.health);
  const healthError = useApp((s) => s.healthError);
  if (healthError) return <Badge tone="red">{t("status.backend_down")}</Badge>;
  if (!health) return null;
  const stt = health.stt;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {health.api_key.present ? (
        <Badge tone={health.translation.auth_failed ? "red" : "green"}>
          {t("status.api_ok", { last4: health.api_key.last4 ?? "" })}
        </Badge>
      ) : (
        <Badge tone="red">{t("status.api_missing")}</Badge>
      )}
      {stt.ready ? (
        <Badge tone="green" title={[stt.model, stt.device, stt.compute_type].filter(Boolean).join(" · ")}>
          {t("status.stt_ready")}
        </Badge>
      ) : stt.state === "loading" ? (
        <Badge tone="amber" title={stt.message}>
          {t("status.stt_loading", { pct: Math.round(stt.progress * 100) })}
        </Badge>
      ) : stt.state === "error" ? (
        <Badge tone="red" title={stt.error ?? ""}>
          {t("status.stt_error")}
        </Badge>
      ) : (
        <Badge tone="slate">{t("status.stt_idle")}</Badge>
      )}
      {!health.network.online && <Badge tone="red">{t("status.offline")}</Badge>}
    </div>
  );
}

/** Màu chấm trạng thái tổng hợp (dùng khi thanh bên thu gọn). */
function useHealthTone(): "green" | "amber" | "red" {
  const health = useApp((s) => s.health);
  const healthError = useApp((s) => s.healthError);
  if (healthError || !health) return "red";
  if (!health.network.online || health.translation.auth_failed || health.stt.state === "error") return "red";
  if (!health.stt.ready || !health.api_key.present) return "amber";
  return "green";
}

const DOT = { green: "bg-emerald-500", amber: "bg-amber-500", red: "bg-red-500" };

const NAV: { to: string; label: MessageKey; icon: LucideIcon; end?: boolean }[] = [
  { to: "/", label: "nav.home", icon: House, end: true },
  { to: "/search", label: "nav.search", icon: Search },
  { to: "/glossary", label: "nav.glossary", icon: BookA },
  { to: "/flashcards", label: "nav.flashcards", icon: Layers },
];

const COLLAPSE_KEY = "lezionelive.sidebar_collapsed";
const RECENT_LIMIT = 12;

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function navItemCls(active: boolean, collapsed: boolean) {
  return `group flex items-center gap-2.5 rounded-lg text-sm transition ${collapsed ? "h-9 w-9 justify-center" : "px-2.5 py-1.5"} ${
    active
      ? "bg-slate-200/80 font-medium text-slate-900 dark:bg-slate-800 dark:text-white"
      : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800/70 dark:hover:text-white"
  }`;
}

function Sidebar({
  collapsed,
  onToggleCollapse,
  onClose,
}: {
  collapsed: boolean;
  /** Không có ở ngăn kéo di động. */
  onToggleCollapse?: () => void;
  /** Chỉ có ở ngăn kéo di động. */
  onClose?: () => void;
}) {
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const tone = useHealthTone();
  const [recent, setRecent] = useState<Session[]>([]);

  // Nạp lại danh sách gần đây mỗi lần đổi trang (buổi học mới, đổi tên, xoá…).
  useEffect(() => {
    let cancelled = false;
    api
      .sessions()
      .then((list) => !cancelled && setRecent(list.slice(0, RECENT_LIMIT)))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Logo + thu gọn */}
      <div className={`flex items-center gap-2 px-3 pb-2 pt-3 ${collapsed ? "flex-col" : ""}`}>
        <Link to="/" className="flex min-w-0 items-center gap-2" title="LezioneLive">
          <img
            src="/mascot.webp"
            alt=""
            className="h-8 w-8 shrink-0 rounded-full shadow-sm ring-1 ring-slate-200 dark:ring-slate-700"
          />
          {!collapsed && (
            <span className="truncate text-[0.95rem] font-semibold tracking-tight">
              Lezione<span className="text-brand-600 dark:text-brand-400">Live</span>
            </span>
          )}
        </Link>
        <div className={collapsed ? "" : "ml-auto"}>
          {onToggleCollapse && (
            <IconButton label={collapsed ? t("nav.expand") : t("nav.collapse")} onClick={onToggleCollapse} size="sm">
              {collapsed ? <PanelLeftOpen className="h-[1.1rem] w-[1.1rem]" /> : <PanelLeftClose className="h-[1.1rem] w-[1.1rem]" />}
            </IconButton>
          )}
          {onClose && (
            <IconButton label={t("common.close")} onClick={onClose} size="sm">
              <X className="h-[1.1rem] w-[1.1rem]" />
            </IconButton>
          )}
        </div>
      </div>

      {/* Buổi học mới + điều hướng */}
      <div className={`space-y-0.5 px-2 ${collapsed ? "flex flex-col items-center" : ""}`}>
        <button
          type="button"
          onClick={() => navigate("/?start=1")}
          title={t("nav.new_session")}
          className={`mb-1.5 flex items-center gap-2.5 rounded-lg text-sm font-medium text-slate-800 transition hover:bg-slate-200/60 dark:text-slate-100 dark:hover:bg-slate-800 ${collapsed ? "h-9 w-9 justify-center" : "w-full px-2 py-1.5"}`}
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white shadow-sm">
            <Plus className="h-4 w-4" strokeWidth={2.25} />
          </span>
          {!collapsed && t("nav.new_session")}
        </button>
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            title={collapsed ? t(label) : undefined}
            className={({ isActive }) => `${navItemCls(isActive, collapsed)} ${collapsed ? "" : "w-full"}`}
          >
            <Icon className="h-[1.1rem] w-[1.1rem]" />
            {!collapsed && <span className="truncate">{t(label)}</span>}
          </NavLink>
        ))}
      </div>

      {/* Buổi học gần đây */}
      <div className="mt-4 min-h-0 flex-1 overflow-y-auto px-2">
        {!collapsed && (
          <>
            <p className="px-2.5 pb-1 text-[0.7rem] font-medium uppercase tracking-wider text-slate-400">
              {t("nav.recents")}
            </p>
            {recent.length === 0 && <p className="px-2.5 py-1.5 text-xs text-slate-400">{t("nav.no_recent")}</p>}
            <ul className="space-y-px">
              {recent.map((s) => {
                const to = s.status === "ended" ? `/sessions/${s.id}` : `/live/${s.id}`;
                const active = location.pathname === `/sessions/${s.id}`;
                return (
                  <li key={s.id}>
                    <Link
                      to={to}
                      title={[s.title, s.course_name].filter(Boolean).join(" · ")}
                      className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm ${
                        active
                          ? "bg-slate-200/80 text-slate-900 dark:bg-slate-800 dark:text-white"
                          : "text-slate-600 hover:bg-slate-200/50 dark:text-slate-300 dark:hover:bg-slate-800/70"
                      }`}
                    >
                      <span className="min-w-0 flex-1 truncate">{s.title}</span>
                      {s.status !== "ended" && (
                        <Radio
                          className={`h-3.5 w-3.5 ${s.status === "live" ? "text-red-500" : "text-amber-500"}`}
                          aria-label={s.status}
                        />
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
            {recent.length >= RECENT_LIMIT && (
              <Link to="/" className="block px-2.5 py-1.5 text-xs font-medium text-brand-600 hover:underline dark:text-brand-400">
                {t("nav.view_all")}
              </Link>
            )}
          </>
        )}
      </div>

      {/* Trạng thái + cài đặt */}
      <div className={`space-y-1 border-t border-slate-200 px-2 py-2 dark:border-slate-800 ${collapsed ? "flex flex-col items-center" : ""}`}>
        {collapsed ? (
          <span className="flex h-9 w-9 items-center justify-center" title={t("nav.status")}>
            <span className={`h-2.5 w-2.5 rounded-full ${DOT[tone]}`} />
          </span>
        ) : (
          <div className="px-1.5 py-1">
            <StatusPills />
          </div>
        )}
        <NavLink
          to="/settings"
          title={collapsed ? t("nav.settings") : undefined}
          className={({ isActive }) => `${navItemCls(isActive, collapsed)} ${collapsed ? "" : "w-full"}`}
        >
          <Settings className="h-[1.1rem] w-[1.1rem]" />
          {!collapsed && <span>{t("nav.settings")}</span>}
        </NavLink>
      </div>
    </div>
  );
}

export function Layout() {
  const t = useT();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => setDrawerOpen(false), [location.pathname, location.search]);

  const toggleCollapse = () =>
    setCollapsed((v) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, v ? "0" : "1");
      } catch {
        /* localStorage không dùng được */
      }
      return !v;
    });

  return (
    <div className="flex h-screen overflow-hidden">
      <aside
        className={`hidden shrink-0 border-r border-slate-200 bg-slate-100/70 transition-[width] duration-200 dark:border-slate-800 dark:bg-slate-900 md:block ${
          collapsed ? "w-[3.75rem]" : "w-64"
        }`}
      >
        <Sidebar collapsed={collapsed} onToggleCollapse={toggleCollapse} />
      </aside>

      {drawerOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-slate-950/40" onClick={() => setDrawerOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85%] border-r border-slate-200 bg-slate-50 shadow-xl dark:border-slate-800 dark:bg-slate-900">
            <Sidebar collapsed={false} onClose={() => setDrawerOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-slate-200 bg-white/90 px-3 py-2 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90 md:hidden">
          <IconButton label={t("nav.menu")} onClick={() => setDrawerOpen(true)}>
            <Menu className="h-5 w-5" />
          </IconButton>
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <img src="/mascot.webp" alt="" className="h-7 w-7 rounded-full" />
            <span>
              Lezione<span className="text-brand-600">Live</span>
            </span>
          </Link>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
