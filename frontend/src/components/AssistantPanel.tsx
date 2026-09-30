/** Cột phải màn hình học: [Hỏi bài] [Hỏi GV] [Slide] [Ghim] (mục 12.2). */
import { FileText, Hand, MessagesSquare, Pin } from "lucide-react";
import { forwardRef, useImperativeHandle, useRef, useState, type ReactNode } from "react";

import type { Pin as PinT, Segment } from "../api/types";
import { useT } from "../i18n";
import { useSession } from "../store/session";
import { ChatPanel, type ChatPanelHandle } from "./ChatPanel";
import { ComposePanel } from "./ComposePanel";
import { PinList } from "./PinList";
import { SlidesPanel } from "./SlidesPanel";
import { Tabs } from "./ui";

export type AssistantTab = "qa" | "compose" | "slides" | "pins";

export interface AssistantHandle {
  focusChat: () => void;
  prefill: (text: string) => void;
  ask: (text: string) => void;
  newChat: () => void;
  showTab: (tab: AssistantTab) => void;
  /** Đính kèm ảnh vào khung hỏi bài, kèm câu hỏi gợi ý nếu ô nhập đang trống. */
  attachImage: (dataUrl: string, question?: string) => void;
}

export const AssistantPanel = forwardRef<
  AssistantHandle,
  {
    sessionId: number;
    onTimestamp: (ms: number) => void;
    onAskPin: (pin: PinT, seg: Segment) => void;
    onJump: (seg: Segment) => void;
    tabs?: AssistantTab[];
    onBusyChange?: (busy: boolean) => void;
  }
>(function AssistantPanel(
  { sessionId, onTimestamp, onAskPin, onJump, tabs = ["qa", "compose", "slides", "pins"], onBusyChange },
  ref,
) {
  const t = useT();
  const [tab, setTab] = useState<AssistantTab>(tabs[0]);
  const chatRef = useRef<ChatPanelHandle>(null);
  const pinCount = useSession((s) => s.pins.length);

  useImperativeHandle(ref, () => ({
    focusChat: () => {
      setTab("qa");
      window.setTimeout(() => chatRef.current?.focus(), 0);
    },
    prefill: (text) => {
      setTab("qa");
      window.setTimeout(() => chatRef.current?.prefill(text), 0);
    },
    ask: (text) => {
      setTab("qa");
      window.setTimeout(() => chatRef.current?.ask(text), 0);
    },
    newChat: () => {
      setTab("qa");
      window.setTimeout(() => chatRef.current?.newChat(), 0);
    },
    showTab: setTab,
    attachImage: (dataUrl, question) => {
      setTab("qa");
      window.setTimeout(() => chatRef.current?.attachImage(dataUrl, question), 0);
    },
  }));

  const icon = "h-3.5 w-3.5";
  const labels: Record<AssistantTab, ReactNode> = {
    qa: (
      <>
        <MessagesSquare className={icon} /> {t("chat.tab_qa")}
      </>
    ),
    compose: (
      <>
        <Hand className={icon} /> {t("chat.tab_compose")}
      </>
    ),
    slides: (
      <>
        <FileText className={icon} /> {t("chat.tab_slides")}
      </>
    ),
    pins: (
      <>
        <Pin className={icon} /> {t("chat.tab_pins")}
        {pinCount > 0 && (
          <span className="rounded-full bg-slate-200 px-1.5 text-[0.65rem] font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">
            {pinCount}
          </span>
        )}
      </>
    ),
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-slate-200 p-2 dark:border-slate-700">
        <Tabs tabs={tabs.map((id) => ({ id, label: labels[id] }))} value={tab} onChange={setTab} />
      </div>
      <div className="min-h-0 flex-1">
        {/* QA luôn được giữ mount để không mất câu trả lời đang stream khi đổi tab. */}
        <div className={tab === "qa" ? "h-full" : "hidden"}>
          <ChatPanel ref={chatRef} sessionId={sessionId} onTimestamp={onTimestamp} onBusyChange={onBusyChange} />
        </div>
        {tab === "compose" && <ComposePanel sessionId={sessionId} />}
        {tab === "slides" && <SlidesPanel sessionId={sessionId} />}
        {tab === "pins" && (
          <div className="h-full overflow-y-auto">
            <PinList onAsk={onAskPin} onJump={onJump} />
          </div>
        )}
      </div>
    </div>
  );
});
