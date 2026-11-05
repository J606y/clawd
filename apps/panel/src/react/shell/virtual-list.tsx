import { useVirtualizer } from "@tanstack/react-virtual";
import { useLayoutEffect, useRef, type ReactNode, type UIEvent } from "react";
import { anchoredScrollTop, type SizedRow } from "./session-logic";

export function AnchoredVirtualList(props: {
  rows: readonly SizedRow[];
  resetKey: string;
  className?: string;
  label: string;
  footer?: ReactNode;
  children: (index: number) => ReactNode;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const offsetRef = useRef(0);
  const rowsRef = useRef(props.rows);
  const seenRows = useRef(props.rows);
  const seenReset = useRef(props.resetKey);
  const pendingScroll = useRef<number | null>(null);

  if (seenReset.current !== props.resetKey) {
    seenReset.current = props.resetKey;
    pendingScroll.current = 0;
    offsetRef.current = 0;
    seenRows.current = props.rows;
  } else if (seenRows.current !== props.rows) {
    pendingScroll.current = anchoredScrollTop(seenRows.current, props.rows, offsetRef.current);
    seenRows.current = props.rows;
  }
  rowsRef.current = props.rows;

  const virtualizer = useVirtualizer({
    count: props.rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => rowsRef.current[index]?.size ?? 68,
    getItemKey: (index) => rowsRef.current[index]?.id ?? index,
    overscan: 8,
    anchorTo: offsetRef.current > 1 ? "end" : "start",
    followOnAppend: false,
    onChange: (instance) => {
      if (pendingScroll.current === null) offsetRef.current = instance.scrollOffset ?? 0;
    },
  });

  useLayoutEffect(() => {
    const parent = parentRef.current;
    const next = pendingScroll.current;
    pendingScroll.current = null;
    if (!parent || next === null) return;
    const top = Math.max(0, next);
    if (Math.abs(parent.scrollTop - top) > 1) parent.scrollTop = top;
    offsetRef.current = parent.scrollTop;
  }, [props.rows, props.resetKey]);

  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    if (pendingScroll.current !== null) return;
    offsetRef.current = event.currentTarget.scrollTop;
  };

  const items = virtualizer.getVirtualItems();
  return (
    <div
      ref={parentRef}
      className={props.className}
      role="list"
      aria-label={props.label}
      onScroll={onScroll}
    >
      <div className="shell-vsizer" style={{ height: virtualizer.getTotalSize() }}>
        {items.map((item) => (
          <div
            key={String(item.key)}
            data-index={item.index}
            role="listitem"
            className="shell-vitem"
            style={{ height: item.size, transform: `translateY(${item.start}px)` }}
          >
            {props.children(item.index)}
          </div>
        ))}
      </div>
      {props.footer}
    </div>
  );
}
