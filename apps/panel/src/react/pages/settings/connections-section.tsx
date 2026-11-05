import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "@/lib/api";
import { type WriteFacts } from "@/lib/admin-api";
import { Empty, LoadError, Loading, Notice, Switch, errText } from "./bits";

const SIDECAR = new URL("panel-sidecar/", document.baseURI);
const KEY = ["settings-section", "connections"] as const;

const NAMES: Record<string, string> = {
  telegram: "Telegram",
  discord: "Discord",
  slack: "Slack",
  mattermost: "Mattermost",
  matrix: "Matrix",
  whatsapp: "WhatsApp",
  signal: "Signal",
  bluebubbles: "BlueBubbles",
  email: "邮件",
  sms: "短信",
  dingtalk: "钉钉",
  feishu: "飞书",
  google_chat: "Google Chat",
  wecom: "企业微信",
  wecom_callback: "企业微信回调",
  weixin: "微信",
  qqbot: "QQ",
  yuanbao: "元宝",
  api_server: "API 服务",
  webhook: "Webhook",
  msgraph_webhook: "Microsoft Graph",
  relay: "Relay",
};

interface TokenStatus {
  key: string;
  required: boolean;
  valueSet: boolean;
}

interface Platform {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  enabledExplicit: boolean | null;
  tokens: TokenStatus[];
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

function tokens(raw: unknown): TokenStatus[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.key !== "string") return [];
    return [{ key: row.key, required: row.required === true, valueSet: row.valueSet === true }];
  });
}

function rows(payload: unknown): Platform[] {
  if (!payload || typeof payload !== "object" || !Array.isArray((payload as { data?: unknown }).data)) {
    throw new Error("服务端没有返回连接平台");
  }
  return (payload as { data: unknown[] }).data.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (typeof row.id !== "string" || typeof row.enabled !== "boolean") return [];
    return [{
      id: row.id,
      name: typeof row.name === "string" ? row.name : row.id,
      description: typeof row.description === "string" ? row.description : "",
      enabled: row.enabled,
      enabledExplicit: typeof row.enabledExplicit === "boolean" ? row.enabledExplicit : null,
      tokens: tokens(row.tokens),
    }];
  });
}

function explicitText(item: Platform): string {
  if (item.enabledExplicit === true) return "配置里已显式启用";
  if (item.enabledExplicit === false) return "配置里已显式停用";
  return item.enabled ? "配置未写开关；已有凭据时网关会当成启用" : "配置未写开关";
}

export function ConnectionsSection() {
  const client = useQueryClient();
  const busyRef = useRef("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [rowError, setRowError] = useState<{ key: string; message: string } | null>(null);
  const [query, setQuery] = useState("");
  const platforms = useQuery({
    queryKey: KEY,
    queryFn: async () => rows(await request<unknown>("connections/platforms", {}, 60000, SIDECAR)),
  });

  async function toggle(item: Platform) {
    if (busyRef.current) return;
    busyRef.current = item.id;
    setBusy(item.id);
    setNotice("");
    setRowError(null);
    try {
      const result = await request<WriteFacts>("connections/platforms/enabled", {
        method: "POST",
        body: JSON.stringify({ id: item.id, enabled: !item.enabled }),
      }, 90000, SIDECAR);
      setNotice(effectNotice(result, "已保存启用位。没有改令牌，运行中的网关也不会因此连接或断开。"));
      await client.invalidateQueries({ queryKey: KEY });
    } catch (error) {
      setRowError({ key: item.id, message: errText(error, "切换失败") });
    } finally {
      busyRef.current = "";
      setBusy("");
    }
  }

  const list = platforms.data ?? [];
  const q = query.trim().toLowerCase();
  const filtered = q ? list.filter((item) => `${item.id} ${item.name} ${NAMES[item.id] ?? ""}`.toLowerCase().includes(q)) : list;

  return (
    <section aria-label="连接">
      <h2>连接</h2>
      <p className="sub">只改平台启用位。令牌只显示已设置或未设置，不能在这里填写、查看或配对。保存不等于当前网关已经连上或断开。</p>
      {notice ? <Notice text={notice} /> : null}
      <div className="search">
        <input type="search" placeholder="搜索平台" aria-label="搜索平台" value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      {platforms.isError ? (
        <LoadError message={errText(platforms.error, "连接加载失败")} onRetry={() => void platforms.refetch()} />
      ) : platforms.isLoading && !list.length ? (
        <Loading text="加载连接…" />
      ) : filtered.length ? (
        <div className="card list">
          {filtered.map((item) => (
            <div key={item.id} className="row" data-off={item.enabled ? undefined : ""}>
              <div className="desc">
                <div className="t">{NAMES[item.id] ?? item.name}<span className="badge">{explicitText(item)}</span></div>
                {item.description ? <span>{item.description}</span> : null}
                <code>{item.id}</code>
                <div className="meta">
                  {item.tokens.length ? item.tokens.map((token) => (
                    <span key={token.key}>{token.key}：{token.valueSet ? "已设置" : "未设置"}{token.required ? "，必需" : ""}</span>
                  )) : <span>没有可报告的令牌状态</span>}
                </div>
              </div>
              <div className="ctl">
                <Switch checked={item.enabled} disabled={busy.length > 0} label={`${item.enabled ? "停用" : "启用"} ${NAMES[item.id] ?? item.name}`} onClick={() => void toggle(item)} />
              </div>
              <div className="side">
                {busy === item.id ? <span className="muted">保存中…</span> : <span className="muted">下次启动读取</span>}
                {rowError?.key === item.id ? <span className="error" role="alert">{rowError.message}</span> : null}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Empty>{list.length ? "没有匹配的平台" : "没有可配置的连接"}</Empty>
      )}
    </section>
  );
}
