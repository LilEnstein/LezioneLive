/**
 * Cổng kích hoạt (created by Turin): chưa nhập đúng email + mã do Turin cấp thì không vào được app.
 * Backend chưa chạy → nhường cho App hiện hướng dẫn khởi động, cứ 3 giây kiểm tra lại.
 */
import { KeyRound, Mail } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { api, ApiError } from "../api/client";
import type { LicenseStatus } from "../api/types";
import { useT } from "../i18n";
import { AUTHOR, CREATED_BY } from "../lib/branding";
import { Button, Field, inputCls, Spinner } from "./ui";

type GateState = { kind: "checking" } | { kind: "unreachable" } | { kind: "ready"; status: LicenseStatus };

export function LicenseGate({ children }: { children: ReactNode }) {
  const t = useT();
  const [state, setState] = useState<GateState>({ kind: "checking" });
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    try {
      setState({ kind: "ready", status: await api.licenseStatus() });
    } catch {
      setState({ kind: "unreachable" });
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  const active = state.kind === "ready" && state.status.active;
  useEffect(() => {
    if (active) return;
    const timer = window.setInterval(check, 3000);
    return () => window.clearInterval(timer);
  }, [active, check]);

  if (state.kind === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-500">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }
  if (state.kind === "unreachable" || active) return <>{children}</>;

  const { contact } = state.status;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setState({ kind: "ready", status: await api.licenseActivate(email, code) });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-md space-y-4 rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-700 dark:bg-slate-900"
      >
        <div className="flex items-center gap-3">
          <img src="/mascot.webp" alt="" className="h-10 w-10 rounded-full ring-1 ring-slate-200 dark:ring-slate-700" />
          <div>
            <h1 className="flex items-center gap-2 text-lg font-bold">
              <KeyRound className="h-5 w-5 text-brand-600" /> {t("license.title")}
            </h1>
            <p className="text-xs text-slate-500">{CREATED_BY}</p>
          </div>
        </div>
        <p className="text-sm text-slate-600 dark:text-slate-300">{t("license.body", { author: AUTHOR, contact })}</p>
        <Field label={t("license.email")}>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputCls}
          />
        </Field>
        <Field label={t("license.code")} hint={t("license.code_hint")}>
          <input
            required
            autoComplete="off"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            className={`${inputCls} font-mono tracking-widest`}
          />
        </Field>
        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" loading={busy}>
            {t("license.submit")}
          </Button>
          <a
            href={`mailto:${contact}?subject=${encodeURIComponent("Xin mã kích hoạt LezioneLive")}`}
            className="inline-flex items-center gap-1.5 px-2 text-sm font-medium text-brand-600 hover:underline dark:text-brand-400"
          >
            <Mail className="h-4 w-4" /> {t("license.request")}
          </a>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">{t("license.pull_hint")}</p>
      </form>
    </div>
  );
}
