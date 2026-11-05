// Markdown → 经 DOMPurify 净化的 HTML。代码块带语言标签与复制按钮（由聊天视图事件委托处理）。

import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdownLang from "highlight.js/lib/languages/markdown";
import nginx from "highlight.js/lib/languages/nginx";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { Marked, type Token, type Tokens, type TokensList } from "marked";

const LANGS = { bash, css, diff, go, ini, javascript, json, markdown: markdownLang, nginx, python, rust, sql, typescript, xml, yaml };
for (const [name, def] of Object.entries(LANGS)) hljs.registerLanguage(name, def);
hljs.registerAliases(["sh", "shell", "zsh", "console"], { languageName: "bash" });
hljs.registerAliases(["js", "jsx", "mjs"], { languageName: "javascript" });
hljs.registerAliases(["ts", "tsx"], { languageName: "typescript" });
hljs.registerAliases(["py"], { languageName: "python" });
hljs.registerAliases(["yml"], { languageName: "yaml" });
hljs.registerAliases(["html", "svg"], { languageName: "xml" });
hljs.registerAliases(["toml", "conf", "env"], { languageName: "ini" });
hljs.registerAliases(["md"], { languageName: "markdown" });

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

export function highlight(code: string, lang?: string): string {
  const l = (lang ?? "").trim().toLowerCase();
  if (l && hljs.getLanguage(l)) return hljs.highlight(code, { language: l, ignoreIllegals: true }).value;
  return escapeHtml(code);
}

/** 设置页把高亮结果插进 DOM。已知语言的返回值是 HTML，必须再净化。 */
export function highlightSafe(code: string, lang?: string): string {
  return DOMPurify.sanitize(highlight(code, lang));
}

/** 统一代码块外层 HTML 结构，供 renderer 与未闭合流式高亮共用，保证标记完全一致 */
export function codeBlockHTML(label: string, innerHtml: string): string {
  const safeLabel = label || "text";
  return (
    `<div class="code"><div class="code-head"><span>${escapeHtml(safeLabel)}</span>` +
    `<button type="button" data-copy="">复制</button></div>` +
    `<pre><code class="hljs">${innerHtml}</code></pre></div>`
  );
}

const marked = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    code({ text, lang }: Tokens.Code): string {
      const label = (lang ?? "").split(/\s/)[0] ?? "";
      return codeBlockHTML(label, highlight(text, label));
    },
  },
});

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

// Assistant prose is inserted as HTML. Artifact cards own the only safe images.
// Remote img/CSS/SVG in model text must not fetch. HTML profile drops SVG.
const proseSanitize = {
  USE_PROFILES: { html: true },
  ADD_ATTR: ["target"],
  FORBID_TAGS: ["img", "audio", "video", "source", "iframe", "style", "link"],
  FORBID_ATTR: ["style", "src", "srcset", "poster", "background"],
};

export interface MarkdownBlock {
  raw: string;
  references: string;
  html: string;
}

// Only this module's escaped code renderer can attest provenance. Source HTML or
// a caller-created block cannot opt out of prose sanitization by setting a flag.
interface GeneratedCodeTail { readonly label: string; readonly prefix: string; readonly text: string }
const generatedCodeBlocks = new WeakMap<MarkdownBlock, GeneratedCodeTail>();
const generatedTextBlocks = new WeakSet<MarkdownBlock>();
export function isGeneratedTextBlock(block: MarkdownBlock): boolean {
  return generatedTextBlocks.has(block);
}
export function isGeneratedCodeBlock(block: MarkdownBlock): boolean {
  return generatedCodeBlocks.has(block);
}
export function generatedCodeTail(block: MarkdownBlock): GeneratedCodeTail | undefined {
  return generatedCodeBlocks.get(block);
}

export interface CodeHighlightThrottleState {
  lang: string;
  highlightedText: string;
  highlightedHtml: string;
  at: number | null;
}

export interface MarkdownStreamState {
  lastSrc: string;
  lastTokens: TokensList | Token[];
  codeThrottle?: CodeHighlightThrottleState;
}

export function createMarkdownStreamState(): MarkdownStreamState {
  return {
    lastSrc: "",
    lastTokens: [],
  };
}

const SAFE_LITERAL_APPEND = /^(?:[A-Za-z0-9 ,;:!?'"\u3400-\u9FFF\uF900-\uFAFF])+$/u;

/** A pure append that cannot retokenize a single-text paragraph or complete an autolink. */
function canAppendLiteral(text: string, append: string): boolean {
  if (!SAFE_LITERAL_APPEND.test(append)) return false;
  // "[ref]" + ": url" is a link definition, not a longer paragraph.
  if (/^ {0,3}\[[^\]]+\]:/.test(text + append)) return false;
  if (!/[A-Za-z0-9]/.test(append)) return true;
  if (/(?:https?:\/\/|ftp:\/\/)$/i.test(text) || /www\.$/.test(text) || /(?:mailto|xmpp):$/i.test(text)) return false;
  return !/[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.$/.test(text);
}

/**
 * No cut point: extend one already-lexed literal paragraph instead of running
 * marked.lexer on the complete prefix. Any unsafe append returns null.
 */
function extendLiteralParagraph(src: string, oldSrc: string, oldTokens: TokensList | Token[]): TokensList | null {
  if (!oldSrc || src.length <= oldSrc.length || !src.startsWith(oldSrc) || oldTokens.length === 0) return null;
  if (oldTokens.map((token) => token.raw).join("") !== oldSrc) return null;
  if (oldTokens.some((token) => token.type === "html")) return null;
  const links = (oldTokens as TokensList).links || {};
  if (Object.keys(links).length > 0) return null;
  const last = oldTokens[oldTokens.length - 1];
  if (last.type !== "paragraph") return null;
  const paragraph = last as Tokens.Paragraph;
  if (paragraph.raw !== paragraph.text || !paragraph.tokens || paragraph.tokens.length !== 1) return null;
  const inline = paragraph.tokens[0];
  if (inline.type !== "text" || inline.text !== paragraph.text || inline.raw !== paragraph.text) return null;
  const append = src.slice(oldSrc.length);
  if (/^ {0,3}\[[^\]]+\]:/m.test(append) || !canAppendLiteral(paragraph.text, append)) return null;
  const nextInline = { ...inline, raw: inline.raw + append, text: inline.text + append };
  const nextParagraph = { ...paragraph, raw: paragraph.raw + append, text: paragraph.text + append, tokens: [nextInline] };
  return Object.assign([...oldTokens.slice(0, -1), nextParagraph], { links });
}

/** 判断 token.raw 是否未闭合 fenced code */
function isUnclosedFencedCode(token: Token): boolean {
  if (token.type !== "code") return false;
  const codeToken = token as Tokens.Code;
  if (codeToken.codeBlockStyle === "indented") return false;
  const raw = token.raw;
  const lines = raw.endsWith("\n") ? raw.slice(0, -1).split("\n") : raw.split("\n");
  if (lines.length <= 1) {
    // 只有 opening fence 行，必定未闭合
    return true;
  }
  const openMatch = lines[0].match(/^ {0,3}(`{3,}|~{3,})/);
  if (!openMatch) return false;
  const fenceChar = openMatch[1][0];
  const fenceLen = openMatch[1].length;
  const lastLine = lines[lines.length - 1];
  // CommonMark 规则：closing fence 允许 0-3 个前置空格，字符相同，长度不小于 opening fence，后续仅能有可选空白
  const closeRe = new RegExp(`^ {0,3}${fenceChar === "`" ? "`" : "~"}{${fenceLen},}[ \\t]*$`);
  return !closeRe.test(lastLine);
}

/** 判断不可被空行后续文本延续的前驱块类型 */
function isDisallowContinuationToken(token: Token): boolean {
  const t = token.type;
  if (t === "paragraph" || t === "heading" || t === "hr" || t === "table" || t === "blockquote") {
    return true;
  }
  if (t === "code") {
    const codeToken = token as Tokens.Code;
    if (codeToken.codeBlockStyle === "indented") return false;
    // 闭合的 fenced code (``` 或 ~~~)
    return !isUnclosedFencedCode(token);
  }
  return false;
}

/** 渲染单个 token 块对应的 HTML */
function renderSingleTokenBlock(
  token: Token,
  links: Record<string, unknown>,
  isTailUnclosedCode: boolean,
  state?: MarkdownStreamState,
  nowMs?: number,
): string {
  if (isTailUnclosedCode && token.type === "code") {
    const codeToken = token as Tokens.Code;
    const label = (codeToken.lang ?? "").split(/\s/)[0] ?? "";
    const langKey = label.trim().toLowerCase();
    const canHighlight = !!(langKey && hljs.getLanguage(langKey));

    if (!canHighlight) {
      // 无语言或未注册语言，直接转义，不走节流
      return codeBlockHTML(label, escapeHtml(codeToken.text));
    }

    const clock = typeof nowMs === "number" ? nowMs : (typeof performance !== "undefined" ? performance.now() : Date.now());
    if (!state) {
      return codeBlockHTML(label, highlight(codeToken.text, label));
    }

    let throttle = state.codeThrottle;
    if (!throttle || throttle.lang !== label) {
      throttle = {
        lang: label,
        highlightedText: "",
        highlightedHtml: "",
        at: null,
      };
      state.codeThrottle = throttle;
    }

    const text = codeToken.text;
    if (!text.startsWith(throttle.highlightedText)) {
      throttle.highlightedText = "";
      throttle.highlightedHtml = "";
      throttle.at = null;
    }

    const lastNewlineIdx = text.lastIndexOf("\n");
    const complete = lastNewlineIdx >= 0 ? text.slice(0, lastNewlineIdx + 1) : "";

    // 判定是否触发高亮更新：完整行且产生新内容，且距上次高亮 >= 150ms（或初次具备完整行）
    if (complete !== throttle.highlightedText) {
      if (complete.length === 0) {
        // 无完整行时回退为空
        throttle.highlightedText = "";
        throttle.highlightedHtml = "";
      } else if (throttle.at === null || clock - throttle.at >= 150) {
        throttle.highlightedText = complete;
        throttle.highlightedHtml = highlight(complete, label);
        throttle.at = clock;
      }
    }

    // 输出：已高亮的完整行 + 未高亮完整行/当前未完成行转义纯文本
    let innerHtml = throttle.highlightedHtml;
    if (text.length > throttle.highlightedText.length) {
      innerHtml += escapeHtml(text.slice(throttle.highlightedText.length));
    }
    return codeBlockHTML(label, innerHtml);
  }

  // 正常块解析与渲染
  const block = Object.assign([token], { links });
  return DOMPurify.sanitize(marked.parser(block, { ...marked.defaults, async: false }), proseSanitize);
}

/** Keep only the current response's blocks; never cache every streaming prefix globally. */
export function renderMarkdownBlocks(
  src: string,
  previous: MarkdownBlock[] = [],
  state?: MarkdownStreamState,
  nowMs?: number,
): MarkdownBlock[] {
  let tokens: TokensList | Token[] | null = null;
  const isPureAppend = !!(state && state.lastSrc && src.startsWith(state.lastSrc));

  // 无论是否走增量，只要不是纯追加，彻底清空代码块节流状态
  if (state && !isPureAppend) {
    state.codeThrottle = undefined;
  }

  if (state && state.lastSrc && state.lastTokens.length > 0) {
    const oldSrc = state.lastSrc;
    const oldTokens = state.lastTokens;
    const oldLinks = (oldTokens as TokensList).links || {};
    const oldLinksEmpty = Object.keys(oldLinks).length === 0;

    // 条件 1: 新 src 以旧 src 开头（纯追加）
    // 条件 2: 旧 tokens links 为空，且新增文本不含引用定义 (/^ {0,3}\[[^\]]+\]:/m)
    const appendedText = isPureAppend ? src.slice(oldSrc.length) : "";
    const hasRefDefinition = /^ {0,3}\[[^\]]+\]:/m.test(appendedText);
    // 条件 3: 没有任何 html 类型 token
    const hasHtmlToken = oldTokens.some((t) => t.type === "html");

    if (isPureAppend && oldLinksEmpty && !hasRefDefinition && !hasHtmlToken) {
      // 条件 4: 存在切点 i (i <= n - 2): tokens[i-1].type === 'space' && tokens[i-2] 是不可被延续类型
      let splitIndex = -1;
      const n = oldTokens.length;
      for (let i = n - 2; i >= 2; i--) {
        if (oldTokens[i - 1].type === "space" && isDisallowContinuationToken(oldTokens[i - 2])) {
          splitIndex = i;
          break;
        }
      }

      if (splitIndex !== -1) {
        // 增量切点有效：保留 tokens[0..splitIndex)，对 src.slice(Σraw) 调 marked.lexer 拼接
        const retainedTokens = oldTokens.slice(0, splitIndex);
        let prefixRawLength = 0;
        for (let j = 0; j < splitIndex; j++) {
          prefixRawLength += retainedTokens[j].raw.length;
        }

        const tailSrc = src.slice(prefixRawLength);
        const tailTokens = marked.lexer(tailSrc, marked.defaults);
        const tailHasHtml = tailTokens.some((t) => t.type === "html");
        const tailLinksEmpty = Object.keys(tailTokens.links || {}).length === 0;

        if (!tailHasHtml && tailLinksEmpty) {
          const mergedTokens = Object.assign([...retainedTokens, ...tailTokens], {
            links: oldLinks,
          });
          tokens = mergedTokens;
        }
      }
    }
  }

  // 若无法走增量，单段字面追加只延长已见段落；其余仍走全文 lexer
  if (!tokens && isPureAppend && state) {
    tokens = extendLiteralParagraph(src, state.lastSrc, state.lastTokens);
  }
  if (!tokens) {
    tokens = marked.lexer(src, marked.defaults);
  }

  // 更新流状态
  if (state) {
    if (!isPureAppend) {
      state.codeThrottle = undefined;
    }
    state.lastSrc = src;
    state.lastTokens = tokens;
  }

  const effectiveLinks = ((tokens as TokensList).links || {}) as Record<string, unknown>;
  const references = JSON.stringify(effectiveLinks);

  // Raw HTML can span token boundaries. Keep its original whole-document parsing semantics.
  if (tokens.some((token) => token.type === "html")) {
    const old = previous[0];
    return old?.raw === src && old.references === references
      ? [old]
      : [{ raw: src, references, html: renderMarkdown(src, false) }];
  }

  const totalTokens = tokens.length;
  const lastIndex = totalTokens - 1;
  const lastToken = totalTokens > 0 ? tokens[lastIndex] : null;
  const isTailUnclosed = !!(lastToken && isUnclosedFencedCode(lastToken));

  // 如果最后一个 token 不是未闭合 code（闭合了或非代码），清空代码块节流状态
  if (state && !isTailUnclosed && state.codeThrottle) {
    state.codeThrottle = undefined;
  }

  return tokens.map((token, index) => {
    const old = previous[index];
    const isTailBlock = index === lastIndex && isTailUnclosed;

    // 非尾部未闭合代码块时，若 raw 和 references 完全相同，复用旧块对象
    if (!isTailBlock && old?.raw === token.raw && old.references === references) {
      return old;
    }

    // Only a literal single-line paragraph can bypass the general HTML pipeline.
    // No entities, escapes, inline markup, URLs or line-break semantics are inferred here.
    if (token.type === "paragraph" && /^[A-Za-z0-9 .,;:!?'"()-]+$/.test(token.text)
      && token.tokens?.every(part => part.type === "text")) {
      const block = Object.freeze({ raw: token.raw, references, html: `<p>${escapeHtml(token.text)}</p>\n` });
      generatedTextBlocks.add(block);
      return block;
    }
    const closedCode = token.type === "code" && !isTailBlock ? token as Tokens.Code : undefined;
    const closedLabel = (closedCode?.lang ?? "").split(/\s/)[0] ?? "";
    const closedInner = closedCode ? highlight(closedCode.text, closedLabel) : undefined;
    const html = closedInner === undefined ? renderSingleTokenBlock(token, effectiveLinks, isTailBlock, state, nowMs)
      : codeBlockHTML(closedLabel, closedInner);
    const block = { raw: token.raw, references, html };
    if (token.type === "code") {
      const code = token as Tokens.Code;
      const label = (code.lang ?? "").split(/\s/)[0] ?? "";
      const throttle = state?.codeThrottle;
      const hasLanguage = !!hljs.getLanguage(label.trim().toLowerCase());
      const highlighted = isTailBlock && throttle?.lang === label && hasLanguage ? throttle : undefined;
      Object.freeze(block);
      generatedCodeBlocks.set(block, Object.freeze({ label,
        prefix: closedCode && hasLanguage ? closedInner! : highlighted?.highlightedHtml ?? "",
        text: closedCode && hasLanguage ? "" : code.text.slice(highlighted?.highlightedText.length ?? 0),
      }));
    }
    return block;
  });
}

/** Finish only the throttled code tail; completed sanitized blocks are already final. */
export function finalizeMarkdownBlocks(previous: MarkdownBlock[], state: MarkdownStreamState): MarkdownBlock[] {
  const lastIndex = state.lastTokens.length - 1;
  const token = state.lastTokens[lastIndex];
  if (!token || !isUnclosedFencedCode(token) || !previous[lastIndex]) return previous;
  const code = token as Tokens.Code;
  const label = (code.lang ?? "").split(/\s/)[0] ?? "";
  const inner = highlight(code.text, label);
  const block = Object.freeze({ ...previous[lastIndex], html: codeBlockHTML(label, inner) });
  const hasLanguage = !!hljs.getLanguage(label.trim().toLowerCase());
  generatedCodeBlocks.set(block, Object.freeze({ label,
    prefix: hasLanguage ? inner : "",
    text: hasLanguage ? "" : code.text,
  }));
  const result = previous.slice();
  result[lastIndex] = block;
  state.codeThrottle = undefined;
  return result;
}

const cache = new Map<string, string>();
const CACHE_MAX = 300;

/** 已完成消息走缓存；流式中的正文每帧变化，传 cache=false。 */
export function renderMarkdown(src: string, useCache = true): string {
  if (useCache) {
    const hit = cache.get(src);
    if (hit !== undefined) return hit;
  }
  const html = DOMPurify.sanitize(marked.parse(src, { async: false }), proseSanitize);
  if (useCache) {
    if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value!);
    cache.set(src, html);
  }
  return html;
}
