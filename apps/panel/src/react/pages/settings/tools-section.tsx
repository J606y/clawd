import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api";
import { type WriteFacts } from "@/lib/admin-api";
import { Empty, LoadError, Loading, Notice, Switch, errText } from "./bits";

const SIDECAR = new URL("panel-core/", document.baseURI);
const KEY = ["settings-section", "tools"] as const;

interface Toolset {
  name: string;
  enabled: boolean;
  description: string;
  platform: string;
  configOnly: boolean;
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

function rows(payload: unknown): Toolset[] {
  if (!payload || typeof payload !== "object" || !Array.isArray((payload as { data?: unknown }).data)) {
    throw new Error("服务端没有返回工具集名单");
  }
  return (payload as { data: unknown[] }).data.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.name !== "string" || typeof row.enabled !== "boolean") return [];
    return [{
      name: row.name,
      enabled: row.enabled,
      description: typeof row.description === "string" ? row.description : "",
      platform: typeof row.platform === "string" ? row.platform : "",
      configOnly: row.configOnly === true,
    }];
  });
}

export function ToolsSection() {
  const client = useQueryClient();
  const busyRef = useRef("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null);
  const [query, setQuery] = useState("");
  const toolsets = useQuery({
    queryKey: KEY,
    queryFn: async () => rows(await request<unknown>("tools/toolsets", {}, 60000, SIDECAR)),
  });

  async function toggle(item: Toolset) {
    if (busyRef.current) return;
    const key = item.name;
    busyRef.current = key;
    setBusy(key);
    setNotice("");
    setRowError(null);
    try {
      const result = await request<WriteFacts>("tools/toolsets/enabled", {
        method: "POST",
        body: JSON.stringify({ name: item.name, enabled: !item.enabled }),
      }, 90000, SIDECAR);
      setNotice(effectNotice(result, "已保存。当前会话的工具集没有改，下一会话才读取。"));
      await client.invalidateQueries({ queryKey: KEY });
    } catch (error) {
      setRowError({ key, message: errText(error, "切换失败") });
    } finally {
      busyRef.current = "";
      setBusy("");
    }
  }

  const list = toolsets.data ?? [];
  const q = query.trim().toLowerCase();
  const filtered = q ? list.filter((item) => `${item.name} ${item.description} ${item.platform}`.toLowerCase().includes(q)) : list;

  return (
    <section aria-label="工具集">
      <h2>工具集</h2>
      <p className="sub">开关只写入当前进程所属 profile。不会改正在进行的对话，也不会安装依赖。平台名是工具配置目标，不是配置档名称。</p>
      {notice ? <Notice text={notice} /> : null}
      <div className="search">
        <input type="search" placeholder="搜索工具集" aria-label="搜索工具集" value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      {toolsets.isError ? (
        <LoadError message={errText(toolsets.error, "工具集加载失败")} onRetry={() => void toolsets.refetch()} />
      ) : toolsets.isLoading && !list.length ? (
        <Loading text="加载工具集…" />
      ) : filtered.length ? (
        <div className="card list">
          {filtered.map((item) => (
            <div key={item.name} className="row" data-off={item.enabled ? undefined : ""}>
              <div className="desc">
                <div className="t">{item.name}</div>
                {item.description ? <span>{item.description}</span> : null}
                <code>{item.configOnly ? `${item.name}.enabled` : `platform_toolsets.${item.platform || "?"}`}</code>
              </div>
              <div className="ctl">
                <Switch checked={item.enabled} disabled={busy.length > 0} label={`${item.enabled ? "停用" : "启用"}工具集 ${item.name}`} onClick={() => void toggle(item)} />
              </div>
              <div className="side">
                {busy === item.name ? <span className="muted">保存中…</span> : <span className="muted">下一会话</span>}
                {rowError?.key === item.name ? <span className="error" role="alert">{rowError.message}</span> : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Empty>{list.length ? "没有匹配的工具集" : "没有可配置的工具集"}</Empty>
      )}
    </section>
  );
}
