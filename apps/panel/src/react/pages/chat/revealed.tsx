import { useEffect, useRef, useState } from "react";
import { renderMarkdown } from "@/lib/markdown";
import { releaseCount, segmentWords, sliceCodePoints, splitRegions, codePointLength } from "./reveal";

const FINE = window.matchMedia("(hover: hover) and (pointer: fine)");
const DESKTOP = window.matchMedia("(min-width: 768px)");
const REDUCE_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
const REDUCE_TRANSPARENCY = window.matchMedia("(prefers-reduced-transparency: reduce)");

export function reducedMotion(): boolean {
  return REDUCE_MOTION.matches;
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
  const total = codePointLength(text);
  const [shown, setShown] = useState(active && pace && !reducedMotion() ? 0 : total);
  const [blur, setBlur] = useState(blurEligible);
  const seen = useRef(new Set<string>());
  const flags = useRef(new Map<string, boolean>());
  const lastKey = useRef(ownerKey);
  if (lastKey.current !== ownerKey) {
    lastKey.current = ownerKey;
    seen.current = new Set();
    flags.current = new Map();
    setShown(active && pace && !reducedMotion() ? 0 : total);
    setBlur(blurEligible());
  }

  const totalRef = useRef(total);
  const activeRef = useRef(active);
  const paceRef = useRef(pace);
  const shownRef = useRef(shown);
  totalRef.current = total;
  activeRef.current = active;
  paceRef.current = pace;
  shownRef.current = shown;

  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      if (now - last > 32) setBlur(false);
      last = now;
      const target = totalRef.current;
      const flush = !activeRef.current || !paceRef.current || reducedMotion() || document.visibilityState === "hidden";
      const prev = shownRef.current;
      const next = flush || prev > target ? target : prev >= target ? prev : Math.min(target, prev + releaseCount(target - prev));
      if (next !== prev) {
        shownRef.current = next;
        setShown(next);
      }
      if (activeRef.current || shownRef.current < target) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [ownerKey, active, text]);

  const visible = sliceCodePoints(text, shown);
  const animate = active && !reducedMotion();
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
          return (
            <div key={key} className={fresh ? "hp-block-in" : undefined} dangerouslySetInnerHTML={{ __html: renderMarkdown(region.text, false) }} />
          );
        }
        let cursor = region.start;
        return segmentWords(region.text).map((word) => {
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
