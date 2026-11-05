import type { ModelChoice } from "./api.js";

const ARGON_MODEL = "gemini-4-argon";
const GEMINI_PROVIDER = "sub2api-gemini";
const FLASH_MODEL = "gemini-3.8-flash";
const FLASH_VARIANTS = /^gemini-3\.8-flash(?:-(?:low|medium|high))?$/;

/** 按模型名识别，不因提供方名称中含 Gemini 而误判其他模型。 */
export function isGeminiModel(model: string): boolean {
  return /(?:^|[/:])gemini(?:[-._]|$)/i.test(model);
}

/** 按模型名识别。grok-4.20 也算，不把无关名字里的 grok 片段算进去。 */
export function isGrokModel(model: string): boolean {
  return /(?:^|[/:])grok(?:[-._]|$)/i.test(model);
}

/** 保留原始偏好。Gemini 有效档最多 high。Grok 的 max/ultra 收到官方上限 xhigh。 */
export function modelEffort(model: string, effort: string): string {
  if (isGrokModel(model)) {
    if (effort === "low" || effort === "medium" || effort === "high") return effort;
    if (effort === "xhigh" || effort === "max" || effort === "ultra") return "xhigh";
    return effort;
  }
  if (!isGeminiModel(model)) return effort;
  return effort === "low" || effort === "medium" ? effort : "high";
}

/** Flash 各变体共用入口；保留既有 Argon 精确固定模型的语义。 */
export function canonicalModel(model: string, choices: ModelChoice[], provider = ""): string {
  const candidates = provider ? choices.filter(c => c.provider === provider) : choices;
  if (FLASH_VARIANTS.test(model)
    && candidates.some(c => c.provider === GEMINI_PROVIDER && FLASH_VARIANTS.test(c.model))) return FLASH_MODEL;
  if (candidates.some(c => c.model === model)) return model;
  if (/^gemini-4-argon-(low|medium|high)$/.test(model)
    && candidates.some(c => c.provider === GEMINI_PROVIDER && c.model === ARGON_MODEL)) return ARGON_MODEL;
  return model;
}

/**
 * 只为接口已确认的 Argon/Flash 拼接变体；其他 Gemini 仅收敛 effort。
 * 核心 gemini profile 的 thinkingLevel 只服务名为 gemini 的提供方。
 * sub2api-gemini 走 custom profile，只转发 reasoning_effort，不改模型名。
 * 不能在这里去掉后缀，否则代理仍按基础模型名路由。
 */
export function modelEffortFields(model: string, provider: string, effort: string, choices: ModelChoice[] = []) {
  effort = modelEffort(model, effort);
  model = canonicalModel(model, choices, provider);
  const dynamicBase = model === ARGON_MODEL ? ARGON_MODEL : FLASH_VARIANTS.test(model) ? FLASH_MODEL : "";
  if (dynamicBase && provider === GEMINI_PROVIDER) {
    return {
      model: `${dynamicBase}-${effort}`,
      provider,
      require_model_lock: true,
      model_options: { reasoning_effort: effort },
    };
  }
  return { model, provider, model_options: { reasoning_effort: effort } };
}
