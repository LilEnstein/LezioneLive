/** Danh sách ghim (FR-PIN-02): mỗi ghim có nút "Hỏi Claude" và "Tới đoạn". */
import { CircleHelp, type LucideIcon, MessagesSquare, SquareCheckBig, Star, X } from "lucide-react";
import { useState } from "react";

import { api } from "../api/client";
import type { Pin, PinKind, Segment } from "../api/types";
import { useT } from "../i18n";
import { fmtTs } from "../lib/time";
import { useSession } from "../store/session";
import { Button, inputCls } from "./ui";

const KINDS: PinKind[] = ["confused", "important", "todo"];
const KIND_ICON: Record<PinKind, { icon: LucideIcon; cls: string }> = {
  confused: { icon: CircleHelp, cls: "text-violet-600 dark:text-violet-400" },
  important: { icon: Star, cls: "text-amber-500" },
  todo: { icon: SquareCheckBig, cls: "text-emerald-600 dark:text-emerald-400" },
};

function PinItem({
  pin,
  seg,
  onAsk,
  onJump,
}: {
  pin: Pin;
  seg: Segment | undefined;
  onAsk: (pin: Pin, seg: Segment) => void;
  onJump: (seg: Segment) => void;
}) {
  const t = useT();
  const addPin = useSession((s) => s.addPin);
  const removePin = useSession((s) => s.removePin);
  const [note, setNote] = useState(pin.note ?? "");

  return (
    <li className="space-y-2 rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-sm dark:border-amber-800/60 dark:bg-amber-950/20">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-slate-500">{seg ? `[${fmtTs(seg.t_start_ms)}]` : "—"}</span>
        {(() => {
          const { icon: KindIcon, cls } = KIND_ICON[pin.kind];
          return <KindIcon className={`h-4 w-4 ${cls}`} aria-hidden />;
        })()}
        <select
          value={pin.kind}
          className="rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-xs dark:border-slate-600 dark:bg-slate-800"
          onChange={async (e) => addPin(await api.updatePin(pin.id, { kind: e.target.value as PinKind }))}
        >
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`pins.kind.${k}`)}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="ml-auto rounded-md p-1 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"
          title={t("common.delete")}
          aria-label={t("common.delete")}
          onClick={async () => {
            await api.deletePin(pin.id);
            removePin(pin.id);
          }}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {seg && (
        <div>
          <p className="text-slate-900 dark:text-slate-100">{seg.text_it_edited ?? seg.text_it}</p>
          {seg.text_vi && <p className="text-slate-600 dark:text-slate-300">{seg.text_vi}</p>}
        </div>
      )}
      <input
        className={`${inputCls} py-1 text-xs`}
        placeholder={t("pins.note_placeholder")}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        onBlur={async () => {
          if (note !== (pin.note ?? "")) addPin(await api.updatePin(pin.id, { note: note || null }));
        }}
      />
      {seg && (
        <div className="flex gap-2">
          <Button size="sm" variant="primary" onClick={() => onAsk(pin, seg)}>
            <MessagesSquare className="h-3.5 w-3.5" /> {t("pins.ask")}
          </Button>
          <Button size="sm" onClick={() => onJump(seg)}>
            {t("pins.jump")}
          </Button>
        </div>
      )}
    </li>
  );
}

export function PinList({
  onAsk,
  onJump,
}: {
  onAsk: (pin: Pin, seg: Segment) => void;
  onJump: (seg: Segment) => void;
}) {
  const t = useT();
  const pins = useSession((s) => s.pins);
  const segments = useSession((s) => s.segments);
  if (pins.length === 0) return <p className="p-4 text-sm text-slate-500 dark:text-slate-400">{t("pins.empty")}</p>;
  const sorted = [...pins].sort(
    (a, b) =>
      (segments[a.segment_id ?? -1]?.t_start_ms ?? 0) - (segments[b.segment_id ?? -1]?.t_start_ms ?? 0) || a.id - b.id,
  );
  return (
    <ul className="space-y-2 overflow-y-auto p-3">
      {sorted.map((pin) => (
        <PinItem
          key={pin.id}
          pin={pin}
          seg={pin.segment_id !== null ? segments[pin.segment_id] : undefined}
          onAsk={onAsk}
          onJump={onJump}
        />
      ))}
    </ul>
  );
}
