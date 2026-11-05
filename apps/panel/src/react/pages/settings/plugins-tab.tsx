import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  queryKeys,
  useCreateProfile,
  usePlugins,
  useProfileDetail,
  useProfiles,
} from "@/lib/queries";
import type { PluginInfo, ProfileInfo } from "@/lib/sidecar";
import {
  confirmDeleteProfile,
  getPluginConfig,
  pluginNotice,
  postProfileUse,
  previewDeleteProfile,
  previewReady,
  profileDeleteNotice,
  profileUseNotice,
  setPluginConfig,
  setPluginEnabled,
  type DeletePreview,
  type WriteFacts,
} from "@/lib/admin-api";
import { Empty, errText, Head, Loading, Switch } from "./bits";

const NAME_RE = /^[a-z0-9_-]{1,32}$/;
const DESC_MAX = 200;

type ListedPlugin = PluginInfo & { key?: string };

interface PluginField {
  key: string;
  type: string;
  label?: string;
  description?: string;
  required?: boolean;
  secret?: boolean;
  valueSet?: boolean;
  valueWithheld?: boolean;
  stored?: boolean;
  value?: unknown;
  default?: unknown;
  choices?: string[];
  env?: string;
}

interface PluginConfigView extends WriteFacts {
  name: string;
  key: string;
  fields: PluginField[];
  managed?: boolean;
}

function pluginKey(plugin: ListedPlugin): string {
  return typeof plugin.key === "string" && plugin.key.length > 0 ? plugin.key : plugin.name;
}

function asConfig(value: unknown): PluginConfigView | null {
  if (!value || typeof value !== "object") return null;
  const row = value as PluginConfigView;
  if (typeof row.name !== "string" || typeof row.key !== "string" || !Array.isArray(row.fields)) return null;
  if (!row.fields.every((item) => item && typeof item.key === "string" && typeof item.type === "string")) return null;
  return row;
}

function pluginConfigNotice(result: WriteFacts): string {
  if (result.loadedNow === true || result.effectiveNow === true || result.applied === true || result.hotLoaded === true || result.processEnvChanged === true) {
    return "服务端报告插件配置已在本进程加载或生效，与约定不符，请刷新确认。";
  }
  if (result.saved === true && result.ok !== false && result.loadedNow === false) {
    return result.note || "已保存非秘密配置。本进程没有加载插件。";
  }
  return "服务端没有确认已保存，不能当成已生效。";
}

function fieldText(field: PluginField): string {
  if (field.secret || field.valueWithheld || field.value === undefined || field.value === null) return "";
  if (field.type === "json") return JSON.stringify(field.value, null, 2);
  return String(field.value);
}

function changedFields(view: PluginConfigView, text: Record<string, string>, flags: Record<string, boolean>): Record<string, unknown> | string {
  const out: Record<string, unknown> = {};
  for (const field of view.fields) {
    if (field.secret || field.type === "secret") continue;
    const label = field.label || field.key;
    if (field.type === "boolean") {
      const next = flags[field.key] === true;
      if (next !== (field.value === true)) out[field.key] = next;
      continue;
    }
    const raw = text[field.key] ?? "";
    if (field.valueWithheld && raw === "") continue;
    if (field.type === "number") {
      const trimmed = raw.trim();
      if (!trimmed) {
        if (field.value == null) continue;
        return `${label} 不能清空`;
      }
      if (!/^-?\d+(\.\d+)?$/.test(trimmed)) return `${label} 不是数字`;
      const parsed = trimmed.includes(".") ? Number(trimmed) : Number.parseInt(trimmed, 10);
      if (!Number.isFinite(parsed)) return `${label} 不是数字`;
      if (parsed !== field.value) out[field.key] = parsed;
      continue;
    }
    if (field.type === "json") {
      if (!raw.trim()) {
        if (field.value == null) continue;
        return `${label} 需要 JSON 对象或数组`;
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return `${label} 不是合法 JSON`;
      }
      if (parsed === null || typeof parsed !== "object") return `${label} 必须是对象或数组`;
      if (JSON.stringify(parsed) !== JSON.stringify(field.value ?? null)) out[field.key] = parsed;
      continue;
    }
    if (field.type === "enum") {
      if (!field.choices?.includes(raw)) {
        if (raw === fieldText(field)) continue;
        return `${label} 不在允许的选项中`;
      }
      if (raw !== field.value) out[field.key] = raw;
      continue;
    }
    const previous = typeof field.value === "string" ? field.value : field.value == null ? "" : String(field.value);
    if (raw === previous) continue;
    if (field.required && !raw) return `${label} 为必填`;
    out[field.key] = raw;
  }
  return out;
}

export function PluginsTab() {
  const client = useQueryClient();
  const profiles = useProfiles();
  const plugins = usePlugins();
  const createProfile = useCreateProfile();
  const busyRef = useRef("");
  const configReq = useRef("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null);
  const [query, setQuery] = useState("");
  const [onlyEnabled, setOnlyEnabled] = useState(false);
  const [expanded, setExpanded] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");
  const [preview, setPreview] = useState<(DeletePreview & { receivedAt: number }) | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formClone, setFormClone] = useState("");
  const [formError, setFormError] = useState("");
  const [creating, setCreating] = useState(false);
  const [configFor, setConfigFor] = useState("");
  const [configBusy, setConfigBusy] = useState("");
  const [configView, setConfigView] = useState<PluginConfigView | null>(null);
  const [configError, setConfigError] = useState("");
  const [textDraft, setTextDraft] = useState<Record<string, string>>({});
  const [flagDraft, setFlagDraft] = useState<Record<string, boolean>>({});
  const detail = useProfileDetail(expanded, expanded.length > 0);

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

  const profileRows = profiles.data?.data ?? [];
  const raw = profiles.data && profiles.data.data.length === 0 && profiles.data.raw && profiles.data.raw.trim() ? profiles.data.raw : null;
  const pluginRows = (plugins.data ?? []) as ListedPlugin[];
  const q = query.trim().toLowerCase();
  const filtered = pluginRows.filter((item) => (!onlyEnabled || item.enabled) && (!q || `${item.name} ${item.description} ${pluginKey(item)}`.toLowerCase().includes(q)));

  function validate(): string {
    const name = formName.trim();
    if (!name) return "请填写名称";
    if (name === "default") return "名称不能是 default";
    if (!NAME_RE.test(name)) return "名称只能包含小写字母、数字、下划线、连字符，最长 32 个字符";
    if (profileRows.some((item) => item.name === name)) return "已存在同名配置档";
    if (formDesc.length > DESC_MAX) return `描述最长 ${DESC_MAX} 个字符`;
    return "";
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current || creating) return;
    const problem = validate();
    if (problem) {
      setFormError(problem);
      return;
    }
    const name = formName.trim();
    setFormError("");
    setCreating(true);
    busyRef.current = "create";
    setBusy("create");
    setNotice("");
    try {
      const result = await createProfile.mutateAsync({ name, cloneFrom: formClone || null, description: formDesc.trim() });
      setNotice(result.note ?? `已创建配置档「${name}」`);
      setFormName("");
      setFormDesc("");
      setFormClone("");
      setFormOpen(false);
    } catch (error) {
      setFormError(errText(error, "创建失败"));
    } finally {
      setCreating(false);
      end();
    }
  }

  async function activate(profile: ProfileInfo) {
    const key = `p:${profile.name}`;
    if (!begin(key)) return;
    try {
      const result = await postProfileUse(profile.name);
      setNotice(profileUseNotice(result, profile.name));
      void client.invalidateQueries({ queryKey: queryKeys.profiles });
      void client.invalidateQueries({ queryKey: queryKeys.modelOptions });
      void client.invalidateQueries({ queryKey: queryKeys.jobs });
      void client.invalidateQueries({ queryKey: queryKeys.settings });
    } catch (error) {
      setRowError({ key, message: errText(error, "设置失败") });
    } finally {
      end();
    }
  }

  async function requestPreview(profile: ProfileInfo) {
    const key = `p:${profile.name}`;
    if (!begin(key)) return;
    setPreview(null);
    setConfirmDelete("");
    try {
      const result = await previewDeleteProfile(profile.name);
      const paths = Array.isArray(result.paths) && result.paths.every((item) => typeof item === "string") ? result.paths : null;
      if (result.deleted === true || paths === null) {
        setRowError({ key, message: "路径预览不完整，没有进入确认，也没有删除。" });
        return;
      }
      setPreview({ ...result, paths, receivedAt: Date.now() });
      if (!previewReady(result)) {
        setRowError({ key, message: result.note || "当前不能确认删除。上面的路径只是预览，没有删除。" });
        return;
      }
      setConfirmDelete(profile.name);
    } catch (error) {
      setRowError({ key, message: errText(error, "无法读取将删除的路径") });
    } finally {
      end();
    }
  }

  function previewFresh(ticket: DeletePreview & { receivedAt: number }): boolean {
    return Date.now() - ticket.receivedAt < ticket.expiresInS * 1000;
  }

  async function confirmRemove(profile: ProfileInfo) {
    const key = `p:${profile.name}`;
    const ticket = preview;
    if (!ticket || ticket.name !== profile.name || confirmDelete !== profile.name || !previewReady(ticket) || !ticket.token || !ticket.fingerprint) {
      setRowError({ key, message: "还没有列出当次路径预览，不能确认删除。" });
      return;
    }
    if (!previewFresh(ticket)) {
      setPreview(null);
      setConfirmDelete("");
      setRowError({ key, message: "预览已过期，请重新读取路径。未删除。" });
      return;
    }
    if (!begin(key)) return;
    try {
      const result = await confirmDeleteProfile({
        name: profile.name,
        confirm: profile.name,
        paths: ticket.paths,
        token: ticket.token,
        fingerprint: ticket.fingerprint,
      });
      setConfirmDelete("");
      setPreview(null);
      if (expanded === profile.name && result.deleted === true) setExpanded("");
      setNotice(profileDeleteNotice(result));
      void client.invalidateQueries({ queryKey: queryKeys.profiles });
    } catch (error) {
      setPreview(null);
      setConfirmDelete("");
      setRowError({ key, message: errText(error, "删除失败") });
    } finally {
      end();
    }
  }

  async function togglePlugin(plugin: ListedPlugin) {
    const id = pluginKey(plugin);
    const key = `g:${id}`;
    if (!begin(key)) return;
    const next = !plugin.enabled;
    try {
      const result = await setPluginEnabled(id, next);
      setNotice(pluginNotice(result, plugin.name, next));
      void client.invalidateQueries({ queryKey: queryKeys.plugins });
    } catch (error) {
      setRowError({ key, message: errText(error, "切换失败") });
    } finally {
      end();
    }
  }

  function seed(view: PluginConfigView) {
    const text: Record<string, string> = {};
    const flags: Record<string, boolean> = {};
    for (const field of view.fields) {
      if (field.type === "boolean") flags[field.key] = field.value === true;
      else text[field.key] = fieldText(field);
    }
    setTextDraft(text);
    setFlagDraft(flags);
  }

  async function openConfig(plugin: ListedPlugin) {
    const id = pluginKey(plugin);
    if (configFor === id) {
      configReq.current = "";
      setConfigFor("");
      return;
    }
    configReq.current = id;
    setConfigFor(id);
    setConfigView(null);
    setConfigError("");
    setTextDraft({});
    setFlagDraft({});
    setConfigBusy(id);
    try {
      const result = await getPluginConfig(id);
      if (configReq.current !== id) return;
      const view = asConfig(result);
      if (!view) {
        setConfigError("配置响应不完整，没有显示可编辑字段。");
        return;
      }
      if (view.loadedNow === true || view.applied === true || view.hotLoaded === true || view.effectiveNow === true) {
        setConfigError("服务端报告插件已在本进程生效，与约定不符。下面的字段不能当成已经加载。");
      }
      setConfigView(view);
      seed(view);
    } catch (error) {
      if (configReq.current !== id) return;
      setConfigError(errText(error, "配置读取失败"));
    } finally {
      if (configReq.current === id) setConfigBusy("");
    }
  }

  async function saveConfig(event: FormEvent, plugin: ListedPlugin) {
    event.preventDefault();
    const id = pluginKey(plugin);
    const view = configView;
    if (!view || configFor !== id) {
      setConfigError("还没有读到这份配置，不能保存。");
      return;
    }
    if (view.managed === true) {
      setConfigError("当前安装由包管理器托管，没有保存。");
      return;
    }
    const built = changedFields(view, textDraft, flagDraft);
    if (typeof built === "string") {
      setConfigError(built);
      return;
    }
    if (Object.keys(built).length === 0) {
      setConfigError("没有改动，未保存。");
      return;
    }
    const key = `c:${id}`;
    if (!begin(key)) return;
    setConfigError("");
    try {
      const result = await setPluginConfig(view.key, built);
      setNotice(pluginConfigNotice(result));
      const next = asConfig(result);
      if (next) {
        setConfigView(next);
        seed(next);
      }
    } catch (error) {
      setConfigError(errText(error, "保存失败"));
    } finally {
      end();
    }
  }

  return (
    <div className="plugins-root">
      <Head title="插件与配置档" />
      {notice ? <div className="notice ok" role="status">{notice}</div> : null}
      <h2>配置档</h2>
      <p className="hint">“设为当前”只影响之后新启动的命令行与网关，需重启网关才切换运行中的网关。</p>
      <div className="bar">
        <button type="button" className="btn" aria-expanded={formOpen} aria-label={`${formOpen ? "收起" : "展开"}新建配置档表单`} onClick={() => setFormOpen((open) => !open)}>
          {formOpen ? "收起" : "新建配置档"}
        </button>
      </div>
      {formOpen ? (
        <form className="card" onSubmit={(event) => void create(event)} noValidate>
          <fieldset disabled={creating}>
            <label htmlFor="pf-name">名称</label>
            <input id="pf-name" type="text" maxLength={32} autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder="小写字母、数字、下划线、连字符" value={formName} onChange={(event) => setFormName(event.target.value)} />
            <label htmlFor="pf-desc">描述（可选）</label>
            <input id="pf-desc" type="text" maxLength={DESC_MAX} value={formDesc} onChange={(event) => setFormDesc(event.target.value)} />
            <label htmlFor="pf-clone">克隆自</label>
            <select id="pf-clone" value={formClone} onChange={(event) => setFormClone(event.target.value)}>
              <option value="">不克隆</option>
              {profileRows.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
            </select>
            {formClone ? <div className="warnbox" role="alert">克隆会复制该配置档的 .env（含 API 密钥）与配置。</div> : null}
          </fieldset>
          <div className="formacts">
            <button className="btn primary" type="submit" aria-label="创建配置档" disabled={busy.length > 0}>{creating ? "创建中…" : "创建"}</button>
            <button className="btn" type="button" aria-label="取消新建配置档" disabled={creating} onClick={() => { setFormOpen(false); setFormError(""); }}>取消</button>
            {formError ? <span className="error" role="alert">{formError}</span> : null}
          </div>
        </form>
      ) : null}
      <div style={{ height: 12 }} />
      {profiles.isError ? (
        <div className="empty section-error"><span className="error">{errText(profiles.error, "配置档加载失败")}</span><br />
          <button type="button" className="link" aria-label="重试加载配置档" onClick={() => void profiles.refetch()}>重试</button>
        </div>
      ) : profiles.isLoading && !profileRows.length && raw === null ? (
        <Loading />
      ) : raw !== null ? (
        <div className="card rawbox">
          <p className="error">无法解析配置档列表</p>
          <pre>{raw}</pre>
        </div>
      ) : profileRows.length ? (
        <div className="card list">
          {profileRows.map((profile) => {
            const key = `p:${profile.name}`;
            const rowBusy = busy === key;
            const open = expanded === profile.name;
            return (
              <div key={profile.name} className="row">
                <div className="line">
                  <button
                    type="button"
                    className="name"
                    aria-expanded={open}
                    aria-label={`${open ? "收起" : "查看"}配置档 ${profile.name} 详情`}
                    onClick={() => setExpanded((current) => (current === profile.name ? "" : profile.name))}
                  >
                    <b>{profile.name}</b>
                    <div className="meta">
                      <span>网关：{profile.gateway ?? "—"}</span>
                      {profile.alias ? <span>别名：{profile.alias}</span> : null}
                    </div>
                  </button>
                  {profile.active ? <span className="pill on">当前</span> : null}
                </div>
                <div className="acts">
                  {profile.active ? null : (
                    <button type="button" className="btn" aria-label={`将 ${profile.name} 设为当前配置档`} disabled={busy.length > 0} onClick={() => void activate(profile)}>
                      {rowBusy ? "设置中…" : "设为当前"}
                    </button>
                  )}
                  {profile.name === "default" ? null : confirmDelete === profile.name && preview?.name === profile.name ? (
                    <>
                      <button type="button" className="btn danger" aria-label={`确认删除配置档 ${profile.name}`} disabled={busy.length > 0} onClick={() => void confirmRemove(profile)}>
                        {rowBusy ? "删除中…" : "确认删除"}
                      </button>
                      <button type="button" className="btn" aria-label="取消删除" disabled={busy.length > 0} onClick={() => { setConfirmDelete(""); setPreview(null); }}>取消</button>
                    </>
                  ) : (
                    <button type="button" className="btn danger" aria-label={`删除配置档 ${profile.name}`} disabled={busy.length > 0} onClick={() => void requestPreview(profile)}>
                      {rowBusy ? "读取中…" : "删除"}
                    </button>
                  )}
                </div>
                {preview?.name === profile.name ? (
                  <>
                    <div className="muted tiny rowerr">{preview.token ? `将触及这些路径，${preview.expiresInS} 秒内确认一次` : "这些路径现在不能确认删除"}</div>
                    <pre>{preview.paths.join("\n")}</pre>
                  </>
                ) : null}
                {rowError?.key === key ? <div className="error rowerr" role="alert">{rowError.message}</div> : null}
                {open ? detail.isError ? (
                  <div className="error rowerr" role="alert">{errText(detail.error, "详情加载失败")}</div>
                ) : detail.data?.text == null ? (
                  <div className="muted tiny rowerr">加载中…</div>
                ) : (
                  <pre>{detail.data.text}</pre>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <Empty>没有配置档</Empty>
      )}
      <h2>插件</h2>
      <p className="hint">安装与卸载请在服务器上执行 clawd plugins。这里只保存已安装插件清单声明的非秘密配置，不会安装，也不会在本进程加载。{pluginRows.length ? `共 ${pluginRows.length} 个。` : ""}</p>
      <div className="bar">
        <input type="search" placeholder="搜索插件" aria-label="搜索插件" value={query} onChange={(event) => setQuery(event.target.value)} />
        <label className="only" style={{ margin: 0 }}>
          <Switch checked={onlyEnabled} label="仅显示已启用" onClick={() => setOnlyEnabled((current) => !current)} />
          仅显示已启用
        </label>
      </div>
      {plugins.isError ? (
        <div className="empty section-error"><span className="error">{errText(plugins.error, "插件加载失败")}</span><br />
          <button type="button" className="link" aria-label="重试加载插件" onClick={() => void plugins.refetch()}>重试</button>
        </div>
      ) : plugins.isLoading && !pluginRows.length ? (
        <div aria-busy="true" aria-label="插件加载中">
          <div className="muted tiny">加载中…（通常需要几秒）</div>
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : filtered.length ? (
        <div className="card list">
          {filtered.map((plugin) => {
            const id = pluginKey(plugin);
            const key = `g:${id}`;
            const open = configFor === id;
            return (
              <div key={id} className="row plugin" data-off={plugin.enabled ? undefined : ""}>
                <div className="line">
                  <div className="name">
                    <b>{plugin.name}</b>
                    <div className="meta">
                      {plugin.version ? <span>v{plugin.version}</span> : null}
                      {plugin.source ? <span className="pill">{plugin.source}</span> : null}
                      {id !== plugin.name ? <span>键：{id}</span> : null}
                    </div>
                    {plugin.description ? <div className="desc" title={plugin.description}>{plugin.description}</div> : null}
                  </div>
                  <Switch checked={plugin.enabled} disabled={busy.length > 0} label={`${plugin.enabled ? "停用" : "启用"}插件 ${plugin.name}`} onClick={() => void togglePlugin(plugin)} />
                </div>
                <div className="acts">
                  <button type="button" className="btn" aria-expanded={open} aria-label={`${open ? "收起" : "展开"}插件 ${plugin.name} 的配置`} disabled={busy.length > 0} onClick={() => void openConfig(plugin)}>
                    {configBusy === id ? "读取中…" : open ? "收起配置" : "配置"}
                  </button>
                </div>
                {rowError?.key === key ? <div className="error rowerr" role="alert">{rowError.message}</div> : null}
                {open ? configBusy === id ? (
                  <div className="muted tiny rowerr">加载配置…</div>
                ) : configView ? (
                  <form onSubmit={(event) => void saveConfig(event, plugin)} noValidate>
                    <fieldset disabled={busy === `c:${id}` || configView.managed === true}>
                      {configView.managed ? <div className="warnbox" role="alert">当前安装由包管理器托管，可以查看非秘密字段，不能保存。</div> : null}
                      {configView.note ? <p className="hint">{configView.note}</p> : null}
                      {configView.fields.length === 0 ? (
                        <p className="hint">该插件没有声明可编辑的 config_schema。环境变量和 requires_env 不在这里填写。</p>
                      ) : configView.fields.map((field) => {
                        const inputId = `pg-${id}-${field.key}`.replace(/[^A-Za-z0-9_-]/g, "-");
                        const label = field.label || field.key;
                        if (field.secret || field.type === "secret") {
                          return (
                            <div key={field.key}>
                              <div className="hint">{label}：{field.valueSet ? "已设置" : "未设置"}。密钥不在这里修改，面板不读取明文。{field.env ? `环境变量 ${field.env}。` : ""}</div>
                            </div>
                          );
                        }
                        if (field.type === "boolean") {
                          return (
                            <label key={field.key} className="only" htmlFor={inputId}>
                              <Switch checked={flagDraft[field.key] === true} label={label} onClick={() => setFlagDraft((current) => ({ ...current, [field.key]: current[field.key] !== true }))} />
                              {label}{field.required ? "（必填）" : ""}{field.stored === false ? "（清单默认）" : ""}
                            </label>
                          );
                        }
                        return (
                          <div key={field.key}>
                            <label htmlFor={inputId}>{label}{field.required ? "（必填）" : ""}{field.stored === false ? "（清单默认）" : ""}</label>
                            {field.description ? <div className="hint">{field.description}</div> : null}
                            {field.valueWithheld ? <div className="hint">当前值已隐藏。留空表示不修改。</div> : null}
                            {field.type === "enum" ? (
                              <select id={inputId} value={textDraft[field.key] ?? ""} onChange={(event) => setTextDraft((current) => ({ ...current, [field.key]: event.target.value }))}>
                                {(field.choices ?? []).map((choice) => <option key={choice} value={choice}>{choice}</option>)}
                              </select>
                            ) : field.type === "json" ? (
                              <textarea id={inputId} rows={4} spellCheck={false} value={textDraft[field.key] ?? ""} onChange={(event) => setTextDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
                            ) : (
                              <input id={inputId} type="text" inputMode={field.type === "number" ? "decimal" : undefined} autoComplete="off" spellCheck={false} value={textDraft[field.key] ?? ""} onChange={(event) => setTextDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
                            )}
                          </div>
                        );
                      })}
                    </fieldset>
                    {configView.fields.some((field) => !field.secret && field.type !== "secret") ? (
                      <div className="formacts">
                        <button className="btn primary" type="submit" aria-label={`保存插件 ${plugin.name} 的非秘密配置`} disabled={busy.length > 0 || configView.managed === true}>{busy === `c:${id}` ? "保存中…" : "保存"}</button>
                      </div>
                    ) : null}
                    {configError ? <div className="error rowerr" role="alert">{configError}</div> : null}
                  </form>
                ) : configError ? (
                  <div className="error rowerr" role="alert">{configError}</div>
                ) : (
                  <div className="muted tiny rowerr">没有可读的配置响应。</div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <Empty>{pluginRows.length ? "没有匹配的插件" : "没有插件"}</Empty>
      )}
    </div>
  );
}
