import { LazyMotion, domAnimation, m } from "motion/react";
import type { ReactNode } from "react";

export const spring = { type: "spring", stiffness: 360, damping: 30 } as const;

export function MotionRoot({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={domAnimation} strict>
      {children}
    </LazyMotion>
  );
}

export { m };
