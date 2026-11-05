import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAdvanced, resetSetting, savedNotice, setAdvanced, setConfig, setSetting, type AdvancedRead, type ConfigWriteResult } from "@/lib/admin-api";
import { highlightSafe } from "@/lib/markdown";
import { queryKeys, useConfigYaml, useSettings } from "@/lib/queries";
import type { SettingsField, SettingsView } from "@/lib/sidecar";
import zhText from "@/lib/settings-zh.json";
import zhSupplement from "@/lib/settings-zh-supplement.json";
import { Empty, errText, Head, LoadError, Loading } from "./bits";
import { ConnectionsSection } from "./connections-section";
import { MemorySection } from "./memory-section";
import { ToolsSection } from "./tools-section";

const ZH = zhText;
const ZH_SUPPLEMENT = zhSupplement;
const SEARCH_LIMIT = 150;

function lookupZh(key: string): string {
  if (Object.prototype.hasOwnProperty.call(ZH, key)) return ZH[key as keyof typeof ZH];
  if (Object.prototype.hasOwnProperty.call(ZH_SUPPLEMENT, key)) return ZH_SUPPLEMENT[key as keyof typeof ZH_SUPPLEMENT];
  return "";
}

const describe = (field: SettingsField): string => lookupZh(field.key) || field.description || field.key;

function show(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

interface RowState {
  busy?: boolean;
  error?: string;
  ok?: boolean;
  draft?: string;
  confirmReset?: boolean;
}

const ADVANCED_KEYS: { key: string; label: string; masked: boolean }[] = [
  { key: "credential_pool_strategies", label: "凭证池策略", masked: true },
  { key: "agent.reasoning_overrides", label: "按模型覆盖推理强度", masked: false },
  { key: "terminal.docker_env", label: "Docker 环境变量", masked: true },
  { key: "web.provider_tier", label: "网页提供方层级", masked: false },
  { key: "browser.camofox", label: "Camofox 浏览器映射", masked: false },
  { key: "compression.model_thresholds", label: "按模型的压缩阈值", masked: false },
  { key: "auxiliary.vision.extra_body", label: "辅助模型 → 视觉 → 附加请求体", masked: false },
  { key: "auxiliary.compression.extra_body", label: "辅助模型 → 上下文压缩 → 附加请求体", masked: false },
  { key: "auxiliary.skills_hub.extra_body", label: "辅助模型 → 技能库 → 附加请求体", masked: false },
  { key: "auxiliary.approval.extra_body", label: "辅助模型 → 审批 → 附加请求体", masked: false },
  { key: "auxiliary.mcp.extra_body", label: "辅助模型 → MCP → 附加请求体", masked: false },
  { key: "auxiliary.title_generation.extra_body", label: "辅助模型 → 标题 → 附加请求体", masked: false },
  { key: "auxiliary.memory_query_rewrite.extra_body", label: "辅助模型 → 记忆改写 → 附加请求体", masked: false },
  { key: "auxiliary.tts_audio_tags.extra_body", label: "辅助模型 → 语音标签 → 附加请求体", masked: false },
  { key: "auxiliary.voice_chat.extra_body", label: "辅助模型 → 语音对话 → 附加请求体", masked: false },
  { key: "auxiliary.triage_specifier.extra_body", label: "辅助模型 → 分诊 → 附加请求体", masked: false },
  { key: "auxiliary.kanban_decomposer.extra_body", label: "辅助模型 → 看板拆解 → 附加请求体", masked: false },
  { key: "auxiliary.profile_describer.extra_body", label: "辅助模型 → 配置档描述 → 附加请求体", masked: false },
  { key: "auxiliary.goal_judge.extra_body", label: "辅助模型 → 目标判断 → 附加请求体", masked: false },
  { key: "auxiliary.curator.extra_body", label: "辅助模型 → 整理器 → 附加请求体", masked: false },
  { key: "auxiliary.monitor.extra_body", label: "辅助模型 → 监控 → 附加请求体", masked: false },
  { key: "auxiliary.background_review.extra_body", label: "辅助模型 → 后台复查 → 附加请求体", masked: false },
  { key: "auxiliary.moa_reference.extra_body", label: "辅助模型 → 多模型参考 → 附加请求体", masked: false },
  { key: "auxiliary.moa_aggregator.extra_body", label: "辅助模型 → 多模型汇总 → 附加请求体", masked: false },
  { key: "display.status_phrases", label: "状态短语", masked: false },
  { key: "delegation.backends", label: "委派后端设置", masked: false },
  { key: "delegation.request_overrides", label: "委派请求覆盖", masked: false },
  { key: "honcho", label: "Honcho 映射", masked: false },
  { key: "slack.channel_prompts", label: "Slack 频道提示词", masked: false },
  { key: "discord.channel_prompts", label: "Discord 频道提示词", masked: false },
  { key: "whatsapp", label: "WhatsApp 附加设置", masked: false },
  { key: "telegram.channel_prompts", label: "Telegram 频道提示词", masked: false },
  { key: "mattermost.channel_prompts", label: "Mattermost 频道提示词", masked: false },
  { key: "quick_commands", label: "快捷命令", masked: false },
  { key: "platform_hints", label: "平台提示", masked: false },
  { key: "hooks", label: "钩子", masked: false },
  { key: "personalities", label: "个性", masked: false },
  { key: "lsp.servers", label: "LSP 服务器", masked: false },
];

function advancedNotice(result: AdvancedRead): string {
  const home = result.profileHome;
  const profile = home
    ? `写入目录 ${home}（本进程名称 ${result.runningProfile ?? "未返回"}，由该目录派生，不是页面猜测）。sticky ${result.stickyKnown ? result.stickyProfile ?? "空" : "文件不存在或未确认，不当成 default"}。`
    : "服务端没有返回 profile 目录，不能确认写到了哪个配置档。";
  if (
    result.applied === true
    || result.hotLoaded === true
    || result.effectiveNow === true
    || result.processEnvChanged === true
    || result.processSwitched === true
    || result.loadedNow === true
  ) {
    return `服务端报告了本进程生效或配置档切换，与约定不符。${profile}`;
  }
  return `${savedNotice(result)} ${profile}`;
}

function profileLine(meta: AdvancedRead | null): string {
  if (!meta?.profileHome) return "尚未从服务端确认 profile 目录。保存不会猜测 default。";
  const sticky = meta.stickyKnown ? meta.stickyProfile ?? "空" : "文件不存在或未确认，不当成 default";
  return `本进程目录 ${meta.profileHome}。名称 ${meta.runningProfile ?? "未返回"} 来自该目录。sticky：${sticky}。保存不等于本进程已生效。`;
}

export function ConfigTab() {
  const settings = useSettings();
  const config = useConfigYaml();
  const client = useQueryClient();
  const [sub, setSub] = useState<"form" | "yaml">("form");
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [notice, setNotice] = useState("");
  const [yamlKey, setYamlKey] = useState("");
  const [yamlValue, setYamlValue] = useState("");
  const [result, setResult] = useState<ConfigWriteResult | null>(null);
  const [resultError, setResultError] = useState("");
  const [yamlSaving, setYamlSaving] = useState(false);
  const timers = useRef<Record<string, number>>({});
  const switched = useRef(false);
  const sawForm = useRef(false);

  useEffect(() => {
    if (settings.data) sawForm.current = true;
  }, [settings.data]);

  useEffect(() => {
    if (settings.isError && !sawForm.current && !switched.current) {
      switched.current = true;
      setSub("yaml");
    }
  }, [settings.isError]);

  useEffect(() => {
    const id = settings.data?.categories[0]?.id;
    if (id) setCategory((current) => current || id);
  }, [settings.data]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of Object.values(pending)) window.clearTimeout(timer);
    };
  }, []);

  function patchRow(rowKey: string, patch: RowState) {
    setRows((current) => ({ ...current, [rowKey]: { ...current[rowKey], ...patch } }));
  }

  function patchField(rowKey: string, valueNext: unknown) {
    client.setQueryData<SettingsView>(queryKeys.settings, (prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        fields: prev.fields.map((field) => (field.key === rowKey ? { ...field, value: valueNext, userSet: true } : field)),
      };
    });
  }

  async function commit(field: SettingsField, next: unknown) {
    if (rows[field.key]?.busy) return;
    patchRow(field.key, { busy: true, error: "", ok: false });
    try {
      const written = await setSetting(field.key, next);
      patchField(field.key, next);
      patchRow(field.key, { busy: false, ok: true, draft: undefined });
      setNotice(savedNotice(written));
      void client.invalidateQueries({ queryKey: queryKeys.settings });
      window.clearTimeout(timers.current[field.key]);
      timers.current[field.key] = window.setTimeout(() => patchRow(field.key, { ok: false }), 2500);
    } catch (error) {
      patchRow(field.key, { busy: false, error: errText(error, "保存失败") });
    }
  }

  async function reset(field: SettingsField) {
    if (!rows[field.key]?.confirmReset) {
      patchRow(field.key, { confirmReset: true });
      window.clearTimeout(timers.current[`reset:${field.key}`]);
      timers.current[`reset:${field.key}`] = window.setTimeout(() => patchRow(field.key, { confirmReset: false }), 3000);
      return;
    }
    patchRow(field.key, { busy: true, error: "", confirmReset: false });
    try {
      const written = await resetSetting(field.key);
      patchRow(field.key, { busy: false, draft: undefined });
      setNotice(savedNotice(written));
      const refreshed = await settings.refetch();
      if (refreshed.isError) setSub("yaml");
    } catch (error) {
      patchRow(field.key, { busy: false, error: errText(error, "恢复失败") });
    }
  }

  async function saveYaml(event: FormEvent) {
    event.preventDefault();
    const trimmed = yamlKey.trim();
    if (!trimmed || yamlSaving) return;
    setYamlSaving(true);
    setResult(null);
    setResultError("");
    try {
      const written = await setConfig(trimmed, yamlValue);
      setResult(written);
      void client.invalidateQueries({ queryKey: queryKeys.config });
    } catch (error) {
      setResultError(errText(error, "保存失败"));
    } finally {
      setYamlSaving(false);
    }
  }

  const view = settings.data;
  const searching = query.trim() !== "";
  const visible = (() => {
    if (!view) return { list: [] as SettingsField[], capped: false };
    const q = query.trim().toLowerCase();
    let list = q
      ? view.fields.filter((field) => `${field.key} ${describe(field)} ${field.description}`.toLowerCase().includes(q))
      : view.fields.filter((field) => field.category === category);
    if (onlyChanged) list = list.filter((field) => field.userSet);
    return list.length > SEARCH_LIMIT && q ? { list: list.slice(0, SEARCH_LIMIT), capped: true } : { list, capped: false };
  })();

  return (
    <div className="cfg-root">
      <Head title="设置" />
      <div className="tabs" role="tablist" aria-label="设置方式">
        <button type="button" role="tab" aria-selected={sub === "form"} onClick={() => setSub("form")}>表单</button>
        <button type="button" role="tab" aria-selected={sub === "yaml"} onClick={() => setSub("yaml")}>YAML 原文</button>
      </div>
      <div hidden={sub !== "form"}>
        {settings.isError ? (
          <LoadError
            message={errText(settings.error, "加载失败")}
            onRetry={() => void settings.refetch().then((next) => { if (next.isError) setSub("yaml"); })}
          />
        ) : !view ? (
          <Loading text="加载配置项…" />
        ) : (
          <>
            <div className="toolbar">
              <input
                type="search"
                placeholder={`搜索 ${view.fields.length} 个配置项`}
                aria-label="搜索配置项"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <label>
                <input type="checkbox" checked={onlyChanged} onChange={(event) => setOnlyChanged(event.target.checked)} />
                只看已修改的
              </label>
            </div>
            {searching ? null : (
              <div className="cats" role="group" aria-label="分组">
                {view.categories.map((item) => (
                  <button key={item.id} type="button" className="cat" aria-pressed={category === item.id} onClick={() => setCategory(item.id)}>
                    {item.label}
                    <small>{item.count}</small>
                  </button>
                ))}
              </div>
            )}
            {notice ? <div className="notice ok">{notice}</div> : null}
            {visible.list.length ? (
              <div className="card list">
                {visible.list.map((field) => (
                  <SettingRow key={field.key} field={field} state={rows[field.key]} onCommit={commit} onRow={patchRow} onReset={reset} />
                ))}
              </div>
            ) : (
              <Empty>{searching ? "没有匹配的配置项" : onlyChanged ? "这个分组里还没有改过的配置项" : "这个分组是空的"}</Empty>
            )}
            {visible.capped ? <p className="sub">只显示前 {SEARCH_LIMIT} 项，请把关键字写得更具体些。</p> : null}
          </>
        )}
        {view || settings.isError ? <ConfigGroups onNotice={setNotice} notice={view ? "" : notice} /> : null}
      </div>
      {sub === "yaml" ? (
        <>
          <section>
            <h2>修改配置项</h2>
            <form className="card" onSubmit={(event) => void saveYaml(event)}>
              <div className="pair">
                <div>
                  <label htmlFor="ck">配置项（点号路径）</label>
                  <input id="ck" type="text" placeholder="例：display.compact" autoComplete="off" spellCheck={false} value={yamlKey} onChange={(event) => setYamlKey(event.target.value)} />
                </div>
                <div>
                  <label htmlFor="cv">新值</label>
                  <input id="cv" type="text" placeholder="例：true" autoComplete="off" spellCheck={false} value={yamlValue} onChange={(event) => setYamlValue(event.target.value)} />
                </div>
              </div>
              <div className="actions">
                <button className="btn primary" type="submit" disabled={yamlSaving || !yamlKey.trim()}>{yamlSaving ? "保存中…" : "保存"}</button>
                <span className="muted tiny">由 clawd 校验后写入；部分设置需在「状态」页重启 clawd 网关后生效。密钥类配置不能在这里改。</span>
              </div>
              {resultError ? <div className="out error">{resultError}</div> : null}
              {result ? (
                <div className="out">
                  <span className="ok">{result.saved === false ? "未确认保存" : "已保存"}</span>
                  {result.applied === false ? "，applied: false，本进程未生效" : result.applied === true ? "，服务端标记已生效" : "，未返回 applied"}
                  {result.restartRequired === true ? "，待重启" : ""}
                  {" "}{result.key} = {result.value ?? "（未设置）"}
                  {result.output ? `\n${result.output}` : ""}
                </div>
              ) : null}
            </form>
          </section>
          <section className="yaml">
            <h2>当前配置</h2>
            {config.isError ? (
              <LoadError message={errText(config.error, "加载失败")} onRetry={() => void config.refetch()} />
            ) : config.data ? (
              <>
                <div className="meta">{config.data.path} · 更新于 {new Date(config.data.modifiedAt * 1000).toLocaleString("zh-CN")} · 密钥已隐藏</div>
                <div className="code">
                  <div className="code-head"><span>yaml</span></div>
                  <pre><code className="hljs" dangerouslySetInnerHTML={{ __html: highlightSafe(config.data.yaml, "yaml") }} /></pre>
                </div>
              </>
            ) : (
              <Loading />
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

function ConfigGroups({ onNotice, notice }: { onNotice: (text: string) => void; notice: string }) {
  return (
    <>
      {notice ? <div className="notice ok">{notice}</div> : null}
      <ToolsSection />
      <MemorySection />
      <ConnectionsSection />
      <AdvancedObjects onNotice={onNotice} />
    </>
  );
}

function AdvancedObjects({ onNotice }: { onNotice: (text: string) => void }) {
  const [key, setKey] = useState(ADVANCED_KEYS[0].key);
  const [draft, setDraft] = useState("");
  const [meta, setMeta] = useState<AdvancedRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loaded = meta ? JSON.stringify(meta.value ?? {}, null, 2) : "";
  const selected = ADVANCED_KEYS.find((item) => item.key === key);
  const masked = meta?.plan === "masked-advanced" || selected?.masked === true;

  useEffect(() => {
    let cancelled = false;
    setBusy(true);
    setError("");
    getAdvanced(key).then((view) => {
      if (cancelled) return;
      setMeta(view);
      setDraft(JSON.stringify(view.value ?? {}, null, 2));
      setBusy(false);
    }).catch((loadError: unknown) => {
      if (cancelled) return;
      setMeta(null);
      setDraft("");
      setError(errText(loadError, "读取开放对象失败"));
      setBusy(false);
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || !meta) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      setError("需要合法的 JSON 对象");
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      setError("需要 JSON 对象，不能是数组");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const written = await setAdvanced(key, parsed);
      onNotice(advancedNotice(written));
      try {
        const fresh = await getAdvanced(key);
        setMeta(fresh);
        setDraft(JSON.stringify(fresh.value ?? {}, null, 2));
      } catch (readError) {
        setDraft("");
        setMeta(null);
        setError(`已提交，但回读失败，输入已清空。${errText(readError, "回读失败")}`);
      }
    } catch (saveError) {
      setError(errText(saveError, "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2>开放对象</h2>
      <form className="card" onSubmit={(event) => void save(event)}>
        <div className="ctl">
          <select aria-label="开放对象" value={key} disabled={busy} onChange={(event) => setKey(event.currentTarget.value)}>
            {ADVANCED_KEYS.map((item) => (
              <option key={item.key} value={item.key}>{item.label}{item.masked ? "（值只写不读）" : ""}</option>
            ))}
          </select>
        </div>
        <p className="sub">{profileLine(meta)}</p>
        <p className="sub">
          {masked
            ? "值不回显。***、空字符串或删掉键都会保留原值，不会用掩码覆盖。要删除请写成 null，要更换请写新值。"
            : "秘密键如果是 ***、空字符串或被删掉，会保留原值。要清除秘密请写成 null。没出现的非秘密键会被删除。"}
          {" "}保存只写本进程配置文件，applied 保持 false。
        </p>
        {error ? <div className="out error" role="alert">{error}</div> : null}
        <textarea
          aria-label={key}
          spellCheck={false}
          disabled={busy || !meta}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <div className="actions">
          <button className="btn primary" type="submit" disabled={busy || !meta || draft === loaded}>{busy ? "保存中…" : "保存"}</button>
          <button
            className="btn"
            type="button"
            disabled={busy || draft === loaded}
            onClick={() => {
              setDraft(loaded);
              setError("");
            }}
          >
            撤销
          </button>
          <code>{key}</code>
        </div>
      </form>
    </section>
  );
}

function SettingRow({
  field,
  state,
  onCommit,
  onRow,
  onReset,
}: {
  field: SettingsField;
  state: RowState | undefined;
  onCommit: (field: SettingsField, value: unknown) => Promise<void>;
  onRow: (key: string, patch: RowState) => void;
  onReset: (field: SettingsField) => Promise<void>;
}) {
  const row = state ?? {};
  return (
    <div className="row">
      <div className="desc">
        <div className="t">
          {describe(field)}
          {field.userSet ? <span className="badge">已修改</span> : null}
        </div>
        <code>{field.key}</code>
      </div>
      <div className="ctl"><FieldControl field={field} row={row} onCommit={onCommit} onRow={onRow} /></div>
      <div className="side">
        {row.busy ? <span className="muted">保存中…</span> : null}
        {row.ok ? <span className="ok">已保存</span> : null}
        {row.error ? <span className="error" role="alert">{row.error}</span> : null}
        {field.userSet && !field.locked && !row.busy ? (
          <button type="button" className="link" onClick={() => void onReset(field)}>
            {row.confirmReset ? "再点一次确认恢复默认" : "恢复默认"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function FieldControl({
  field,
  row,
  onCommit,
  onRow,
}: {
  field: SettingsField;
  row: RowState;
  onCommit: (field: SettingsField, value: unknown) => Promise<void>;
  onRow: (key: string, patch: RowState) => void;
}) {
  if (field.locked) {
    const value = field.valueSet === null ? show(field.value) : field.valueSet ? "已设置" : "未设置";
    return (
      <div className="locked">
        {value ? <span>{value}</span> : null}
        {value ? " · " : null}
        {field.locked}
      </div>
    );
  }
  if (field.type === "boolean") {
    return (
      <button
        type="button"
        className="switch"
        role="switch"
        aria-checked={field.value === true}
        aria-label={field.key}
        disabled={row.busy}
        onClick={() => void onCommit(field, field.value !== true)}
      />
    );
  }
  if (field.type === "number") return <NumberField field={field} disabled={!!row.busy} onInvalid={() => onRow(field.key, { error: "需要一个数字" })} onCommit={onCommit} />;
  if (field.type === "select") return <SelectField field={field} disabled={!!row.busy} onCommit={onCommit} />;
  if (field.type === "list") return <ListField field={field} row={row} onRow={onRow} onCommit={onCommit} />;
  return <TextField field={field} disabled={!!row.busy} onCommit={onCommit} />;
}

function NumberField({
  field,
  disabled,
  onInvalid,
  onCommit,
}: {
  field: SettingsField;
  disabled: boolean;
  onInvalid: () => void;
  onCommit: (field: SettingsField, value: unknown) => Promise<void>;
}) {
  const shown = field.value === null || field.value === undefined ? "" : String(field.value);
  const ref = useNativeChange(shown, (raw, node) => {
    if (raw.trim() === "" || !Number.isFinite(Number(raw))) {
      onInvalid();
      node.value = shown;
      return;
    }
    void onCommit(field, Number(raw));
  });
  return (
    <input
      ref={ref}
      type="number"
      step="any"
      aria-label={field.key}
      disabled={disabled}
      defaultValue={shown}
      key={shown}
      onWheel={(event) => event.currentTarget.blur()}
    />
  );
}

function TextField({
  field,
  disabled,
  onCommit,
}: {
  field: SettingsField;
  disabled: boolean;
  onCommit: (field: SettingsField, value: unknown) => Promise<void>;
}) {
  const shown = typeof field.value === "string" ? field.value : show(field.value ?? "");
  const ref = useNativeChange(shown, (raw) => void onCommit(field, raw));
  return (
    <input
      ref={ref}
      type="text"
      autoComplete="off"
      spellCheck={false}
      aria-label={field.key}
      disabled={disabled}
      defaultValue={shown}
      key={shown}
    />
  );
}

function useNativeChange(value: string, onCommit: (value: string, node: HTMLInputElement) => void) {
  const ref = useRef<HTMLInputElement>(null);
  const commit = useRef(onCommit);
  commit.current = onCommit;
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const handler = () => commit.current(node.value, node);
    node.addEventListener("change", handler);
    return () => node.removeEventListener("change", handler);
  }, [value]);
  return ref;
}

function SelectField({
  field,
  disabled,
  onCommit,
}: {
  field: SettingsField;
  disabled: boolean;
  onCommit: (field: SettingsField, value: unknown) => Promise<void>;
}) {
  const options = field.options ?? [];
  const current = typeof field.value === "string" ? field.value : "";
  return (
    <select aria-label={field.key} disabled={disabled} value={current} onChange={(event) => void onCommit(field, event.currentTarget.value)}>
      {options.includes(current) ? null : <option value={current}>{current || "（空）"}</option>}
      {options.map((option) => (
        <option key={option || "empty"} value={option}>{option === "" ? "（默认 / 空）" : option}</option>
      ))}
    </select>
  );
}

function ListField({
  field,
  row,
  onRow,
  onCommit,
}: {
  field: SettingsField;
  row: RowState;
  onRow: (key: string, patch: RowState) => void;
  onCommit: (field: SettingsField, value: unknown) => Promise<void>;
}) {
  const base = JSON.stringify(field.value ?? [], null, 2);
  const text = row.draft ?? base;
  return (
    <div style={{ width: "100%", display: "grid", gap: 6 }}>
      <textarea
        aria-label={field.key}
        spellCheck={false}
        disabled={row.busy}
        value={text}
        onChange={(event) => onRow(field.key, { draft: event.target.value, error: "" })}
      />
      {text !== base ? (
        <div className="ctl" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" disabled={row.busy} onClick={() => onRow(field.key, { draft: undefined, error: "" })}>撤销</button>
          <button
            type="button"
            className="btn primary"
            disabled={row.busy}
            onClick={() => {
              try {
                const parsed: unknown = JSON.parse(text);
                if (!Array.isArray(parsed)) throw new Error("not array");
                void onCommit(field, parsed);
              } catch {
                onRow(field.key, { error: "需要合法的 JSON 数组，例如 [\"a\", \"b\"]" });
              }
            }}
          >
            保存
          </button>
        </div>
      ) : null}
    </div>
  );
}
