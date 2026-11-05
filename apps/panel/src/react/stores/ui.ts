import { create } from "zustand";
import type { Attachment } from "../lib/attachments.js";
import { currentEffort, EFFORT_LEVELS, setEffort } from "../lib/effort.js";
import type { LiveView } from "../lib/live-view.js";
import { loadLiveView, saveLiveView } from "../lib/live-view.js";
import type { ThemePref } from "../lib/theme.js";
import { getThemePref, setThemePref as applyThemePref } from "../lib/theme.js";

export type SessionListFilter = "live" | "chats" | "auto";
export type ImageAspectPref = "square" | "landscape" | "portrait";

export interface ComposerDraft {
  value: string;
  attachments: Attachment[];
}

const EFFORT_IDS = new Set(EFFORT_LEVELS.map((level) => level.id));
const IMAGE_MODEL_KEY = "hp.image.model";
const IMAGE_ASPECT_KEY = "hp.image.aspect";
const EFFORT_MAP_KEY = "hp_effort_by_session";

function readEffortMap(): Record<string, string> {
  try {
    const raw = window.localStorage.getItem(EFFORT_MAP_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, string> = {};
    for (const [sessionId, level] of Object.entries(parsed)) {
      if (typeof level === "string" && EFFORT_IDS.has(level)) out[sessionId] = level;
    }
    return out;
  } catch {
    return {};
  }
}

function readStored(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 记不住不影响本次界面。
  }
}

function readAspect(): ImageAspectPref {
  const value = readStored(IMAGE_ASPECT_KEY);
  if (value === "square" || value === "landscape" || value === "portrait") return value;
  return "square";
}

export interface UiState {
  drawerOpen: boolean;
  themePref: ThemePref;
  effortBySession: Record<string, string>;
  effortNext: string;
  composerDraft: ComposerDraft;
  sessionListFilter: SessionListFilter;
  sessionListQuery: string;
  selectedSessionId: string | null;
  liveView: LiveView | null;
  imageModelPref: string;
  imageAspectPref: ImageAspectPref;
  setDrawerOpen: (open: boolean) => void;
  setThemePref: (pref: ThemePref) => void;
  setEffortFor: (level: string, sessionId?: string) => void;
  setComposerDraft: (draft: ComposerDraft) => void;
  setSessionListFilter: (filter: SessionListFilter) => void;
  setSessionListQuery: (query: string) => void;
  setSelectedSessionId: (id: string | null) => void;
  setLiveView: (view: LiveView | null) => void;
  setImageModelPref: (model: string) => void;
  setImageAspectPref: (aspect: ImageAspectPref) => void;
}

const initialTheme = getThemePref();
applyThemePref(initialTheme);

export const useUiStore = create<UiState>()((set) => ({
  drawerOpen: false,
  themePref: initialTheme,
  effortBySession: readEffortMap(),
  effortNext: currentEffort(""),
  composerDraft: { value: "", attachments: [] },
  sessionListFilter: "chats",
  sessionListQuery: "",
  selectedSessionId: null,
  liveView: loadLiveView(),
  imageModelPref: readStored(IMAGE_MODEL_KEY),
  imageAspectPref: readAspect(),
  setDrawerOpen: (open) => set({ drawerOpen: open }),
  setThemePref: (pref) => {
    applyThemePref(pref);
    set({ themePref: pref });
  },
  setEffortFor: (level, sessionId = "") => {
    if (!EFFORT_IDS.has(level)) return;
    setEffort(level, sessionId);
    set((state) => ({
      effortNext: currentEffort(""),
      effortBySession: sessionId
        ? { ...state.effortBySession, [sessionId]: currentEffort(sessionId) }
        : state.effortBySession,
    }));
  },
  setComposerDraft: (draft) => set({ composerDraft: draft }),
  setSessionListFilter: (filter) => set({ sessionListFilter: filter }),
  setSessionListQuery: (query) => set({ sessionListQuery: query }),
  setSelectedSessionId: (id) => set({ selectedSessionId: id }),
  setLiveView: (view) => {
    saveLiveView(view);
    set({ liveView: view });
  },
  setImageModelPref: (model) => {
    writeStored(IMAGE_MODEL_KEY, model);
    set({ imageModelPref: model });
  },
  setImageAspectPref: (aspect) => {
    writeStored(IMAGE_ASPECT_KEY, aspect);
    set({ imageAspectPref: aspect });
  },
}));
