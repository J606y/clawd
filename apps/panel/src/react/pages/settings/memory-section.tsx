import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api";
import { type WriteFacts } from "@/lib/admin-api";
import { Empty, LoadError, Loading, Notice, Switch, errText } from "./bits";

const SIDECAR = new URL("panel-sidecar/", document.baseURI);
const KEY = ["settings-section", "memory"] as const;

interface ProviderField {
  key: string;
  label: string;
  kind: string;
  description: string;
  secret: boolean;
  valueSet: boolean;
  value: string;
  placeholder: string;
  options: { value: string; label: string }[];
}

interface ProviderRow {
  name: string;
  active: boolean;
  builtin: boolean;
  missing: boolean;
  writable: boolean;
  gap: string;
  fields: ProviderField[];
}

interface ProviderList {
  active: string | null;
  activeOmitted: boolean;
  data: ProviderRow[];
}

function effectNotice(result: WriteFacts, fallback: string): string {
  if (result.applied === true || result.effectiveNow === true || result.hotLoaded === true || result.loadedNow === true || result.processEnvChanged === true) {
    return "服务端把这次保存标成已生效，与约定不符，请刷新确认。不能当成已经生效。";
  }
  if (result.saved === false || result.ok === false) {
    return "服务端没有确认已保存，不能当成已生效。";
  }
  return result.note || fallback;
}

function fields(raw: unknown): ProviderField[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.key !== "string") return [];
    const secret = row.secret === true || row.kind === "secret";
    const options = Array.isArray(row.options)
      ? row.options.flatMap((opt) => {
          if (!opt || typeof opt !== "object") return [];
          const choice = opt as Record<string, unknown>;
          return typeof choice.value === "string" ? [{ value: choice.value, label: typeof choice.label === "string" ? choice.label : choice.value }] : [];
        })
      : [];
    return [{
      key: row.key,
      label: typeof row.label === "string" ? row.label : row.key,
      kind: secret ? "secret" : typeof row.kind === "string" ? row.kind : "text",
      description: typeof row.description === "string" ? row.description : "",
      secret,
      valueSet: row.valueSet === true,
      value: secret || typeof row.value !== "string" ? "" : row.value,
      placeholder: typeof row.placeholder === "string" ? row.placeholder : "",
      options,
    }];
  });
}

function parse(payload: unknown): ProviderList {
  if (!payload || typeof payload !== "object" || !Array.isArray((payload as { data?: unknown }).data)) {
    throw new Error("服务端没有返回记忆提供方");
  }
  const body = payload as { data: unknown[]; active?: unknown; activeOmitted?: unknown };
  return {
    active: typeof body.active === "string" ? body.active : null,
    activeOmitted: body.activeOmitted === true,
    data: body.data.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const row = item as Record<string, unknown>;
      if (typeof row.name !== "string") return [];
      return [{
        name: row.name,
        active: row.active === true,
        builtin: row.builtin === true,
        missing: row.missing === true,
        writable: row.writable === true,
        gap: typeof row.gap === "string" ? row.gap : "",
        fields: fields(row.fields),
      }];
    }),
  };
}

export function MemorySection() {
  const client = useQueryClient();
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const providers = useQuery({
    queryKey: KEY,
    queryFn: async () => parse(await request<unknown>("memory/providers", {}, 60000, SIDECAR)),
  });
  const list = providers.data?.data ?? [];
  const current = list.find((item) => item.name === selected) ?? list.find((item) => item.writable) ?? list[0];
  const dirtyKeys = current ? Object.keys(drafts).filter((key) => key.startsWith(`${current.name}:`)) : [];

  function draft(item: ProviderRow, field: ProviderField): string {
    const key = `${item.name}:${field.key}`;
    return key in drafts ? drafts[key] : field.value;
  }

  function edit(item: ProviderRow, field: ProviderField, value: string) {
    if (field.secret || !item.writable) return;
    setDrafts((prev) => ({ ...prev, [`${item.name}:${field.key}`]: value }));
    setNotice("");
    setError("");
  }

  async function save(item: ProviderRow) {
    if (busyRef.current || !item.writable) return;
    const body: Record<string, string | boolean> = {};
    for (const field of item.fields) {
      if (field.secret) continue;
      const key = `${item.name}:${field.key}`;
      if (!(key in drafts)) continue;
      body[field.key] = field.kind === "bool" ? drafts[key] === "true" : drafts[key];
    }
    if (!Object.keys(body).length) return;
    busyRef.current = true;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const result = await request<WriteFacts>("memory/providers/config", {
        method: "PUT",
        body: JSON.stringify({ name: item.name, fields: body }),
      }, 90000, SIDECAR);
      setDrafts((prev) => {
        const next = { ...prev };
        for (const key of Object.keys(body)) delete next[`${item.name}:${key}`];
        return next;
      });
      setNotice(effectNotice(result, "已保存非秘密配置。没有切换当前记忆提供方，也没有跑安装。"));
      await client.invalidateQueries({ queryKey: KEY });
    } catch (caught) {
      setError(errText(caught, "保存失败"));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  const active = providers.data?.active;

  return (
    <section aria-label="记忆提供方">
      <h2>记忆提供方</h2>
      <p className="sub">这里只改提供方自己的非秘密配置。不切换 memory.provider，不跑 setup，也不新建项目记忆。项目指示和项目记忆只在项目页修改，不会写进这里的全局记忆。配置里没有这一项时显示未设置；字面值若是内置哨兵，也只是记忆选择，不是配置档名。</p>
      {notice ? <Notice text={notice} /> : null}
      {error ? <Notice text={error} bad /> : null}
      <p className="sub">当前 memory.provider：{providers.isSuccess ? (providers.data?.activeOmitted ? "已省略不安全的字面值" : active ?? "未设置") : "尚未读取"}。保存配置不会改这个值。</p>
      {providers.isError ? (
        <LoadError message={errText(providers.error, "记忆提供方加载失败")} onRetry={() => void providers.refetch()} />
      ) : providers.isLoading && !list.length ? (
        <Loading text="加载记忆提供方…" />
      ) : list.length ? (
        <>
          <div className="cats" role="group" aria-label="记忆提供方">
            {list.map((item) => (
              <button key={item.name} type="button" className="cat" aria-pressed={current?.name === item.name} onClick={() => setSelected(item.name)}>
                {item.name}
                {item.active ? <small>当前</small> : null}
              </button>
            ))}
          </div>
          {current ? (
            <div className="card list">
              {current.gap ? <div className="row"><div className="desc"><div className="t">{current.name}</div><span>{current.gap}</span></div></div> : null}
              {current.fields.map((field) => (
                <div key={field.key} className="row">
                  <div className="desc">
                    <div className="t">{field.label}{field.secret ? <span className="badge">只报状态</span> : null}</div>
                    {field.description ? <span>{field.description}</span> : null}
                    <code>{field.key}</code>
                  </div>
                  <div className="ctl">
                    {field.secret ? (
                      <span className="muted">{field.valueSet ? "已设置" : "未设置"}</span>
                    ) : field.kind === "bool" ? (
                      <Switch
                        checked={draft(current, field) === "true"}
                        disabled={!current.writable || busy}
                        label={`${field.label}`}
                        onClick={() => edit(current, field, draft(current, field) === "true" ? "false" : "true")}
                      />
                    ) : field.options.length ? (
                      <select aria-label={field.label} disabled={!current.writable || busy} value={draft(current, field)} onChange={(event) => edit(current, field, event.target.value)}>
                        <option value="">未选择</option>
                        {field.options.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                      </select>
                    ) : (
                      <input
                        aria-label={field.label}
                        disabled={!current.writable || busy}
                        value={draft(current, field)}
                        placeholder={field.placeholder || (field.valueSet ? "" : "未设置")}
                        onChange={(event) => edit(current, field, event.target.value)}
                      />
                    )}
                  </div>
                  <div className="side">
                    {field.secret ? <span className="muted">{field.valueSet ? "已设置" : "未设置"}</span> : dirtyKeys.includes(`${current.name}:${field.key}`) ? <span className="muted">未保存</span> : !field.valueSet ? <span className="muted">未写入</span> : null}
                  </div>
                </div>
              ))}
              {current.writable ? (
                <div className="row">
                  <div className="side">
                    <button type="button" disabled={busy || dirtyKeys.length === 0} onClick={() => void save(current)}>保存非秘密配置</button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <Empty>没有可显示的提供方</Empty>
          )}
        </>
      ) : (
        <Empty>目录里没有记忆提供方。这不是已经配置完成。</Empty>
      )}
    </section>
  );
}
