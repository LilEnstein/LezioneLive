/** Nội dung cửa sổ phụ đề nổi (Document PiP): vài câu gần nhất, chữ lớn, nền tối. */
import { useEffect, useRef } from "react";

import { useSession } from "../store/session";

export function PipSubtitles({ hideVi }: { hideVi: boolean }) {
  const order = useSession((s) => s.order);
  const segments = useSession((s) => s.segments);
  const partial = useSession((s) => s.partial);
  const speaking = useSession((s) => s.speaking);
  const bottomRef = useRef<HTMLDivElement>(null);
  const last = order.slice(-4);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [order.length, partial]);

  return (
    <div className="flex h-screen flex-col justify-end gap-2 overflow-hidden bg-slate-950 p-3 font-sans text-slate-100">
      {last.map((id) => {
        const seg = segments[id];
        const vi = seg.text_vi ?? partial[id] ?? "⋯";
        return (
          <div key={id} className="rounded-lg bg-slate-900/80 px-3 py-2">
            <div className="text-sm leading-snug text-slate-400">{seg.text_it_edited ?? seg.text_it}</div>
            <div className={`text-lg font-medium leading-snug ${hideVi ? "blur-[6px] hover:blur-none" : ""}`}>{vi}</div>
          </div>
        );
      })}
      {speaking && <div className="px-3 text-sm italic text-brand-300">▌ …</div>}
      <div ref={bottomRef} />
    </div>
  );
}
