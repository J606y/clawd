import { useCallback, useEffect, useImperativeHandle, useRef, type ReactNode, type Ref, type UIEvent } from "react";
import { anchoredScrollTop } from "./reveal";

export interface Anchor {
  key: string;
  offset: number;
}

const ESTIMATE = 96;
const STICK_PX = 80;

export interface ListHandle {
  capture(): Anchor | null;
}

export function MessageList({
  keys,
  stick,
  hold,
  listRef,
  onStick,
  onNearTop,
  onUserScroll,
  children,
}: {
  keys: readonly string[];
  stick: boolean;
  hold: Anchor | null;
  listRef: Ref<ListHandle>;
  onStick: (stick: boolean, atBottom: boolean) => void;
  onNearTop: () => void;
  onUserScroll: () => void;
  children: ReactNode;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const sizes = useRef(new Map<string, number>());
  const lastTop = useRef(0);
  const adjusting = useRef<number | null>(null);
  const stickRef = useRef(stick);
  stickRef.current = stick;
  const adjustScroll = useCallback((el: HTMLDivElement, requested: number) => {
    const target = Math.max(0, Math.min(requested, el.scrollHeight - el.clientHeight));
    if (target === el.scrollTop) return;
    el.scrollTop = target;
    // A clamped no-op emits no scroll event; never consume the next reader gesture.
    adjusting.current = el.scrollTop;
    lastTop.current = el.scrollTop;
  }, []);

  useImperativeHandle(listRef, () => ({
    capture() {
      const el = parentRef.current;
      if (!el) return null;
      const bounds = el.getBoundingClientRect();
      const nodes = el.querySelectorAll<HTMLElement>("[data-history-source]");
      const count = nodes.length;
      if (count === 0) return null;
      const intersects = (rect: DOMRect) => rect.bottom > bounds.top && rect.top < bounds.bottom;
      const anchorOf = (node: HTMLElement, rect: DOMRect): Anchor => ({
        key: node.dataset.historySource ?? "",
        offset: rect.top - bounds.top,
      });
      const linear = (): Anchor | null => {
        for (const node of nodes) {
          const rect = node.getBoundingClientRect();
          if (intersects(rect)) return anchorOf(node, rect);
        }
        return null;
      };
      // 历史源按 DOM 从上往下是纯单调块布局时，用与旧 readingAnchor 相同的下界二分。
      // 采样 bottom 逆序，或前一个节点并未完全在视口之上，说明不是单调布局，退回线性扫描。
      const seen = new Map<number, DOMRect>();
      const rectAt = (index: number) => {
        const cached = seen.get(index);
        if (cached) return cached;
        const rect = nodes[index]!.getBoundingClientRect();
        seen.set(index, rect);
        return rect;
      };
      let low = 0;
      let high = count - 1;
      let firstIdx = -1;
      let monotonic = true;
      let lastIndex = -1;
      let lastBottom = Number.NEGATIVE_INFINITY;
      while (low <= high) {
        const mid = (low + high) >> 1;
        const rect = rectAt(mid);
        if (lastIndex >= 0) {
          const inverted = mid > lastIndex ? rect.bottom < lastBottom : rect.bottom > lastBottom;
          if (inverted) {
            monotonic = false;
            break;
          }
        }
        lastIndex = mid;
        lastBottom = rect.bottom;
        if (rect.bottom > bounds.top) {
          firstIdx = mid;
          high = mid - 1;
        } else {
          low = mid + 1;
        }
      }
      if (!monotonic) return linear();
      if (firstIdx < 0) return null;
      const rect = rectAt(firstIdx);
      const prevOk = firstIdx === 0 || rectAt(firstIdx - 1).bottom <= bounds.top;
      if (!prevOk) return linear();
      if (intersects(rect)) return anchorOf(nodes[firstIdx]!, rect);
      return null;
    },
  }), []);

  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const pin = () => {
      if (!stickRef.current) return;
      adjustScroll(el, el.scrollHeight - el.clientHeight);
    };
    pin();
    const content = el.firstElementChild;
    if (!(content instanceof HTMLElement)) return;
    const observer = new ResizeObserver(pin);
    observer.observe(content);
    return () => observer.disconnect();
  }, [stick, keys, children, adjustScroll]);

  useEffect(() => {
    if (!hold) return;
    const el = parentRef.current;
    if (!el) return;
    const top = anchoredScrollTop(keys, sizes.current, ESTIMATE, hold.key, hold.offset);
    const node = el.querySelector(`[data-history-source="${CSS.escape(hold.key)}"]`);
    if (node instanceof HTMLElement) {
      const delta = node.getBoundingClientRect().top - el.getBoundingClientRect().top - hold.offset;
      if (Math.abs(delta) > 1) adjustScroll(el, el.scrollTop + delta);
      return;
    }
    if (top !== null) adjustScroll(el, top);
  }, [hold, keys, adjustScroll]);

  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    const expected = adjusting.current;
    adjusting.current = null;
    if (expected !== null && Math.abs(el.scrollTop - expected) < 1) {
      lastTop.current = el.scrollTop;
      return;
    }
    onUserScroll();
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    const up = el.scrollTop < lastTop.current;
    let nextStick = stick;
    if (up && gap > 2) nextStick = false;
    else if (gap < STICK_PX && el.scrollTop > lastTop.current) nextStick = true;
    lastTop.current = el.scrollTop;
    onStick(nextStick, nextStick && gap < STICK_PX);
    if (up && el.scrollTop < 180 && el.scrollHeight > el.clientHeight) onNearTop();
  };

  return (
    <div ref={parentRef} className="scroll" onScroll={onScroll}>
      {children}
    </div>
  );
}
