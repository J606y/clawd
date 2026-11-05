import { useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

/** 未缩放时，螃蟹动画全程外接盒相对 SVG 边框的实测值。按轨道高度缩小，外接盒才进得了轨道。 */
const LOCAL_X = 17.444686889648438;
const LOCAL_Y = 71.01053619384766;
const UNION_W = 367.3545684814453;
const UNION_H = 274.15045166015625;
const PAD = 1;

function fitCrab(height: number) {
  const scale = Math.max(0, height - PAD) / UNION_H;
  return {
    scale,
    slot: UNION_W * scale + PAD,
    left: PAD / 2 - LOCAL_X * scale,
    top: PAD / 2 - LOCAL_Y * scale,
  };
}

export const ULTRA_CRAB_WIDTH = `${fitCrab(28).slot}px`;

const NOTE_COLORS = ["#A78BFA", "#8B5CF6"] as const;
const EMIT_X = 118;
const EMIT_Y = 205;
const NOTES = [
  { x: 0, y: 0, dx: "-22px", dy: "-22px", r: "-8deg", d: "7.8s", l: "0s" },
  { x: 2, y: -1, dx: "4px", dy: "-26px", r: "7deg", d: "7.8s", l: "1.3s" },
  { x: -2, y: 1, dx: "24px", dy: "-18px", r: "-11deg", d: "7.8s", l: "2.6s" },
  { x: 1, y: 2, dx: "-26px", dy: "6px", r: "9deg", d: "7.8s", l: "3.9s" },
  { x: -1, y: -2, dx: "22px", dy: "12px", r: "-6deg", d: "7.8s", l: "5.2s" },
  { x: 2, y: 1, dx: "-6px", dy: "20px", r: "5deg", d: "7.8s", l: "6.5s" },
];

const GLYPH_H = 33;

function letterInkHeight() {
  const label = document.querySelector(".effort-picker .cur");
  if (!label) return 7;
  const style = getComputedStyle(label);
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) return 7;
  const font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  ctx.font = font;
  const width = Math.ceil(ctx.measureText("a").width) + 4;
  const size = Math.ceil(parseFloat(style.fontSize)) + 12;
  canvas.width = Math.max(width, 1);
  canvas.height = Math.max(size, 1);
  ctx.font = font;
  ctx.fillStyle = "#000";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("a", 2, size - 4);
  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let min = canvas.height;
  let max = 0;
  let seen = false;
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      if (pixels[(y * canvas.width + x) * 4 + 3] > 0) {
        seen = true;
        min = Math.min(min, y);
        max = Math.max(max, y);
      }
    }
  }
  return seen ? max - min + 1 : 7;
}

export function UltraCrab({
  onPointerDown,
  onPointerMove,
  onPointerUp,
}: {
  onPointerDown?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onPointerMove?: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onPointerUp?: (event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(() => fitCrab(28));
  useLayoutEffect(() => {
    const track = ref.current?.parentElement;
    const slot = track?.parentElement;
    if (!track || !slot) return;
    const apply = () => {
      const height = track.getBoundingClientRect().height;
      if (height <= 0) return;
      const next = fitCrab(height);
      track.style.setProperty("--ultra-crab", `${next.slot}px`);
      slot.style.setProperty("--note-x", `${next.left + EMIT_X * next.scale}px`);
      slot.style.setProperty("--note-y", `${next.top + EMIT_Y * next.scale}px`);
      setFit((prev) => (Math.abs(prev.scale - next.scale) < 0.0001 ? prev : next));
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(track);
    return () => {
      observer.disconnect();
      track.style.removeProperty("--ultra-crab");
      slot.style.removeProperty("--note-x");
      slot.style.removeProperty("--note-y");
    };
  }, []);
  return (
    <div
      className="ultra-crab"
      data-ultra-crab=""
      ref={ref}
      aria-hidden="true"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <svg
        className="ultra-crab-svg"
        width="380"
        height="350"
        viewBox="0 -50 380 350"
        shapeRendering="crispEdges"
        overflow="visible"
        style={{ left: fit.left, top: fit.top, transform: `scale(${fit.scale})` }}
      >
        <g fill="#A78BFA">
          <rect className="sl" x="6" y="140" width="20" height="4" />
          <rect className="sl" x="6" y="165" width="20" height="4" style={{ animationDelay: "-.08s" }} />
          <rect className="sl" x="6" y="190" width="20" height="4" style={{ animationDelay: "-.04s" }} />
          <rect className="sl" x="352" y="150" width="20" height="4" style={{ animationDelay: "-.1s" }} />
          <rect className="sl" x="352" y="172" width="20" height="4" style={{ animationDelay: "-.05s" }} />
        </g>
        <g className="bang">
          <g fill="#D97757">
            <rect className="la" x="100" y="240" width="20" height="40" />
            <rect className="lb" x="140" y="240" width="20" height="40" />
            <rect className="lb" x="220" y="240" width="20" height="40" />
            <rect className="la" x="260" y="240" width="20" height="40" />
            <rect x="80" y="120" width="220" height="120" />
            <rect className="strum" x="40" y="200" width="40" height="40" />
          </g>
          <g fill="#1F1E1D">
            <rect x="120" y="170" width="20" height="10" />
            <rect x="110" y="180" width="10" height="10" />
            <rect x="140" y="180" width="10" height="10" />
            <rect x="240" y="170" width="20" height="10" />
            <rect x="230" y="180" width="10" height="10" />
            <rect x="260" y="180" width="10" height="10" />
          </g>
          <g className="hat">
            <g fill="#8B5A2B">
              <rect x="120" y="60" width="140" height="40" />
              <rect x="140" y="50" width="40" height="10" />
              <rect x="200" y="50" width="40" height="10" />
              <rect x="60" y="100" width="260" height="20" />
              <rect x="40" y="80" width="20" height="20" />
              <rect x="320" y="80" width="20" height="20" />
            </g>
            <rect x="120" y="85" width="140" height="15" fill="#5A3A1A" />
          </g>
          <g className="guitar">
            <rect x="230" y="218" width="118" height="10" fill="#5A3A1A" />
            <g fill="#C98A3B">
              <rect x="130" y="210" width="110" height="40" />
              <rect x="150" y="196" width="70" height="14" />
            </g>
            <rect x="175" y="222" width="20" height="16" fill="#3A2410" />
            <rect x="300" y="200" width="40" height="40" fill="#D97757" />
            <rect x="348" y="208" width="14" height="30" fill="#5A3A1A" />
          </g>
          <rect className="strum" x="90" y="224" width="80" height="16" fill="#D97757" />
        </g>
      </svg>
    </div>
  );
}

export function UltraNotes() {
  const [scale, setScale] = useState(7 / GLYPH_H);
  useLayoutEffect(() => {
    setScale(letterInkHeight() / GLYPH_H);
  }, []);
  return (
    <svg className="ultra-notes" data-ultra-notes="" aria-hidden="true" overflow="visible">
      {NOTES.map((note, index) => (
        <g key={note.l} transform={`translate(${note.x} ${note.y})`}>
          <g className="fly" data-ultra-note="" fill={NOTE_COLORS[index % 2]} style={{
              ["--dx" as string]: note.dx,
              ["--dy" as string]: note.dy,
              ["--r" as string]: note.r,
              ["--d" as string]: note.d,
              ["--l" as string]: note.l,
            }}>
            <g transform={`scale(${scale})`}>
              <rect x="0" y="0" width="12" height="9" />
              <rect x="9" y="-24" width="3" height="24" />
              <rect x="12" y="-24" width="9" height="5" />
              <rect x="15" y="-19" width="6" height="6" />
            </g>
          </g>
        </g>
      ))}
    </svg>
  );
}
