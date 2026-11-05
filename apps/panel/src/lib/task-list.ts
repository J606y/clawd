// 从历史消息里的 todo_list 调用折叠当前清单。实时 SSE 参数不完整，不在这里读。

export type TaskStatus = "pending" | "in_progress" | "completed" | "cancelled";

export type TaskExecutionState =
  | "running"
  | "stalled"
  | "awaiting_acceptance"
  | "failed"
  | "cancelled"
  | "interrupted"
  | "unknown";

export interface TaskLink {
  delegation_id?: string;
  child_id?: string;
}

export interface TaskItem {
  id: string;
  content: string;
  status: TaskStatus;
  parent?: string;
  delegations?: TaskLink[];
  /** 仅投影写入。折叠历史时丢弃，避免把工具参数里的伪造执行态当真。 */
  execution?: TaskExecutionState;
}

export interface TaskExecution {
  delegationId?: string;
  delegation_id?: string;
  childId?: string;
  child_id?: string;
  todoId?: string;
  todo_id?: string;
  sessionId?: string;
  session_id?: string;
  profileKey?: string;
  profile_key?: string;
  status?: string;
  executionStatus?: string;
}

export interface TaskExecutionScope {
  sessionId: string;
  profileKey: string;
}

interface TaskMessage {
  role?: unknown;
  content?: unknown;
  tool_call_id?: unknown;
  tool_calls?: unknown;
}

interface ParsedTodo {
  id: string;
  content: string;
  status: TaskStatus;
  /** parent 键存在且为字符串。空字符串是清掉信号，不是缺省。 */
  parentSpecified: boolean;
  parent: string;
  linksSpecified: boolean;
  links: TaskLink[];
  delegationIdSpecified: boolean;
  delegationId: string;
  childIdSpecified: boolean;
  childId: string;
}

const STATUSES: readonly TaskStatus[] = ["pending", "in_progress", "completed", "cancelled"];
const MAX_LINKS = 32;
const MAX_LINK_CHARS = 256;

function cleanLinkId(value: unknown): string {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || text.length > MAX_LINK_CHARS) return "";
  if (text.indexOf("\n") >= 0 || text.indexOf("\r") >= 0 || text.indexOf("\u0000") >= 0) return "";
  return text;
}

function linkFrom(entry: unknown): TaskLink | null {
  const obj = asObject(entry);
  if (!obj) return null;
  const link: TaskLink = {};
  const delegation = cleanLinkId(obj.delegation_id);
  const child = cleanLinkId(obj.child_id);
  if (delegation) link.delegation_id = delegation;
  if (child) link.child_id = child;
  return link.delegation_id || link.child_id ? link : null;
}

function dedupeLinks(links: readonly TaskLink[]): TaskLink[] {
  const seen = new Set<string>();
  const out: TaskLink[] = [];
  for (const link of links) {
    const key = `${link.delegation_id ?? ""}\0${link.child_id ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ ...link });
    if (out.length >= MAX_LINKS) break;
  }
  return out;
}

function linksFromList(raw: unknown): TaskLink[] {
  if (!Array.isArray(raw)) return [];
  const links: TaskLink[] = [];
  for (const entry of raw) {
    const link = linkFrom(entry);
    if (link) links.push(link);
  }
  return dedupeLinks(links);
}

function isStatus(value: unknown): value is TaskStatus {
  if (typeof value !== "string") return false;
  for (const status of STATUSES) {
    if (status === value) return true;
  }
  return false;
}

function asObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function parseObject(value: unknown): Record<string, unknown> | null {
  if (typeof value === "string") {
    try {
      return asObject(JSON.parse(value));
    } catch {
      return null;
    }
  }
  return asObject(value);
}

function bareName(name: string): string {
  return name.startsWith("functions.") ? name.slice("functions.".length) : name;
}

function lastSegment(name: string): string {
  let start = 0;
  for (let i = 0; i < name.length; i++) {
    if (name[i] === ".") start = i + 1;
  }
  return name.slice(start);
}

function textContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const parts: string[] = [];
  for (const part of content) {
    const obj = asObject(part);
    if (obj && obj.type === "text" && typeof obj.text === "string") parts.push(obj.text);
  }
  return parts.join("\n\n");
}

/** 能解析成对象才判失败；纯文本、数组、解析失败都不算失败。 */
function resultObject(content: unknown): Record<string, unknown> | null {
  if (typeof content === "string" || Array.isArray(content)) {
    const text = textContent(content);
    if (!text) return null;
    try {
      return asObject(JSON.parse(text));
    } catch {
      return null;
    }
  }
  return asObject(content);
}

function isFailedResult(content: unknown): boolean {
  const obj = resultObject(content);
  if (!obj) return false;
  if (obj.error) return true;
  if (typeof obj.exit_code === "number" && obj.exit_code !== 0) return true;
  return obj.success === false;
}

function parseTodo(raw: unknown): ParsedTodo | null {
  const obj = asObject(raw);
  if (!obj) return null;
  if (typeof obj.id !== "string" || obj.id === "") return null;
  if (typeof obj.content !== "string" || obj.content === "") return null;
  if (!isStatus(obj.status)) return null;
  const parsed: ParsedTodo = {
    id: obj.id,
    content: obj.content,
    status: obj.status,
    parentSpecified: false,
    parent: "",
    linksSpecified: Object.prototype.hasOwnProperty.call(obj, "delegations"),
    links: Object.prototype.hasOwnProperty.call(obj, "delegations") ? linksFromList(obj.delegations) : [],
    delegationIdSpecified: Object.prototype.hasOwnProperty.call(obj, "delegation_id") && typeof obj.delegation_id === "string",
    delegationId: typeof obj.delegation_id === "string" ? obj.delegation_id.trim() : "",
    childIdSpecified: Object.prototype.hasOwnProperty.call(obj, "child_id") && typeof obj.child_id === "string",
    childId: typeof obj.child_id === "string" ? obj.child_id.trim() : "",
  };
  if (Object.prototype.hasOwnProperty.call(obj, "parent") && typeof obj.parent === "string") {
    parsed.parentSpecified = true;
    parsed.parent = obj.parent;
  }
  return parsed;
}

function copyLinks(links: readonly TaskLink[] | undefined): TaskLink[] | undefined {
  if (!links || links.length === 0) return undefined;
  return links.map((link) => ({ ...link }));
}

function applyLinks(item: TaskItem, parsed: ParsedTodo, previous: TaskLink[] | undefined): void {
  if (parsed.linksSpecified) {
    const links = copyLinks(parsed.links);
    if (links) item.delegations = links;
    return;
  }
  if (!parsed.delegationIdSpecified && !parsed.childIdSpecified) {
    const kept = copyLinks(previous);
    if (kept) item.delegations = kept;
    return;
  }
  const existing = copyLinks(previous) ?? [];
  if (parsed.delegationIdSpecified && parsed.childIdSpecified) {
    if (!cleanLinkId(parsed.delegationId) && !cleanLinkId(parsed.childId)) return;
    const next: TaskLink = {};
    const delegation = cleanLinkId(parsed.delegationId);
    const child = cleanLinkId(parsed.childId);
    if (delegation) next.delegation_id = delegation;
    if (child) next.child_id = child;
    if (!next.delegation_id && !next.child_id) return;
    const kept = existing.filter((link) => {
      const sameDelegation = Boolean(next.delegation_id && link.delegation_id === next.delegation_id);
      const sameChild = Boolean(next.child_id && link.child_id === next.child_id);
      return !sameDelegation && !sameChild;
    });
    kept.push(next);
    const links = dedupeLinks(kept);
    if (links.length) item.delegations = links;
    return;
  }
  const key = parsed.delegationIdSpecified ? "delegation_id" : "child_id";
  const raw = parsed.delegationIdSpecified ? parsed.delegationId : parsed.childId;
  const value = cleanLinkId(raw);
  if (existing.length > 1) {
    item.delegations = existing;
    return;
  }
  if (existing.length === 0) {
    if (!value) return;
    const created: TaskLink = {};
    if (key === "delegation_id") created.delegation_id = value;
    else created.child_id = value;
    item.delegations = [created];
    return;
  }
  const only: TaskLink = { ...existing[0] };
  if (value) {
    if (key === "delegation_id") only.delegation_id = value;
    else only.child_id = value;
  } else if (key === "delegation_id") delete only.delegation_id;
  else delete only.child_id;
  if (only.delegation_id || only.child_id) item.delegations = [only];
}

function itemFrom(parsed: ParsedTodo, keepEmptyParent: boolean): TaskItem {
  const item: TaskItem = { id: parsed.id, content: parsed.content, status: parsed.status };
  if (parsed.parentSpecified && (parsed.parent !== "" || keepEmptyParent)) item.parent = parsed.parent;
  applyLinks(item, parsed, undefined);
  return item;
}

/** 整表替换：重复 id 保留最后一次，并排到最后出现的位置。 */
function replaceList(raws: unknown[]): TaskItem[] {
  const order: string[] = [];
  const byId = new Map<string, TaskItem>();
  for (const raw of raws) {
    const parsed = parseTodo(raw);
    if (!parsed) continue;
    let at = -1;
    for (let i = 0; i < order.length; i++) {
      if (order[i] === parsed.id) {
        at = i;
        break;
      }
    }
    if (at >= 0) order.splice(at, 1);
    order.push(parsed.id);
    byId.set(parsed.id, itemFrom(parsed, true));
  }
  const out: TaskItem[] = [];
  for (const id of order) {
    const item = byId.get(id);
    if (item) out.push(item);
  }
  return out;
}

/** merge：已有 id 就地更新，不改顺序；新 id 追加。缺省 parent 保留，空字符串清掉。 */
function mergeList(list: readonly TaskItem[], raws: unknown[]): TaskItem[] {
  const next = list.map((item) => ({ ...item }));
  for (const raw of raws) {
    const parsed = parseTodo(raw);
    if (!parsed) continue;
    let idx = -1;
    for (let i = 0; i < next.length; i++) {
      if (next[i]?.id === parsed.id) {
        idx = i;
        break;
      }
    }
    if (idx < 0) {
      next.push(itemFrom(parsed, false));
      continue;
    }
    const prev = next[idx];
    if (!prev) continue;
    const updated: TaskItem = { id: prev.id, content: parsed.content, status: parsed.status };
    if (parsed.parentSpecified) {
      if (parsed.parent !== "") updated.parent = parsed.parent;
    } else if (prev.parent !== undefined) {
      updated.parent = prev.parent;
    }
    applyLinks(updated, parsed, prev.delegations);
    next[idx] = updated;
  }
  return next;
}

function childCalls(name: string, args: unknown): { wrapper: "tool_call" | "parallel"; calls: unknown[] } | null {
  const last = lastSegment(bareName(name));
  if (last !== "tool_call" && last !== "parallel") return null;
  const parsed = parseObject(args);
  if (!parsed) return null;
  const calls = last === "tool_call" ? parsed.calls : parsed.tool_uses;
  if (!Array.isArray(calls)) return null;
  return { wrapper: last, calls };
}

function childOf(wrapper: "tool_call" | "parallel", call: unknown): { name: string; args: unknown } | null {
  const obj = asObject(call);
  if (!obj) return null;
  const name = wrapper === "tool_call" ? obj.name : obj.recipient_name;
  if (typeof name !== "string" || name === "") return null;
  return { name, args: wrapper === "tool_call" ? obj.arguments : obj.parameters };
}

/** 展开包装，收集 todo_list 参数。嵌套最多 3 层。非 todo 调用跳过，不让整批作废。 */
function collectPayloads(name: string, args: unknown, depth: number): Record<string, unknown>[] {
  const base = bareName(name);
  if (base === "todo_list") {
    const parsed = parseObject(args);
    return parsed ? [parsed] : [];
  }
  if (depth >= 3) return [];
  const wrapped = childCalls(name, args);
  if (!wrapped) return [];
  const out: Record<string, unknown>[] = [];
  for (const call of wrapped.calls) {
    const child = childOf(wrapped.wrapper, call);
    if (!child) continue;
    const nested = collectPayloads(child.name, child.args, depth + 1);
    for (const item of nested) out.push(item);
  }
  return out;
}

/** null 表示包装展不开。叶子名已去掉 functions. 前缀。 */
function leafNames(name: string, args: unknown, depth: number): string[] | null {
  const base = bareName(name);
  if (base === "todo_list") return ["todo_list"];
  const last = lastSegment(base);
  if (last !== "tool_call" && last !== "parallel") return [base];
  if (depth >= 3) return null;
  const wrapped = childCalls(name, args);
  if (!wrapped) return null;
  const names: string[] = [];
  for (const call of wrapped.calls) {
    const child = childOf(wrapped.wrapper, call);
    if (!child) return null;
    const nested = leafNames(child.name, child.args, depth + 1);
    if (!nested) return null;
    for (const item of nested) names.push(item);
  }
  return names;
}

function failedIds(messages: readonly TaskMessage[]): Set<string> {
  const failed = new Set<string>();
  for (const message of messages) {
    if (message.role !== "tool" || typeof message.tool_call_id !== "string" || message.tool_call_id === "") continue;
    if (isFailedResult(message.content)) failed.add(message.tool_call_id);
  }
  return failed;
}

export function foldTaskList(messages: readonly TaskMessage[]): TaskItem[] | null {
  const failed = failedIds(messages);
  let list: TaskItem[] = [];
  let seen = false;
  for (const message of messages) {
    if (message.role !== "assistant" || !Array.isArray(message.tool_calls)) continue;
    for (const call of message.tool_calls) {
      const obj = asObject(call);
      if (!obj) continue;
      if (typeof obj.id === "string" && failed.has(obj.id)) continue;
      const fn = asObject(obj.function);
      if (!fn || typeof fn.name !== "string") continue;
      const payloads = collectPayloads(fn.name, fn.arguments, 0);
      for (const payload of payloads) {
        if (!Array.isArray(payload.todos)) continue;
        seen = true;
        list = payload.merge === true ? mergeList(list, payload.todos) : replaceList(payload.todos);
      }
    }
  }
  return seen ? list : null;
}

export function taskProgress(items: readonly TaskItem[]): { done: number; open: number } {
  let done = 0;
  let open = 0;
  for (const item of items) {
    if (item.status === "cancelled") continue;
    open += 1;
    if (item.status === "completed") done += 1;
  }
  return { done, open };
}

/** 只有这份消息确实属于当前打开的会话时才给出清单。换会话后的旧消息返回空。执行记录缺省时不投影。 */
export function tasksForSession(
  owner: string,
  sessionId: string,
  messages: readonly TaskMessage[],
  executions?: readonly TaskExecution[] | null,
  scope?: TaskExecutionScope | null,
): TaskItem[] {
  if (!sessionId || owner !== sessionId) return [];
  const folded = foldTaskList(messages) ?? [];
  if (!executions || !scope || scope.sessionId !== sessionId || !scope.profileKey) return folded;
  return projectTaskExecution(folded, executions, scope);
}

export function isTodoStep(step: { kind?: unknown; name?: unknown; args?: unknown }): boolean {
  if (!step || step.kind !== "tool" || typeof step.name !== "string") return false;
  if (bareName(step.name) === "todo_list") return true;
  const last = lastSegment(bareName(step.name));
  if (last !== "tool_call" && last !== "parallel") return false;
  const names = leafNames(step.name, step.args, 0);
  if (!names || names.length === 0) return false;
  for (const name of names) {
    if (name !== "todo_list") return false;
  }
  return true;
}

const EXECUTION_INPUT = ["running", "stalled", "returned", "failed", "cancelled", "interrupted", "unknown"] as const;

function executionText(record: TaskExecution, camel: "delegationId" | "childId" | "todoId" | "sessionId" | "profileKey", snake: "delegation_id" | "child_id" | "todo_id" | "session_id" | "profile_key"): string {
  const primary = record[camel];
  const fallback = record[snake];
  return cleanLinkId(typeof primary === "string" ? primary : fallback);
}

function foldExecution(states: readonly string[]): TaskExecutionState | null {
  if (states.length === 0) return null;
  if (states.indexOf("running") >= 0) return "running";
  if (states.indexOf("failed") >= 0) return "failed";
  if (states.indexOf("interrupted") >= 0) return "interrupted";
  if (states.indexOf("cancelled") >= 0) return "cancelled";
  if (states.indexOf("missing") >= 0 || states.indexOf("unknown") >= 0) return "unknown";
  if (states.indexOf("stalled") >= 0) return "stalled";
  for (const state of states) {
    if (state !== "returned") return "unknown";
  }
  return "awaiting_acceptance";
}

function pickExecution(matched: readonly string[]): string | null {
  if (matched.length === 0) return null;
  if (matched.indexOf("running") >= 0) return "running";
  if (matched.indexOf("failed") >= 0) return "failed";
  if (matched.indexOf("interrupted") >= 0) return "interrupted";
  if (matched.indexOf("cancelled") >= 0) return "cancelled";
  if (matched.indexOf("unknown") >= 0) return "unknown";
  if (matched.indexOf("stalled") >= 0) return "stalled";
  for (const state of matched) {
    if (state !== "returned") return "unknown";
  }
  return "returned";
}

function matchExecution(
  records: readonly { delegationId: string; childId: string; todoId: string; status: string }[],
  itemId: string,
  delegationId: string,
  childId: string,
): string | null {
  const matched: string[] = [];
  for (const record of records) {
    if (record.todoId && record.todoId !== itemId) continue;
    if (delegationId && record.delegationId && record.delegationId !== delegationId) continue;
    if (childId && record.childId && record.childId !== childId) continue;
    const byDelegation = Boolean(delegationId && record.delegationId === delegationId);
    const byChild = Boolean(childId && record.childId === childId);
    if (byDelegation || byChild) matched.push(record.status || "unknown");
  }
  return pickExecution(matched);
}

/** 按显式 id 投影执行指示。不改验收 status，不缓存，不按标题匹配。 */
export function projectTaskExecution(
  items: readonly TaskItem[],
  executions: readonly TaskExecution[],
  scope: TaskExecutionScope,
): TaskItem[] {
  const publicItems = items.map((item) => {
    const copied: TaskItem = { id: item.id, content: item.content, status: item.status };
    if (item.parent !== undefined) copied.parent = item.parent;
    const links = copyLinks(item.delegations);
    if (links) copied.delegations = links;
    return copied;
  });
  if (!scope.sessionId || !scope.profileKey) return publicItems;
  const claimsD = new Map<string, Set<string>>();
  const claimsC = new Map<string, Set<string>>();
  for (const item of publicItems) {
    for (const link of item.delegations ?? []) {
      if (link.delegation_id) {
        const owners = claimsD.get(link.delegation_id) ?? new Set<string>();
        owners.add(item.id);
        claimsD.set(link.delegation_id, owners);
      }
      if (link.child_id) {
        const owners = claimsC.get(link.child_id) ?? new Set<string>();
        owners.add(item.id);
        claimsC.set(link.child_id, owners);
      }
    }
  }
  const scoped: { delegationId: string; childId: string; todoId: string; status: string }[] = [];
  for (const raw of executions) {
    if (executionText(raw, "sessionId", "session_id") !== scope.sessionId || executionText(raw, "profileKey", "profile_key") !== scope.profileKey) continue;
    const rawStatus = typeof raw.executionStatus === "string" && raw.executionStatus
      ? raw.executionStatus
      : raw.status;
    const status = typeof rawStatus === "string" ? rawStatus.trim().toLowerCase() : "";
    let known = false;
    for (const allowed of EXECUTION_INPUT) {
      if (allowed === status) known = true;
    }
    scoped.push({
      delegationId: executionText(raw, "delegationId", "delegation_id"),
      childId: executionText(raw, "childId", "child_id"),
      todoId: executionText(raw, "todoId", "todo_id"),
      status: known ? status : "unknown",
    });
  }
  for (const item of publicItems) {
    const links = item.delegations ?? [];
    if (item.status === "completed" || item.status === "cancelled" || links.length === 0) continue;
    const states: string[] = [];
    for (const link of links) {
      const delegationId = link.delegation_id ?? "";
      const childId = link.child_id ?? "";
      const delegationOwners = delegationId ? claimsD.get(delegationId) : undefined;
      const childOwners = childId ? claimsC.get(childId) : undefined;
      if ((delegationOwners && delegationOwners.size !== 1) || (childOwners && childOwners.size !== 1)) {
        states.push("unknown");
        continue;
      }
      states.push(matchExecution(scoped, item.id, delegationId, childId) ?? "missing");
    }
    const folded = foldExecution(states);
    if (folded) item.execution = folded;
  }
  return publicItems;
}

const ACCEPTANCE_LABEL: Record<TaskStatus, string> = {
  pending: "待办",
  in_progress: "进行中",
  completed: "已完成",
  cancelled: "已取消",
};

/** 执行指示与验收 status 分开。无 execution 时与旧标志一致。 */
export function taskIndicator(item: TaskItem): { markStatus: TaskStatus; label: string; tag: string | null } {
  const acceptance = isStatus(item.status) ? item.status : "pending";
  if (acceptance === "completed" || acceptance === "cancelled") {
    return {
      markStatus: acceptance,
      label: ACCEPTANCE_LABEL[acceptance],
      tag: acceptance === "cancelled" ? "已取消" : null,
    };
  }
  if (item.execution === "running") return { markStatus: "in_progress", label: "进行中", tag: null };
  if (item.execution === "awaiting_acceptance") return { markStatus: "pending", label: "待验收", tag: "待验收" };
  if (item.execution === "failed") return { markStatus: acceptance, label: "失败", tag: "失败" };
  if (item.execution === "interrupted") return { markStatus: acceptance, label: "已中断", tag: "已中断" };
  if (item.execution === "cancelled") return { markStatus: acceptance, label: "已取消", tag: "已取消" };
  if (item.execution === "unknown") return { markStatus: acceptance, label: "状态不明", tag: "状态不明" };
  if (item.execution === "stalled") return { markStatus: acceptance, label: "疑似卡住", tag: "疑似卡住" };
  return { markStatus: acceptance, label: ACCEPTANCE_LABEL[acceptance], tag: null };
}
