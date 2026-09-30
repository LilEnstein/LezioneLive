/** Nút chọn nguồn âm thanh (FR-AC-01): tab trình duyệt qua getDisplayMedia, hoặc hiển thị chế độ loopback. */
import { Headphones, Volume2 } from "lucide-react";
import { useT } from "../i18n";
import { Button } from "./ui";

export function AudioSourcePicker({
  mode,
  capturing,
  label,
  onPick,
  disabled,
}: {
  mode: "tab" | "loopback";
  capturing: boolean;
  label: string;
  onPick: () => void;
  disabled?: boolean;
}) {
  const t = useT();
  if (mode === "loopback") {
    return (
      <span className="inline-flex items-center gap-1 rounded-md bg-violet-100 px-2 py-1 text-xs font-medium text-violet-800 dark:bg-violet-900/40 dark:text-violet-200">
        <Volume2 className="h-3.5 w-3.5" /> {t("live.loopback_active")}
      </span>
    );
  }
  return (
    <Button
      size="sm"
      variant={capturing ? "secondary" : "primary"}
      onClick={onPick}
      disabled={disabled}
      title={capturing ? label : t("live.source_hint")}
    >
      <Headphones className="h-3.5 w-3.5" /> {capturing ? (label ? truncate(label, 22) : t("live.change_source")) : t("live.pick_source")}
    </Button>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
