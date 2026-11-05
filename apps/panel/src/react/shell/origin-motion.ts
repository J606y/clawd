export interface OriginRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const ORIGIN_SCALE_MIN = 0.12;
export const ORIGIN_SCALE_MAX = 0.4;

export function sheetFromOrigin(resting: OriginRect, origin: OriginRect): { x: number; y: number; scale: number } | null {
  const values = [resting.x, resting.y, resting.width, resting.height, origin.x, origin.y, origin.width, origin.height];
  if (values.some((value) => !Number.isFinite(value))) return null;
  if (resting.width < 1 || resting.height < 1 || origin.width < 1 || origin.height < 1) return null;
  const scale = Math.min(ORIGIN_SCALE_MAX, Math.max(origin.width / resting.width, ORIGIN_SCALE_MIN));
  return {
    x: (origin.x + origin.width / 2) - (resting.x + resting.width / 2),
    y: (origin.y + origin.height / 2) - (resting.y + resting.height / 2),
    scale,
  };
}

export function originTransform(motion: { x: number; y: number; scale: number }): string {
  return `translate3d(${motion.x}px, ${motion.y}px, 0) scale(${motion.scale})`;
}

export function pickOrigin(candidates: readonly OriginRect[], saved: OriginRect | null): OriginRect | null {
  const visible = candidates.filter((rect) => rect.width >= 1 && rect.height >= 1 && Number.isFinite(rect.x) && Number.isFinite(rect.y));
  if (!visible.length) return saved;
  if (!saved) return visible[0];
  return visible.reduce((best, rect) => {
    const dist = (rect.x - saved.x) ** 2 + (rect.y - saved.y) ** 2;
    const bestDist = (best.x - saved.x) ** 2 + (best.y - saved.y) ** 2;
    return dist < bestDist ? rect : best;
  });
}
