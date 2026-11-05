import { useEffect, useMemo, useRef, useState } from "react";
import type { ModelChoice, ModelOptions } from "@/lib/api";
import { contextRingLevel, contextRingStroke } from "@/lib/context-ring";
import { EFFORT_LEVELS, GROK_EFFORT_LEVELS, type EffortLevel } from "@/lib/effort";
import { canonicalModel, isGeminiModel, isGrokModel } from "@/lib/model-effort";
import { menuOffset, menuRightOffset } from "@/lib/popover";
import { UltraCrab, UltraNotes } from "./ultra-crab";
import type { ContextUsage } from "@/lib/sidecar";

const RING_R = 7;
const RING_C = 2 * Math.PI * RING_R;
const EFFORT_MENU = 264;

export function levelsFor(model: string): EffortLevel[] {
  if (isGeminiModel(model)) return EFFORT_LEVELS.slice(0, 3);
  if (isGrokModel(model)) return GROK_EFFORT_LEVELS;
  return EFFORT_LEVELS;
}

export function sliderEffort(model: string, stored: string): string {
  const levels = levelsFor(model);
  if (levels.some((level) => level.id === stored)) return stored;
  if (isGrokModel(model) && (stored === "xhigh" || stored === "max" || stored === "ultra")) return "ultra";
  return levels[levels.length - 1]?.id ?? "medium";
}

function grouped(choices: ModelChoice[]): boolean {
  return new Set(choices.map((choice) => choice.provider)).size > 1;
}

function compact(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(Math.round(n));
}

function Chevron() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M2.5 6.5 5 4l2.5 2.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </svg>
  );
}

export function ModelPicker({
  value,
  provider,
  options,
  failed,
  onOpen,
  onSelect,
}: {
  value: string;
  provider: string;
  options: ModelOptions | undefined;
  failed: boolean;
  onOpen: () => void;
  onSelect: (model: string, provider: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const choices = options?.choices ?? [];
  const current = canonicalModel(value || options?.current.model || "", choices);
  const groups = useMemo(() => {
    const map = new Map<string, ModelChoice[]>();
    for (const choice of choices) {
      const list = map.get(choice.providerName) ?? [];
      list.push(choice);
      map.set(choice.providerName, list);
    }
    return [...map.entries()];
  }, [choices]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !event.composedPath().includes(rootRef.current)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="model-picker" ref={rootRef}>
      <button
        type="button"
        className="trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        title="切换模型"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next && rootRef.current) {
            setOffset(menuOffset(rootRef.current, 258));
            onOpen();
          }
        }}
      >
        <span>{current || "模型"}</span>
        <Chevron />
      </button>
      {open ? (
        <div className="menu" role="menu" aria-label="选择模型" style={{ left: offset }}>
          <div className="sheet">
          {!options ? <div className="note">{failed ? "模型列表加载失败" : "加载中…"}</div> : null}
          {options && groups.length === 0 ? <div className="note">没有可用的模型</div> : null}
          {groups.map(([name, items]) => (
            <div key={name}>
              {grouped(choices) ? <h4>{name}</h4> : null}
              {items.map((choice) => (
                <button
                  key={`${choice.provider}:${choice.model}`}
                  type="button"
                  role="menuitemradio"
                  aria-checked={choice.model === current}
                  onClick={() => {
                    setOpen(false);
                    if (choice.model === current && choice.provider === provider) return;
                    onSelect(choice.model, choice.provider);
                  }}
                >
                  <span>{choice.model}</span>
                  {choice.model === current ? <span className="check" aria-hidden="true">✓</span> : null}
                </button>
              ))}
            </div>
          ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function EffortPicker({
  model,
  stored,
  sessionId,
  onChange,
}: {
  model: string;
  stored: string;
  sessionId: string;
  onChange: (level: string) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef(false);
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const [dragging, setDragging] = useState(false);
  const levels = levelsFor(model);
  const value = sliderEffort(model, stored);
  const index = Math.max(0, levels.findIndex((level) => level.id === value));
  const current = levels[index] ?? levels[0];
  const ultra = current?.id === "ultra";
  const showCrab = ultra || dragging;
  const last = Math.max(1, levels.length - 1);
  const seek = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const next = levels[Math.round(ratio * last)];
    if (next) onChange(next.id);
  };
  useEffect(() => setOpen(false), [sessionId]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !event.composedPath().includes(rootRef.current)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="effort-picker" ref={rootRef}>
      <button
        type="button"
        className="trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        title="思考强度"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next && rootRef.current) setOffset(menuRightOffset(rootRef.current, EFFORT_MENU));
        }}
      >
        <span>思考：{current?.label ?? "中"}</span>
        <Chevron />
      </button>
      {open ? (
        <div className="panel" role="dialog" aria-label="思考强度（对之后的消息生效）" style={{ right: offset }}>
          <div className="head">
            <span className="title">思考强度</span>
            <span className={ultra ? "cur ultra" : "cur"}>{current?.label ?? "中"}</span>
          </div>
          <div className="ends"><span>更快</span><span>更聪明</span></div>
          <div className="track-slot">
            <div ref={trackRef} className={ultra ? "track ultra" : "track"} style={{ ["--f" as string]: String(index / last) }}>
              <div className="dots" aria-hidden="true">
                {levels.map((level, dot) => <i key={level.id} style={{ ["--p" as string]: String(dot / last) }} />)}
              </div>
              {showCrab ? (
                <UltraCrab
                  onPointerDown={(event) => {
                    if (event.button !== 0) return;
                    event.preventDefault();
                    dragRef.current = true;
                    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* 合成事件没有真实指针 */ }
                    setDragging(true);
                    seek(event.clientX);
                  }}
                  onPointerMove={(event) => {
                    if (!dragRef.current && !event.currentTarget.hasPointerCapture(event.pointerId)) return;
                    seek(event.clientX);
                  }}
                  onPointerUp={(event) => {
                    dragRef.current = false;
                    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
                    setDragging(false);
                  }}
                />
              ) : null}
              <input
                type="range"
                min={0}
                max={Math.max(0, levels.length - 1)}
                step={1}
                value={index}
                aria-label="思考强度"
                aria-valuetext={current?.label ?? ""}
                onChange={(event) => {
                  const next = levels[Number(event.target.value)];
                  if (next) onChange(next.id);
                }}
              />
            </div>
            {ultra ? <UltraNotes /> : null}
          </div>
          <p className="hint" title={current?.hint ?? ""}>{current?.hint ?? ""}</p>
        </div>
      ) : null}
    </div>
  );
}

export function ContextRing({
  sessionId,
  usage,
  failed,
  onOpen,
}: {
  sessionId: string;
  usage: ContextUsage | undefined;
  failed: boolean;
  onOpen: () => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  useEffect(() => setOpen(false), [sessionId]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (rootRef.current && !event.composedPath().includes(rootRef.current)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  if (!sessionId || failed || !usage || typeof usage.used !== "number") return null;
  const used = usage.used;
  const windowTokens = typeof usage.window === "number" ? usage.window : null;
  const ratio = windowTokens ? Math.min(1, used / windowTokens) : null;
  const pct = ratio === null ? null : Math.round(ratio * 100);
  const level = pct === null ? "ok" : contextRingLevel(pct);
  const stroke = contextRingStroke(level);
  const closed = level === "full";
  const title = windowTokens === null
    ? `上下文已用 ${used.toLocaleString()} tokens（窗口大小未知），点击查看详情`
    : `上下文已用 ${used.toLocaleString()} / ${windowTokens.toLocaleString()} tokens（${pct}%），点击查看详情`;
  return (
    <div className="context-ring" ref={rootRef}>
      <button
        type="button"
        className="wrap"
        title={title}
        aria-label={title}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (!next || !rootRef.current) return;
          const width = Math.min(320, window.innerWidth - 32);
          const rect = rootRef.current.getBoundingClientRect();
          setOffset(Math.max(16 - rect.left, Math.min(0, window.innerWidth - 16 - width - rect.left)));
          onOpen();
        }}
      >
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
          <circle className="track" cx="9" cy="9" r={RING_R} fill="none" strokeWidth="2.4" />
          {ratio === null ? null : (
            <circle
              className="fill"
              data-level={level}
              cx="9"
              cy="9"
              r={RING_R}
              fill="none"
              strokeWidth="2.8"
              strokeLinecap={closed ? "butt" : "round"}
              strokeDasharray={closed ? undefined : RING_C}
              strokeDashoffset={closed ? undefined : RING_C * (1 - ratio)}
              style={{ stroke }}
            />
          )}
        </svg>
        {pct === null ? compact(used) : `${pct}%`}
      </button>
      {open ? <ContextBody usage={{ ...usage, used }} pct={pct} level={level} offset={offset} /> : null}
    </div>
  );
}

function ContextBody({ usage, pct, level, offset }: { usage: ContextUsage & { used: number }; pct: number | null; level: string; offset: number }) {
  const win = usage.window;
  const cached = Math.min(usage.cached ?? 0, usage.used);
  const fresh = usage.used - cached;
  const total = usage.session;
  return (
    <div className="pop" role="dialog" aria-label="上下文详情" style={{ left: offset }}>
      <div className="row"><b>上下文窗口</b><span>{compact(usage.used)}{win ? ` / ${compact(win)}（${pct}%）` : "（窗口大小未知）"}</span></div>
      {win ? (
        <>
          <div className="bar" data-level={level} role="img" aria-label={`已用 ${pct}%`}>
            <i className="cached" style={{ width: `${(cached / win) * 100}%` }} />
            <i className="fresh" style={{ width: `${(fresh / win) * 100}%` }} />
          </div>
          {cached > 0 ? (
            <div className="legend">
              <span><i className="c" />缓存命中 {compact(cached)}</span>
              <span><i />新输入 {compact(fresh)}</span>
            </div>
          ) : null}
        </>
      ) : null}
      <div className="sec">
        <h4>最近一次调用</h4>
        <div className="row"><span>输入</span><span>{usage.used.toLocaleString()} tokens</span></div>
        {cached > 0 ? <div className="row"><span>缓存命中率</span><span>{Math.round((cached / usage.used) * 100)}%</span></div> : null}
        {typeof usage.out === "number" ? <div className="row"><span>输出</span><span>{usage.out.toLocaleString()} tokens</span></div> : null}
        <div className="row"><span>模型</span><span>{usage.model ?? "—"}</span></div>
        {usage.at ? <div className="row"><span>时间</span><span>{usage.at.slice(5)}</span></div> : null}
      </div>
      {total ? (
        <div className="sec">
          <h4>本对话累计</h4>
          <div className="row"><span>输入 / 输出</span><span>{compact(total.input)} / {compact(total.output)}</span></div>
          {total.cacheRead > 0 ? <div className="row"><span>缓存读取</span><span>{compact(total.cacheRead)}</span></div> : null}
          <div className="row"><span>API 调用 / 消息</span><span>{total.apiCalls.toLocaleString()} 次 / {total.messages.toLocaleString()} 条</span></div>
        </div>
      ) : null}
      {usage.source === "default" ? <div className="note">窗口大小是 clawd 识别不出该模型时采用的默认值。要精确显示，在服务器上执行 <code>clawd config set model.context_length 窗口大小</code>。</div> : null}
      {usage.source === "unknown" ? <div className="note">clawd 没有记录这个模型的窗口大小，只显示已用量。</div> : null}
    </div>
  );
}
