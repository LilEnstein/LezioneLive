/** Trang chủ: môn học, danh sách buổi học, nút "Bắt đầu buổi học" (mục 12.1). */
import {
  AppWindow,
  ArrowRight,
  Clock,
  GraduationCap,
  Hourglass,
  Layers,
  Pencil,
  Play,
  Plus,
  Trash2,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Course, Session } from "../api/types";
import { ConsentPanel } from "../components/ConsentPanel";
import { Badge, Button, Card, Field, IconButton, Modal, ProgressBar, inputCls } from "../components/ui";
import { useT, type MessageKey } from "../i18n";
import { fmtClock, fmtDateTime, fmtUsd } from "../lib/time";
import { errorMessage, useApp } from "../store/app";

function CourseModal({
  open,
  course,
  onClose,
  onSaved,
}: {
  open: boolean;
  course: Course | null;
  onClose: () => void;
  onSaved: (c: Course) => void;
}) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const [name, setName] = useState("");
  const [nameIt, setNameIt] = useState("");
  const [lecturer, setLecturer] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setName(course?.name ?? "");
      setNameIt(course?.name_it ?? "");
      setLecturer(course?.lecturer ?? "");
    }
  }, [open, course]);

  const save = async () => {
    setSaving(true);
    try {
      const body = { name: name.trim(), name_it: nameIt.trim() || null, lecturer: lecturer.trim() || null };
      onSaved(course ? await api.updateCourse(course.id, body) : await api.createCourse(body));
      onClose();
    } catch (e) {
      toast("error", errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={course ? t("home.edit_course") : t("home.add_course")}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" loading={saving} disabled={!name.trim()} onClick={save}>
            {t("common.save")}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label={t("course.name")}>
          <input className={inputCls} value={name} placeholder={t("course.name_placeholder")} onChange={(e) => setName(e.target.value)} autoFocus />
        </Field>
        <Field label={`${t("course.name_it")} (${t("common.optional")})`}>
          <input className={inputCls} value={nameIt} placeholder={t("course.name_it_placeholder")} onChange={(e) => setNameIt(e.target.value)} />
        </Field>
        <Field label={`${t("course.lecturer")} (${t("common.optional")})`}>
          <input className={inputCls} value={lecturer} placeholder={t("course.lecturer_placeholder")} onChange={(e) => setLecturer(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

type StartMode = "tab" | "loopback" | "inclass";

function StartModal({
  open,
  courses,
  defaultCourse,
  onClose,
  onCourseChanged,
}: {
  open: boolean;
  courses: Course[];
  defaultCourse: number | null;
  onClose: () => void;
  onCourseChanged: () => void;
}) {
  const t = useT();
  const navigate = useNavigate();
  const toast = useApp((s) => s.toast);
  const health = useApp((s) => s.health);
  const loadHealth = useApp((s) => s.loadHealth);
  const [courseId, setCourseId] = useState<number | null>(defaultCourse);
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<StartMode>("tab");
  const [starting, setStarting] = useState(false);
  const [consented, setConsented] = useState<Record<number, string>>({});

  useEffect(() => {
    if (open) {
      setCourseId(defaultCourse ?? courses[0]?.id ?? null);
      setTitle("");
    }
  }, [open, defaultCourse, courses]);

  const stt = health?.stt;
  const sttReady = stt?.ready ?? false;
  const loopbackOk = health?.capabilities.loopback ?? false;
  const course = courses.find((c) => c.id === courseId) ?? null;
  const consentOk = course !== null && Boolean(course.consent_confirmed_at || consented[course.id]);

  const start = async () => {
    if (mode === "inclass") {
      // Lớp học trực tiếp: chọn nguồn, phòng học và kiểm tra âm thanh trước (Flow A).
      const q = new URLSearchParams({ course: String(courseId) });
      if (title.trim()) q.set("title", title.trim());
      navigate(`/inclass?${q.toString()}`);
      return;
    }
    setStarting(true);
    try {
      const res = await api.createSession({ course_id: courseId, title: title.trim() || undefined, audio_mode: mode });
      navigate(`/live/${res.session_id}`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "STT_NOT_READY") void loadHealth();
      toast("error", errorMessage(e));
    } finally {
      setStarting(false);
    }
  };

  return (
    <Modal
      open={open}
      title={
        <>
          <GraduationCap className="h-5 w-5 text-brand-600" /> {t("start.title")}
        </>
      }
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{t("common.cancel")}</Button>
          {mode === "inclass" ? (
            <Button variant="primary" disabled={!consentOk} onClick={start}>
              {t("start.next_soundcheck")} <ArrowRight className="h-4 w-4" />
            </Button>
          ) : (
            <Button variant="primary" loading={starting} disabled={!sttReady} onClick={start}>
              <Play className="h-4 w-4" /> {t("start.go")}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <Field label={t("start.course")}>
          <select className={inputCls} value={courseId ?? ""} onChange={(e) => setCourseId(e.target.value ? Number(e.target.value) : null)}>
            <option value="">{t("start.no_course")}</option>
            {courses.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {c.name_it ? ` — ${c.name_it}` : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label={`${t("start.session_title")} (${t("common.optional")})`}>
          <input className={inputCls} value={title} placeholder={t("start.session_title_placeholder")} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">{t("start.audio_mode")}</legend>
          <div className="space-y-2">
            {(["tab", "loopback", "inclass"] as const).map((m) => (
              <label
                key={m}
                className={`flex cursor-pointer gap-3 rounded-xl border p-3 ${
                  mode === m ? "border-brand-500 bg-brand-50/60 dark:bg-brand-950/30" : "border-slate-200 dark:border-slate-700"
                } ${m === "loopback" && !loopbackOk ? "cursor-not-allowed opacity-50" : ""}`}
              >
                <input type="radio" name="mode" checked={mode === m} disabled={m === "loopback" && !loopbackOk} onChange={() => setMode(m)} />
                <span>
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    {m === "inclass" ? (
                      <GraduationCap className="h-4 w-4 text-brand-600" />
                    ) : m === "tab" ? (
                      <AppWindow className="h-4 w-4 text-brand-600" />
                    ) : (
                      <Volume2 className="h-4 w-4 text-brand-600" />
                    )}
                    {m === "inclass" ? t("start.mode_inclass") : t(m === "tab" ? "start.mode_tab" : "start.mode_loopback")}
                  </span>
                  <span className="block text-xs text-slate-500">
                    {m === "inclass"
                      ? t("start.mode_inclass_hint")
                      : m === "loopback" && !loopbackOk
                        ? t("start.loopback_unavailable")
                        : t(m === "tab" ? "start.mode_tab_hint" : "start.mode_loopback_hint")}
                  </span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {mode === "inclass" && !course && (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            {t("start.inclass_need_course")}
          </p>
        )}
        {mode === "inclass" && course && (
          <ConsentPanel
            course={consented[course.id] ? { ...course, consent_confirmed_at: consented[course.id] } : course}
            onChange={(c) => {
              if (c.consent_confirmed_at) setConsented((prev) => ({ ...prev, [c.id]: c.consent_confirmed_at as string }));
              onCourseChanged();
            }}
          />
        )}
        {!sttReady && mode !== "inclass" && (
          <div className="space-y-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <p className="flex items-center gap-1.5">
              <Hourglass className="h-4 w-4 shrink-0" /> {t("start.stt_not_ready")} {stt?.message}
            </p>
            {stt?.state === "loading" && <ProgressBar value={stt.progress} />}
            {stt?.error && <p className="text-red-700 dark:text-red-300">{stt.error}</p>}
            {stt?.state !== "loading" && (
              <Button size="sm" onClick={() => void api.sttPrepare().then(loadHealth)}>
                {t("start.prepare")}
              </Button>
            )}
          </div>
        )}
        <p className="text-xs text-slate-500">{t("start.slides_hint")}</p>
      </div>
    </Modal>
  );
}

export default function Home() {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const toast = useApp((s) => s.toast);
  const [courses, setCourses] = useState<Course[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [dueCount, setDueCount] = useState(0);
  const [startOpen, setStartOpen] = useState(false);
  const [courseModal, setCourseModal] = useState<{ open: boolean; course: Course | null }>({ open: false, course: null });
  const [params, setParams] = useSearchParams();

  // Nút "Buổi học mới" ở thanh bên điều hướng tới /?start=1.
  useEffect(() => {
    if (params.get("start") !== "1") return;
    setStartOpen(true);
    setParams({}, { replace: true });
  }, [params, setParams]);

  const reload = useCallback(async () => {
    try {
      const [c, s, stats] = await Promise.all([api.courses(), api.sessions(), api.flashcardStats()]);
      setCourses(c);
      setSessions(s);
      setDueCount(stats.due);
    } catch (e) {
      toast("error", errorMessage(e));
    }
  }, [toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const visible = selected === null ? sessions : sessions.filter((s) => s.course_id === selected);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight">{t("app.name")}</h1>
          <p className="text-sm text-slate-500">{t("app.tagline")}</p>
        </div>
        {dueCount > 0 && (
          <Link to="/flashcards">
            <Badge tone="violet" className="px-3 py-1 text-sm">
              <Layers className="h-3.5 w-3.5" /> {t("home.due_cards", { n: dueCount })}
            </Badge>
          </Link>
        )}
        <Button variant="primary" className="px-5 py-2.5 text-base" onClick={() => setStartOpen(true)}>
          <Plus className="h-4 w-4" strokeWidth={2.25} /> {t("home.start")}
        </Button>
      </div>

      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <Card className="h-fit p-3">
          <div className="mb-2 flex items-center justify-between px-1">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t("home.courses")}</h2>
            <IconButton size="sm" label={t("home.add_course")} onClick={() => setCourseModal({ open: true, course: null })}>
              <Plus className="h-4 w-4" />
            </IconButton>
          </div>
          <ul className="space-y-0.5">
            <li>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className={`w-full rounded-lg px-2.5 py-1.5 text-left text-sm ${selected === null ? "bg-brand-50 font-semibold text-brand-700 dark:bg-brand-900/40 dark:text-brand-200" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}
              >
                {t("home.all_courses")} <span className="text-slate-400">({sessions.length})</span>
              </button>
            </li>
            {courses.map((c) => (
              <li key={c.id} className="group flex items-center">
                <button
                  type="button"
                  onClick={() => setSelected(c.id)}
                  className={`min-w-0 flex-1 truncate rounded-lg px-2.5 py-1.5 text-left text-sm ${selected === c.id ? "bg-brand-50 font-semibold text-brand-700 dark:bg-brand-900/40 dark:text-brand-200" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}
                  title={[c.name_it, c.lecturer].filter(Boolean).join(" · ")}
                >
                  {c.name} <span className="text-slate-400">({c.session_count ?? 0})</span>
                </button>
                <button
                  type="button"
                  className="invisible rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700 group-hover:visible dark:hover:bg-slate-800 dark:hover:text-slate-200"
                  onClick={() => setCourseModal({ open: true, course: c })}
                  title={t("common.edit")}
                  aria-label={t("common.edit")}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
            {courses.length === 0 && <li className="px-2.5 py-1.5 text-sm text-slate-400">{t("home.no_courses")}</li>}
          </ul>
        </Card>

        <div className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">{t("home.sessions")}</h2>
          {visible.length === 0 && (
            <Card className="p-10 text-center text-slate-500">{t("home.no_sessions")}</Card>
          )}
          {visible.map((s) => (
            <Card key={s.id} className="flex flex-wrap items-center gap-4 p-4">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Link
                    to={s.status === "ended" ? `/sessions/${s.id}` : `/live/${s.id}`}
                    className="truncate font-semibold hover:text-brand-600"
                  >
                    {s.title}
                  </Link>
                  <Badge tone={s.status === "live" ? "red" : s.status === "paused" ? "amber" : "slate"}>
                    {t(`home.status.${s.status}` as MessageKey)}
                  </Badge>
                  {s.inclass && (
                    <Badge tone="violet">
                      <GraduationCap className="h-3 w-3" /> {t("home.inclass")}
                    </Badge>
                  )}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 text-sm text-slate-500">
                  {s.course_name && <span>{s.course_name}</span>}
                  <span>{fmtDateTime(s.started_at, lang)}</span>
                  <span className="inline-flex items-center gap-1">
                    <Clock className="h-3.5 w-3.5" /> {fmtClock(s.active_ms)}
                  </span>
                  <span>{t("common.segments", { n: s.segment_count ?? 0 })}</span>
                  {(s.pin_count ?? 0) > 0 && <span>{t("home.pins", { n: s.pin_count ?? 0 })}</span>}
                  <span className="font-mono">{fmtUsd(s.cost_usd_est)}</span>
                </div>
              </div>
              <div className="flex gap-2">
                {s.status !== "ended" && (
                  <Link to={`/live/${s.id}`}>
                    <Button variant="primary" size="sm">
                      <Play className="h-3.5 w-3.5" /> {t("home.continue")}
                    </Button>
                  </Link>
                )}
                <Link to={`/sessions/${s.id}`}>
                  <Button size="sm">{t("home.review")}</Button>
                </Link>
                <IconButton
                  size="sm"
                  label={t("common.delete")}
                  className="hover:!text-red-600"
                  onClick={async () => {
                    if (!confirm(t("home.delete_session"))) return;
                    await api.deleteSession(s.id);
                    void reload();
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </IconButton>
              </div>
            </Card>
          ))}
        </div>
      </div>

      <StartModal
        open={startOpen}
        courses={courses}
        defaultCourse={selected}
        onClose={() => setStartOpen(false)}
        onCourseChanged={() => void reload()}
      />
      <CourseModal
        open={courseModal.open}
        course={courseModal.course}
        onClose={() => setCourseModal({ open: false, course: null })}
        onSaved={() => void reload()}
      />
    </div>
  );
}
