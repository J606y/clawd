import { useEffect, useRef, useState } from "react";
import { ApiError, type HealthDetailed } from "@/lib/api";
import { useHealthDetailed, useHostStatus, useRestartService } from "@/lib/queries";
import { CLAWD_VERSION } from "@/lib/version";
import type { ServiceState } from "@/lib/sidecar";
import { usePageVisible } from "./visible";
import "./status.css";

const PLATFORM_LABEL: Record<string, string> = {
  api_server: "面板接口",
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  webhook: "Webhook",
  discord: "Discord",
  slack: "Slack",
};

const UNITS = ["B", "KB", "MB", "GB", "TB"];

function bytes(n: number | null | undefined): string {
  if (n == null) return "—";
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${UNITS[i]}`;
}

function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} 天 ${h} 小时`;
  if (h) return `${h} 小时 ${m} 分`;
  return `${m} 分钟`;
}

function pct(used: number, total: number): number {
  return total > 0 ? Math.round((used / total) * 100) : 0;
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : "刷新失败";
}

function Meter(props: { label: string; value: string; detail: string; percent: number }) {
  const level = props.percent >= 90 ? "high" : props.percent >= 75 ? "mid" : "ok";
  return (
    <div className="card metric">
      <div className="k">{props.label}</div>
      <div className="v">{props.value}</div>
      <div className="d">{props.detail}</div>
      <div className="bar" role="meter" aria-valuenow={props.percent} aria-valuemin={0} aria-valuemax={100} aria-label={props.label}>
        <i style={{ width: `${Math.min(100, props.percent)}%` }} data-level={level} />
      </div>
    </div>
  );
}

function serviceState(active: string, load?: string) {
  if (load === "not-found") return <span className="pill off">未安装</span>;
  if (load === "masked") return <span className="pill off">已屏蔽</span>;
  if (active === "active") return <span className="pill on">运行中</span>;
  if (active === "activating" || active === "reloading") return <span className="pill mid">启动中</span>;
  if (active === "failed") return <span className="pill bad">失败</span>;
  if (active === "deactivating") return <span className="pill mid">停止中</span>;
  return <span className="pill off">{active === "inactive" ? "已停止" : "状态未知"}</span>;
}

export function StatusPage() {
  const visible = usePageVisible();
  const hostQuery = useHostStatus(visible);
  const healthQuery = useHealthDetailed(visible);
  const restartMut = useRestartService();
  const [restarting, setRestarting] = useState("");
  const [confirm, setConfirm] = useState("");
  const [forceConfirm, setForceConfirm] = useState("");
  const [notice, setNotice] = useState("");
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const host = hostQuery.data ?? null;
  const health: HealthDetailed | null = healthQuery.data ?? null;
  const failed = hostQuery.error ?? healthQuery.error;
  const error = failed ? errorText(failed) : "";
  const metrics = host?.host;
  const platforms = Object.entries(health?.platforms ?? {});

  const restart = async (service: ServiceState, force = false) => {
    setConfirm("");
    setRestarting(service.name);
    setNotice("");
    try {
      await restartMut.mutateAsync({ name: service.name, force });
      if (!mounted.current) return;
      setForceConfirm("");
      setNotice(`${service.label} 已重启`);
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof ApiError && (error.code === "active_runs_exist" || error.code === "activity_state_unavailable")) {
        setForceConfirm(service.name);
        setNotice(`${error.message}。再次点击确认将强制重启。`);
      } else {
        setForceConfirm("");
        setNotice(error instanceof ApiError ? `重启失败：${error.message}` : "重启失败");
      }
    } finally {
      if (mounted.current) setRestarting("");
      void hostQuery.refetch();
      void healthQuery.refetch();
    }
  };

  return (
    <section className="status-page" aria-label="状态">
      <div className="page">
        <div className="head"><h1>状态</h1>{error ? <span className="error">{error}</span> : null}</div>
        {notice ? <div className={`notice ${notice.includes("失败") ? "error" : "ok"}`}>{notice}</div> : null}
        {metrics ? (
          <div className="grid">
            <Meter
              label="CPU"
              value={`${metrics.cpuPercent}%`}
              detail={`${metrics.cpuCount} 核 · 负载 ${metrics.load.map((load) => load.toFixed(2)).join(" ")}`}
              percent={metrics.cpuPercent}
            />
            <Meter
              label="内存"
              value={`${pct(metrics.mem.total - metrics.mem.available, metrics.mem.total)}%`}
              detail={`${bytes(metrics.mem.total - metrics.mem.available)} / ${bytes(metrics.mem.total)}`}
              percent={pct(metrics.mem.total - metrics.mem.available, metrics.mem.total)}
            />
            <Meter
              label="Swap"
              value={`${pct(metrics.swap.total - metrics.swap.free, metrics.swap.total)}%`}
              detail={`${bytes(metrics.swap.total - metrics.swap.free)} / ${bytes(metrics.swap.total)}`}
              percent={pct(metrics.swap.total - metrics.swap.free, metrics.swap.total)}
            />
            <Meter
              label="磁盘"
              value={`${pct(metrics.disk.used, metrics.disk.total)}%`}
              detail={`剩余 ${bytes(metrics.disk.free)} · 已开机 ${duration(metrics.uptimeS)}`}
              percent={pct(metrics.disk.used, metrics.disk.total)}
            />
          </div>
        ) : error ? null : (
          <div className="loading">加载中…</div>
        )}
        {host ? (
          <section>
            <h2>服务</h2>
            <div className="card list">
              {host.services.map((service) => {
                const limit = service.memoryMax && service.memoryMax < 2 ** 60 ? ` / ${bytes(service.memoryMax)}` : "";
                return (
                  <div className="row" key={service.name}>
                    <div className="name">
                      {service.label}
                      <small>
                        {service.name}
                        {service.runningForS != null && service.active === "active" ? ` · 已运行 ${duration(service.runningForS)}` : ""}
                      </small>
                    </div>
                    {serviceState(service.active, service.load)}
                    <div className="mem">{service.memory != null ? `内存 ${bytes(service.memory)}${limit}` : ""}</div>
                    <div className="acts">
                      {!service.restartable ? null : forceConfirm === service.name ? (
                        <>
                          <button type="button" className="btn danger" onClick={() => void restart(service, true)}>强制重启</button>
                          <button type="button" className="btn" onClick={() => { setForceConfirm(""); setNotice(""); }}>取消</button>
                        </>
                      ) : confirm === service.name ? (
                        <>
                          <button type="button" className="btn danger" onClick={() => void restart(service, false)}>确认重启</button>
                          <button type="button" className="btn" onClick={() => setConfirm("")}>取消</button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="btn"
                          disabled={restarting.length > 0}
                          onClick={() => { setConfirm(service.name); setForceConfirm(""); }}
                        >
                          {restarting === service.name ? "重启中…" : "重启"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        ) : null}
        {platforms.length ? (
          <section>
            <h2>clawd {CLAWD_VERSION} · 消息平台</h2>
            <div className="card list">
              {platforms.map(([name, platform]) => (
                <div className="plat" key={name}>
                  <span>{PLATFORM_LABEL[name] ?? name}</span>
                  <span title={platform.error_message ?? ""}>
                    {platform.state === "connected" ? (
                      <span className="pill on">已连接</span>
                    ) : (
                      <span className={`pill ${platform.needs_attention ? "bad" : "mid"}`}>{platform.state}</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
