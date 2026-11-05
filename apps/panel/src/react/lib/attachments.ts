// 附件：图片走内联 data URL（api_server 只接受 image_url），文本文件嵌入消息正文。
// 文本附件用可识别的围栏格式写入，历史回看时再解析回"附件卡片"。

import type { OutgoingContent } from "./api.js";

export type Attachment =
  | { id: string; kind: "image"; name: string; dataUrl: string; bytes: number; absPath?: string; originalBytes?: number }
  | { id: string; kind: "text"; name: string; text: string; bytes: number }
  /**
   * 其他文件：已上传到服务器上传目录，消息里只带路径，由 Hermes 用工具读取。
   * image 为真表示这其实是一张图片（回合进行中的插话不能内联图片，改为上传），消息里会多带一句让模型用看图工具读取的提示。
   */
  | { id: string; kind: "file"; name: string; absPath: string; bytes: number; image?: boolean };

export class AttachmentError extends Error {}

export const MAX_IMAGES = 6;
const MAX_TEXT_BYTES = 512 * 1024;
const MAX_IMAGE_EDGE = 2048;
const KEEP_AS_IS_BYTES = 1_500_000;
/** api_server 单请求上限 10MB，base64 膨胀 4/3，留出正文余量。 */
export const MAX_TOTAL_BYTES = 7_000_000;

const TEXT_EXT = new Set(
  (
    "txt md markdown log csv tsv json jsonl yaml yml toml ini conf cfg env xml html htm css scss js mjs cjs ts tsx jsx " +
    "py rb go rs java kt c h cpp hpp cs php sh bash zsh fish ps1 sql graphql proto diff patch srt vtt service"
  ).split(" "),
);
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

let seq = 0;
const nextId = () => `att-${Date.now().toString(36)}-${(seq++).toString(36)}`;

function ext(name: string): string {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i + 1).toLowerCase();
}

function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new AttachmentError("读取失败"));
    r.readAsDataURL(blob);
  });
}

/** 大图缩到长边 2048 并转 JPEG；小于 1.5MB 的常见格式原样保留（保住 PNG 透明与 GIF）。 */
async function prepareImage(file: File): Promise<Attachment> {
  if (file.size <= KEEP_AS_IS_BYTES && IMAGE_TYPES.has(file.type)) {
    return { id: nextId(), kind: "image", name: file.name || "图片", dataUrl: await readAsDataUrl(file), bytes: file.size };
  }
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new AttachmentError(`无法读取图片：${file.name}`);
  }
  try {
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = new OffscreenCanvas(w, h);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new AttachmentError("浏览器不支持图片压缩");
    ctx.fillStyle = "#fff"; // JPEG 无透明通道，透明区域按白底处理
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bitmap, 0, 0, w, h);
    const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: 0.85 });
    return { id: nextId(), kind: "image", name: file.name || "图片", dataUrl: await readAsDataUrl(blob), bytes: blob.size };
  } finally {
    bitmap.close();
  }
}

async function prepareText(file: File): Promise<Attachment> {
  if (file.size > MAX_TEXT_BYTES) throw new AttachmentError(`${file.name} 超过 512KB，太大了`);
  const text = await file.text();
  if (text.includes("\u0000")) throw new AttachmentError(`${file.name} 看起来不是文本文件`);
  return { id: nextId(), kind: "text", name: file.name, text, bytes: file.size };
}

/** 图片与 ≤512KB 的文本内联进消息；其余（PDF、Office、压缩包、大文本……）先上传到服务器。 */
export function isInline(file: File): boolean {
  if (file.type.startsWith("image/") && file.type !== "image/svg+xml") return true;
  const textual = file.type.startsWith("text/") || TEXT_EXT.has(ext(file.name)) || file.type === "application/json";
  return textual && file.size <= MAX_TEXT_BYTES;
}

export async function prepare(file: File): Promise<Attachment> {
  if (file.type.startsWith("image/")) return prepareImage(file);
  return prepareText(file);
}

export function uploadedAttachment(name: string, absPath: string, bytes: number, image = false): Attachment {
  return { id: nextId(), kind: "file", name, absPath, bytes, ...(image ? { image: true } : {}) };
}

// ---- 文本附件的围栏格式 ----

const MARK = "附件";

/** 围栏长度取正文中最长反引号串 + 1，保证文件内容里的 ``` 不会提前闭合。 */
function fence(text: string): string {
  const longest = Math.max(2, ...(text.match(/`+/g) ?? []).map((m) => m.length));
  return "`".repeat(longest + 1);
}

function encodeTextAttachment(a: Extract<Attachment, { kind: "text" }>): string {
  const f = fence(a.text);
  const lang = ext(a.name);
  return `--- ${MARK}：${a.name} ---\n${f}${lang}\n${a.text.replace(/\n$/, "")}\n${f}`;
}

const UPLOAD_MARK = "附件已上传";

/** 上传的图片文件后面跟的提示行：插话只能发文字，模型要靠看图工具按路径读图，不提示它可能不去读。 */
const IMAGE_HINT = "（这是图片文件，请用看图工具读取该路径）";
/** 普通发送的照片：模型已随消息收到压缩图，原图只为历史预览/下载存档——不能再让它按路径重读原图（额度翻倍）。 */
const ARCHIVE_HINT = "（这是本消息所附图片的原图存档，图片已随消息提供，无需再读取）";

function encodeUploaded(a: Extract<Attachment, { kind: "file" }>, hint = IMAGE_HINT): string {
  return `[${UPLOAD_MARK}：${a.absPath}（${formatBytes(a.bytes)}）]${a.image ? `\n${hint}` : ""}`;
}

export function buildContent(text: string, attachments: Attachment[]): OutgoingContent {
  const files = attachments.filter((a): a is Extract<Attachment, { kind: "text" }> => a.kind === "text");
  const images = attachments.filter((a): a is Extract<Attachment, { kind: "image" }> => a.kind === "image");
  const uploaded = attachments.filter((a): a is Extract<Attachment, { kind: "file" }> => a.kind === "file");
  const archived = images.filter((a) => a.absPath).map((a) => encodeUploaded({
    id: a.id, kind: "file", name: a.name, absPath: a.absPath!, bytes: a.originalBytes ?? a.bytes, image: true,
  }, ARCHIVE_HINT));
  const body = [text.trim(), ...files.map(encodeTextAttachment), ...uploaded.map((a) => encodeUploaded(a)), ...archived].filter(Boolean).join("\n\n");
  if (!images.length) return body;
  return [
    ...(body ? [{ type: "text" as const, text: body }] : []),
    ...images.map((a) => ({ type: "image_url" as const, image_url: { url: a.dataUrl } })),
  ];
}

/**
 * 回合进行中的插话是纯文字通道（Hermes 的 /v1/runs/{id}/steer 只收文字）：文本和上传文件的格式与普通发送一致，
 * 但不能带内联图片——图片要先改成上传文件（composer 在插话时已这样处理），这里遇到内联图片就拒绝，不静默丢掉。
 */
export function buildSteerText(text: string, attachments: Attachment[]): string {
  if (attachments.some((a) => a.kind === "image")) throw new AttachmentError("插话里的图片需要先上传");
  const content = buildContent(text, attachments);
  return typeof content === "string" ? content : "";
}

export interface ParsedUserText {
  text: string;
  files: { name: string; text: string }[];
  /** 已上传到服务器的附件（只有路径）。 */
  uploads: { name: string; absPath: string; size: string; image?: boolean }[];
  /** 服务端入库时把图片替换成 [screenshot] 占位，原图不保留。 */
  droppedImages: number;
}

const FILE_RE = new RegExp(`(?:^|\\n\\n)--- ${MARK}：(.+?) ---\\n(\`{3,})[^\\n]*\\n([\\s\\S]*?)\\n\\2(?=\\n|$)`, "g");
const SCREENSHOT_RE = /^\[screenshot(?: removed[^\]]*)?\]$/;
// Hermes 把本地路径和远程 URL 写成独立行给模型当工具参数。入库后仍在正文里，气泡不显示。
// 路径和 URL 都可能含 ]，只认整行，避免吃掉同一行里的普通说明。
const NATIVE_IMAGE_HINT_RE = /^\[Image attached at: .+\]$/;
const NATIVE_IMAGE_URL_HINT_RE = /^\[Image attached: .+\]$/;
// grok 父回合插件把这段追加进模型看到的用户消息，入库后仍在正文末尾。气泡不显示。
const DELEGATION_GATE = "派发闸门：你是父代理。本回合若有独立、可并行、或会把大段日志、多文件读进父上下文的子任务，先调用 delegate_task，不要自己串行做完。单步查询、要跟用户来回、机械循环用现有工具或 execute_code，不派。用户明确说亲自做，不派。子代理自报完成不是验收。";
// plan-mode 插件 light_note()（plan_mode/render.py）。pre_llm_call 的 context 以空行追加在用户正文后，
// 入库后仍在 content 末尾。只认完整段落：目录可变，启用的后续句子按 bridge、blocked、skill 顺序整句出现，
// 未启用的句子直接跳过，且不超过 400 字。
const PLAN_NOTE_PREFIX = "Plan mode is ON. Plans directory: ";
const PLAN_NOTE_TAIL = '. Submit with plan_mode; do not ask "should I proceed?" in prose.';
const PLAN_NOTE_LEVERS = [
  ' If plan_mode is not in your tool list: tool_call(calls=[{"name": "plan_mode", "arguments": {"action": "submit"}}]).',
  " Terminal and execute_code stay blocked until approval.",
  " Do not load planning, TDD or debugging skills.",
] as const;

export function isPlanModeLightNote(text: string): boolean {
  if (text.length > 400 || !text.startsWith(PLAN_NOTE_PREFIX)) return false;
  const tailAt = text.indexOf(PLAN_NOTE_TAIL);
  if (tailAt < PLAN_NOTE_PREFIX.length) return false;
  const plansDir = text.slice(PLAN_NOTE_PREFIX.length, tailAt);
  if (!plansDir || /[\r\n]/.test(plansDir)) return false;
  let rest = text.slice(tailAt + PLAN_NOTE_TAIL.length);
  for (const lever of PLAN_NOTE_LEVERS) {
    if (rest.startsWith(lever)) rest = rest.slice(lever.length);
  }
  return rest.length === 0;
}

// 闭合栏必须不少于开栏长度。只比字符会把较短的 ``` 当成 ````` 的结束，剥掉仍在代码块里的计划提示。
function endsInsideFence(text: string): boolean {
  let marker = "";
  let length = 0;
  for (const line of text.split("\n")) {
    const match = /^[ \t]*(`{3,}|~{3,})/.exec(line);
    if (!match?.[1]) continue;
    const fence = match[1];
    const ch = fence[0] ?? "";
    if (!marker) {
      marker = ch;
      length = fence.length;
    } else if (ch === marker && fence.length >= length) {
      marker = "";
      length = 0;
    }
  }
  return marker !== "";
}

function stripTrailingPlanNote(text: string): string {
  if (endsInsideFence(text)) return text;
  const lines = text.split("\n");
  let last = lines.length - 1;
  while (last >= 0 && lines[last]!.trim() === "") last--;
  if (last < 0 || !isPlanModeLightNote(lines[last]!.trim())) return text;
  return lines.slice(0, last).join("\n");
}
// 路径可含 ] 和全角括号；仅把完整单行末尾的大小字段视为分隔符。
const UPLOAD_RE = /^\[附件已上传：(\/[^\r\n]+)（(\d+(?:\.\d+)? (?:B|KB|MB))）\]$/gm;
// 回合进行中的"插话"：Hermes 入库时包一层自描述标记（agent/prompt_builder.py 的 format_steer_marker：
// \n\n[OUT-OF-BAND USER MESSAGE — …]\n正文\n[/OUT-OF-BAND USER MESSAGE]）。给模型看的外壳，用户不该看到。
// 开头那行里没有嵌套的 "]"，所以 [^\]]* 足够。
const STEER_RE = /\[OUT-OF-BAND USER MESSAGE[^\]]*\]\n([\s\S]*?)\n\[\/OUT-OF-BAND USER MESSAGE\]/g;

export function parseUserText(raw: string): ParsedUserText {
  const files: ParsedUserText["files"] = [];
  let text = raw.replace(STEER_RE, (_m, body: string) => body).replace(FILE_RE, (_m, name: string, _f: string, body: string) => {
    files.push({ name, text: body });
    return "";
  });
  const uploads: ParsedUserText["uploads"] = [];
  text = text.replace(UPLOAD_RE, (_m, absPath: string, size: string, offset: number, source: string) => {
    const rest = source.slice(offset + _m.length);
    const image = rest.startsWith(`\n${IMAGE_HINT}`) || rest.startsWith(`\n${ARCHIVE_HINT}`);
    uploads.push({ name: absPath.split("/").pop() ?? absPath, absPath, size, ...(image ? { image: true } : {}) });
    return "";
  });
  for (const hint of [IMAGE_HINT, ARCHIVE_HINT]) text = text.replaceAll(`\n${hint}`, "").replaceAll(hint, "");
  let droppedImages = 0;
  text = text
    .split("\n")
    .filter((line) => {
      const trimmed = line.trim();
      if (SCREENSHOT_RE.test(trimmed)) {
        droppedImages++;
        return false;
      }
      return !(NATIVE_IMAGE_HINT_RE.test(trimmed) || NATIVE_IMAGE_URL_HINT_RE.test(trimmed) || trimmed === DELEGATION_GATE);
    })
    .join("\n")
    .replaceAll(DELEGATION_GATE, "");
  text = stripTrailingPlanNote(text).trim();
  droppedImages = Math.max(0, droppedImages - uploads.filter((a) => a.image).length);
  return { text, files, uploads, droppedImages };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
