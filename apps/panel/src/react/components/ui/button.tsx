import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { m, spring } from "@/styles/motion";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium hover:brightness-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)] disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-[var(--accent)] text-[var(--accent-text)]",
        destructive: "bg-[var(--danger)] text-white",
        outline: "border border-[var(--line)] bg-[var(--bg-elevated)] text-[var(--text)]",
        secondary: "bg-[var(--bg-hover)] text-[var(--text)]",
        ghost: "bg-transparent text-[var(--text)]",
        link: "text-[var(--accent)] underline-offset-4 hover:underline",
      },
      size: {
        default: "h-11 px-4",
        sm: "h-9 px-3",
        lg: "h-12 px-6",
        icon: "h-11 w-11",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function Button({ className, variant, size, asChild = false, type = "button", children, ...props }: ButtonProps) {
  const classes = cn(buttonVariants({ variant, size }), className);
  if (asChild) return <Slot className={classes} {...props}>{children}</Slot>;
  return (
    <m.span
      className="inline-flex"
      whileTap={reducedMotion() ? undefined : { scale: 0.97 }}
      transition={spring}
    >
      <button type={type} className={cn(classes, "w-full")} {...props}>{children}</button>
    </m.span>
  );
}

export { buttonVariants };
