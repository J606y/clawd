import { useEffect, useRef, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";
import {
  codePointLength,
  releaseCount,
  segmentStateWords,
  segmentWordsIncremental,
  sliceCodePoints,
  splitRegions,
  type SegmentState,
} from "./reveal";

const FINE = window.matchMedia("(hover: hover) and (pointer: fine)");
const DESKTOP = window.matchMedia("(min-width: 768px)");
const COARSE = window.matchMedia("(pointer: coarse)");
const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
const REDUCE_TRANSPARENCY = window.matchMedia("(prefers-reduced-transparency: reduce)");

export function reducedMotion(): boolean {
  return REDUCE_MOTION.matches;
}

/** 窄屏或触屏：默认不做逐词揭示，直接全量显示（逐词揭示是移动端掉帧的主因）。 */
export function mobileReveal(): boolean {
  return !DESKTOP.matches || COARSE.matches;
}

export function blurEligible(): boolean {
  return FINE.matches && DESKTOP.matches && !REDUCE_MOTION.matches && !REDUCE_TRANSPARENCY.matches;
}

function markdown(html: string) {
  return <div className="hp-md" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function StaticMarkdown({ text }: { text: string }) {
  if (!text) return null;
  return markdown(renderMarkdown(text));
}

/** 揭示节流：两次 setShown 至少间隔 33ms（约 30fps），追平目标时立即刷新。 */
const REVEAL_FLUSH_MS = 33;
/** 模糊降级阈值：连续两拍间隔超过它，说明主线程已吃紧，关掉词模糊。 */
const BLUR_DROP_MS = 66;

export function RevealedBody({
  text,
  active,
  pace,
  ownerKey,
}: {
  text: string;
  active: boolean;
  pace: boolean;
  ownerKey: string;
}) {
  const shouldPace = (a: boolean, p: boolean) => a && p && !reducedMotion() && !mobileReveal();
  const total = codePointLength(text);
  const [shown, setShown] = useState(shouldPace(active, pace) ? 0 : total);
  const [blur, setBlur] = useState(blurEligible);
  const seen = useRef(new Set<string>());
  const flags = useRef(new Map<string, boolean>());
  const segCache = useRef(new Map<number, SegmentState>());
  const blockCache = useRef(new Map<number, { text: string; html: string }>());
  const totalRef = useRef(total);
  const activeRef = useRef(active);
  const paceRef = useRef(pace);
  const shownRef = useRef(shown);
  const lastKey = useRef(ownerKey);
  if (lastKey.current !== ownerKey) {
    lastKey.current = ownerKey;
    seen.current = new Set();
    flags.current = new Map();
    segCache.current = new Map();
    blockCache.current = new Map();
    const next = shouldPace(active, pace) ? 0 : total;
    shownRef.current = next;
    setShown(next);
    setBlur(blurEligible());
  }

  totalRef.current = total;
  activeRef.current = active;
  paceRef.current = pace;

  useEffect(() => {
    let timer = 0;
    let last = performance.now();
    let lastFlush = 0;
    const tick = () => {
      const now = performance.now();
      if (now - last > BLUR_DROP_MS) setBlur(false);
      last = now;
      const target = totalRef.current;
      const flush = !activeRef.current || !paceRef.current || reducedMotion() || mobileReveal() || document.visibilityState === "hidden";
      const prev = shownRef.current;
      const next = flush || prev > target ? target : prev >= target ? prev : Math.min(target, prev + releaseCount(target - prev));
      if (next !== prev) {
        shownRef.current = next;
        // 30fps 节流：内部进度每拍都推进，只把 setShown 压到 33ms 一次。
        if (flush || next >= target || now - lastFlush >= REVEAL_FLUSH_MS) {
          lastFlush = now;
          setShown(next);
        }
      }
      if (activeRef.current || shownRef.current < target) timer = window.setTimeout(tick, REVEAL_FLUSH_MS);
    };
    timer = window.setTimeout(tick, REVEAL_FLUSH_MS);
    return () => window.clearTimeout(timer);
  }, [ownerKey, active]);

  const visible = sliceCodePoints(text, shown);
  const animate = active && !reducedMotion() && !mobileReveal();
  const allowBlur = animate && blur && blurEligible();
  return (
    <div className="hp-md">
      {splitRegions(visible).map((region) => {
        if (region.kind === "block") {
          const key = `b:${region.start}`;
          const known = flags.current.get(key);
          const fresh = known ?? (animate && !seen.current.has(key));
          if (known === undefined) flags.current.set(key, fresh);
          if (fresh) seen.current.add(key);
          // 代码块脏检查：内容没变直接复用上次的 HTML，不重跑 renderMarkdown。
          const cached = blockCache.current.get(region.start);
          let html: string;
          if (cached && cached.text === region.text) {
            html = cached.html;
          } else {
            html = renderMarkdown(region.text, false);
            blockCache.current.set(region.start, { text: region.text, html });
          }
          return (
            <div key={key} className={fresh ? "hp-block-in" : undefined} dangerouslySetInnerHTML={{ __html: html }} />
          );
        }
        if (!animate) {
          // 全量显示（移动端默认 / 减弱动态 / 已结束）：整段一个文本节点，
          // 不分词、不建词 span；复用 hp-word 的 display 与 white-space，外观与无动画的词 span 一致。
          return <span key={`p:${region.start}`} className="hp-word">{region.text}</span>;
        }
        let cursor = region.start;
        const state = segmentWordsIncremental(segCache.current.get(region.start) ?? null, region.text);
        segCache.current.set(region.start, state);
        return segmentStateWords(state).map((word) => {
          const key = `w:${cursor}`;
          cursor += word.length;
          const known = flags.current.get(key);
          const fresh = known ?? (animate && word.trim().length > 0);
          if (known === undefined) flags.current.set(key, fresh);
          const className = fresh ? (allowBlur ? "hp-word hp-word-in hp-word-blur" : "hp-word hp-word-in") : "hp-word";
          return <span key={key} className={className}>{word}</span>;
        });
      })}
    </div>
  );
}
