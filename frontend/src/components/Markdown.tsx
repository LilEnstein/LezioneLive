/** Markdown + LaTeX (KaTeX) + mốc thời gian [mm:ss] bấm được (FR-AS-03). */
import { memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { parseTs } from "../lib/time";

const TS_RE = /\[(\d{1,3}:\d{2}(?::\d{2})?)\](?!\()/g;

/**
 * Tiền xử lý (bỏ qua khối code):
 * - [mm:ss] → link "#t=ms" để bấm được;
 * - dòng chỉ gồm "$$...$$" → khối công thức riêng (remark-math chỉ coi là display math khi $$ nằm riêng dòng).
 */
export function linkTimestamps(md: string): string {
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(/^[ \t]*\$\$([^\n]+?)\$\$[ \t]*$/gm, (_m, tex: string) => `$$\n${tex.trim()}\n$$`)
            .replace(TS_RE, (_m, ts: string) => {
              const ms = parseTs(ts);
              return ms === null ? `[${ts}]` : `[\\[${ts}\\]](#t=${ms})`;
            }),
    )
    .join("");
}

export const Markdown = memo(function Markdown({
  text,
  onTimestamp,
  className = "",
}: {
  text: string;
  onTimestamp?: (ms: number) => void;
  className?: string;
}) {
  return (
    <div
      className={`prose prose-sm max-w-none dark:prose-invert prose-headings:mb-2 prose-headings:mt-4 prose-p:my-1.5 prose-ul:my-1.5 prose-li:my-0.5 prose-table:text-xs ${className}`}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#t=")) {
              const ms = Number(href.slice(3));
              return (
                <button
                  type="button"
                  className="rounded bg-brand-100 px-1 font-mono text-[0.85em] font-semibold text-brand-700 no-underline hover:bg-brand-200 dark:bg-brand-900/50 dark:text-brand-200"
                  onClick={() => onTimestamp?.(ms)}
                >
                  {children}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {linkTimestamps(text)}
      </ReactMarkdown>
    </div>
  );
});
