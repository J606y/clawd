export interface TextRevealPacing {
  ownerKey: string;
  /** Monotonic source-receive time; zero is valid. */
  receivedAt: number;
  active: boolean;
  canReveal?: () => boolean;
}

type Arrival = { end: number; at: number };
type Leaf = { node: Text; parent: Element; signature: string };
type View = Leaf & { target: string; shown: number; arrivals: Arrival[]; lastTick: number; easeFrom: number; easeAt: number };
type Transaction = {
  enabled: boolean;
  source: string;
  oldSource: string | undefined;
  oldOwner: string | null;
  owner: string | null;
  oldLeaf: Leaf | null;
  visible: string;
  canonical: string;
  arrivals: Arrival[];
  receivedAt: number;
};

/** Only plain paragraph/code leaves are paced; complex sanitized DOM stays immediate. */
function safeLeaf(container: Element | null): Leaf | null {
  const last = container?.lastElementChild;
  if (!last) return null;
  const element = last.localName === "p" ? last
    : last.localName === "pre" ? last.querySelector(":scope > code")
    : last.classList.contains("code") ? last.querySelector(":scope > pre > code") : null;
  if (!element || element.childNodes.length !== 1 || !(element.firstChild instanceof Text)) return null;
  const signature = element.localName + JSON.stringify(Array.from(element.attributes, a => [a.name, a.value]));
  return { node: element.firstChild, parent: element, signature };
}

/** 出字用先快后收，不用匀速补齐。200ms 内走完，240ms 截止仍强制补全。 */
function easeOutNav(t: number): number {
  const x = t <= 0 ? 0 : t >= 1 ? 1 : t;
  return 1 - (1 - x) * (1 - x) * (1 - x);
}

function scalarBoundary(text: string, end: number): number {
  if (end > 0 && end < text.length) {
    const a = text.charCodeAt(end - 1), b = text.charCodeAt(end);
    if (a >= 0xd800 && a <= 0xdbff && b >= 0xdc00 && b <= 0xdfff) return end - 1;
  }
  return end;
}

/**
 * One component owns one controller. Real source renders retain the normal sanitizer
 * and DOM patcher. Only their completed safe leaf is revealed; animation ticks never
 * call Lit, Markdown, DOMPurify or a template parser. Deadlines are scheduling targets,
 * not a promise that an externally blocked browser can paint within a hard bound.
 */
export class TextRevealController {
  private frame = 0;
  private view: View | null = null;
  private transaction: Transaction | null = null;
  private container: Element | null = null;
  private source: string | undefined;
  private owner: string | null = null;
  private enabled = false;
  private pacing: TextRevealPacing | undefined;
  private receivedAt = 0;
  private readonly deadlineMs = 240;
  private readonly easeMs = 200;

  get rafId(): number { return this.frame; }
  get targetNode(): Text | null { return this.view?.node ?? null; }
  get arrivalTime(): number { return this.receivedAt; }

  /** Pause before the ordinary source render; never modify its source or templates. */
  beforeUpdate(source: string, pacing: TextRevealPacing | undefined, streaming: boolean, container: Element | null): void {
    this.pacing = pacing;
    const enabled = !!(streaming && pacing?.active && pacing.ownerKey && document.visibilityState !== "hidden" && (pacing.canReveal?.() ?? true));
    const owner = pacing?.ownerKey ?? null;
    if (source === this.source && owner === this.owner && enabled === this.enabled) return;
    this.receivedAt = pacing && Number.isFinite(pacing.receivedAt) ? pacing.receivedAt : performance.now();
    const leaf = safeLeaf(container);
    const prior = this.view && this.isIntact(this.view) ? this.view : null;
    const visible = leaf?.node.data ?? "";
    const canonical = prior?.target ?? visible;
    this.pause();
    this.transaction = {
      enabled, source, oldSource: this.source, oldOwner: this.owner, owner,
      oldLeaf: leaf, visible, canonical, arrivals: prior?.arrivals.slice() ?? [], receivedAt: this.receivedAt,
    };
    // The block patcher may reuse an unchanged former tail by identity. Restore its
    // canonical text before every source transaction; afterUpdate reapplies the
    // visible prefix only when the same safe leaf still owns the active tail.
    this.flushView();
    this.view = null;
    this.container = container;
    this.source = source;
    this.owner = owner;
    this.enabled = enabled;
  }

  /** Runs in the same Lit update transaction, before the browser can render a frame. */
  afterUpdate(container: Element | null): void {
    this.container = container;
    const tx = this.transaction;
    this.transaction = null;
    if (!tx?.enabled || !container || document.visibilityState === "hidden") return;
    if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const leaf = safeLeaf(container);
    if (!leaf) return;
    const target = leaf.node.data;
    const first = !tx.oldLeaf && !tx.oldSource;
    const sameOwner = tx.oldOwner === null || tx.oldOwner === tx.owner;
    const extension = tx.oldSource !== undefined && tx.source.startsWith(tx.oldSource);
    const sameLeaf = tx.oldLeaf?.node === leaf.node && tx.oldLeaf.parent === leaf.parent && tx.oldLeaf.signature === leaf.signature;
    if (!first && !(sameOwner && extension && sameLeaf && target.startsWith(tx.canonical))) return;
    if (!target || !target.startsWith(tx.visible)) return;
    let shown = first ? scalarBoundary(target, Math.min(24, target.length)) : tx.visible.length;
    const arrivals = tx.arrivals.filter(a => a.end > shown);
    if (target.length > tx.canonical.length) arrivals.push({ end: target.length, at: tx.receivedAt });
    const now = performance.now();
    for (const arrival of arrivals) if (now - arrival.at >= this.deadlineMs) shown = Math.max(shown, arrival.end);
    shown = scalarBoundary(target, Math.min(shown, target.length));
    if (shown >= target.length) return;
    // Truncate only newly exposed suffix within this unpainted render transaction;
    // existing visible text and its selection offsets never move backwards.
    leaf.node.deleteData(shown, target.length - shown);
    this.view = { ...leaf, target, shown, arrivals: arrivals.filter(a => a.end > shown), lastTick: now, easeFrom: shown, easeAt: now };
    this.schedule();
  }

  /** Also works after removal: complete the component-owned node for reconnection. */
  cancelAndFlush(): void {
    this.pause();
    this.flushView();
    this.view = null;
    this.transaction = null;
    this.container = null;
  }

  private pause(): void {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.frame = 0;
  }

  private isIntact(view: View): boolean {
    if (!this.container?.contains(view.node)) return false;
    const leaf = safeLeaf(this.container);
    return leaf?.node === view.node && leaf.parent === view.parent && leaf.signature === view.signature
      && view.node.data === view.target.slice(0, view.shown);
  }

  private flushView(): void {
    const view = this.view;
    if (view && this.isIntact(view)) view.node.appendData(view.target.slice(view.shown));
    this.view = null;
  }

  private schedule(): void {
    if (!this.frame && this.view) this.frame = requestAnimationFrame(this.tick);
  }

  private readonly tick = (now: number): void => {
    this.frame = 0;
    const view = this.view;
    if (!view) return;
    if (!this.isIntact(view)) { this.view = null; return; }
    if (!view.node.isConnected || document.visibilityState === "hidden" || !(this.pacing?.canReveal?.() ?? true)) {
      this.cancelAndFlush();
      return;
    }
    const span = view.target.length - view.easeFrom;
    const t = span <= 0 ? 1 : Math.min(1, (now - view.easeAt) / this.easeMs);
    let end = view.easeFrom + (span > 0 ? Math.ceil(span * easeOutNav(t)) : 0);
    if (t >= 1) end = view.target.length;
    end = Math.max(view.shown, end);
    for (const arrival of view.arrivals) if (now - arrival.at >= this.deadlineMs) end = Math.max(end, arrival.end);
    end = Math.min(view.target.length, Math.max(view.shown, end));
    end = scalarBoundary(view.target, end);
    if (end < view.shown) end = view.shown;
    if (end > view.shown) view.node.appendData(view.target.slice(view.shown, end));
    view.shown = end;
    view.lastTick = now;
    view.arrivals = view.arrivals.filter(a => a.end > end);
    if (end >= view.target.length) this.view = null;
    else this.schedule();
  };
}
