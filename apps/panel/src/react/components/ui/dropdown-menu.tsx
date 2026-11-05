import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const DropdownMenu = DropdownMenuPrimitive.Root;
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
export const DropdownMenuGroup = DropdownMenuPrimitive.Group;
export const DropdownMenuPortal = DropdownMenuPrimitive.Portal;
export const DropdownMenuSub = DropdownMenuPrimitive.Sub;
export const DropdownMenuRadioGroup = DropdownMenuPrimitive.RadioGroup;

export function DropdownMenuContent({ className, sideOffset = 6, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return (
    <DropdownMenuPrimitive.Portal container={document.body}>
      <DropdownMenuPrimitive.Content
        sideOffset={sideOffset}
        className={cn("solid-surface z-50 min-w-40 rounded-xl p-1 outline-none", className)}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
}

export function DropdownMenuItem({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Item>) {
  return (
    <DropdownMenuPrimitive.Item
      className={cn("flex min-h-11 cursor-pointer items-center rounded-lg px-3 text-sm outline-none focus-visible:bg-[var(--bg-hover)] data-[highlighted]:bg-[var(--bg-hover)]", className)}
      {...props}
    />
  );
}

export function DropdownMenuCheckboxItem({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.CheckboxItem>) {
  return (
    <DropdownMenuPrimitive.CheckboxItem
      className={cn("flex min-h-11 items-center rounded-lg px-3 text-sm outline-none focus-visible:bg-[var(--bg-hover)]", className)}
      {...props}
    />
  );
}

export function DropdownMenuRadioItem({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.RadioItem>) {
  return (
    <DropdownMenuPrimitive.RadioItem
      className={cn("flex min-h-11 items-center rounded-lg px-3 text-sm outline-none focus-visible:bg-[var(--bg-hover)]", className)}
      {...props}
    />
  );
}

export function DropdownMenuLabel({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Label>) {
  return <DropdownMenuPrimitive.Label className={cn("px-3 py-2 text-xs text-[var(--text-secondary)]", className)} {...props} />;
}

export function DropdownMenuSeparator({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.Separator>) {
  return <DropdownMenuPrimitive.Separator className={cn("my-1 h-px bg-[var(--line)]", className)} {...props} />;
}

export function DropdownMenuShortcut({ className, ...props }: ComponentProps<"span">) {
  return <span className={cn("ml-auto text-xs text-[var(--text-secondary)]", className)} {...props} />;
}

export function DropdownMenuSubTrigger({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.SubTrigger>) {
  return (
    <DropdownMenuPrimitive.SubTrigger
      className={cn("flex min-h-11 items-center rounded-lg px-3 text-sm outline-none focus-visible:bg-[var(--bg-hover)]", className)}
      {...props}
    />
  );
}

export function DropdownMenuSubContent({ className, ...props }: ComponentProps<typeof DropdownMenuPrimitive.SubContent>) {
  return (
    <DropdownMenuPrimitive.Portal container={document.body}>
      <DropdownMenuPrimitive.SubContent className={cn("solid-surface z-50 min-w-40 rounded-xl p-1", className)} {...props} />
    </DropdownMenuPrimitive.Portal>
  );
}
