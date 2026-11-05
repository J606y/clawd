import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useLogs, useLogSources } from "@/lib/queries";
import type { LogQuery, LogSources } from "@/lib/sidecar";
import { errText, Head, LoadError, Loading, usePageVisible } from "./bits";

const LINE_OPTIONS = [100, 200, 500] as const;
const NEAR_BOTTOM_PX = 40;
const JOURNAL_BOUNDARY = "系统服务日志属于宿主边界（501 journal_host_boundary）。不读取 systemd，也不以空列表表示成功。";
const COMPONENT_LABEL: Record<string, string> = {
  gateway: "网关",
  agent: "agent",
  tools: "工具",
  cli: "cli",
  cron: "定时",
};
const RECORD_START = /^(\d{4}-\d{2}-\d{2}|[A-Z][a-z]{2} +\d{1,2} \d{2}:)/;

type Tone = "err" | "warn" | "dim" | "";

function toneOf(line: string): Tone {
  if (line.includes(" ERROR ") || line.includes(" CRITICAL ")) return "err";
  if (line.includes(" WARNING ")) return "warn";
  if (line.includes(" DEBUG ")) return "dim";
  return "";
}

function classify(lines: string[]): { text: string; tone: Tone }[] {
  const rows: { text: string; tone: Tone }[] = [];
  let prev: Tone = "";
  for (const text of lines) {
    const tone: Tone = RECORD_START.test(text) ? toneOf(text) : prev;
    prev = tone;
    rows.push({ text, tone });
  }
  return rows;
}

function firstFileSource(meta: LogSources): string {
  const files = meta.data.filter((item) => item.kind === "file");
  return (files.find((item) => item.available) ?? files[0])?.id ?? "";
}

export function LogsTab() {
  const sources = useLogSources();
  if (sources.isError) {
    return (
      <div className="logs-root">
        <Head title="日志" />
        <LoadError message={errText(sources.error, "加载日志来源失败")} onRetry={() => void sources.refetch()} />
      </div>
    );
  }
  if (!sources.data) {
    return (
      <div className="logs-root">
        <Head title="日志" />
        <Loading />
      </div>
    );
  }
  return <LogsReady meta={sources.data} />;
}

function LogsReady({ meta }: { meta: LogSources }) {
  const visible = usePageVisible();
  const fileSources = meta.data.filter((item) => item.kind === "file");
  const journalSources = meta.data.filter((item) => item.kind === "journal");
  const [source, setSource] = useState(() => firstFileSource(meta));
  const [level, setLevel] = useState("");
  const [component, setComponent] = useState("");
  const [lines, setLines] = useState(200);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [follow, setFollow] = useState(true);
  const [note, setNote] = useState("");
  const [rows, setRows] = useState<{ text: string; tone: Tone }[]>([]);
  const timer = useRef<number | undefined>(undefined);
  const noteTimer = useRef<number | undefined>(undefined);
  const parentRef = useRef<HTMLDivElement>(null);
  const wasHidden = useRef(false);
  const sticking = useRef(false);
  const active = fileSources.some((item) => item.id === source) ? source : "";
  const query: LogQuery = { source: active, lines, level, component, q: debounced.trim() };
  const logs = useLogs(query, Boolean(active) && follow && visible);
  const journalError = logs.error instanceof ApiError && logs.error.code === "journal_host_boundary";

  useEffect(() => {
    if (logs.data && active && logs.data.source === active) setRows(classify(logs.data.lines));
  }, [logs.data, active]);

  useEffect(() => {
    return () => {
      window.clearTimeout(timer.current);
      window.clearTimeout(noteTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!visible) {
      wasHidden.current = true;
      return;
    }
    if (wasHidden.current && follow && active) void logs.refetch();
    wasHidden.current = false;
  }, [visible, follow, active, logs.refetch]);

  useEffect(() => {
    if (!follow) return;
    const box = parentRef.current;
    if (!box) return;
    sticking.current = true;
    box.scrollTop = box.scrollHeight;
    const id = window.setTimeout(() => { sticking.current = false; }, 80);
    return () => window.clearTimeout(id);
  }, [rows, follow]);

  function flash(message: string) {
    setNote(message);
    window.clearTimeout(noteTimer.current);
    noteTimer.current = window.setTimeout(() => setNote(""), 2000);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(rows.map((row) => row.text).join("\n"));
      flash("已复制");
    } catch {
      flash("复制失败");
    }
  }

  function onScroll() {
    const box = parentRef.current;
    if (sticking.current || !box || !follow) return;
    if (box.scrollHeight - box.scrollTop - box.clientHeight > NEAR_BOTTOM_PX) setFollow(false);
  }

  const loaded = !logs.isLoading || rows.length > 0 || logs.isError || logs.isFetched;
  const showBoundary = !active || journalError;
  const boundaryText = journalError || journalSources.length ? JOURNAL_BOUNDARY : "没有可用的日志文件";

  return (
    <div className="logs-root">
      <Head title="日志">
        <button type="button" className="btn" aria-label="复制当前日志" disabled={!rows.length || showBoundary} onClick={() => void copy()}>复制</button>
        <button type="button" className="btn" aria-label="立即刷新日志" disabled={!active || logs.isFetching} onClick={() => void logs.refetch()}>刷新</button>
      </Head>
      <div className="toolbar" role="group" aria-label="日志筛选">
        <select aria-label="日志来源" value={active} onChange={(event) => { setRows([]); setSource(event.target.value); }}>
          {fileSources.length ? (
            <optgroup label="日志文件">
              {fileSources.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{item.available ? "" : "（不可用）"}</option>)}
            </optgroup>
          ) : null}
        </select>
        <select aria-label="日志级别" value={level} onChange={(event) => setLevel(event.target.value)}>
          <option value="">全部级别</option>
          {meta.levels.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <select aria-label="日志组件" value={component} onChange={(event) => setComponent(event.target.value)}>
          <option value="">全部组件</option>
          {meta.components.map((item) => <option key={item} value={item}>{COMPONENT_LABEL[item] ?? item}</option>)}
        </select>
        <select aria-label="显示行数" value={lines} onChange={(event) => setLines(Number(event.target.value))}>
          {LINE_OPTIONS.map((item) => <option key={item} value={item}>{item} 行</option>)}
        </select>
        <input
          className="search"
          type="search"
          aria-label="关键字"
          placeholder="关键字"
          value={q}
          onChange={(event) => {
            const next = event.target.value;
            setQ(next);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => setDebounced(next), 300);
          }}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            window.clearTimeout(timer.current);
            setDebounced(event.currentTarget.value);
          }}
        />
        <span className="follow">
          <button
            type="button"
            className="switch"
            role="switch"
            aria-checked={follow}
            aria-label="自动跟随"
            onClick={() => {
              setFollow((current) => {
                const next = !current;
                if (next && active) void logs.refetch();
                return next;
              });
            }}
          />
          自动跟随
        </span>
      </div>
      <div className="status" aria-live="polite">
        {journalSources.length && active ? <span>{JOURNAL_BOUNDARY}</span> : null}
        {logs.isError && !journalError ? <span className="error">{errText(logs.error, "拉取日志失败")}</span> : null}
        {note ? <span>{note}</span> : null}
        {active && loaded && !logs.isError ? <span>{rows.length} 行{follow ? " · 每 5 秒更新" : ""}</span> : null}
      </div>
      {showBoundary ? (
        <div className="card"><div className="log-empty">{boundaryText}</div></div>
      ) : !loaded ? (
        <Loading />
      ) : rows.length ? (
        <div className="card" style={{ overflow: "hidden" }}>
          <div ref={parentRef} className="log" tabIndex={0} role="log" aria-label="日志内容" onScroll={onScroll}>
            {rows.map((row, index) => (
              <span key={index} className="ln" data-tone={row.tone}>{row.text}</span>
            ))}
          </div>
        </div>
      ) : logs.isError ? null : (
        <div className="card"><div className="log-empty">没有匹配的日志</div></div>
      )}
    </div>
  );
}
