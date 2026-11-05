import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent } from "react";
import { useNavigate } from "react-router";
import type { SessionSummary } from "@/lib/api";
import { useUsage } from "@/lib/queries";
import type { Usage, UsageDay, UsageTop } from "@/lib/sidecar";
import { relativeTime } from "@/lib/time";
import { Empty, errText, Head, Loading, openSession, usePageVisible } from "./bits";

const PERIODS = [7, 30, 90] as const;
const SOURCE_LABEL: Record<string, string> = {
  api_server: "面板",
  tui: "面板",
  cli: "终端",
  cron: "定时",
  webhook: "Webhook",
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  subagent: "子任务",
};

const sourceName = (source: string | null | undefined): string => (source ? (SOURCE_LABEL[source] ?? source) : "—");
const trim1 = (value: number): string => value.toFixed(1).replace(/\.0$/, "");

function compact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${trim1(n / 1e8)} 亿`;
  if (abs >= 1e4) return `${trim1(n / 1e4)} 万`;
  return n.toLocaleString("zh-CN");
}

const full = (n: number): string => n.toLocaleString("zh-CN");

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

const CH_H = 200;
const PAD_L = 52;
const PAD_R = 8;
const PAD_T = 10;
const PAD_B = 24;

export function UsageTab() {
  const visible = usePageVisible();
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const [hold, setHold] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const usage = useUsage(days, visible);
  const skip = useRef(true);
  const requestId = useRef(0);

  useEffect(() => {
    if (skip.current) {
      skip.current = false;
      return;
    }
    const id = ++requestId.current;
    setHold(true);
    void usage.refetch().finally(() => {
      if (requestId.current === id) setHold(false);
    });
  }, [days]);

  useEffect(() => {
    if (usage.isSuccess) {
      setPicked(null);
      setHovered(null);
    }
  }, [usage.dataUpdatedAt, usage.isSuccess]);

  const data = hold ? null : usage.data ?? null;
  const showErrorOnly = data === null && usage.isError && !hold;
  const empty = data !== null && data.totals.sessions === 0 && data.daily.length === 0;

  return (
    <div className="usage-root">
      <Head title="用量">
        {data !== null && usage.isError ? <span className="error">{errText(usage.error, "加载失败")}</span> : null}
        <div className="seg" role="group" aria-label="统计周期">
          {PERIODS.map((period) => (
            <button
              key={period}
              type="button"
              aria-pressed={days === period}
              aria-label={`最近 ${period} 天`}
              onClick={() => { if (period !== days) setDays(period); }}
            >
              {period} 天
            </button>
          ))}
        </div>
        <button type="button" className="btn" aria-label="刷新用量" disabled={usage.isFetching} onClick={() => void usage.refetch()}>
          {usage.isFetching ? "加载中…" : "刷新"}
        </button>
      </Head>
      {showErrorOnly ? (
        <div className="retry">
          <span className="error">{errText(usage.error, "加载失败")}</span>
          <button type="button" className="btn" aria-label="重试加载用量" onClick={() => void usage.refetch()}>重试</button>
        </div>
      ) : data === null ? (
        <Loading />
      ) : empty ? (
        <Empty>最近 {days} 天没有用量记录</Empty>
      ) : (
        <UsageBody
          data={data}
          picked={picked}
          hovered={hovered}
          onHover={setHovered}
          onPick={setPicked}
          onOpen={(event, session) => openSession(event.currentTarget, session, navigate)}
        />
      )}
      <p className="foot">费用数据服务端未记录，这里只统计 token；输入口径以 clawd 记录为准。</p>
    </div>
  );
}

function UsageBody({
  data,
  picked,
  hovered,
  onHover,
  onPick,
  onOpen,
}: {
  data: Usage;
  picked: number | null;
  hovered: number | null;
  onHover: (index: number | null) => void;
  onPick: (index: number | null) => void;
  onOpen: (event: MouseEvent<HTMLButtonElement>, session: SessionSummary) => void;
}) {
  const total = data.totals.input + data.totals.output;
  const now = Date.now();
  return (
    <div>
      <div className="stats">
        <Stat k="总 token" v={total} />
        <Stat k="输入" v={data.totals.input} />
        <Stat k="输出" v={data.totals.output} />
        <Stat k="会话数" v={data.totals.sessions} />
        <Stat k="API 调用" v={data.totals.apiCalls} />
      </div>
      {data.daily.length ? (
        <section className="card">
          <h2>每日 token</h2>
          <Chart daily={data.daily} picked={picked} hovered={hovered} onHover={onHover} onPick={onPick} />
        </section>
      ) : null}
      {data.byModel.length ? (
        <Table
          title="按模型"
          head={["模型", "提供方", "会话", "输入", "输出", "调用"]}
          textCols={2}
          rows={data.byModel.map((item) => [item.model || "—", item.provider || "—", item.sessions, item.input, item.output, item.apiCalls])}
        />
      ) : null}
      {data.bySource.length ? (
        <Table
          title="按来源"
          head={["来源", "会话", "输入", "输出"]}
          rows={data.bySource.map((item) => [sourceName(item.source), item.sessions, item.input, item.output])}
        />
      ) : null}
      {data.aux.length ? (
        <Table
          title="辅助任务"
          head={["任务", "会话", "调用", "输入", "输出"]}
          rows={data.aux.map((item) => [item.task, item.sessions, item.apiCalls, item.input, item.output])}
        />
      ) : null}
      {data.top.length ? (
        <section className="card">
          <h2>最耗量会话 Top 10</h2>
          <div className="list" style={{ marginTop: 8 }}>
            {data.top.slice(0, 10).map((item) => (
              <button
                key={item.id}
                type="button"
                className="top"
                aria-label={`打开会话 ${item.title || item.id.slice(0, 12)}`}
                onClick={(event) => onOpen(event, toSession(item))}
              >
                <span className="t">{item.title || item.id.slice(0, 12)}</span>
                <span className="n">{full(item.totalTokens)}</span>
                <small>{sourceName(item.source)} · {item.model ?? "—"} · {relativeTime(item.startedAt, now)}</small>
                <small className="r">{full(item.apiCalls)} 次调用</small>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function toSession(item: UsageTop): SessionSummary {
  return {
    id: item.id,
    source: item.source ?? "",
    title: item.title,
    model: item.model,
    started_at: item.startedAt,
    last_active: item.startedAt,
    message_count: item.messages,
    preview: null,
    pinned: false,
    archived: false,
  };
}

function Stat({ k, v }: { k: string; v: number }) {
  return (
    <div className="card"><div className="k">{k}</div><div className="v" title={full(v)}>{compact(v)}</div></div>
  );
}

function Chart({
  daily,
  picked,
  hovered,
  onHover,
  onPick,
}: {
  daily: UsageDay[];
  picked: number | null;
  hovered: number | null;
  onHover: (index: number | null) => void;
  onPick: (index: number | null) => void;
}) {
  const n = daily.length;
  const slot = Math.min(64, Math.max(14, Math.floor(780 / n)));
  const bw = Math.max(6, slot - Math.max(4, Math.round(slot * 0.25)));
  const w = PAD_L + n * slot + PAD_R;
  const plotH = CH_H - PAD_T - PAD_B;
  const max = niceMax(Math.max(...daily.map((day) => day.input + day.output)));
  const y = (value: number) => PAD_T + plotH - (value / max) * plotH;
  const labelEvery = Math.ceil(46 / slot);
  const active = hovered ?? picked;
  const detail = active === null ? null : daily[active];
  return (
    <div>
      <div className="legend">
        <span className="c-in"><i />输入</span><span className="c-out"><i />输出</span>
      </div>
      <div className="chart">
        <svg width={w} height={CH_H} viewBox={`0 0 ${w} ${CH_H}`} role="group" aria-label="每日 token 用量柱状图">
          {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
            const value = max * tick;
            return (
              <g key={tick}>
                <line className="grid" x1={PAD_L} x2={w - PAD_R} y1={y(value)} y2={y(value)} />
                <text x={PAD_L - 6} y={y(value) + 4} textAnchor="end" fontSize="10" fill="var(--text-tertiary)">{compact(value)}</text>
              </g>
            );
          })}
          {daily.map((day, index) => {
            const x = PAD_L + index * slot;
            const bx = x + (slot - bw) / 2;
            const hIn = (day.input / max) * plotH;
            const hOut = (day.output / max) * plotH;
            const label = `${day.day}：输入 ${full(day.input)}，输出 ${full(day.output)}，会话 ${full(day.sessions)}`;
            return (
              <g key={day.day}>
                <rect className="in" x={bx} y={PAD_T + plotH - hIn} width={bw} height={hIn} />
                <rect className="out" x={bx} y={PAD_T + plotH - hIn - hOut} width={bw} height={hOut} />
                {index % labelEvery === 0 ? <text x={x + slot / 2} y={CH_H - 7} textAnchor="middle" fontSize="10" fill="var(--text-tertiary)">{day.day.slice(5)}</text> : null}
                <rect
                  className="hit"
                  data-on={active === index ? "true" : "false"}
                  x={x}
                  y={PAD_T}
                  width={slot}
                  height={plotH}
                  tabIndex={0}
                  role="button"
                  aria-label={label}
                  onMouseEnter={() => onHover(index)}
                  onMouseLeave={() => onHover(null)}
                  onFocus={() => onHover(index)}
                  onBlur={() => onHover(null)}
                  onClick={() => onPick(picked === index ? null : index)}
                  onKeyDown={(event: ReactKeyboardEvent<SVGRectElement>) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onPick(picked === index ? null : index);
                    }
                  }}
                />
              </g>
            );
          })}
        </svg>
      </div>
      <div className="detail" aria-live="polite">
        {detail ? <><b>{detail.day}</b> · 输入 {full(detail.input)} · 输出 {full(detail.output)} · 会话 {full(detail.sessions)} · 调用 {full(detail.apiCalls)}</> : "悬停或点按柱子查看当日明细"}
      </div>
    </div>
  );
}

function Table({ title, head, rows, textCols = 1 }: { title: string; head: string[]; rows: (string | number)[][]; textCols?: number }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      <div className="tablewrap">
        <table>
          <thead>
            <tr>{head.map((item, index) => <th key={item} className={index < textCols ? "txt" : ""}>{item}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, index) => (
                  <td key={index} className={index < textCols ? "txt" : ""}>
                    {typeof cell === "number" ? full(cell) : cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
