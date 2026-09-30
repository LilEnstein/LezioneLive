/** Xin phép giảng viên (IC-FR-80/81, IC-AC-09): xác nhận theo từng môn + Claude soạn email tiếng Ý để chỉnh sửa. */
import { Check, CircleCheck, Copy, Handshake, Mail, Sparkles, Undo2 } from "lucide-react";
import { useState } from "react";

import { api } from "../api/client";
import type { Course } from "../api/types";
import { useT } from "../i18n";
import { fmtDateTime } from "../lib/time";
import { errorMessage, useApp } from "../store/app";
import { Button, Field, Modal, inputCls } from "./ui";

const NAME_KEY = "lezionelive.studentName";

function ConsentMessageModal({ course, open, onClose }: { course: Course; open: boolean; onClose: () => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const hasKey = useApp((s) => s.health?.assistant?.ready ?? s.health?.api_key.present ?? false);
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem(NAME_KEY) ?? "";
    } catch {
      return "";
    }
  });
  const [subject, setSubject] = useState("");
  const [email, setEmail] = useState("");
  const [back, setBack] = useState("");
  const [busy, setBusy] = useState(false);

  const generate = async () => {
    setBusy(true);
    try {
      try {
        localStorage.setItem(NAME_KEY, name.trim());
      } catch {
        /* ignore */
      }
      const res = await api.consentMessage(course.id, { student_name: name.trim(), lecturer: course.lecturer });
      setSubject(res.subject_it);
      setEmail(res.email_it);
      setBack(res.back_translation_vi);
    } catch (e) {
      toast("error", errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} wide title={
        <>
          <Mail className="h-5 w-5 text-brand-600" /> {t("consent.compose")}
        </>
      } onClose={onClose}>
      <div className="space-y-3 text-sm">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[12rem] flex-1">
            <Field label={t("consent.student_name")}>
              <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
          </div>
          <Button variant="primary" loading={busy} disabled={!hasKey} onClick={generate}>
            <Sparkles className="h-4 w-4" /> {t("consent.generate")}
          </Button>
        </div>
        {!hasKey && <p className="text-xs text-amber-700">{t("chat.no_key")}</p>}
        {email && (
          <>
            <Field label={t("consent.subject")}>
              <input className={inputCls} value={subject} onChange={(e) => setSubject(e.target.value)} />
            </Field>
            <Field label={`🇮🇹 ${t("consent.email")}`}>
              <textarea className={`${inputCls} min-h-[12rem]`} value={email} onChange={(e) => setEmail(e.target.value)} />
            </Field>
            <Button
              onClick={async () => {
                await navigator.clipboard.writeText(subject ? `${subject}\n\n${email}` : email);
                toast("success", t("common.copied"));
              }}
            >
              <Copy className="h-4 w-4" /> {t("consent.copy")}
            </Button>
            <div>
              <span className="inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
                <Undo2 className="h-3.5 w-3.5" /> {t("consent.back")}
              </span>
              <p className="whitespace-pre-wrap text-slate-700 dark:text-slate-300">{back}</p>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

export function ConsentPanel({ course, onChange }: { course: Course; onChange: (c: Course) => void }) {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const toast = useApp((s) => s.toast);
  const [checked, setChecked] = useState(false);
  const [compose, setCompose] = useState(false);
  const [busy, setBusy] = useState(false);

  if (course.consent_confirmed_at) {
    return (
      <p className="flex items-center gap-1.5 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200">
        <CircleCheck className="h-4 w-4 shrink-0" /> {t("consent.confirmed_at", { date: fmtDateTime(course.consent_confirmed_at, lang) })}
      </p>
    );
  }
  return (
    <div className="space-y-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/30">
      <p className="flex items-center gap-1.5 font-semibold">
        <Handshake className="h-4 w-4" /> {t("consent.title")}
      </p>
      <p className="text-slate-700 dark:text-slate-300">{t("consent.body")}</p>
      <Button size="sm" onClick={() => setCompose(true)}>
        <Mail className="h-3.5 w-3.5" /> {t("consent.compose")}
      </Button>
      <label className="flex items-start gap-2">
        <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        <span>{t("consent.checkbox")}</span>
      </label>
      <Button
        variant="primary"
        size="sm"
        disabled={!checked}
        loading={busy}
        onClick={async () => {
          setBusy(true);
          try {
            onChange(await api.confirmConsent(course.id, true));
          } catch (e) {
            toast("error", errorMessage(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Check className="h-4 w-4" /> {t("consent.confirm")}
      </Button>
      <p className="text-xs text-slate-500">{t("consent.not_legal")}</p>
      <ConsentMessageModal course={course} open={compose} onClose={() => setCompose(false)} />
    </div>
  );
}
