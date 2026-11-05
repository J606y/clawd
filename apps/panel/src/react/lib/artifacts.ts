// Assistant-only artifact references. Server independently rechecks real paths and secrets.
import { Lexer, type Token } from "marked";
export interface Artifact {
  source: string;
  name: string;
  image: boolean;
  preview: string;
  download: string;
}
export interface AssistantContent { text: string; artifacts: Artifact[] }
const LEGACY_ROOTS = ["/root/hermes-uploads/", "/root/hermes-workspace/", "/root/.hermes/images/", "/root/.hermes/cron/output/"];

function normalizeArtifactRoot(item: string): string | null {
  if (!item.startsWith("/") || item.startsWith("//") || item.includes("\\") || item.includes("..") || /[\u0000-\u001f\u007f]/.test(item)) return null;
  const parts = item.split("/").filter((part) => part.length > 0);
  if (!parts.length || parts.some((part) => part === "." || part === "..")) return null;
  return `/${parts.join("/")}/`;
}

// Injected clawd roots replace the legacy production list. A missing injection keeps the old adapter roots.
export function artifactRoots(): string[] {
  const extra = (globalThis as { __CLAWD_ARTIFACT_ROOTS__?: unknown }).__CLAWD_ARTIFACT_ROOTS__;
  if (extra !== undefined) {
    if (!Array.isArray(extra)) return [];
    const roots: string[] = [];
    for (const item of extra) {
      if (typeof item !== "string") continue;
      const normalized = normalizeArtifactRoot(item);
      if (normalized) roots.push(normalized);
    }
    return [...new Set(roots)].sort((a, b) => b.length - a.length);
  }
  return [...LEGACY_ROOTS].sort((a, b) => b.length - a.length);
}

function injectedBarePattern(roots: string[]): RegExp | null {
  const prefixes = roots
    .map((root) => root.replace(/\/$/, ""))
    .filter((root) => root.startsWith("/") && root !== "/root" && !root.startsWith("/root/"))
    .sort((a, b) => b.length - a.length);
  if (!prefixes.length) return null;
  const alt = prefixes.map((root) => root.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return new RegExp(`"((?:${alt})/[^"\\n]+)"|'((?:${alt})/[^'\\n]+)'|<((?:${alt})/[^>\\n]+)>|(?:${alt})/[^\\s<>"'\`()\\[\\],，。;；]+`, "g");
}
const IMAGE = /\.(?:png|jpe?g|gif|webp|bmp)$/i;
const OUTPUT = /\.(?:png|jpe?g|gif|webp|bmp|tiff?|svg|pdf|docx?|xlsx?|pptx?|odt|ods|odp|rtf|txt|md|epub|csv|tsv|json|xml|ya?ml|geojson|gpx|km[lz]|zip|tar|gz|tgz|bz2|xz|7z|rar|apk|ipa|html?|mp3|m2a|wav|ogg|opus|m4a|flac|mp4|mov|avi|mkv|webm|3gp)$/i;
// Mirror sidecar DENY_NAME_RE, with additional private credential containers.
const SECRET = /(^config\.ya?ml$|\.db(?:-wal|-shm|-journal)?$|\.(?:key|pem|p12|pfx)$|^id_[a-z0-9_]+$|^authorized_keys$|^auth\.json$|^(?:node_modules|credentials?|secrets?|tokens?)$|(?:^|[_-])(?:api[_-]?keys?|keys?|secrets?|passw(?:or)?ds?|private[_-]?keys?|cookies?|authorization|token|hash(?:es)?|salts?|credentials?)$)/i;
const cache = new Map<string, AssistantContent>();
const ARTIFACT_SPECIAL = /[/:[\]()<>`'"]/;
const literalSources = new WeakMap<AssistantContent, true>();

function markLiteral(source: string, result: AssistantContent): AssistantContent {
  if (result.text === source && result.artifacts.length === 0 && !ARTIFACT_SPECIAL.test(source)) literalSources.set(result, true);
  return result;
}

export function artifactReference(path: string): Artifact | null {
  const data = /^data:image\/(png|jpeg|gif|webp|bmp);base64,([A-Za-z0-9+/]+={0,2})$/.exec(path);
  if (data && path.length <= 2 * 1024 * 1024 && data[2].length % 4 === 0) return { source: path, name: `生成图片.${data[1] === "jpeg" ? "jpg" : data[1]}`, image: true, preview: path, download: path };
  const roots = artifactRoots();
  if (path.length > 4096 || !roots.some((root) => path.startsWith(root))) return null;
  const parts = path.split("/");
  if (parts.some((p) => p === "." || p === "..") || /[\x00-\x1f\x7f\\?#%]/.test(path)) return null;
  // Root's .hermes is intentional; all descendant dotfiles and secret containers are not.
  const relative = path.slice(roots.find((root) => path.startsWith(root))!.length);
  if (relative.split("/").some((p) => p.startsWith(".") || SECRET.test(p))) return null;
  const name = parts.at(-1)!;
  if (!name) return null;
  const url = `/panel-sidecar/files/artifact?path=${encodeURIComponent(path)}`;
  return { source: path, name, image: IMAGE.test(path), preview: `${url}&preview=1`, download: url };
}

export function parseAssistantContent(source: string, options: { cache?: boolean; priorSource?: string; priorResult?: AssistantContent } = {}): AssistantContent {
  const useCache = options.cache ?? true;
  if (useCache) {
    const hit = cache.get(source);
    if (hit) {
      cache.delete(source);
      cache.set(source, hit);
      return hit;
    }
  }
  const priorSource = options.priorSource;
  const priorResult = options.priorResult;
  if (!useCache && priorResult && typeof priorSource === "string" && source.startsWith(priorSource) && literalSources.get(priorResult) === true) {
    const delta = source.slice(priorSource.length);
    if (delta && !ARTIFACT_SPECIAL.test(delta)) {
      const extended = markLiteral(source, { text: priorResult.text + delta, artifacts: [] });
      return extended;
    }
  }
  const artifacts: Artifact[] = [];
  const scan = source.slice(0, 2 * 1024 * 1024);
  const protectedText = new Uint8Array(scan.length);
  const markdownRefs: { raw: string; path: string; offset: number }[] = [];
  const locateSegment = (parent: string, raw: string, fromIndex = 0): { start: number; end: number } | null => {
    const at = parent.indexOf(raw, fromIndex);
    if (at >= 0) return { start: at, end: at + raw.length };
    const rawLines = raw.split("\n");
    const escapedLines = rawLines.map((line, idx) => {
      const escaped = line.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      return idx === 0 ? escaped : "(?:[ \\t]*>[ \\t]?|[ \\t]+)*" + escaped;
    });
    const re = new RegExp(escapedLines.join("\\n"), "g");
    re.lastIndex = fromIndex;
    const match = re.exec(parent);
    if (match) return { start: match.index, end: match.index + match[0].length };
    return null;
  };
  const protect = (tokens: Token[], base = 0, parent = scan) => {
    let cursor = 0;
    for (const token of tokens) {
      const loc = locateSegment(parent, token.raw, cursor);
      const children = "tokens" in token && token.tokens ? token.tokens
        : token.type === "list" ? token.items
        : token.type === "table" ? [...token.header, ...token.rows.flat()].flatMap((cell) => cell.tokens) : [];
      if (!loc) {
        // Marked removes quote/list prefixes from nested raw text. Descend into exact inline
        // spans; if a code block still cannot be mapped, conservatively protect its container.
        if (["code", "codespan", "html"].includes(token.type)) protectedText.fill(1, base, base + parent.length);
        else if (children.length) protect(children, base, parent);
        continue;
      }
      cursor = loc.end;
      const at = loc.start;
      if (token.type === "link" || token.type === "image") markdownRefs.push({ raw: token.raw, path: token.href, offset: base + at });
      if (["code", "codespan", "html"].includes(token.type)) protectedText.fill(1, base + at, base + cursor);
      else if (children.length) protect(children, base + at, parent.slice(loc.start, loc.end));
    }
  };
  protect(Lexer.lex(scan));
  const replacements: { start: number; end: number }[] = [];
  const collect = (raw: string, path: string, offset: number, explicit: boolean) => {
    if (protectedText.subarray(offset, offset + raw.length).some(Boolean) || replacements.some((r) => offset < r.end && offset + raw.length > r.start)) return;
    if (artifacts.length >= 32 || (!explicit && !OUTPUT.test(path))) return;
    const artifact = artifactReference(path);
    if (!artifact) return;
    if (!artifacts.some((v) => v.source === artifact.source)) artifacts.push(artifact);
    replacements.push({ start: offset, end: offset + raw.length });
  };
  for (const ref of markdownRefs) collect(ref.raw, ref.path, ref.offset, true);
  // Quoted destinations with spaces are emitted by some tools although not standard Markdown.
  for (const m of scan.matchAll(/!?\[[^\]\n]{0,256}\]\(\s*(?:<([^>\n]+)>|"([^"\n]+)"|'([^'\n]+)'|([^\s)]+))\s*(?:["'][^\n]*?["']\s*)?\)/g)) collect(m[0], m[1] ?? m[2] ?? m[3] ?? m[4], m.index, true);
  for (const m of scan.matchAll(/MEDIA:\s*(?:"([^"\n]+)"|'([^'\n]+)'|<([^>\n]+)>|([^\s<>"'`,，。;；]+))/g)) collect(m[0], m[1] ?? m[2] ?? m[3] ?? m[4], m.index, true);
  for (const m of scan.matchAll(/data:image\/(?:png|jpeg|gif|webp|bmp);base64,[A-Za-z0-9+/]+={0,2}/g)) collect(m[0], m[0], m.index, true);
  for (const m of scan.matchAll(/"(\/root\/[^"\n]+)"|'(\/root\/[^'\n]+)'|<(\/root\/[^>\n]+)>|\/root\/[^\s<>"'`()\[\],，。;；]+/g)) {
    if (m.index && !/[\s(:：]/.test(scan[m.index - 1])) continue;
    collect(m[0], m[1] ?? m[2] ?? m[3] ?? m[0], m.index, false);
  }
  const extraBare = injectedBarePattern(artifactRoots());
  if (extraBare) {
    for (const m of scan.matchAll(extraBare)) {
      if (m.index && !/[\s(:：]/.test(scan[m.index - 1])) continue;
      collect(m[0], m[1] ?? m[2] ?? m[3] ?? m[0], m.index, false);
    }
  }
  // Ordered by appearance, regardless of which syntax discovered a reference.
  artifacts.sort((a, b) => scan.indexOf(a.source) - scan.indexOf(b.source));
  let cursor = 0;
  let text = "";
  for (const r of replacements.sort((a, b) => a.start - b.start)) { text += scan.slice(cursor, r.start); cursor = r.end; }
  text += source.slice(cursor);
  const result = { text, artifacts };
  markLiteral(source, result);
  if (useCache && source.length <= 65536) {
    if (cache.has(source)) {
      cache.delete(source);
    } else if (cache.size >= 100) {
      cache.delete(cache.keys().next().value!);
    }
    cache.set(source, result);
  }
  return result;
}
