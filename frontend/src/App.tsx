import { Unplug } from "lucide-react";
import { useEffect, useRef } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Layout } from "./components/Layout";
import { Toasts } from "./components/Toasts";
import { Spinner } from "./components/ui";
import { useT } from "./i18n";
import Flashcards from "./pages/Flashcards";
import Glossary from "./pages/Glossary";
import Home from "./pages/Home";
import LiveSession from "./pages/LiveSession";
import Onboarding from "./pages/Onboarding";
import Search from "./pages/Search";
import SessionReview from "./pages/SessionReview";
import Settings from "./pages/Settings";
import SoundCheck from "./pages/SoundCheck";
import { useApp } from "./store/app";

/** Flow A bước B: backend chưa chạy → hiện hướng dẫn và tự thử lại. */
function BackendDown() {
  const t = useT();
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="max-w-lg space-y-4 rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-700 dark:bg-slate-900">
        <h1 className="flex items-center gap-2 text-xl font-bold">
          <Unplug className="h-6 w-6 text-red-500" /> {t("gate.title")}
        </h1>
        <p className="text-sm text-slate-600 dark:text-slate-300">{t("gate.body")}</p>
        <pre className="overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
          {
            "# Windows: nháy đúp scripts\\start.bat\n# hoặc PowerShell:\npowershell -ExecutionPolicy Bypass -File scripts\\start.ps1\n\n# macOS / Linux\nbash scripts/start.sh"
          }
        </pre>
        <p className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner /> {t("gate.retrying")}
        </p>
      </div>
    </div>
  );
}

/** Lần đầu mở app (chưa hoàn tất onboarding) → chuyển tới hướng dẫn cài đặt. */
function OnboardingRedirect() {
  const health = useApp((s) => s.health);
  const navigate = useNavigate();
  const location = useLocation();
  const done = useRef(false);
  useEffect(() => {
    if (done.current || !health) return;
    done.current = true;
    if (!health.onboarding_done && location.pathname === "/") navigate("/onboarding", { replace: true });
  }, [health, location.pathname, navigate]);
  return null;
}

export default function App() {
  const health = useApp((s) => s.health);
  const healthError = useApp((s) => s.healthError);
  const loadHealth = useApp((s) => s.loadHealth);
  const loadSettings = useApp((s) => s.loadSettings);
  // Đang tải/nạp model (Whisper hoặc model dịch trên máy): thăm dò dày hơn để hiện tiến độ.
  const localState = health?.translation.local_model?.state;
  const loading = health?.stt.state === "loading" || localState === "downloading" || localState === "loading";

  useEffect(() => {
    void loadHealth().then((h) => h && loadSettings());
  }, [loadHealth, loadSettings]);

  // Thăm dò /health: 1,5 s khi đang tải model, 3 s khi mất backend, 10 s bình thường.
  useEffect(() => {
    const interval = loading ? 1500 : healthError ? 3000 : 10000;
    const timer = window.setInterval(async () => {
      const wasDown = useApp.getState().healthError || !useApp.getState().settings;
      const h = await loadHealth();
      if (h && wasDown) void loadSettings();
    }, interval);
    return () => window.clearInterval(timer);
  }, [loading, healthError, loadHealth, loadSettings]);

  if (!health) {
    return healthError ? (
      <BackendDown />
    ) : (
      <div className="flex min-h-screen items-center justify-center text-slate-500">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  return (
    <BrowserRouter>
      <OnboardingRedirect />
      <Routes>
        <Route path="/live/:id" element={<LiveSession />} />
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="onboarding" element={<Onboarding />} />
          <Route path="sessions/:id" element={<SessionReview />} />
          <Route path="inclass" element={<SoundCheck />} />
          <Route path="glossary" element={<Glossary />} />
          <Route path="flashcards" element={<Flashcards />} />
          <Route path="search" element={<Search />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <Toasts />
    </BrowserRouter>
  );
}
