// 运行事件里的 clarify.request 与 POST /v1/runs/{id}/clarify 的答案编码。
// 线上一次只推一题；questions 里每题都有自己的 prompt_id 时才分页。

export const CLARIFY_ANSWER_MAX = 16384;

const RECOMMENDED = /\s*\(recommended\)\s*$/i;
const MAX_CHOICES = 4;
const MAX_QUESTIONS = 5;
const MAX_QUESTION_CHARS = 8000;
const MAX_CHOICE_CHARS = 8000;
const MAX_ID_CHARS = 200;

export interface ClarifyChoice {
  label: string;
  recommended: boolean;
}

export interface ClarifyQuestion {
  requestId: string;
  question: string;
  choices: ClarifyChoice[];
  multiSelect: boolean;
}

export interface ClarifyPrompt {
  generation: number;
  questions: ClarifyQuestion[];
  pending: boolean;
  error: string;
}

export interface ClarifyAnswer {
  requestId: string;
  answer: string;
}

export function clarifyIdentity(prompt: ClarifyPrompt): string {
  return `${prompt.generation}:${prompt.questions.map((q) => q.requestId).join("|")}`;
}

export function presentChoice(raw: string): ClarifyChoice | null {
  const trimmed = raw.trim();
  if (!trimmed || trimmed.length > MAX_CHOICE_CHARS) return null;
  const recommended = RECOMMENDED.test(trimmed);
  const label = trimmed.replace(RECOMMENDED, "").trim();
  if (!label || label.length > MAX_CHOICE_CHARS) return null;
  return { label, recommended };
}

function requestIdOf(item: Record<string, unknown>, fallback = ""): string {
  const id = item.prompt_id ?? item.request_id;
  const value = typeof id === "string" && id.trim() ? id.trim() : fallback.trim();
  return value.length > 0 && value.length <= MAX_ID_CHARS ? value : "";
}

function generationOf(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 1_000_000_000 ? value : null;
}

function parseQuestion(item: Record<string, unknown>, fallbackId: string): ClarifyQuestion | null {
  const question = typeof item.question === "string" ? item.question.trim() : "";
  const requestId = requestIdOf(item, fallbackId);
  if (!question || question.length > MAX_QUESTION_CHARS || !requestId) return null;
  const choices: ClarifyChoice[] = [];
  if (Array.isArray(item.choices)) {
    for (const raw of item.choices) {
      if (choices.length >= MAX_CHOICES) break;
      if (typeof raw !== "string") continue;
      const choice = presentChoice(raw);
      if (choice) choices.push(choice);
    }
  }
  return {
    requestId,
    question,
    choices,
    multiSelect: item.multi_select === true && choices.length > 0,
  };
}

function parseBatch(data: Record<string, unknown>): ClarifyQuestion[] | null {
  if (!Array.isArray(data.questions) || data.questions.length < 2) return null;
  const questions: ClarifyQuestion[] = [];
  for (const item of data.questions.slice(0, MAX_QUESTIONS)) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const question = parseQuestion(item as Record<string, unknown>, "");
    if (!question) return null;
    questions.push(question);
  }
  if (data.questions.length > MAX_QUESTIONS) return null;
  const ids = new Set(questions.map((q) => q.requestId));
  return ids.size === questions.length ? questions : null;
}

/** 缺代数、题干或请求标识时返回 null，不拿空答案去提交。 */
export function parseClarifyEvent(data: Record<string, unknown>): ClarifyPrompt | null {
  const generation = generationOf(data.execution_generation);
  const topId = requestIdOf(data);
  if (generation === null || !topId) return null;
  const batch = parseBatch(data);
  const single = batch ? null : parseQuestion(data, topId);
  const questions = batch ?? (single ? [single] : null);
  return questions ? { generation, questions, pending: false, error: "" } : null;
}

/** 单选：输入优先于选项。多选：选项标签的 JSON 数组，输入作为额外一项。 */
export function clarifyAnswer(question: ClarifyQuestion, selected: readonly number[], text: string): string | null {
  const typed = text.trim();
  if (typed.length > CLARIFY_ANSWER_MAX) return null;
  if (!question.choices.length) return typed || null;
  if (!question.multiSelect) {
    if (typed) return typed;
    const choice = question.choices[selected[0] ?? -1];
    return choice?.label ?? null;
  }
  const labels: string[] = [];
  for (const index of selected) {
    const choice = question.choices[index];
    if (choice && !labels.includes(choice.label)) labels.push(choice.label);
  }
  if (typed && !labels.includes(typed)) labels.push(typed);
  if (!labels.length) return null;
  const encoded = JSON.stringify(labels);
  return encoded.length <= CLARIFY_ANSWER_MAX ? encoded : null;
}

export function clarifyAnswers(prompt: ClarifyPrompt, selected: readonly (readonly number[])[], texts: readonly string[]): ClarifyAnswer[] | null {
  if (selected.length !== prompt.questions.length || texts.length !== prompt.questions.length) return null;
  const answers: ClarifyAnswer[] = [];
  for (let i = 0; i < prompt.questions.length; i++) {
    const question = prompt.questions[i]!;
    const answer = clarifyAnswer(question, selected[i] ?? [], texts[i] ?? "");
    if (!answer) return null;
    answers.push({ requestId: question.requestId, answer });
  }
  return answers;
}
