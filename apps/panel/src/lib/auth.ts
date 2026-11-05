// 登录凭据 = SHA-256("hermes-panel:v2:" + 用户名(小写、去首尾空格) + "\n" + 密码) 的十六进制串，写入 Cookie。
// nginx（/etc/nginx/hermes-panel/auth-gate.conf）比对同一个哈希：命中才放行接口并注入 Hermes 密钥。
// 明文用户名与密码既不落地浏览器，也不存在服务器上。
// Safari/iOS（含主屏幕 PWA）把脚本写入的 Cookie 最多保留 7 天。脚本 Cookie 只留 10 分钟，
// 登录后由 nginx 的 /panel-auth/renew 用 HttpOnly 响应头重新下发，按 Max-Age 保留 30 天。

const COOKIE = "hp_auth";
const SALT = "hermes-panel:v2:";
// 脚本写入的 Cookie 不能带 HttpOnly。只保留 10 分钟，供续期请求带上。
// 长期凭据由 /panel-auth/renew 的服务端 Set-Cookie 下发，并且必须是 HttpOnly。
const BOOTSTRAP_MAX_AGE_S = 600;
const RENEW_KEY = "hp_auth_renewed_at";
const RENEW_EVERY_MS = 24 * 3600 * 1000;

export function normalizeUser(user: string): string {
  return user.trim().toLowerCase();
}

export async function credentialFor(user: string, password: string): Promise<string> {
  const data = new TextEncoder().encode(`${SALT}${normalizeUser(user)}\n${password}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function secureFlag(): string {
  // localhost HTTP cannot store Secure cookies; HTTPS (production and tests) still sets it.
  return typeof location !== "undefined" && location.protocol === "http:" ? "" : "; Secure";
}

export function storeCredential(hex: string): void {
  document.cookie = `${COOKIE}=${hex}; Path=/; Max-Age=${BOOTSTRAP_MAX_AGE_S}${secureFlag()}; SameSite=Strict`;
}

export function clearCredential(): void {
  document.cookie = `${COOKIE}=; Path=/; Max-Age=0${secureFlag()}; SameSite=Strict`;
  try {
    localStorage.removeItem(RENEW_KEY);
  } catch {
    // 存储不可用：下次登录本来就会强制续期。
  }
}

/** 请服务端重新下发登录 Cookie（见文件头）。`force` 用于刚登录时；否则一天最多一次。失败不影响使用，下次再续。 */
export async function renewCredential(force = false): Promise<void> {
  try {
    if (!force && Date.now() - Number(localStorage.getItem(RENEW_KEY) ?? 0) < RENEW_EVERY_MS) return;
    const res = await fetch(new URL("panel-auth/renew", document.baseURI), { method: "POST", credentials: "same-origin", cache: "no-store" });
    if (res.status === 204) localStorage.setItem(RENEW_KEY, String(Date.now()));
    else console.warn(`登录续期未生效：HTTP ${res.status}`);
  } catch (e) {
    console.warn("登录续期失败", e);
  }
}
