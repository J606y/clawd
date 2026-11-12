// vault.* 运行事件（vault.request / vault.settled）与 POST /v1/runs/{id}/vault 的答案编码。
// 秘密（密码 / 一次性验证码）只进答案帧：请求事件里只有站点 / 来源，解析时绝不接受密码字段。

export type VaultMethod = "vault.save_login" | "vault.code" | "vault.unlock_prompt";

export const VAULT_TIMEOUT_S: Record<string, number> = {
  save_login: 180,
  code: 180,
  unlock_prompt: 120,
};

export interface VaultPrompt {
  requestId: string;
  generation: number;
  method: VaultMethod;
  origin: string;
  site: string;
  backend: string;
  displayName: string;
  hint: string;
  timeoutSeconds: number;
  receivedAt: number;
  pending: boolean;
  error: string;
}

const MAX_ID_CHARS = 200;
const MAX_TEXT_CHARS = 512;
const VAULT_ANSWER_MAX = 16384;

function textOf(value: unknown, max = MAX_TEXT_CHARS): string {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  return trimmed.length <= max ? trimmed : "";
}

export function parseVaultEvent(d: Record<string, unknown>): VaultPrompt | null {
  const requestId = textOf(d.prompt_id ?? d.request_id, MAX_ID_CHARS);
  const method = typeof d.method === "string" ? d.method : "";
  if (
    !requestId ||
    (method !== "vault.save_login" && method !== "vault.code" && method !== "vault.unlock_prompt")
  ) {
    return null;
  }
  const generation =
    typeof d.execution_generation === "number" &&
    Number.isInteger(d.execution_generation) &&
    d.execution_generation >= 0 &&
    d.execution_generation <= 1_000_000_000
      ? d.execution_generation
      : 0;
  const params = d.params && typeof d.params === "object" ? (d.params as Record<string, unknown>) : {};
  const key = method.slice("vault.".length);
  const timeoutSeconds =
    typeof d.timeout_s === "number" && d.timeout_s > 0 && d.timeout_s <= 600
      ? Math.floor(d.timeout_s)
      : (VAULT_TIMEOUT_S[key] ?? 180);
  return {
    requestId,
    generation,
    method: method as VaultMethod,
    origin: textOf(params.origin),
    site: textOf(params.site),
    backend: textOf(params.backend),
    displayName: textOf(params.display_name),
    hint: textOf(params.hint),
    timeoutSeconds,
    receivedAt: Date.now(),
    pending: false,
    error: "",
  };
}

/** 把卡片里填的值编成答案帧：save_login 是一条 JSON 字符串，code / unlock 是原文。空串 = 拒绝。 */
export function encodeVaultAnswer(
  prompt: VaultPrompt,
  values: { identifier?: string; password?: string; code?: string },
): string {
  if (prompt.method === "vault.save_login") {
    const identifier = (values.identifier ?? "").trim();
    const password = values.password ?? "";
    if (!identifier || !password) return "";
    return JSON.stringify({ identifier, password });
  }
  const value = prompt.method === "vault.code" ? (values.code ?? "") : (values.password ?? "");
  return value.length <= VAULT_ANSWER_MAX ? value : "";
}

export function vaultMethodLabel(method: VaultMethod): string {
  return method === "vault.save_login"
    ? "保存登录信息"
    : method === "vault.code"
      ? "输入一次性验证码"
      : "解锁密码管理器";
}
