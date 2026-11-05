// Page-local saves. iOS standalone ignores <a download> and replaces the PWA with Quick Look.

const UNSAFE_NAME = /[\u0000-\u001f\u007f/\\]/g;
const SAVE_CAP = 50 * 1024 * 1024;
const IMAGE_TYPE: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  bmp: "image/bmp",
};

export interface DownloadHost {
  userAgent: string;
  platform: string;
  maxTouchPoints: number;
}

export function safeDownloadName(name: string): string {
  const cleaned = name.replace(UNSAFE_NAME, "_").trim();
  return cleaned || "附件";
}

export function isIosDownloadHost(nav: DownloadHost): boolean {
  if (/iPad|iPhone|iPod/.test(nav.userAgent)) return true;
  return nav.platform === "MacIntel" && nav.maxTouchPoints > 1;
}

export function shouldShareDownload(ios: boolean, canShareFiles: boolean): boolean {
  return ios && canShareFiles;
}

function typeFromName(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return IMAGE_TYPE[ext] ?? "application/octet-stream";
}

function currentHost(): DownloadHost {
  return {
    userAgent: navigator.userAgent,
    platform: navigator.platform,
    maxTouchPoints: navigator.maxTouchPoints,
  };
}

export async function pageSave(blob: Blob, name: string): Promise<"shared" | "downloaded"> {
  const safe = safeDownloadName(name);
  const headerType = blob.type.split(";")[0]?.trim() ?? "";
  const type = headerType && headerType !== "application/octet-stream" ? headerType : typeFromName(safe);
  const file = new File([blob], safe, { type });
  const ios = isIosDownloadHost(currentHost());
  if (shouldShareDownload(ios, navigator.canShare?.({ files: [file] }) ?? false)) {
    try {
      await navigator.share({ files: [file] });
    } catch (error) {
      if (!(error instanceof DOMException) || error.name !== "AbortError") throw error;
    }
    return "shared";
  }
  if (ios) throw new Error("无法在当前页面保存，请长按图片保存");
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = safe;
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return "downloaded";
}

export async function fetchAndSave(url: string, name: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(url, { credentials: "same-origin", signal });
  if (!response.ok) {
    throw new Error(response.status === 404 ? "文件不存在或已被移走" : response.status === 401 ? "登录已过期，请重新登录后下载" : response.status === 403 ? "没有权限下载这个文件" : `下载失败（${response.status}），请重试`);
  }
  const declared = Number(response.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > SAVE_CAP) throw new Error("文件超过 50MB，无法在页面内保存");
  if (!response.body) throw new Error("文件内容为空，请重试");
  const reader = response.body.getReader();
  const chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > SAVE_CAP) {
        await reader.cancel();
        throw new Error("文件超过 50MB，无法在页面内保存");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const headerType = response.headers.get("Content-Type")?.split(";")[0]?.trim() || "";
  await pageSave(new Blob(chunks, { type: headerType || typeFromName(name) }), name);
}
