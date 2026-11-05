import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/index.css";
import { ChatStyles } from "@/pages/chat/chat-styles";
import { Composer } from "@/pages/chat/composer";
import type { Attachment } from "@/lib/attachments";
import { useUiStore } from "@/stores/ui";

declare global {
  interface Window {
    __ready?: boolean;
    __errors: string[];
    __uploads: Array<{ url: string; done: boolean }>;
    __sends: number;
    __holdBitmap: boolean;
    __bitmapEntered: number;
    __releaseBitmap?: () => void;
    __reset?: (spec: ResetSpec) => void;
    __setSession?: (spec: SwitchSpec) => void;
    __switchSession?: (sessionId: string) => void;
    __pin?: () => boolean;
    __pinnedDraft?: { value: string; attachments: Attachment[] } | null;
    __pinnedBox?: HTMLTextAreaElement | null;
    __unmount?: () => void;
    __mount?: () => void;
    __pickFiles?: (specs: FileSpec[]) => Promise<void>;
    __read?: () => Snapshot;
    __replaceText?: (value: string) => void;
    __pressEnter?: () => void;
    abandonUploads: () => void;
  }
}

interface ResetSpec {
  value?: string;
  attachments?: Attachment[];
  sessionId?: string;
  running?: boolean;
}

interface SwitchSpec {
  sessionId: string;
  value: string;
  attachments?: Attachment[];
  remount?: boolean;
}

interface FileSpec {
  name: string;
  kind: "png" | "bmp" | "pdf" | "huge";
}

interface Snapshot {
  value: string;
  names: string[];
  storeValue: string;
  storeNames: string[];
  storeSession: string | null;
  domSession: string;
  sameDraft: boolean;
  sameBox: boolean;
  flash: string;
  busy: boolean;
  sends: number;
  uploads: number;
  uploadUrls: string[];
  mounted: boolean;
}

const PNG = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="), (char) => char.charCodeAt(0));

function fileFrom(spec: FileSpec): File {
  if (spec.kind === "huge") {
    return new File([new Uint8Array(50 * 1024 * 1024 + 1)], spec.name, { type: "application/pdf" });
  }
  if (spec.kind === "pdf") {
    return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], spec.name, { type: "application/pdf" });
  }
  const type = spec.kind === "bmp" ? "image/bmp" : "image/png";
  return new File([PNG], spec.name, { type });
}

function Parent() {
  const draft = useUiStore((state) => state.composerDraft);
  const setDraft = useUiStore((state) => state.setComposerDraft);
  const sessionId = useUiStore((state) => state.selectedSessionId) ?? "";
  const [running, setRunning] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [flash, setFlash] = useState("");
  const [mountKey, setMountKey] = useState(0);
  const [alive, setAlive] = useState(true);
  const [sends, setSends] = useState(0);

  useEffect(() => {
    const read = (): Snapshot => {
      const box = document.querySelector<HTMLTextAreaElement>("textarea[aria-label='消息']");
      const current = useUiStore.getState();
      return {
        value: box?.value ?? "",
        names: [...document.querySelectorAll(".att")].map((node) => node.getAttribute("title") || "").filter(Boolean),
        storeValue: current.composerDraft.value,
        storeNames: current.composerDraft.attachments.map((item) => item.name),
        storeSession: current.selectedSessionId,
        domSession: document.querySelector(".hp-chat")?.getAttribute("data-session") ?? "",
        sameDraft: Boolean(window.__pinnedDraft) && current.composerDraft === window.__pinnedDraft,
        sameBox: Boolean(window.__pinnedBox) && document.querySelector("textarea[aria-label='消息']") === window.__pinnedBox,
        flash: document.querySelector(".err")?.textContent ?? "",
        busy: Boolean(document.querySelector(".att.busy")),
        sends: window.__sends,
        uploads: window.__uploads.length,
        uploadUrls: window.__uploads.map((item) => item.url),
        mounted: Boolean(box),
      };
    };
    window.__read = read;
    window.__reset = (spec) => {
      window.abandonUploads();
      window.__holdBitmap = false;
      window.__bitmapEntered = 0;
      window.__pinnedDraft = null;
      window.__pinnedBox = null;
      useUiStore.setState({
        selectedSessionId: spec.sessionId ?? "session-a",
        composerDraft: { value: spec.value ?? "", attachments: spec.attachments ?? [] },
      });
      setRunning(Boolean(spec.running));
      setPreparing(false);
      setFlash("");
      setSends(0);
      window.__sends = 0;
      setAlive(true);
      setMountKey((key) => key + 1);
    };
    window.__setSession = (spec) => {
      useUiStore.getState().setSelectedSessionId(spec.sessionId);
      useUiStore.getState().setComposerDraft({ value: spec.value, attachments: spec.attachments ?? [] });
      setPreparing(false);
      setFlash("");
    };
    window.__switchSession = (sessionId) => {
      useUiStore.getState().setSelectedSessionId(sessionId);
    };
    window.__pin = () => {
      window.__pinnedDraft = useUiStore.getState().composerDraft;
      window.__pinnedBox = document.querySelector("textarea[aria-label='消息']");
      return Boolean(window.__pinnedBox);
    };
    window.__unmount = () => setAlive(false);
    window.__mount = () => {
      setAlive(true);
      setMountKey((key) => key + 1);
    };
    window.__pickFiles = async (specs) => {
      const input = document.querySelector<HTMLInputElement>("input[type='file']");
      if (!input) throw new Error("file input missing");
      const transfer = new DataTransfer();
      for (const spec of specs) transfer.items.add(fileFrom(spec));
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    window.__replaceText = (value) => {
      const box = document.querySelector<HTMLTextAreaElement>("textarea[aria-label='消息']");
      if (!box) throw new Error("textarea missing");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(box, value);
      box.dispatchEvent(new InputEvent("input", { bubbles: true, data: value, inputType: value ? "insertText" : "deleteContentBackward" }));
    };
    window.__pressEnter = () => {
      const box = document.querySelector<HTMLTextAreaElement>("textarea[aria-label='消息']");
      box?.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true, cancelable: true }));
    };
    window.__ready = true;
  }, []);

  return (
    <section className="hp-chat" data-session={sessionId} style={{ width: "100%", minHeight: "100vh" }}>
      <ChatStyles />
      <div className="composer">
        {alive ? (
          <Composer
            key={mountKey}
            sessionId={sessionId}
            value={draft.value}
            attachments={draft.attachments}
            disabled={false}
            running={running}
            canSteer={false}
            preparing={preparing}
            stopping={false}
            flash={flash}
            onChange={(value, attachments) => setDraft({ value, attachments })}
            onPreparing={setPreparing}
            onFlash={setFlash}
            onSend={() => {
              setSends((count) => count + 1);
              window.__sends += 1;
            }}
            onSteer={() => undefined}
            onStop={() => undefined}
          />
        ) : null}
      </div>
    </section>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("缺少根节点");
createRoot(root).render(<Parent />);
