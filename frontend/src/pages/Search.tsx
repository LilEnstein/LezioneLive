/** Tìm kiếm full-text trên mọi transcript (FR-LE-04). */
import { Search as SearchIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { SearchResult } from "../api/types";
import { Card, Spinner, inputCls } from "../components/ui";
import { useT } from "../i18n";
import { fmtDate, fmtTs } from "../lib/time";
import { useApp } from "../store/app";

/** Hiển thị đoạn có đánh dấu (ký tự \u0002…\u0003 từ backend) mà không dùng HTML thô. */
function Highlighted({ text, marks }: { text: string; marks: [string, string] }): ReactNode {
  const [start, end] = marks;
  const parts: ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest.length) {
    const i = rest.indexOf(start);
    if (i < 0) {
      parts.push(rest);
      break;
    }
    parts.push(rest.slice(0, i));
    const j = rest.indexOf(end, i + 1);
    const hit = j < 0 ? rest.slice(i + 1) : rest.slice(i + 1, j);
    parts.push(
      <mark key={key++} className="rounded bg-yellow-200 px-0.5 text-slate-900 dark:bg-yellow-500/40 dark:text-yellow-50">
        {hit}
      </mark>,
    );
    rest = j < 0 ? "" : rest.slice(j + 1);
  }
  return <>{parts}</>;
}

export default function Search() {
  const t = useT();
  const lang = useApp((s) => s.lang);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [marks, setMarks] = useState<[string, string]>(["\u0002", "\u0003"]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!q.trim()) {
      setResults([]);
      return;
    }
    setLoading(true);
    const timer = window.setTimeout(() => {
      api
        .search(q.trim())
        .then((res) => {
          setResults(res.results);
          setMarks(res.highlight);
        })
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [q]);

  const groups = new Map<number, SearchResult[]>();
  for (const r of results) groups.set(r.session_id, [...(groups.get(r.session_id) ?? []), r]);

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">{t("search.title")}</h1>
      <div className="relative">
        <input
          autoFocus
          className={`${inputCls} py-3 pl-10 text-base`}
          placeholder={t("search.placeholder")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <span className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400">{loading ? <Spinner /> : <SearchIcon className="h-[1.1rem] w-[1.1rem]" />}</span>
      </div>
      {q.trim() && !loading && (
        <p className="text-sm text-slate-500">{results.length ? t("search.results", { n: results.length }) : t("search.no_results")}</p>
      )}
      {[...groups.entries()].map(([sessionId, hits]) => (
        <Card key={sessionId} className="p-4">
          <div className="mb-2 flex flex-wrap items-baseline gap-2">
            <Link to={`/sessions/${sessionId}`} className="font-semibold hover:text-brand-600">
              {hits[0].session_title}
            </Link>
            <span className="text-sm text-slate-500">
              {hits[0].course_name ? `${hits[0].course_name} · ` : ""}
              {fmtDate(hits[0].session_started_at, lang)}
            </span>
          </div>
          <ul className="space-y-2">
            {hits.map((h) => (
              <li key={h.segment_id}>
                <Link
                  to={`/sessions/${sessionId}?t=${h.t_start_ms}`}
                  className="block rounded-lg px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <span className="mr-2 font-mono text-xs text-slate-400">[{fmtTs(h.t_start_ms)}]</span>
                  <span className="text-slate-900 dark:text-slate-100">
                    <Highlighted text={h.text_it} marks={marks} />
                  </span>
                  {h.text_vi && (
                    <span className="block pl-14 text-sm text-slate-600 dark:text-slate-300">
                      <Highlighted text={h.text_vi} marks={marks} />
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}
