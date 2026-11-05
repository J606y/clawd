import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { createMcp, savedNotice, type McpCreateInput, type WriteFacts } from "@/lib/admin-api";
import { queryKeys, useMcpServers, useRemoveMcp, useSetMcpEnabled, useTestMcp } from "@/lib/queries";
import type { McpServer } from "@/lib/sidecar";
import { Empty, errText, Head, LoadError, Loading, Switch } from "./bits";

const CONFIRM_MS = 3000;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const ENV_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SECRET_IN_TEXT = /(?:^|[\s;])[A-Za-z_]*(?:api[_-]?key|token|secret|password|passwd|credential)[A-Za-z0-9_]*\s*=\s*(?!\$\{)[^\s;]+|(?:--?(?:api[-_]?key|token|secret|password|bearer))\s*[=\s]\s*(?!\$\{)\S+|Bearer\s+\S|sk-[A-Za-z0-9]|ghp_|github_pat_|xox[baprs]-|AKIA[0-9A-Z]{16}|-----BEGIN /i;
const URL_SECRET = /:\/\/[^/\s]*:[^/\s]*@|[?&](?:api[_-]?key|token|secret|password|authorization)=(?!\$\{)[^&\s]+/i;

interface TestResult {
  ok: boolean;
  output: string;
}

interface EnvRow {
  key: string;
  value: string;
}

interface Draft {
  name: string;
  transport: "stdio" | "http";
  command: string;
  args: string;
  url: string;
  enabled: boolean;
  env: EnvRow[];
}

interface CreateDraft {
  body: McpCreateInput;
  envKeys: string[];
  envValues: string[];
}

function emptyDraft(): Draft {
  return { name: "", transport: "stdio", command: "", args: "", url: "", enabled: true, env: [{ key: "", value: "" }] };
}

const SECRET_INPUT_STYLE = {
  width: "100%",
  font: "inherit",
  fontSize: 16,
  color: "var(--text)",
  background: "var(--bg)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-m)",
  padding: "8px 12px",
  outline: "none",
  boxSizing: "border-box" as const,
};

export function McpTab() {
  const servers = useMcpServers();
  const setEnabled = useSetMcpEnabled();
  const remove = useRemoveMcp();
  const test = useTestMcp();
  const client = useQueryClient();
  const busyRef = useRef("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [errName, setErrName] = useState("");
  const [errMsg, setErrMsg] = useState("");
  const [confirming, setConfirming] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [results, setResults] = useState<Record<string, TestResult>>({});
  const timer = useRef<number | undefined>(undefined);

  function clearConfirm() {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    setConfirming("");
  }

  useEffect(() => () => window.clearTimeout(timer.current), []);

  function begin(key: string): boolean {
    if (busyRef.current) return false;
    busyRef.current = key;
    setBusy(key);
    setErrName("");
    setErrMsg("");
    setNotice("");
    return true;
  }

  function end() {
    busyRef.current = "";
    setBusy("");
  }

  async function write(action: string, name: string, fn: () => Promise<{ note?: string }>, okText: string): Promise<boolean> {
    if (!begin(`${action}:${name}`)) return false;
    let ok = false;
    try {
      const result = await fn();
      setNotice(result.note || okText);
      ok = true;
    } catch (error) {
      setErrName(name);
      setErrMsg(errText(error, "操作失败"));
    }
    if (ok) {
      const before = client.getQueryState(queryKeys.mcp)?.dataUpdatedAt ?? 0;
      await client.refetchQueries({ queryKey: queryKeys.mcp });
      const state = client.getQueryState(queryKeys.mcp);
      if (state?.error && state.dataUpdatedAt === before) {
        setErrName(name);
        setErrMsg(`操作已完成，但刷新失败：${errText(state.error, "未知错误")}`);
      }
    }
    end();
    return ok;
  }

  async function runTest(server: McpServer) {
    if (!begin(`test:${server.name}`)) return;
    try {
      const result = await test.mutateAsync(server.name);
      setResults((current) => ({ ...current, [server.name]: { ok: result.ok, output: result.output } }));
    } catch (error) {
      setErrName(server.name);
      setErrMsg(errText(error, "测试失败"));
    } finally {
      end();
    }
  }

  function requestRemove(server: McpServer) {
    if (busyRef.current) return;
    if (confirming !== server.name) {
      clearConfirm();
      setConfirming(server.name);
      timer.current = window.setTimeout(() => clearConfirm(), CONFIRM_MS);
      return;
    }
    clearConfirm();
    void write("remove", server.name, () => remove.mutateAsync(server.name), `「${server.name}」已删除`).then((ok) => {
      if (!ok) return;
      setResults((current) => {
        const next = { ...current };
        delete next[server.name];
        return next;
      });
    });
  }

  const list = servers.data;
  return (
    <div className="mcp-root">
      <Head title="MCP 服务" />
      {list?.length ? <p className="sub">共 {list.length} 个。启停与删除在新会话或网关重启后生效。</p> : null}
      {notice ? <div className="notice ok" role="status">{notice}</div> : null}
      {servers.isError ? (
        <LoadError message={errText(servers.error, "加载失败")} onRetry={() => void servers.refetch()} />
      ) : !list ? (
        <Loading />
      ) : list.length ? (
        list.map((server) => (
          <McpCard
            key={server.name}
            server={server}
            busy={busy.length > 0}
            testing={busy === `test:${server.name}`}
            removing={busy === `remove:${server.name}`}
            confirming={confirming === server.name}
            open={expanded.has(server.name)}
            result={results[server.name]}
            error={errName === server.name ? errMsg : ""}
            onToggle={() => {
              const next = !server.enabled;
              void write("toggle", server.name, () => setEnabled.mutateAsync({ name: server.name, enabled: next }), `「${server.name}」已${next ? "启用" : "停用"}，新会话或网关重启后生效`);
            }}
            onTest={() => void runTest(server)}
            onRemove={() => requestRemove(server)}
            onExpand={() => setExpanded((current) => {
              const next = new Set(current);
              if (!next.delete(server.name)) next.add(server.name);
              return next;
            })}
          />
        ))
      ) : (
        <Empty>还没有配置 MCP 服务。用下方表单新增。</Empty>
      )}
      <CreateMcpForm
        busy={busy.length > 0}
        existingNames={list?.map((server) => server.name) ?? []}
        begin={begin}
        end={end}
        onNotice={setNotice}
      />
    </div>
  );
}

function CreateMcpForm({
  busy,
  existingNames,
  begin,
  end,
  onNotice,
}: {
  busy: boolean;
  existingNames: string[];
  begin: (key: string) => boolean;
  end: () => void;
  onNotice: (text: string) => void;
}) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [formError, setFormError] = useState("");

  function update(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function updateEnv(index: number, patch: Partial<EnvRow>) {
    setDraft((current) => ({
      ...current,
      env: current.env.map((row, rowIndex) => rowIndex === index ? { ...row, ...patch } : row),
    }));
  }

  function buildBody(): CreateDraft | string {
    const name = draft.name.trim();
    if (!NAME_RE.test(name)) return "名称只能包含字母、数字、下划线和连字符，且不能为空";
    if (existingNames.includes(name)) return "已存在同名服务";
    const body: McpCreateInput = { name, transport: draft.transport, enabled: draft.enabled };
    const envValues: string[] = [];
    if (draft.transport === "stdio") {
      const command = draft.command.trim();
      if (!command) return "stdio 需要填写可执行文件";
      const args = draft.args.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
      const visible = [command, ...args].join("\n");
      if (SECRET_IN_TEXT.test(visible)) return "命令或参数里不能写密钥。密钥只能写入下方环境变量，且不会回显。";
      body.command = command;
      if (args.length) body.args = args;
      const env: Record<string, string> = {};
      for (const row of draft.env) {
        const key = row.key.trim();
        if (!key && !row.value) continue;
        if (!ENV_NAME_RE.test(key)) return "环境变量名不合法";
        if (key in env) return "环境变量名重复";
        env[key] = row.value;
        if (row.value) envValues.push(row.value);
      }
      if (Object.keys(env).length) body.env = env;
      return { body, envKeys: Object.keys(env), envValues };
    }
    const url = draft.url.trim();
    if (!url) return "HTTP 需要填写地址";
    if (URL_SECRET.test(url) || SECRET_IN_TEXT.test(url)) return "地址里不能写密钥。";
    body.url = url;
    return { body, envKeys: [], envValues };
  }

  function responseLeaks(result: WriteFacts, values: string[]): boolean {
    const record = result as WriteFacts & { server?: { env?: unknown } };
    if (record.server?.env && typeof record.server.env === "object") return true;
    let blob = "";
    try {
      blob = JSON.stringify(result);
    } catch {
      return true;
    }
    return values.some((value) => value.length >= 8 && blob.includes(value));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const built = buildBody();
    if (typeof built === "string") {
      setFormError(built);
      return;
    }
    const snapshot = draft;
    if (!begin("create")) return;
    setFormError("");
    let result: WriteFacts;
    try {
      result = await createMcp(built.body);
    } catch (error) {
      setFormError(errText(error, "保存失败"));
      end();
      return;
    }
    if (responseLeaks(result, built.envValues)) {
      setFormError("服务端回读包含不应展示的值，表单未清空。");
      end();
      return;
    }
    if (result.saved !== true || result.ok !== true || result.applied !== false) {
      setFormError(result.applied === true || result.hotLoaded === true || result.effectiveNow === true
        ? "服务端报告已在本进程生效，与约定不符。表单未清空。"
        : "服务端没有确认已保存。表单未清空。");
      end();
      return;
    }
    const before = client.getQueryState(queryKeys.mcp)?.dataUpdatedAt ?? 0;
    await client.refetchQueries({ queryKey: queryKeys.mcp });
    const state = client.getQueryState(queryKeys.mcp);
    const rows = Array.isArray(state?.data) ? state.data as McpServer[] : null;
    const loaded = rows?.find((item) => item.name === built.body.name);
    if (!loaded) {
      const refresh = state?.error && state.dataUpdatedAt === before ? errText(state.error, "未知错误") : "列表里还没有这项";
      setFormError(`保存响应已返回，但另行加载未确认：${refresh}。表单未清空。`);
      end();
      return;
    }
    if (built.envKeys.some((key) => !loaded.envKeys.includes(key))) {
      setFormError("已保存，但另行加载没有确认环境变量名。表单未清空。");
      end();
      return;
    }
    onNotice(`${savedNotice(result)} 列表已另行加载，不是用保存响应填的。`);
    setDraft((current) => current === snapshot ? emptyDraft() : current);
    end();
  }

  const http = draft.transport === "http";
  return (
    <form className="card server" autoComplete="off" noValidate onSubmit={(event) => void submit(event)}>
      <div className="top">
        <h3>新增服务</h3>
        <span className="transport">{http ? "HTTP" : "stdio"}</span>
      </div>
      <div>
        <label>
          名称
          <input value={draft.name} autoComplete="off" aria-label="MCP 服务名称" onChange={(event) => update({ name: event.target.value })} />
        </label>
        <div className="acts">
          <button type="button" className="btn" aria-pressed={draft.transport === "stdio"} onClick={() => update({ transport: "stdio" })}>stdio</button>
          <button type="button" className="btn" aria-pressed={draft.transport === "http"} onClick={() => update({ transport: "http" })}>HTTP</button>
          <span className="hint">不提供目录安装。保存只写配置，不表示本进程已加载。</span>
        </div>
        {http ? (
          <label>
            地址
            <input value={draft.url} autoComplete="off" aria-label="MCP HTTP 地址" placeholder="https://example.com/mcp" onChange={(event) => update({ url: event.target.value })} />
          </label>
        ) : (
          <>
            <label>
              命令
              <input value={draft.command} autoComplete="off" aria-label="MCP 可执行文件" placeholder="npx" onChange={(event) => update({ command: event.target.value })} />
            </label>
            <label>
              参数
              <textarea value={draft.args} aria-label="MCP 命令参数" placeholder={"一行一个参数"} onChange={(event) => update({ args: event.target.value })} />
            </label>
            <div className="line">环境变量<span className="muted">（只写不回显，刷新后只显示名称）</span></div>
            {draft.env.map((row, index) => (
              <div className="acts" key={index}>
                <input value={row.key} autoComplete="off" aria-label={`环境变量名 ${index + 1}`} placeholder="名称" onChange={(event) => updateEnv(index, { key: event.target.value })} />
                <input
                  type="password"
                  value={row.value}
                  autoComplete="new-password"
                  spellCheck={false}
                  aria-label={`环境变量值 ${index + 1}，不会回显`}
                  placeholder="值不会回显"
                  style={SECRET_INPUT_STYLE}
                  onChange={(event) => updateEnv(index, { value: event.target.value })}
                />
              </div>
            ))}
            <div className="acts">
              <button type="button" className="btn" onClick={() => update({ env: [...draft.env, { key: "", value: "" }] })}>添加环境变量</button>
            </div>
          </>
        )}
        <div className="acts">
          <span className="hint">启用</span>
          <Switch checked={draft.enabled} disabled={busy} label="新增服务默认启用" onClick={() => update({ enabled: !draft.enabled })} />
        </div>
        <div className="acts">
          <button type="submit" className="btn" disabled={busy}>{busy ? "请等待当前操作结束" : "保存配置"}</button>
          <span className="hint">保存和列表加载是两次请求。applied 为 false 时不能当成已生效。</span>
        </div>
      </div>
      {formError ? <div className="msg error" role="alert">{formError}</div> : null}
    </form>
  );
}

function McpCard({
  server,
  busy,
  testing,
  removing,
  confirming,
  open,
  result,
  error,
  onToggle,
  onTest,
  onRemove,
  onExpand,
}: {
  server: McpServer;
  busy: boolean;
  testing: boolean;
  removing: boolean;
  confirming: boolean;
  open: boolean;
  result: TestResult | undefined;
  error: string;
  onToggle: () => void;
  onTest: () => void;
  onRemove: () => void;
  onExpand: () => void;
}) {
  const command = [server.command, ...server.args].filter((item): item is string => !!item).join(" ");
  const target = server.transport === "http" ? server.url || "—" : command || "—";
  const allow = server.toolAllowlist;
  return (
    <div className="card server" data-off={server.enabled ? undefined : ""}>
      <div className="top">
        <h3 title={server.name}>{server.name}</h3>
        <span className="transport">{server.transport === "http" ? "HTTP" : "stdio"}</span>
        <Switch checked={server.enabled} disabled={busy || !server.editable} label={`${server.enabled ? "停用" : "启用"} ${server.name}`} onClick={onToggle} />
      </div>
      <div className="body">
        <span className="mono" title={target}>{target}</span>
        {server.auth ? <div className="line">认证：{server.auth}</div> : null}
        {allow ? (
          <div className="tools">
            <span className="muted">仅启用 {allow.length} 个工具</span>
            {allow.length ? (
              <>
                {" · "}
                <button type="button" className="link" aria-expanded={open} aria-label={`${open ? "收起" : "展开"} ${server.name} 的工具白名单`} onClick={onExpand}>
                  {open ? "收起" : "查看"}
                </button>
              </>
            ) : null}
            {open ? <div className="toolchips">{allow.map((tool) => <code key={tool}>{tool}</code>)}</div> : null}
          </div>
        ) : null}
        <SecretLine label="环境变量" keys={server.envKeys} />
        <SecretLine label="请求头" keys={server.headerKeys} />
      </div>
      {server.editable ? (
        <div className="acts">
          <button type="button" className="btn" aria-label={`测试连接 ${server.name}`} disabled={busy} onClick={onTest}>{testing ? "测试中…" : "测试连接"}</button>
          {confirming ? (
            <button type="button" className="btn danger" aria-label={`确认删除 ${server.name}`} disabled={busy} onClick={onRemove}>确认删除？</button>
          ) : (
            <button type="button" className="btn danger" aria-label={`删除 ${server.name}`} disabled={busy} onClick={onRemove}>{removing ? "删除中…" : "删除"}</button>
          )}
          <span className="hint">测试会真实拉起或连接该服务。</span>
        </div>
      ) : <div className="readonly">由插件提供，只读。</div>}
      {result ? (
        <div className="result">
          <span className={`pill badge ${result.ok ? "on" : "bad"}`}>{result.ok ? "连接成功" : "连接失败"}</span>
          <pre aria-label={`${server.name} 的测试输出`} tabIndex={0}>{result.output || "（无输出）"}</pre>
        </div>
      ) : null}
      {error ? <div className="msg error" role="alert">{error}</div> : null}
    </div>
  );
}

function SecretLine({ label, keys }: { label: string; keys: string[] }) {
  if (!keys.length) return null;
  return <div className="line">{label}：<code>{keys.join("、")}</code><span className="muted">（值已隐藏）</span></div>;
}
