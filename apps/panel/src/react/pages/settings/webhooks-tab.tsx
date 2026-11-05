import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useCreateWebhook,
  useDeleteWebhook,
  useSetWebhookEnabled,
  useTestWebhook,
  useWebhookDeliveries,
  useWebhooks,
} from "@/lib/queries";
import type { WebhookCreate, WebhookInfo } from "@/lib/sidecar";
import { Empty, errText, Head, Loading, Switch, usePageVisible } from "./bits";

const NAME_RE = /^[a-z0-9_-]{1,64}$/;
const TOKEN_RE = /^[A-Za-z0-9_:-]{1,40}$/;
const PROMPT_MAX = 4000;
const DESC_MAX = 200;
const EVENTS_MAX = 20;

interface Draft {
  name: string;
  prompt: string;
  description: string;
  deliver: string;
  events: string;
}

interface Reveal {
  name: string;
  url: string;
  secret: string;
}

const EMPTY_DRAFT: Draft = { name: "", prompt: "", description: "", deliver: "", events: "" };

function fmtTime(raw: string | null | undefined): string {
  if (!raw) return "—";
  const t = Date.parse(raw);
  return Number.isNaN(t) ? raw : new Date(t).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function fullUrl(path: string): string {
  return `${location.origin}${path}`;
}

export function WebhooksTab() {
  const visible = usePageVisible();
  const hooks = useWebhooks();
  const createHook = useCreateWebhook();
  const deleteHook = useDeleteWebhook();
  const setEnabled = useSetWebhookEnabled();
  const testHook = useTestWebhook();
  const client = useQueryClient();
  const busyRef = useRef("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null);
  const [copyMsg, setCopyMsg] = useState<{ key: string; message: string } | null>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; output: string }>>({});
  const [promptOpen, setPromptOpen] = useState<Record<string, boolean>>({});
  const [confirmDelete, setConfirmDelete] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>({ ...EMPTY_DRAFT });
  const [formError, setFormError] = useState("");
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [revealMsg, setRevealMsg] = useState("");
  const [route, setRoute] = useState("");
  const deliveries = useWebhookDeliveries(route, 30, visible);

  function begin(key: string): boolean {
    if (busyRef.current) return false;
    busyRef.current = key;
    setBusy(key);
    setNotice("");
    setRowError(null);
    return true;
  }

  function end() {
    busyRef.current = "";
    setBusy("");
  }

  function parseEvents(raw: string): string[] {
    return raw.split(",").map((item) => item.trim()).filter((item) => item.length > 0);
  }

  function validate(): string {
    const name = draft.name.trim();
    if (!name) return "请填写名称";
    if (!NAME_RE.test(name)) return "名称只能包含小写字母、数字、下划线、连字符，最长 64 个字符";
    if (hooks.data?.data.some((item) => item.name === name)) return "已存在同名 Webhook";
    if (!draft.prompt.trim()) return "请填写提示词";
    if (draft.prompt.length > PROMPT_MAX) return `提示词最长 ${PROMPT_MAX} 个字符`;
    if (draft.description.length > DESC_MAX) return `描述最长 ${DESC_MAX} 个字符`;
    const deliver = draft.deliver.trim();
    if (deliver && !TOKEN_RE.test(deliver)) return "投递目标只能包含字母、数字、下划线、冒号、连字符，最长 40 个字符";
    const events = parseEvents(draft.events);
    if (events.length > EVENTS_MAX) return `事件最多 ${EVENTS_MAX} 项`;
    const bad = events.find((item) => !TOKEN_RE.test(item));
    if (bad) return `事件「${bad}」格式不正确：只能包含字母、数字、下划线、冒号、连字符，最长 40 个字符`;
    return "";
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current || !hooks.data?.platformEnabled) return;
    const problem = validate();
    if (problem) {
      setFormError(problem);
      return;
    }
    const body: WebhookCreate = { name: draft.name.trim(), prompt: draft.prompt.trim() };
    if (draft.description.trim()) body.description = draft.description.trim();
    if (draft.deliver.trim()) body.deliver = draft.deliver.trim();
    const events = parseEvents(draft.events);
    if (events.length) body.events = events;
    if (!begin("create")) return;
    setFormError("");
    try {
      const result = await createHook.mutateAsync(body);
      setReveal({ name: result.name, url: fullUrl(result.path), secret: result.secret });
      setDraft({ ...EMPTY_DRAFT });
      setFormOpen(false);
    } catch (error) {
      setFormError(errText(error, "创建失败"));
    } finally {
      end();
    }
  }

  async function toggle(item: WebhookInfo) {
    if (!begin(item.name)) return;
    const next = !item.enabled;
    try {
      await setEnabled.mutateAsync({ name: item.name, enabled: next });
      setNotice(`「${item.name}」已${next ? "启用" : "停用"}`);
    } catch (error) {
      setRowError({ key: item.name, message: errText(error, "切换失败") });
    } finally {
      end();
    }
  }

  async function remove(item: WebhookInfo) {
    if (!begin(item.name)) return;
    try {
      await deleteHook.mutateAsync(item.name);
      setConfirmDelete("");
      setTests((current) => Object.fromEntries(Object.entries(current).filter(([key]) => key !== item.name)));
      if (route === item.name) setRoute("");
      setNotice(`「${item.name}」已删除`);
      await client.invalidateQueries({ queryKey: ["webhook-deliveries"] });
    } catch (error) {
      setRowError({ key: item.name, message: errText(error, "删除失败") });
    } finally {
      end();
    }
  }

  async function sendTest(item: WebhookInfo) {
    if (!begin(item.name)) return;
    try {
      const result = await testHook.mutateAsync(item.name);
      setTests((current) => ({ ...current, [item.name]: { ok: result.ok, output: result.output } }));
      await client.invalidateQueries({ queryKey: ["webhook-deliveries"] });
    } catch (error) {
      setRowError({ key: item.name, message: errText(error, "测试失败") });
    } finally {
      end();
    }
  }

  async function copyUrl(item: WebhookInfo) {
    try {
      await navigator.clipboard.writeText(fullUrl(item.path));
      setCopyMsg({ key: item.name, message: "已复制" });
    } catch {
      setCopyMsg({ key: item.name, message: "复制失败，请手动选择地址" });
    }
  }

  async function copyReveal(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      setRevealMsg(`${label}已复制`);
    } catch {
      setRevealMsg("复制失败，请手动选择后复制");
    }
  }

  const view = hooks.data;
  const platformOn = view?.platformEnabled ?? false;
  const names = (view?.data ?? []).map((item) => item.name);
  if (route && !names.includes(route)) names.push(route);

  return (
    <div className="webhooks-root">
      <Head title="Webhook">
        <button
          type="button"
          className="btn primary"
          aria-label="新建 Webhook"
          aria-expanded={formOpen}
          disabled={!platformOn || busy.length > 0}
          onClick={() => { setFormOpen((open) => !open); setFormError(""); }}
        >
          新建
        </button>
      </Head>
      {view && !platformOn ? <div className="banner" role="alert">Webhook 平台未开启，无法新建；请在服务器上开启并重启网关</div> : null}
      {notice ? <div className="notice ok" role="status">{notice}</div> : null}
      {formOpen && platformOn ? (
        <form className="card" onSubmit={(event) => void create(event)} noValidate>
          <fieldset disabled={busy === "create" || !platformOn}>
            <label htmlFor="wh-name">名称</label>
            <input id="wh-name" type="text" maxLength={64} autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="小写字母、数字、下划线、连字符" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            <label htmlFor="wh-prompt">提示词（收到请求时发给 Agent 的指令）</label>
            <textarea id="wh-prompt" maxLength={PROMPT_MAX} value={draft.prompt} onChange={(event) => setDraft({ ...draft, prompt: event.target.value })} />
            <label htmlFor="wh-desc">描述（可选）</label>
            <input id="wh-desc" type="text" maxLength={DESC_MAX} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
            <label htmlFor="wh-deliver">投递目标（可选）</label>
            <input id="wh-deliver" type="text" maxLength={40} autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="log / whatsapp / telegram" value={draft.deliver} onChange={(event) => setDraft({ ...draft, deliver: event.target.value })} />
            <label htmlFor="wh-events">事件（可选，逗号分隔，留空为全部事件）</label>
            <input id="wh-events" type="text" autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="push, pull_request" value={draft.events} onChange={(event) => setDraft({ ...draft, events: event.target.value })} />
          </fieldset>
          <div className="formacts">
            <button className="btn primary" type="submit" aria-label="创建 Webhook" disabled={busy.length > 0 || !platformOn}>{busy === "create" ? "创建中…" : "创建"}</button>
            <button className="btn" type="button" aria-label="取消新建 Webhook" disabled={busy === "create"} onClick={() => { setFormOpen(false); setFormError(""); }}>取消</button>
            {formError ? <span className="error" role="alert">{formError}</span> : null}
          </div>
        </form>
      ) : null}
      {hooks.isError ? (
        <div className="empty"><span className="error">{errText(hooks.error, "Webhook 加载失败")}</span><br />
          <button type="button" className="link" aria-label="重试加载 Webhook" onClick={() => void hooks.refetch()}>重试</button>
        </div>
      ) : hooks.isLoading && !view ? (
        <>
          <Loading />
          <div className="skeleton" />
        </>
      ) : view && view.data.length ? (
        <div className="card list">
          {view.data.map((item) => (
            <div key={item.name} className="hook">
              <div className="top">
                <h3>{item.name}</h3>
                <Switch checked={item.enabled} disabled={busy.length > 0} label={`${item.enabled ? "停用" : "启用"} Webhook ${item.name}`} onClick={() => void toggle(item)} />
              </div>
              <div className="path">
                <code title={fullUrl(item.path)}>{item.path}</code>
                <button type="button" className="btn" aria-label={`复制 ${item.name} 的完整地址`} onClick={() => void copyUrl(item)}>复制完整地址</button>
                {copyMsg?.key === item.name ? <span className={`tiny ${copyMsg.message === "已复制" ? "ok" : "error"}`} role="status">{copyMsg.message}</span> : null}
              </div>
              {item.description ? <div className="desc">{item.description}</div> : null}
              <div className="meta">
                <span>事件：{item.events.length ? item.events.join("、") : "全部事件"}</span>
                <span>投递：{item.deliver ?? "—"}</span>
                <span className={`pill ${item.secretSet ? "on" : "off"}`}>{item.secretSet ? "已设置密钥" : "未设置"}</span>
                <span>创建于 {fmtTime(item.createdAt)}</span>
              </div>
              {item.prompt ? (
                <details open={!!promptOpen[item.name]} onToggle={(event) => setPromptOpen((current) => ({ ...current, [item.name]: event.currentTarget.open }))}>
                  <summary>提示词</summary>
                  <pre>{item.prompt}</pre>
                </details>
              ) : null}
              <div className="acts">
                <button type="button" className="btn" aria-label={`向 ${item.name} 发送测试`} disabled={busy.length > 0} onClick={() => void sendTest(item)}>
                  {busy === item.name ? "处理中…" : "发送测试"}
                </button>
                <span className="tiny muted">会触发一次真实的 Agent 运行</span>
                {confirmDelete === item.name ? (
                  <>
                    <button type="button" className="btn danger" aria-label={`确认删除 ${item.name}`} disabled={busy.length > 0} onClick={() => void remove(item)}>确认删除</button>
                    <button type="button" className="btn" aria-label="取消删除" disabled={busy.length > 0} onClick={() => setConfirmDelete("")}>取消</button>
                  </>
                ) : (
                  <button type="button" className="btn danger" aria-label={`删除 ${item.name}`} disabled={busy.length > 0} onClick={() => setConfirmDelete(item.name)}>删除</button>
                )}
              </div>
              {rowError?.key === item.name ? <div className="error rowerr" role="alert">{rowError.message}</div> : null}
              {tests[item.name] ? (
                <>
                  <div className={`tiny ${tests[item.name]?.ok ? "ok" : "error"} rowerr`}>{tests[item.name]?.ok ? "测试已发送" : "测试失败"}</div>
                  {tests[item.name]?.output ? <pre>{tests[item.name]?.output}</pre> : null}
                </>
              ) : null}
            </div>
          ))}
        </div>
      ) : view ? (
        <Empty>还没有 Webhook</Empty>
      ) : null}
      <h2>最近投递</h2>
      <p className="hint">来自网关日志，日志轮转后较早记录会消失。每 15 秒自动刷新。</p>
      <div className="head" style={{ marginBottom: 12 }}>
        <select aria-label="按路由筛选" value={route} onChange={(event) => setRoute(event.target.value)}>
          <option value="">全部路由</option>
          {names.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
        <span style={{ flex: 1 }} />
        <button type="button" className="btn" aria-label="刷新投递记录" onClick={() => void deliveries.refetch()}>刷新</button>
      </div>
      {deliveries.isError ? <div className="error" role="alert" style={{ marginBottom: 8 }}>{errText(deliveries.error, "投递记录加载失败")}</div> : null}
      {deliveries.data?.length ? (
        <div className="card list">
          <div className="drow dhead"><span>时间</span><span>路由</span><span>事件</span><span>长度</span><span>投递 ID</span></div>
          {deliveries.data.map((item, index) => (
            <div key={`${item.delivery}-${index}`} className="drow">
              <span>{fmtTime(item.time)}</span>
              <span title={item.route}>{item.route}</span>
              <span title={item.event}>{item.event || "—"}</span>
              <span title="提示词长度">{item.promptLen}</span>
              <span className="mono" title={item.delivery}>{item.delivery.slice(0, 8)}</span>
            </div>
          ))}
        </div>
      ) : deliveries.isError ? null : (
        <Empty>最近没有收到投递</Empty>
      )}
      {reveal ? (
        <div className="modal" role="dialog" aria-modal="true" aria-labelledby="rv-title">
          <div className="panel">
            <h2 id="rv-title">「{reveal.name}」已创建</h2>
            <div className="error" role="alert">密钥只显示这一次，关闭后无法再查看。</div>
            <label>密钥</label>
            <code className="secret" tabIndex={0}>{reveal.secret}</code>
            <button type="button" className="btn" aria-label="复制密钥" onClick={() => void copyReveal(reveal.secret, "密钥")}>复制密钥</button>
            <label>完整地址</label>
            <code className="secret" tabIndex={0}>{reveal.url}</code>
            <button type="button" className="btn" aria-label="复制完整地址" onClick={() => void copyReveal(reveal.url, "地址")}>复制地址</button>
            {revealMsg ? <div className="tiny muted" role="status" style={{ marginTop: 8 }}>{revealMsg}</div> : null}
            <div className="formacts">
              <button type="button" className="btn primary" aria-label="我已保存密钥，关闭" onClick={() => { setReveal(null); setRevealMsg(""); }}>我已保存</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
