import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { ChatStyles } from "@/pages/chat/chat-styles";
import { LiveTail } from "@/pages/chat/live-tail";
import { MessageList, type ListHandle } from "@/pages/chat/message-list";
import type { LiveRun } from "@/lib/runs";
import "@/styles/index.css";
import "@/shell/shell.css";

declare global {
  interface Window {
    fixtureReady?: boolean;
    fixtureError?: string;
    __capture?: () => { key: string; offset: number } | null;
    __setStream?: (text: string, status: LiveRun["status"], pace: boolean, runId: string, extra?: { stopping?: boolean; approval?: LiveRun["approval"] }) => void;
    __read?: () => { body: string; thought: string; hasThought: boolean };
    __collect?: (ms: number) => Promise<Array<{ body: string; thought: string; hasThought: boolean; source: string; t: number }>>;
    __run?: LiveRun;
    __case?: string;
  }
}

window.addEventListener("error", (event) => {
  window.fixtureError = String(event.error || event.message);
});
window.addEventListener("unhandledrejection", (event) => {
  window.fixtureError = String(event.reason);
});

const params = new URLSearchParams(location.search);
const mode = params.get("case") || "layout";
window.__case = mode;

function readDisplay() {
  const body = document.querySelector(".assistant-body")?.textContent ?? "";
  const thoughtNode = document.querySelector("details.reasoning > div");
  return {
    body,
    thought: thoughtNode?.textContent ?? "",
    hasThought: Boolean(document.querySelector("details.reasoning")),
  };
}

function Past({ source, children }: { source: string; children: ReactNode }) {
  return <div className="past" data-history-source={source}>{children}</div>;
}

function variedRows() {
  const rows: ReactNode[] = [];
  const push = (source: string, node: ReactNode) => rows.push(<Past key={source} source={source}>{node}</Past>);
  push("user:short", <div className="turn user"><div className="bubble">短</div></div>);
  push("user:long", <div className="turn user"><div className="bubble">{"用户气泡 ".repeat(40)}</div></div>);
  push("assistant:prose", <div className="turn assistant"><div className="assistant-body">{"普通历史正文，用来占住真实块高度。".repeat(12)}</div></div>);
  push("assistant:code", <div className="turn assistant"><pre className="assistant-body">{`function keep(order) {\n  return order + ${"x".repeat(180)};\n}`}</pre></div>);
  push("assistant:open-thought", <div className="turn assistant"><details className="reasoning" open><summary>Thought</summary><div>{"已展开的历史思考不应该把后面的历史源抬到上面。".repeat(8)}</div></details><div className="assistant-body">展开后的正文</div></div>);
  push("assistant:closed-thought", <div className="turn assistant"><details className="reasoning"><summary>Thought</summary><div>折叠思考</div></details><div className="assistant-body">折叠后的正文</div></div>);
  push("steer:inside", <div className="turn user steer"><span className="steer-tag">已插话</span><div className="bubble">负外边距插话</div></div>);
  push("assistant:image", <div className="turn assistant"><img alt="合成图" width={220} height={80} src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" /><div className="assistant-body">图后正文</div></div>);
  push("assistant:token", <div className="turn assistant"><div className="assistant-body">{`EDGE_${"W".repeat(240)}_TOKEN`}</div></div>);
  push("user:tiny", <div className="turn user"><div className="bubble">x</div></div>);
  push("assistant:list", <div className="turn assistant"><ul><li>一</li><li>二</li><li>{"三 ".repeat(20)}</li></ul></div>);
  for (let i = 0; i < 18; i += 1) {
    push(`fill:${i}`, <div className="turn assistant"><div className="assistant-body">{`填充行 ${i} ${"内容 ".repeat(i % 5)}`}</div></div>);
  }
  return rows;
}

function plainRows(count: number) {
  return Array.from({ length: count }, (_, index) => (
    <Past key={index} source={`row:${index}`}>
      <div className="turn assistant"><div className="assistant-body">{`第 ${index} 行历史 ${index % 7 === 0 ? "稍长一点的句子，避免全是同高。" : "短句"}`}</div></div>
    </Past>
  ));
}

function LayoutProbe({ count, varied }: { count: number; varied: boolean }) {
  const listRef = useRef<ListHandle>(null);
  useEffect(() => {
    window.__capture = () => listRef.current?.capture() ?? null;
    window.fixtureReady = true;
  }, []);
  return (
    <section className="hp-chat" aria-label="对话">
      <ChatStyles />
      <MessageList keys={[]} stick={false} hold={null} listRef={listRef} onStick={() => undefined} onNearTop={() => undefined} onUserScroll={() => undefined}>
        <div className="col">
          <div className="earlier-bar"><button type="button" className="earlier-btn">加载更早的历史记录</button></div>
          {varied ? variedRows() : plainRows(count)}
          <div className="mount-row"><div className="turn assistant"><div className="assistant-body">实时尾不是历史源</div></div></div>
        </div>
      </MessageList>
    </section>
  );
}

const BASE: LiveRun = {
  sessionId: "display-edge",
  model: "",
  provider: "",
  runId: "boot",
  userText: "合成提问",
  attachments: [],
  steers: [],
  status: "running",
  thinking: "",
  steps: [],
  text: "",
  approval: null,
  clarify: null,
  error: "",
  stopping: false,
  startedAt: 1_700_000_000_000,
  lastEventAt: 1_700_000_000_000,
  remote: false,
  unverified: false,
};

function StreamProbe() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<LiveRun["status"]>("running");
  const [pace, setPace] = useState(false);
  const [runId, setRunId] = useState("boot");
  const [stopping, setStopping] = useState(false);
  const [approval, setApproval] = useState<LiveRun["approval"]>(null);
  const run = useMemo<LiveRun>(() => ({ ...BASE, text, status, runId, stopping, approval }), [text, status, runId, stopping, approval]);
  useEffect(() => {
    window.__run = run;
  }, [run]);
  useEffect(() => {
    window.__read = readDisplay;
    window.__setStream = (nextText, nextStatus, nextPace, nextRunId, extra) => {
      setText(nextText);
      setStatus(nextStatus);
      setPace(nextPace);
      setRunId(nextRunId);
      setStopping(Boolean(extra?.stopping));
      setApproval(extra?.approval ?? null);
    };
    window.__collect = (ms) => new Promise((resolve) => {
      const frames: Array<{ body: string; thought: string; hasThought: boolean; source: string; t: number }> = [];
      const start = performance.now();
      const tick = () => {
        const view = readDisplay();
        const source = window.__run?.text ?? "";
        const row = { ...view, source, t: Math.round(performance.now() - start) };
        const last = frames[frames.length - 1];
        if (!last || last.body !== row.body || last.thought !== row.thought || last.source !== row.source || last.hasThought !== row.hasThought) frames.push(row);
        if (performance.now() - start < ms) requestAnimationFrame(tick);
        else resolve(frames);
      };
      requestAnimationFrame(tick);
    });
    window.fixtureReady = true;
  }, []);
  return (
    <section className="hp-chat" aria-label="对话">
      <ChatStyles />
      <div className="scroll">
        <div className="col">
          <LiveTail run={run} userInHistory pace={pace} onApprove={() => undefined} />
        </div>
      </div>
    </section>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("missing root");
createRoot(root).render(mode === "stream" ? <StreamProbe /> : <LayoutProbe count={mode === "rects" ? 1000 : 30} varied={mode !== "rects"} />);
