import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api";
import { queryKeys, useCronModel, useModelsAdmin, useSetCronModel, useSetExcludedProviders, useSetProviderDiscover, useSetProviderModels } from "@/lib/queries";
import type { CronModelView, ModelsView, ProviderInfo } from "@/lib/sidecar";
import { Empty, errText, Head, LoadError, Loading, Switch } from "./bits";

const MAX_MODELS = 100;
const MAX_NAME = 128;
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._:/-]*$/;
const ENDPOINT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const SIDECAR = new URL("panel-core/", document.baseURI);
const SAVED_NOTE = "端点已保存。当前进程未热加载，新会话才会读取。";

interface EndpointWrite {
  ok?: boolean;
  saved?: boolean;
  hasKey?: boolean;
  hotLoaded?: boolean;
  applied?: boolean;
  effectiveNow?: boolean;
  loadedNow?: boolean;
  processEnvChanged?: boolean;
}

function keyStatus(provider: ProviderInfo): string {
  const value = (provider as ProviderInfo & { hasKey?: unknown }).hasKey;
  if (value === true) return "已设置";
  if (value === false) return "未设置";
  return "状态未知";
}

function createCustomEndpoint(body: { id: string; baseUrl: string; models: string[]; apiKey?: string }): Promise<EndpointWrite> {
  return request<EndpointWrite>("models/custom-endpoints", { method: "POST", body: JSON.stringify(body) }, 90000, SIDECAR);
}

function parseModels(text: string): { list: string[]; error: string } {
  const seen = new Set<string>();
  const list: string[] = [];
  for (const raw of text.split("\n")) {
    const name = raw.trim();
    if (!name || seen.has(name)) continue;
    if (name.length > MAX_NAME) return { list, error: `模型名不能超过 ${MAX_NAME} 个字符：${name.slice(0, 24)}…` };
    if (!NAME_RE.test(name)) return { list, error: `模型名含不支持的字符：${name}（仅限字母、数字与 . _ : / -，且以字母或数字开头）` };
    seen.add(name);
    list.push(name);
  }
  if (!list.length) return { list, error: "至少保留一个模型" };
  if (list.length > MAX_MODELS) return { list, error: `最多 ${MAX_MODELS} 个模型，当前 ${list.length} 个` };
  return { list, error: "" };
}

export function ModelsTab() {
  const models = useModelsAdmin();
  const cron = useCronModel();
  const setCron = useSetCronModel();
  const setModels = useSetProviderModels();
  const setDiscover = useSetProviderDiscover();
  const setExcluded = useSetExcludedProviders();
  const client = useQueryClient();
  const busyRef = useRef("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [errKey, setErrKey] = useState("");
  const [errMsg, setErrMsg] = useState("");
  const [editing, setEditing] = useState("");
  const [editText, setEditText] = useState("");
  const [excludeInput, setExcludeInput] = useState("");
  const [cronSaving, setCronSaving] = useState<string | null>(null);
  const [endpointId, setEndpointId] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [endpointKey, setEndpointKey] = useState("");
  const [endpointModels, setEndpointModels] = useState("");

  function begin(key: string): boolean {
    if (busyRef.current) return false;
    busyRef.current = key;
    setBusy(key);
    setErrKey("");
    setErrMsg("");
    setNotice("");
    return true;
  }

  function end() {
    busyRef.current = "";
    setBusy("");
  }

  async function refreshBoth(): Promise<string> {
    const beforeModels = client.getQueryState(queryKeys.modelsAdmin)?.dataUpdatedAt ?? 0;
    const beforeCron = client.getQueryState(queryKeys.cronModel)?.dataUpdatedAt ?? 0;
    await Promise.all([
      client.refetchQueries({ queryKey: queryKeys.modelsAdmin }),
      client.refetchQueries({ queryKey: queryKeys.cronModel }),
    ]);
    const modelsState = client.getQueryState(queryKeys.modelsAdmin);
    const cronState = client.getQueryState(queryKeys.cronModel);
    if (modelsState?.error && modelsState.dataUpdatedAt === beforeModels) return errText(modelsState.error, "未知错误");
    if (cronState?.error && cronState.dataUpdatedAt === beforeCron) return errText(cronState.error, "未知错误");
    return "";
  }

  async function write(key: string, fn: () => Promise<{ note?: string }>, okText: string): Promise<boolean> {
    if (!begin(key)) return false;
    let ok = false;
    try {
      const result = await fn();
      setNotice(result.note || okText);
      ok = true;
    } catch (error) {
      setErrKey(key);
      setErrMsg(errText(error, "操作失败"));
    }
    if (ok) {
      const failed = await refreshBoth();
      if (failed) {
        setErrKey(key);
        setErrMsg(`已保存，但刷新失败：${failed}`);
      }
    }
    end();
    return ok;
  }

  async function useCron(pick: string) {
    const [provider, ...rest] = pick.split("|");
    const model = rest.join("|");
    setCronSaving(pick);
    try {
      await write("cron", () => (pick ? setCron.mutateAsync({ model, provider }) : setCron.mutateAsync({ model: "" })), pick ? `定时任务改用 ${model}，下一次触发生效` : "定时任务改为跟随主模型");
    } finally {
      setCronSaving(null);
    }
  }

  const view = models.data;
  return (
    <div className="models-root">
      <Head title="模型" />
      {notice ? <div className="notice ok" role="status">{notice}</div> : null}
      {models.isError ? (
        <LoadError message={errText(models.error, "加载失败")} onRetry={() => void models.refetch()} />
      ) : !view ? (
        <Loading />
      ) : (
        <>
          <CronBlock
            view={view}
            cron={cron.data}
            cronError={cron.isError ? errText(cron.error, "定时任务设置加载失败") : ""}
            busy={busy.length > 0}
            cronSaving={cronSaving}
            err={errKey === "cron" ? errMsg : ""}
            onRetry={() => void cron.refetch()}
            onUse={(pick) => void useCron(pick)}
          />
          <div className="section">提供方</div>
          {view.providers.length ? view.providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              view={view}
              busy={busy.length > 0}
              discoverBusy={busy === `${provider.id}:discover`}
              savingModels={busy === `${provider.id}:models`}
              editing={editing === provider.id}
              editText={editText}
              errDiscover={errKey === `${provider.id}:discover` ? errMsg : ""}
              errModels={errKey === `${provider.id}:models` ? errMsg : ""}
              onDiscover={() => void write(`${provider.id}:discover`, () => setDiscover.mutateAsync({ provider: provider.id, discover: !provider.discoverModels }), `自动发现模型已${!provider.discoverModels ? "开启" : "关闭"}`)}
              onEdit={() => { setEditing(provider.id); setEditText(provider.models.join("\n")); setErrKey(""); setErrMsg(""); }}
              onText={setEditText}
              onCancel={() => { setEditing(""); setErrMsg(""); }}
              onSave={() => void saveModels(provider)}
            />
          )) : <Empty>没有可用的提供方</Empty>}
          <EndpointForm
            id={endpointId}
            url={endpointUrl}
            secret={endpointKey}
            models={endpointModels}
            busy={busy.length > 0}
            saving={busy === "endpoint"}
            error={errKey === "endpoint" ? errMsg : ""}
            onId={setEndpointId}
            onUrl={setEndpointUrl}
            onSecret={setEndpointKey}
            onModels={setEndpointModels}
            onSubmit={(event) => void saveEndpoint(event)}
          />
          <div className="section">候选过滤</div>
          <ExcludedBlock
            view={view}
            busy={busy.length > 0}
            pending={busy === "exclude"}
            input={excludeInput}
            error={errKey === "exclude" ? errMsg : ""}
            onInput={setExcludeInput}
            onRemove={(name) => void write("exclude", () => setExcluded.mutateAsync(view.excludedProviders.filter((item) => item !== name)), `已取消排除 ${name}`)}
            onAdd={(event) => void addExcluded(event)}
          />
          <div className="foot">已保存的密钥不会回填。新建端点只写入密钥，保存后只显示是否已设置。</div>
        </>
      )}
    </div>
  );

  async function saveEndpoint(event: FormEvent) {
    event.preventDefault();
    const id = endpointId.trim();
    const baseUrl = endpointUrl.trim();
    const secret = endpointKey.trim();
    const parsed = parseModels(endpointModels);
    if (!ENDPOINT_ID_RE.test(id)) {
      setErrKey("endpoint");
      setErrMsg("端点 ID 只允许字母、数字、下划线和连字符，且不超过 64 个字符");
      return;
    }
    if (!/^https?:\/\//.test(baseUrl) || /\s/.test(baseUrl)) {
      setErrKey("endpoint");
      setErrMsg("Base URL 必须是 http 或 https 地址");
      return;
    }
    if (parsed.error) {
      setErrKey("endpoint");
      setErrMsg(parsed.error);
      return;
    }
    const ok = await write("endpoint", async () => {
      const result = await createCustomEndpoint({ id, baseUrl, models: parsed.list, ...(secret ? { apiKey: secret } : {}) });
      setEndpointKey("");
      if (result.hotLoaded === true || result.applied === true || result.effectiveNow === true || result.loadedNow === true || result.processEnvChanged === true) {
        return { note: "已保存。服务端标记了热加载或已生效，本页不把它当作当前进程已生效。" };
      }
      return { note: SAVED_NOTE };
    }, SAVED_NOTE);
    if (ok) {
      setEndpointId("");
      setEndpointUrl("");
      setEndpointModels("");
      setEndpointKey("");
    }
  }

  async function saveModels(provider: ProviderInfo) {
    const parsed = parseModels(editText);
    if (parsed.error) {
      setErrKey(`${provider.id}:models`);
      setErrMsg(parsed.error);
      return;
    }
    const ok = await write(`${provider.id}:models`, () => setModels.mutateAsync({ provider: provider.id, models: parsed.list }), "模型列表已保存");
    if (ok) setEditing("");
  }

  async function addExcluded(event: FormEvent) {
    event.preventDefault();
    const value = excludeInput.trim();
    const current = view?.excludedProviders ?? [];
    if (!value) return;
    if (value.length > MAX_NAME || !NAME_RE.test(value)) {
      setErrKey("exclude");
      setErrMsg("提供方 ID 仅限字母、数字与 . _ : / -，且以字母或数字开头");
      return;
    }
    if (current.includes(value)) {
      setErrKey("exclude");
      setErrMsg(`「${value}」已在排除列表中`);
      return;
    }
    if (await write("exclude", () => setExcluded.mutateAsync([...current, value]), `已排除 ${value}`)) setExcludeInput("");
  }
}

function isDefault(view: ModelsView, provider: ProviderInfo, model: string): boolean {
  const current = view.current;
  if (current.default !== model) return false;
  return !current.provider || current.provider === provider.id || current.provider === provider.name;
}

function CronBlock({
  view,
  cron,
  cronError,
  busy,
  cronSaving,
  err,
  onRetry,
  onUse,
}: {
  view: ModelsView;
  cron: CronModelView | undefined;
  cronError: string;
  busy: boolean;
  cronSaving: string | null;
  err: string;
  onRetry: () => void;
  onUse: (pick: string) => void;
}) {
  if (!cron) {
    return cronError ? (
      <div className="card block">
        <h2>定时任务模型</h2>
        <div className="msg error" role="alert">{cronError} <button type="button" className="link" onClick={onRetry}>重试</button></div>
      </div>
    ) : null;
  }
  const saved = cron.cronModel && cron.cronProvider ? `${cron.cronProvider}|${cron.cronModel}` : "";
  const groups = view.providers.filter((provider) => provider.editable && provider.models.length);
  const stale = saved && !groups.some((provider) => provider.models.some((model) => `${provider.id}|${model}` === saved));
  return (
    <div className="card block">
      <h2>定时任务模型</h2>
      <div className="muted tiny">没有单独固定模型的定时任务都用它，下一次触发生效；固定了模型的任务和对话不受影响。</div>
      <div className="chips">
        <CronChip value="" current={saved} label="主模型" busy={busy} saving={cronSaving === ""} onUse={onUse}>
          <span className="label">跟随主模型</span>
          {cron.main.model ? <code title={cron.main.model}>{cron.main.model}</code> : null}
        </CronChip>
      </div>
      {groups.map((provider) => (
        <div key={provider.id}>
          <div className="group">{provider.name}</div>
          <div className="chips">
            {provider.models.map((model) => (
              <CronChip key={model} value={`${provider.id}|${model}`} current={saved} label={model} busy={busy} saving={cronSaving === `${provider.id}|${model}`} onUse={onUse}>
                <code title={model}>{model}</code>
              </CronChip>
            ))}
          </div>
        </div>
      ))}
      {stale ? (
        <>
          <div className="group">不在候选列表中</div>
          <div className="chips">
            <CronChip value={saved} current={saved} label={cron.cronModel ?? ""} stale busy={busy} saving={false} onUse={onUse}>
              <code title={cron.cronModel ?? ""}>{cron.cronProvider} · {cron.cronModel}</code>
            </CronChip>
          </div>
        </>
      ) : null}
      {err ? <div className="msg error" role="alert">{err}</div> : null}
      {cron.jobs.length ? (
        <>
          <div className="group">定时任务 · {cron.jobs.length}</div>
          <ul className="jobs">
            {cron.jobs.map((job) => (
              <li key={job.id} className={job.enabled ? "" : "paused"}>
                <span className="name" title={job.name}>{job.name}</span>
                {job.enabled ? null : <span className="pill off">已暂停</span>}
                {job.model ? <span className="uses">固定 <code title={job.model}>{job.model}</code></span> : null}
              </li>
            ))}
          </ul>
        </>
      ) : <div className="muted tiny" style={{ marginTop: 14 }}>暂无定时任务。</div>}
    </div>
  );
}

function CronChip({
  value,
  current,
  label,
  stale,
  busy,
  saving,
  onUse,
  children,
}: {
  value: string;
  current: string;
  label: string;
  stale?: boolean;
  busy: boolean;
  saving: boolean;
  onUse: (pick: string) => void;
  children: React.ReactNode;
}) {
  const on = value === current;
  return (
    <span className={`chip${stale ? " stale" : ""}`} data-default={on ? "" : undefined}>
      {children}
      {on ? <span className="tag">当前</span> : (
        <button type="button" aria-label={`定时任务改用 ${label}`} disabled={busy} onClick={() => onUse(value)}>
          {saving ? "…" : "使用"}
        </button>
      )}
    </span>
  );
}

function ProviderCard({
  provider,
  view,
  busy,
  discoverBusy,
  savingModels,
  editing,
  editText,
  errDiscover,
  errModels,
  onDiscover,
  onEdit,
  onText,
  onCancel,
  onSave,
}: {
  provider: ProviderInfo;
  view: ModelsView;
  busy: boolean;
  discoverBusy: boolean;
  savingModels: boolean;
  editing: boolean;
  editText: string;
  errDiscover: string;
  errModels: string;
  onDiscover: () => void;
  onEdit: () => void;
  onText: (value: string) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const parsed = parseModels(editText);
  const current = view.current.default;
  const removedDefault = !!current && provider.models.includes(current) && isDefault(view, provider, current) && !parsed.list.includes(current);
  return (
    <div className="card provider">
      <div className="top">
        <div className="grow">
          <h3 title={provider.name}>{provider.name}</h3>
          {provider.name !== provider.id ? <div className="muted tiny mono">{provider.id}</div> : null}
        </div>
        {provider.editable ? (
          <div className="toggle">
            <span id={`dl-${provider.id}`}>{discoverBusy ? "切换中…" : "自动发现模型"}</span>
            <Switch checked={provider.discoverModels} disabled={busy} labelledBy={`dl-${provider.id}`} onClick={onDiscover} />
          </div>
        ) : null}
      </div>
      <div className="meta">
        <span>密钥：{keyStatus(provider)}</span>
        <span className="mono">{provider.baseUrl || "—"}</span>
        <span>api_mode：<span className="mono">{provider.apiMode || "—"}</span></span>
        {provider.editable ? null : <span>自动发现：{provider.discoverModels ? "开" : "关"}</span>}
      </div>
      {provider.editable ? null : <div className="readonly">名称含特殊字符，请在服务器上修改。</div>}
      {errDiscover ? <div className="msg error" role="alert">{errDiscover}</div> : null}
      {provider.models.length ? (
        <div className="chips">
          {provider.models.map((model) => <span key={model} className="chip"><code title={model}>{model}</code></span>)}
        </div>
      ) : <div className="muted tiny" style={{ marginTop: 12 }}>没有已配置的模型。</div>}
      {provider.editable ? editing ? (
        <div>
          <label htmlFor={`ed-${provider.id}`}>模型列表（一行一个）</label>
          <textarea id={`ed-${provider.id}`} spellCheck={false} disabled={savingModels} value={editText} onChange={(event) => onText(event.target.value)} />
          <div className="muted tiny" style={{ marginTop: 6 }}>最多 {MAX_MODELS} 个；每个不超过 {MAX_NAME} 个字符，仅限字母、数字与 . _ : / -。当前 {parsed.list.length} 个。</div>
          {removedDefault ? <div className="msg warn" role="status">「{current}」仍是服务器主模型。保存后它将不在此提供方列表中，跟随主模型的定时任务可能无法使用。</div> : null}
          <div className="acts">
            <button type="button" className="btn primary" aria-label={`保存 ${provider.name} 的模型列表`} disabled={busy} onClick={onSave}>{savingModels ? "保存中…" : "保存"}</button>
            <button type="button" className="btn" aria-label="取消编辑" disabled={busy} onClick={onCancel}>取消</button>
          </div>
          {errModels ? <div className="msg error" role="alert">{errModels}</div> : null}
        </div>
      ) : (
        <div className="acts">
          <button type="button" className="btn" aria-label={`编辑 ${provider.name} 的模型列表`} disabled={busy} onClick={onEdit}>编辑模型列表</button>
        </div>
      ) : null}
    </div>
  );
}

function EndpointForm({
  id,
  url,
  secret,
  models,
  busy,
  saving,
  error,
  onId,
  onUrl,
  onSecret,
  onModels,
  onSubmit,
}: {
  id: string;
  url: string;
  secret: string;
  models: string;
  busy: boolean;
  saving: boolean;
  error: string;
  onId: (value: string) => void;
  onUrl: (value: string) => void;
  onSecret: (value: string) => void;
  onModels: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form className="card block" autoComplete="off" onSubmit={onSubmit}>
      <h2>自定义端点</h2>
      <div className="muted tiny">写入当前 profile 的 providers。不设为默认模型，也不热加载当前进程。模型名按原样保存，不去掉 Argon 或 Flash 后缀。</div>
      <label htmlFor="ep-id">端点 ID</label>
      <input id="ep-id" type="text" maxLength={64} spellCheck={false} disabled={busy} value={id} onChange={(event) => onId(event.target.value)} />
      <label htmlFor="ep-url">Base URL</label>
      <input id="ep-url" type="text" inputMode="url" spellCheck={false} disabled={busy} value={url} onChange={(event) => onUrl(event.target.value)} />
      <label htmlFor="ep-key">API 密钥</label>
      <input
        id="ep-key"
        type="password"
        name="clawd-new-endpoint-key"
        autoComplete="new-password"
        spellCheck={false}
        disabled={busy}
        value={secret}
        onChange={(event) => onSecret(event.target.value)}
        style={{ width: "100%", font: "inherit", fontSize: 16, color: "var(--text)", background: "var(--bg)", border: "1px solid var(--border)", borderRadius: "var(--radius-m)", padding: "8px 12px", boxSizing: "border-box" }}
      />
      <div className="muted tiny">可选。只写入，不会从服务器回填。留空表示此端点不带密钥。</div>
      <label htmlFor="ep-models">模型列表（一行一个）</label>
      <textarea id="ep-models" spellCheck={false} disabled={saving} value={models} onChange={(event) => onModels(event.target.value)} />
      <div className="acts">
        <button className="btn primary" type="submit" disabled={busy || !id.trim() || !url.trim() || !models.trim()}>{saving ? "保存中…" : "保存端点"}</button>
      </div>
      {error ? <div className="msg error" role="alert">{error}</div> : null}
    </form>
  );
}

function ExcludedBlock({
  view,
  busy,
  pending,
  input,
  error,
  onInput,
  onRemove,
  onAdd,
}: {
  view: ModelsView;
  busy: boolean;
  pending: boolean;
  input: string;
  error: string;
  onInput: (value: string) => void;
  onRemove: (name: string) => void;
  onAdd: (event: FormEvent) => void;
}) {
  return (
    <div className="card block">
      <h2>排除的提供方</h2>
      <div className="muted tiny">这些提供方不会出现在模型候选里。</div>
      {view.excludedProviders.length ? (
        <div className="chips">
          {view.excludedProviders.map((name) => (
            <span key={name} className="chip plain">
              <code title={name}>{name}</code>
              <button type="button" aria-label={`取消排除 ${name}`} disabled={busy} onClick={() => onRemove(name)}>移除</button>
            </span>
          ))}
        </div>
      ) : <div className="muted tiny" style={{ marginTop: 12 }}>暂无排除项。</div>}
      <form className="addrow" onSubmit={onAdd}>
        <input type="text" placeholder="提供方 ID" aria-label="要排除的提供方 ID" maxLength={MAX_NAME} disabled={busy} value={input} onChange={(event) => onInput(event.target.value)} />
        <button className="btn" type="submit" aria-label="添加排除项" disabled={busy || !input.trim()}>{pending ? "处理中…" : "添加"}</button>
      </form>
      {error ? <div className="msg error" role="alert">{error}</div> : null}
    </div>
  );
}
