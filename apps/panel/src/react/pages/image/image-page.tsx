import { useEffect, useLayoutEffect, useRef, useState, Fragment, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { artifactReference } from "@/lib/artifacts";
import { menuOffset } from "@/lib/popover";
import { queryKeys, useImageDelete, useImageHistory, useImageModels } from "@/lib/queries";
import { POP_MS, reducedMotion } from "@/lib/route-motion";
import { fetchAndSave } from "@/lib/save-file";
import { sidecar, type ImageItem, type ImageModel } from "@/lib/sidecar";
import { useUiStore, type ImageAspectPref } from "@/stores/ui";
import "./image.css";

const TOUCH = window.matchMedia("(pointer: coarse)");
const ASPECTS: { id: ImageAspectPref; label: string }[] = [
  { id: "square", label: "方形" },
  { id: "landscape", label: "横向" },
  { id: "portrait", label: "纵向" },
];
const LINE_PX = 24;
const PAD_PX = 12;
const MAX_PX = 240;
const LEAVE_MS = POP_MS + 40;

type Pending = {
  prompt: string;
  model: string;
  label: string;
  aspect: string;
  startedAt: number;
  state: "running" | "stopped";
};

function failText(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 404 && error.code === "not_found") return "生图服务还没生效";
  return error instanceof ApiError ? error.message : fallback;
}

function aspectLabel(id: string): string {
  return ASPECTS.find((item) => item.id === id)?.label ?? id;
}

function recorded(items: ImageItem[], prompt: string, model: string, startedAt: number): boolean {
  const since = Math.floor(startedAt / 1000) - 30;
  return items.some((item) => item.prompt === prompt && item.model === model && item.createdAt >= since);
}

function syncMenuOffset(host: HTMLDivElement | null, setOffset: (value: number) => void) {
  if (!host) return;
  const menu = host.querySelector(".menu");
  if (!(menu instanceof HTMLElement) || menu.offsetWidth <= 0) return;
  setOffset(menuOffset(host, menu.offsetWidth));
}

function Rise(props: { open: boolean; children: ReactNode }) {
  const [mounted, setMounted] = useState(props.open);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (props.open) {
      setMounted(true);
      setLeaving(false);
      return;
    }
    if (!mounted || reducedMotion()) {
      setMounted(false);
      setLeaving(false);
      return;
    }
    setLeaving(true);
    const timer = window.setTimeout(() => {
      setMounted(false);
      setLeaving(false);
    }, LEAVE_MS);
    return () => window.clearTimeout(timer);
  }, [props.open, mounted]);
  if (!mounted) return null;
  return (
    <div className="presence" data-kind="rise" data-state={leaving ? "leave" : "open"}>
      {props.children}
    </div>
  );
}

export function ImagePage() {
  const queryClient = useQueryClient();
  const modelsQuery = useImageModels();
  const historyQuery = useImageHistory();
  const deleteImage = useImageDelete();
  const setImageModelPref = useUiStore((state) => state.setImageModelPref);
  const setImageAspectPref = useUiStore((state) => state.setImageAspectPref);
  const [models, setModels] = useState<ImageModel[]>([]);
  const [model, setModel] = useState(() => useUiStore.getState().imageModelPref);
  const [aspect, setAspect] = useState<ImageAspectPref>(() => useUiStore.getState().imageAspectPref);
  const [prompt, setPrompt] = useState("");
  const [items, setItems] = useState<ImageItem[]>([]);
  const [pending, setPending] = useState<Pending | null>(null);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState("");
  const [modelOpen, setModelOpen] = useState(false);
  const [aspectOpen, setAspectOpen] = useState(false);
  const [menuId, setMenuId] = useState("");
  const [confirm, setConfirm] = useState("");
  const [enlarged, setEnlarged] = useState("");
  const [saving, setSaving] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [modelOffsetPx, setModelOffsetPx] = useState(0);
  const [aspectOffsetPx, setAspectOffsetPx] = useState(0);
  const pageRef = useRef<HTMLElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const measureRef = useRef<HTMLTextAreaElement>(null);
  const modelHost = useRef<HTMLDivElement>(null);
  const aspectHost = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const mounted = useRef(true);
  const booted = useRef(false);
  const stick = useRef(true);
  const generation = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const itemsRef = useRef(items);
  const pendingRef = useRef(pending);
  const promptRef = useRef(prompt);
  const modelRef = useRef(model);
  const aspectRef = useRef(aspect);
  const modelsRef = useRef(models);
  const generatingRef = useRef(false);
  const modelOpenRef = useRef(false);
  const aspectOpenRef = useRef(false);
  const menuIdRef = useRef("");
  const confirmRef = useRef("");

  itemsRef.current = items;
  pendingRef.current = pending;
  promptRef.current = prompt;
  modelRef.current = model;
  aspectRef.current = aspect;
  modelsRef.current = models;

  const assignModelOpen = (open: boolean) => {
    modelOpenRef.current = open;
    setModelOpen(open);
  };
  const assignAspectOpen = (open: boolean) => {
    aspectOpenRef.current = open;
    setAspectOpen(open);
  };
  const assignMenu = (id: string) => {
    menuIdRef.current = id;
    setMenuId(id);
  };
  const assignConfirm = (id: string) => {
    confirmRef.current = id;
    setConfirm(id);
  };
  const commitItems = (next: ImageItem[]) => {
    itemsRef.current = next;
    setItems(next);
  };
  const commitPending = (next: Pending | null) => {
    pendingRef.current = next;
    setPending(next);
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const update = () => {
      const inset = Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop);
      document.documentElement.style.setProperty("--keyboard-inset", inset > 30 ? `${inset}px` : "0px");
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      document.documentElement.style.removeProperty("--keyboard-inset");
    };
  }, []);

  useEffect(() => {
    if (booted.current) return;
    if (modelsQuery.isSuccess && historyQuery.isSuccess) {
      booted.current = true;
      const nextModels = modelsQuery.data;
      setModels(nextModels);
      commitItems(historyQuery.data.slice().reverse());
      setModel((current) => {
        if (nextModels.some((item) => item.id === current)) return current;
        return nextModels.find((item) => item.id === "gpt-image-2.5-sunburst")?.id ?? nextModels[0]?.id ?? "";
      });
      setNotice("");
      stick.current = true;
      return;
    }
    const error = modelsQuery.error ?? historyQuery.error;
    if (error) setNotice(failText(error, "生图列表加载失败"));
  }, [modelsQuery.isSuccess, modelsQuery.data, modelsQuery.error, historyQuery.isSuccess, historyQuery.data, historyQuery.error]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (modelOpenRef.current && modelHost.current && !modelHost.current.contains(target)) assignModelOpen(false);
      if (aspectOpenRef.current && aspectHost.current && !aspectHost.current.contains(target)) assignAspectOpen(false);
      if (menuIdRef.current) {
        const owner = pageRef.current?.querySelector(`[data-menu="${CSS.escape(menuIdRef.current)}"]`);
        if (owner && !owner.contains(target)) assignMenu("");
      }
      if (confirmRef.current) {
        const inDialog = dialogRef.current?.contains(target) ?? false;
        const owner = pageRef.current?.querySelector(`[data-menu="${CSS.escape(confirmRef.current)}"]`);
        if (!inDialog && !(owner && owner.contains(target))) assignConfirm("");
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      assignModelOpen(false);
      assignAspectOpen(false);
      assignMenu("");
      assignConfirm("");
      setEnlarged("");
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const autosize = () => {
    const input = inputRef.current;
    const measure = measureRef.current;
    if (!input || !measure) return;
    measure.style.width = `${input.getBoundingClientRect().width}px`;
    measure.value = input.value;
    const lines = input.value === "" ? 1 : Math.max(1, Math.round((measure.scrollHeight - PAD_PX) / LINE_PX));
    const want = lines * LINE_PX + PAD_PX;
    const height = `${Math.min(want, MAX_PX)}px`;
    if (input.style.height !== height) input.style.height = height;
    input.style.overflowY = want > MAX_PX ? "auto" : "hidden";
  };

  const scrollToEnd = (force = false) => {
    if (!force && !stick.current) return;
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  };

  useLayoutEffect(() => {
    autosize();
    if (stick.current) scrollToEnd(true);
  }, [prompt, items, pending]);

  useLayoutEffect(() => {
    if (modelOpen) syncMenuOffset(modelHost.current, setModelOffsetPx);
    if (aspectOpen) syncMenuOffset(aspectHost.current, setAspectOffsetPx);
  }, [modelOpen, aspectOpen, models]);

  const currentLabel = () => modelsRef.current.find((item) => item.id === modelRef.current)?.label || modelRef.current || "模型";

  const absorb = async (text: string, usedModel: string, startedAt: number): Promise<boolean> => {
    const next = (await sidecar.imageHistory()).slice().reverse();
    if (!mounted.current) return false;
    commitItems(next);
    return recorded(next, text, usedModel, startedAt);
  };

  const watchLate = async (text: string, usedModel: string, startedAt: number) => {
    for (let i = 0; i < 6; i += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      if (!mounted.current) return;
      try {
        if (await absorb(text, usedModel, startedAt) && pendingRef.current?.startedAt === startedAt) {
          commitPending(null);
          return;
        }
      } catch {
        // 晚到的图下次打开历史还能看到。
      }
    }
  };

  const restore = (text: string) => {
    if (promptRef.current.trim()) return;
    promptRef.current = text;
    setPrompt(text);
  };

  const generate = async () => {
    const text = promptRef.current.trim();
    if (!text || generatingRef.current) return;
    if (!modelRef.current) {
      setNotice("没有可用的生图模型");
      return;
    }
    const usedModel = modelRef.current;
    const usedAspect = aspectRef.current;
    const label = currentLabel();
    const startedAt = Date.now();
    const gen = ++generation.current;
    const ctrl = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ctrl;
    promptRef.current = "";
    setPrompt("");
    setNotice("");
    assignModelOpen(false);
    assignAspectOpen(false);
    assignMenu("");
    generatingRef.current = true;
    setGenerating(true);
    commitPending({ prompt: text, model: usedModel, label, aspect: usedAspect, startedAt, state: "running" });
    stick.current = true;
    try {
      const result = await sidecar.imageGenerate(usedModel, text, usedAspect, ctrl.signal);
      if (!mounted.current || gen !== generation.current) return;
      commitItems([...itemsRef.current.filter((item) => item.id !== result.item.id), result.item]);
      if (pendingRef.current?.startedAt === startedAt) commitPending(null);
      if (!result.historySaved) setNotice("图已生成，但没能写入记录，刷新后可能看不到");
      void queryClient.invalidateQueries({ queryKey: queryKeys.imageHistory });
    } catch (error) {
      if (!mounted.current || gen !== generation.current) return;
      if (ctrl.signal.aborted) {
        if (pendingRef.current?.startedAt === startedAt) {
          commitPending({ prompt: text, model: usedModel, label, aspect: usedAspect, startedAt, state: "stopped" });
        }
        void watchLate(text, usedModel, startedAt);
        return;
      }
      const message = failText(error, "生成失败");
      let absorbed = false;
      try {
        absorbed = await absorb(text, usedModel, startedAt);
      } catch {
        absorbed = false;
      }
      if (!mounted.current || gen !== generation.current) return;
      if (absorbed) {
        if (pendingRef.current?.startedAt === startedAt) commitPending(null);
      } else {
        commitPending(null);
        restore(text);
        setNotice(message);
      }
    } finally {
      if (mounted.current && gen === generation.current) {
        generatingRef.current = false;
        setGenerating(false);
        abortRef.current = null;
        window.setTimeout(() => {
          autosize();
          inputRef.current?.focus();
        }, 0);
      }
    }
  };

  const stop = () => {
    if (!generatingRef.current) return;
    if (pendingRef.current?.state === "running") {
      commitPending({ ...pendingRef.current, state: "stopped" });
    }
    generatingRef.current = false;
    setGenerating(false);
    abortRef.current?.abort();
  };

  const removeItem = async (item: ImageItem) => {
    if (confirmRef.current !== item.id) {
      assignConfirm(item.id);
      return;
    }
    assignConfirm("");
    assignMenu("");
    try {
      await deleteImage.mutateAsync(item.id);
      if (!mounted.current) return;
      commitItems(itemsRef.current.filter((row) => row.id !== item.id));
      setEnlarged((current) => (current === item.id ? "" : current));
    } catch (error) {
      if (mounted.current) setNotice(failText(error, "删除失败"));
    }
  };

  const save = async (item: ImageItem) => {
    const art = item.absPath ? artifactReference(item.absPath) : null;
    if (!art) {
      setNotice("这张图不能下载");
      return;
    }
    setSaving(item.id);
    try {
      await fetchAndSave(art.download, art.name);
    } catch (error) {
      if (mounted.current) setNotice(error instanceof Error ? error.message : "下载失败");
    } finally {
      if (mounted.current) setSaving("");
    }
  };

  const chooseModel = (id: string) => {
    modelRef.current = id;
    setModel(id);
    assignModelOpen(false);
    setImageModelPref(id);
  };

  const chooseAspect = (next: ImageAspectPref) => {
    aspectRef.current = next;
    setAspect(next);
    assignAspectOpen(false);
    setImageAspectPref(next);
  };

  const groups = new Map<string, ImageModel[]>();
  for (const item of models) groups.set(item.providerName, [...(groups.get(item.providerName) ?? []), item]);
  const openItem = items.find((item) => item.id === enlarged);
  const openArt = openItem?.absPath ? artifactReference(openItem.absPath) : null;
  const showPending = pending && !recorded(items, pending.prompt, pending.model, pending.startedAt) ? pending : null;

  return (
    <section className="image-page" aria-label="生图" ref={pageRef}>
      <div
        className="scroll"
        ref={scrollRef}
        onScroll={() => {
          const scroller = scrollRef.current;
          if (!scroller) return;
          const gap = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
          const bottom = gap < 48;
          stick.current = bottom;
          setAtBottom(bottom);
        }}
      >
        <div className="col">
          {items.map((item) => {
            const art = item.status === "success" && item.absPath ? artifactReference(item.absPath) : null;
            return (
              <Fragment key={item.id}>
                <div className="turn user"><div className="bubble">{item.prompt}</div></div>
                <div className="turn assistant">
                  {art ? (
                    <button type="button" className="shot" aria-label="放大查看" onClick={() => setEnlarged(item.id)}>
                      <img src={art.preview} alt={item.prompt} />
                    </button>
                  ) : (
                    <div className="status err">{item.error || "没有生成图片"}</div>
                  )}
                  {art ? (
                    <div className="actions" data-menu={item.id}>
                      <button type="button" className="icon" aria-label="保存图片" disabled={saving === item.id} onClick={() => void save(item)}>
                        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                          <path d="M8 2v8M5 7l3 3 3-3M3 13h10" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        className="icon"
                        aria-label="更多"
                        aria-haspopup="menu"
                        aria-expanded={menuId === item.id}
                        onClick={() => {
                          assignMenu(menuIdRef.current === item.id ? "" : item.id);
                          assignConfirm("");
                        }}
                      >
                        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                          <circle cx="3.5" cy="8" r="1.2" fill="currentColor" />
                          <circle cx="8" cy="8" r="1.2" fill="currentColor" />
                          <circle cx="12.5" cy="8" r="1.2" fill="currentColor" />
                        </svg>
                      </button>
                      <Rise open={menuId === item.id}>
                        <div className="menu" role="menu" aria-label="图片操作">
                          <div className="note">{item.label} · {aspectLabel(item.aspect)}</div>
                          <button type="button" role="menuitem" className="danger" onClick={() => void removeItem(item)}>
                            {confirm === item.id ? "确认删除" : "删除"}
                          </button>
                        </div>
                      </Rise>
                    </div>
                  ) : null}
                </div>
              </Fragment>
            );
          })}
          {showPending ? (
            <>
              <div className="turn user"><div className="bubble">{showPending.prompt}</div></div>
              <div className="turn assistant">
                <div className="status" role="status">{showPending.state === "stopped" ? "已停止" : "思考中"}</div>
              </div>
            </>
          ) : null}
        </div>
      </div>
      {atBottom ? null : (
        <button
          type="button"
          className="jump"
          aria-label="回到底部"
          onClick={() => {
            stick.current = true;
            setAtBottom(true);
            scrollToEnd(true);
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <path d="M7 2v10M3 8l4 4 4-4" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      )}
      <div className="composer">
        <div className="box">
          <div className="row">
            <textarea
              ref={inputRef}
              className="input"
              rows={1}
              maxLength={4000}
              placeholder="描述要生成的画面"
              aria-label="生图提示词"
              enterKeyHint={TOUCH.matches ? "enter" : "send"}
              value={prompt}
              onChange={(event) => {
                promptRef.current = event.target.value;
                setPrompt(event.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229) return;
                if (TOUCH.matches || event.shiftKey) return;
                event.preventDefault();
                if (!generatingRef.current) void generate();
              }}
            />
            <textarea ref={measureRef} className="measure" rows={1} tabIndex={-1} aria-hidden="true" readOnly />
            {generating ? (
              <button type="button" className="btn stop" aria-label="停止" title="停止" onClick={stop}>
                <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><rect width="12" height="12" rx="2" fill="currentColor" /></svg>
              </button>
            ) : (
              <button type="button" className="btn send" aria-label="发送" title="发送" disabled={!prompt.trim()} onClick={() => void generate()}>
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            )}
          </div>
        </div>
        <div className="foot">
          {notice ? <span className="err" role="alert">{notice}</span> : <span className="hint">Enter 发送 · Shift + Enter 换行</span>}
          <div className="tools">
            <div className="model" ref={modelHost}>
              <button
                type="button"
                className="trigger"
                aria-haspopup="menu"
                aria-expanded={modelOpen}
                title="选择生图模型"
                onClick={() => {
                  const next = !modelOpenRef.current;
                  assignModelOpen(next);
                  assignAspectOpen(false);
                  if (next && modelHost.current) setModelOffsetPx(menuOffset(modelHost.current, 258));
                }}
              >
                <span>{currentLabel()}</span>
                <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 6.5 5 4l2.5 2.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" /></svg>
              </button>
              <Rise open={modelOpen}>
                <div className="menu" role="menu" aria-label="选择生图模型" style={{ left: modelOffsetPx }}>
                  {groups.size ? [...groups].map(([name, list]) => (
                    <Fragment key={name}>
                      {groups.size > 1 ? <h4>{name}</h4> : null}
                      {list.map((item) => (
                        <button type="button" role="menuitemradio" aria-checked={item.id === model} key={item.id} onClick={() => chooseModel(item.id)}>
                          <span>{item.label}</span>
                          {item.id === model ? <span className="check" aria-hidden="true">✓</span> : null}
                        </button>
                      ))}
                    </Fragment>
                  )) : <div className="note">没有可用的生图模型</div>}
                </div>
              </Rise>
            </div>
            <div className="aspect" ref={aspectHost}>
              <button
                type="button"
                className="trigger"
                aria-haspopup="menu"
                aria-expanded={aspectOpen}
                title="画面比例"
                onClick={() => {
                  const next = !aspectOpenRef.current;
                  assignAspectOpen(next);
                  assignModelOpen(false);
                  if (next && aspectHost.current) setAspectOffsetPx(menuOffset(aspectHost.current, 220));
                }}
              >
                <span>比例：{aspectLabel(aspect)}</span>
                <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2.5 6.5 5 4l2.5 2.5" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" /></svg>
              </button>
              <Rise open={aspectOpen}>
                <div className="menu" role="menu" aria-label="画面比例" style={{ left: aspectOffsetPx }}>
                  {ASPECTS.map((item) => (
                    <button type="button" role="menuitemradio" aria-checked={aspect === item.id} key={item.id} onClick={() => chooseAspect(item.id)}>
                      <span>{item.label}</span>
                      {aspect === item.id ? <span className="check" aria-hidden="true">✓</span> : null}
                    </button>
                  ))}
                </div>
              </Rise>
            </div>
          </div>
        </div>
      </div>
      {openItem && openArt ? (
        <dialog
          ref={dialogRef}
          open
          aria-label="放大查看"
          onClick={(event) => {
            if (event.target === event.currentTarget) setEnlarged("");
          }}
          onCancel={(event) => {
            event.preventDefault();
            setEnlarged("");
          }}
        >
          <img src={openArt.preview} alt={openItem.prompt} onClick={(event) => event.stopPropagation()} />
          <div className="dialog-actions" onClick={(event) => event.stopPropagation()}>
            <button type="button" disabled={saving === openItem.id} onClick={() => void save(openItem)}>保存</button>
            <button type="button" className="danger" onClick={() => void removeItem(openItem)}>{confirm === openItem.id ? "确认删除" : "删除"}</button>
            <button type="button" onClick={() => setEnlarged("")}>关闭</button>
          </div>
        </dialog>
      ) : null}
    </section>
  );
}
