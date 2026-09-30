/** POST và đọc Server-Sent Events (EventSource không hỗ trợ POST). */
import { ApiError } from "./client";

export interface SseEvent {
  event: string;
  data: unknown;
}

export async function postSSE(
  path: string,
  body: unknown,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
  headers: Record<string, string> = {},
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...headers },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError(0, "BACKEND_UNREACHABLE", "Không kết nối được backend");
  }
  if (!res.ok || !res.body) {
    let code = `HTTP_${res.status}`;
    let message = res.statusText;
    try {
      const data = await res.json();
      code = data?.detail?.code ?? code;
      message = data?.detail?.message ?? message;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, code, message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        let event = "message";
        const dataLines: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        if (!dataLines.length) continue;
        let data: unknown = dataLines.join("\n");
        try {
          data = JSON.parse(data as string);
        } catch {
          /* giữ nguyên chuỗi */
        }
        onEvent({ event, data });
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") throw e;
  }
}
