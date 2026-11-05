import * as PopoverPrimitive from "@radix-ui/react-popover";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;
export const PopoverClose = PopoverPrimitive.Close;

export function PopoverContent({ className, align = "center", sideOffset = 6, ...props }: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal container={document.body}>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn("solid-surface z-50 w-64 rounded-xl p-3 outline-none", className)}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
