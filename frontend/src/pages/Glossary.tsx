/** Quản lý bảng thuật ngữ (FR-GL-01/02): CRUD, lọc theo môn, import/export CSV. */
import { Download, FileText, Pencil, Plus, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { Course, GlossaryTerm } from "../api/types";
import { Button, Card, inputCls } from "../components/ui";
import { useT } from "../i18n";
import { errorMessage, useApp } from "../store/app";

type Draft = { term_it: string; term_vi: string; term_en: string; note: string; course_id: number | null };
const EMPTY: Draft = { term_it: "", term_vi: "", term_en: "", note: "", course_id: null };

function toBody(d: Draft) {
  return {
    term_it: d.term_it.trim(),
    term_vi: d.term_vi.trim(),
    term_en: d.term_en.trim() || null,
    note: d.note.trim() || null,
    course_id: d.course_id,
  };
}

export default function Glossary() {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const [courses, setCourses] = useState<Course[]>([]);
  const [scope, setScope] = useState<string>("");
  const [q, setQ] = useState("");
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [editing, setEditing] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(() => {
    api
      .glossary({ course_id: scope || undefined, q: q || undefined })
      .then(setTerms)
      .catch((e) => toast("error", errorMessage(e)));
  }, [scope, q, toast]);

  useEffect(() => {
    api.courses().then(setCourses).catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(reload, 200);
    return () => window.clearTimeout(timer);
  }, [reload]);

  const courseName = (id: number | null) => (id === null ? t("common.shared") : courses.find((c) => c.id === id)?.name ?? `#${id}`);
  const defaultCourse = scope && scope !== "shared" ? Number(scope) : null;

  const add = async () => {
    try {
      await api.createTerm(toBody({ ...draft, course_id: draft.course_id ?? defaultCourse }));
      setDraft({ ...EMPTY, course_id: draft.course_id });
      reload();
    } catch (e) {
      toast("error", errorMessage(e));
    }
  };

  const courseSelect = (value: number | null, onChange: (v: number | null) => void) => (
    <select className={`${inputCls} py-1.5`} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">{t("common.shared")}</option>
      {courses.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </select>
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("glossary.title")}</h1>
        <p className="text-sm text-slate-500">{t("glossary.subtitle")}</p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block font-medium">{t("glossary.course")}</span>
          <select className={inputCls} value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">{t("common.all")}</option>
            <option value="shared">{t("common.shared")}</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <input className={`${inputCls} max-w-xs`} placeholder={t("glossary.search")} value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="ml-auto flex gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            className="hidden"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              try {
                const res = await api.importGlossary(f, scope || "shared");
                toast("success", t("glossary.imported", res));
                reload();
              } catch (err) {
                toast("error", errorMessage(err));
              } finally {
                e.target.value = "";
              }
            }}
          />
          <Button onClick={() => fileRef.current?.click()} title={t("glossary.csv_hint")}>
            <Upload className="h-4 w-4" /> {t("glossary.import")}
          </Button>
          <a href={api.glossaryExportUrl(scope)} download>
            <Button>
              <Download className="h-4 w-4" /> {t("glossary.export")}
            </Button>
          </a>
        </div>
      </div>

      <Card className="overflow-x-auto">
        <table className="w-full min-w-[48rem] text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-800/60">
            <tr>
              <th className="px-3 py-2">🇮🇹 {t("glossary.col_it")}</th>
              <th className="px-3 py-2">🇻🇳 {t("glossary.col_vi")}</th>
              <th className="px-3 py-2">🇬🇧 {t("glossary.col_en")}</th>
              <th className="px-3 py-2">{t("glossary.col_note")}</th>
              <th className="px-3 py-2">{t("glossary.col_course")}</th>
              <th className="w-32 px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-slate-200 bg-brand-50/40 dark:border-slate-700 dark:bg-brand-950/20">
              {(["term_it", "term_vi", "term_en", "note"] as const).map((k) => (
                <td key={k} className="px-2 py-1.5">
                  <input
                    className={`${inputCls} py-1.5`}
                    value={draft[k]}
                    placeholder={k === "term_it" ? "regressione logistica" : k === "term_vi" ? "hồi quy logistic" : k === "term_en" ? "logistic regression" : ""}
                    onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                    onKeyDown={(e) => e.key === "Enter" && draft.term_it.trim() && draft.term_vi.trim() && void add()}
                  />
                </td>
              ))}
              <td className="px-2 py-1.5">{courseSelect(draft.course_id ?? defaultCourse, (v) => setDraft({ ...draft, course_id: v }))}</td>
              <td className="px-2 py-1.5">
                <Button variant="primary" size="sm" disabled={!draft.term_it.trim() || !draft.term_vi.trim()} onClick={add}>
                  <Plus className="h-3.5 w-3.5" /> {t("common.add")}
                </Button>
              </td>
            </tr>
            {terms.map((term) =>
              editing === term.id ? (
                <tr key={term.id} className="border-b border-slate-100 dark:border-slate-800">
                  {(["term_it", "term_vi", "term_en", "note"] as const).map((k) => (
                    <td key={k} className="px-2 py-1.5">
                      <input className={`${inputCls} py-1.5`} value={editDraft[k]} onChange={(e) => setEditDraft({ ...editDraft, [k]: e.target.value })} />
                    </td>
                  ))}
                  <td className="px-2 py-1.5">{courseSelect(editDraft.course_id, (v) => setEditDraft({ ...editDraft, course_id: v }))}</td>
                  <td className="space-x-1 px-2 py-1.5">
                    <Button
                      size="sm"
                      variant="primary"
                      onClick={async () => {
                        try {
                          await api.updateTerm(term.id, toBody(editDraft));
                          setEditing(null);
                          reload();
                        } catch (e) {
                          toast("error", errorMessage(e));
                        }
                      }}
                    >
                      {t("common.save")}
                    </Button>
                    <Button size="sm" onClick={() => setEditing(null)} title={t("common.cancel")}>
                      <X className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ) : (
                <tr key={term.id} className="border-b border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40">
                  <td className="px-3 py-2 font-medium">{term.term_it}</td>
                  <td className="px-3 py-2">{term.term_vi}</td>
                  <td className="px-3 py-2 text-slate-500">{term.term_en}</td>
                  <td className="px-3 py-2 text-slate-500">{term.note}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">
                    {courseName(term.course_id)}
                    {term.source === "slides" && <FileText className="ml-1 inline h-3.5 w-3.5 align-text-bottom" aria-label="slides" />}
                  </td>
                  <td className="space-x-1 px-2 py-1.5 text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setEditing(term.id);
                        setEditDraft({
                          term_it: term.term_it,
                          term_vi: term.term_vi,
                          term_en: term.term_en ?? "",
                          note: term.note ?? "",
                          course_id: term.course_id,
                        });
                      }}
                      title={t("common.edit")}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={async () => {
                        if (!confirm(t("common.confirm_delete"))) return;
                        await api.deleteTerm(term.id);
                        reload();
                      }}
                      title={t("common.delete")}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ),
            )}
            {terms.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-10 text-center text-slate-500">
                  {t("glossary.empty")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
      <p className="text-xs text-slate-500">{t("glossary.csv_hint")}</p>
    </div>
  );
}
