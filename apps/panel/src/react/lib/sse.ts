// text/event-stream 读取器（EventSource 不能带请求体、也不便处理 401，故手写）：POST 用于 chat/stream，GET 用于订阅运行事件。

import { API_BASE, AUTH_EXPIRED_EVENT, ApiError } from "./api.js";

export interface SseEvent {
  event: string;
  data: string;
  /** 帧里的 `id:`（运行事件流用它做断点续传）。 */
  id?: string;
}

/** GET 订阅 SSE（运行事件流）。 */
export function getSse(path: string, signal: AbortSignal): AsyncGenerator<SseEvent> {
  return openSse(path, { method: "GET" }, signal);
}

async function* openSse(path: string, init: RequestInit, signal: AbortSignal): AsyncGenerator<SseEvent> {
  let res: Response;
  try {
    res = await fetch(new URL(path, API_BASE), {
      ...init,
      credentials: "same-origin",
      headers: { Accept: "text/event-stream", ...(init.headers as Record<string, string> | undefined) },
      signal,
    });
  } catch {
    throw new ApiError("network", 0, "网络不可达");
  }
  if (!res.ok || !res.body) {
    if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
    let message = `${res.status} ${res.statusText}`.trim();
    try {
      const j = (await res.json()) as { error?: { message?: string } | string };
      message = typeof j.error === "string" ? j.error : (j.error?.message ?? message);
    } catch {
      // 非 JSON 错误体，保留状态行。
    }
    const kind = res.status === 401 ? "unauthorized" : res.status === 429 ? "rate-limited" : res.status >= 502 ? "unavailable" : "server";
    throw new ApiError(kind, res.status, message);
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let sep: number;
      while ((sep = buf.search(/\r?\n\r?\n/)) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep).replace(/^\r?\n\r?\n/, "");
        const ev = parseFrame(frame);
        if (ev) yield ev;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseFrame(frame: string): SseEvent | null {
  let event = "message";
  let id: string | undefined;
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const i = line.indexOf(":");
    const field = i === -1 ? line : line.slice(0, i);
    const value = i === -1 ? "" : line.slice(i + 1).replace(/^ /, "");
    if (field === "event") event = value;
    else if (field === "id") id = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n"), ...(id !== undefined ? { id } : {}) } : null;
}
