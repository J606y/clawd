import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { POP_MS, reducedMotion } from "@/lib/route-motion";

/**
 * 确认层必须 showModal 进顶层，并且挂在 body 上。
 * 侧栏进场动画会留下 transform；遮罩再加 backdrop-filter 时，iOS 能看见按钮，点按却落到画面上。
 * 模糊只放 ::backdrop。dialog 自身不能有 transform，否则会掉出顶层。
 */
export function ConfirmDialog(props: {
  open: boolean;
  onClose: () => void;
  labelId: string;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [shown, setShown] = useState(props.open);
  const [phase, setPhase] = useState<"open" | "leave">(props.open ? "open" : "leave");

  useLayoutEffect(() => {
    if (!props.open) return;
    setPhase("open");
    setShown(true);
  }, [props.open]);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!shown || !dialog) return;
    if (props.open) {
      if (!dialog.open) dialog.showModal();
      return;
    }
    if (reducedMotion()) {
      if (dialog.open) dialog.close();
      setShown(false);
      return;
    }
    setPhase("leave");
    const timer = window.setTimeout(() => {
      if (dialog.open) dialog.close();
      setShown(false);
    }, POP_MS + 40);
    return () => window.clearTimeout(timer);
  }, [props.open, shown]);

  useLayoutEffect(() => () => {
    const dialog = dialogRef.current;
    if (dialog?.open) dialog.close();
  }, []);

  if (!shown || typeof document === "undefined") return null;
  return createPortal(
    <dialog
      ref={dialogRef}
      className="live-confirm"
      data-state={phase}
      role="alertdialog"
      aria-labelledby={props.labelId}
      onCancel={(event) => {
        event.preventDefault();
        props.onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) props.onClose();
      }}
    >
      {props.children}
    </dialog>,
    document.body,
  );
}
