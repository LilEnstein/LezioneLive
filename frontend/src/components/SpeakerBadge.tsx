/** Huy hiệu người nói (IC-FR-32): Giảng viên / Sinh viên hỏi. Nhãn "unknown" không hiển thị. */
import { GraduationCap, Hand } from "lucide-react";
import type { Speaker } from "../api/types";
import { useT } from "../i18n";

export function SpeakerBadge({ speaker, score, compact = false }: { speaker?: Speaker; score?: number | null; compact?: boolean }) {
  const t = useT();
  if (!speaker || speaker === "unknown") return null;
  const lecturer = speaker === "lecturer";
  const label = lecturer ? t("live.speaker_lecturer") : t("live.speaker_other");
  const title = score !== null && score !== undefined ? `${label} (${score.toFixed(2)})` : label;
  return (
    <span
      title={title}
      className={`mr-1 inline-flex items-center gap-0.5 rounded-full px-1.5 align-middle text-[0.7em] font-semibold ${
        lecturer
          ? "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200"
          : "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900/50 dark:text-fuchsia-200"
      }`}
    >
      {lecturer ? <GraduationCap className="h-[1.1em] w-[1.1em]" /> : <Hand className="h-[1.1em] w-[1.1em]" />}
      {!compact && <span>{label}</span>}
    </span>
  );
}
