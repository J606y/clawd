import { useEffect, useRef, useState } from "react";
import { useUpdateInfo, useUpdateRun, useUpdateStart } from "@/lib/queries";
import { CLAWD_VERSION } from "@/lib/version";
import type { UpdateChannel, UpdateInfo, UpdateRun } from "@/lib/sidecar";
import { errText, Head } from "./bits";

export function UpdateTab() {
  const [force, setForce] = useState(false);
  const [channel, setChannel] = useState<UpdateChannel>("stable");
  const infoQ = useUpdateInfo(force, channel);
  const [shown, setShown] = useState<UpdateInfo | null>(null);
  const [poll, setPoll] = useState(false);
  const runQ = useUpdateRun(poll);
  const startM = useUpdateStart();
  const knownRunning = useRef(false);
  const sawRunning = useRef(false);

  useEffect(() => {
    if (infoQ.isSuccess && infoQ.data) setShown(infoQ.data);
  }, [infoQ.isSuccess, infoQ.data]);

  // Server remembers the channel; adopt it when a check reports one.
  useEffect(() => {
    if (infoQ.data?.channel) setChannel(infoQ.data.channel);
  }, [infoQ.data?.channel]);

  useEffect(() => {
    if (!infoQ.isError) return;
    setShown((prev) => ({
      version: prev?.version ?? "",
      commit: prev?.commit ?? "",
      localChanges: [],
      behindBy: null,
      error: errText(infoQ.error, "检查失败"),
    }));
  }, [infoQ.isError, infoQ.error, infoQ.dataUpdatedAt]);

  useEffect(() => {
    const data = runQ.data;
    if (data?.running || data?.state === "running") {
      knownRunning.current = true;
      sawRunning.current = true;
      setPoll(true);
      return;
    }
    if (runQ.isError && knownRunning.current) {
      setPoll(true);
      return;
    }
    if (data && !data.running && sawRunning.current) {
      sawRunning.current = false;
      knownRunning.current = false;
      setPoll(false);
      if (force) void infoQ.refetch();
      else setForce(true);
      return;
    }
    if (data && !data.running) setPoll(false);
  }, [runQ.data, runQ.isError, runQ.dataUpdatedAt, force, infoQ]);

  function recheck() {
    if (force) void infoQ.refetch();
    else setForce(true);
  }

  function startUpdate() {
    const info = shown;
    if (!info?.latestTag) return;
    const label = info.channel === "beta" ? "测试版" : "正式版";
    if (
      !window.confirm(
        `更新到 ${info.latestTag}（${label}）？\n更新会重启面板和网关，对话会短暂中断；失败时自动回滚到更新前。`,
      )
    )
      return;
    startM.mutate({ tag: info.latestTag, channel });
  }

  const running = Boolean(runQ.data?.running || runQ.data?.state === "running" || knownRunning.current && poll);
  const info = shown;

  return (
    <div className="update-root">
      <Head title="更新" />
      {!info ? (
        <div className="card upd"><div className="loading">检查更新中…</div></div>
      ) : (
        <div className="card upd">
          <div className="urow">
            <div>
              <div className="uver">
                {CLAWD_VERSION}
                {info.releaseDate ? <> <small>{info.releaseDate}</small></> : null}
              </div>
              <div className="usub">提交 {info.commit || "—"}{info.branch ? ` · 分支 ${info.branch}` : ""}</div>
            </div>
            <div className="uact">
              {info.behindBy == null ? <span className="pill mid">未能检查</span> : info.behindBy === 0 ? <span className="pill on">已是最新</span> : <span className="pill mid">有新版本</span>}
              <button type="button" className="btn" disabled={infoQ.isFetching || running} onClick={recheck}>{infoQ.isFetching ? "检查中…" : "重新检查"}</button>
            </div>
          </div>
          <div className="urow">
            <label className="usub" htmlFor="update-channel">版本通道</label>
            <select id="update-channel" value={channel} disabled={infoQ.isFetching || running} onChange={(event) => setChannel(event.target.value as UpdateChannel)}>
              <option value="stable">正式版</option>
              <option value="beta">测试版</option>
            </select>
            <span className="usub">{channel === "beta" ? "测试版带 C 前缀（如 C0.0.1），从 GitHub prerelease 拉取" : "正式版不带 C 前缀，只看正式 release"}</span>
          </div>
          {info.behindBy != null && info.behindBy > 0 && info.latestVersion ? (
            <div className="uline">
              <div>最新{info.channel === "beta" ? "测试版" : "正式版"}：<strong>{info.latestVersion}</strong>{info.releaseDate ? `（${info.releaseDate}）` : ""}</div>
              {info.releaseNotes ? <div className="uline faint">{info.releaseNotes}</div> : null}
              {info.releaseUrl ? <div><a href={info.releaseUrl} target="_blank" rel="noopener noreferrer">在 GitHub 查看该版本</a></div> : null}
              <button type="button" className="btn" disabled={running || startM.isPending || info.localChanges.length > 0} onClick={startUpdate}>
                {startM.isPending ? "正在启动更新…" : `立即更新到 ${info.latestVersion}`}
              </button>
              {startM.isError ? <div className="error uline">{errText(startM.error, "启动更新失败")}</div> : null}
            </div>
          ) : null}
          <RunBlock run={runQ.data} />
          {info.error ? <div className="error uline">{info.error}{info.stale ? "（以下为上次检查结果）" : ""}</div> : null}
          {info.localChanges.length ? (
            <div className="uline warn">
              安装目录里有 {info.localChanges.length} 处本地改动。此页不能执行更新：
              <code>{info.localChanges.join("、")}</code>
            </div>
          ) : null}
          {info.recent?.length ? (
            <>
              <ul className="ucommits">
                {info.recent.map((item) => (
                  <li key={item.sha}><code>{item.sha}</code> {item.title}<small>{item.date.slice(0, 10)}</small></li>
                ))}
              </ul>
              {info.behindBy && info.behindBy > info.recent.length ? (
                <div className="uline">
                  仅显示最新 {info.recent.length} 条
                  {info.compareUrl ? <>，<a href={info.compareUrl} target="_blank" rel="noopener noreferrer">在 GitHub 查看全部</a></> : null}
                </div>
              ) : null}
            </>
          ) : null}
          <div className="uline faint">{info.checkedAt ? `检查于 ${new Date(info.checkedAt * 1000).toLocaleTimeString()} · ` : ""}检查 J606y/clawd 的 GitHub release，按所选通道过滤</div>
        </div>
      )}
    </div>
  );
}

function RunBlock({ run }: { run: UpdateRun | undefined }) {
  if (!run || run.state === "idle") return null;
  const label = { running: "更新进行中…", done: "更新完成", failed: "更新失败", interrupted: "更新被中断" }[run.state];
  const pill = run.state === "running" ? "进行中" : run.state === "done" ? "成功" : "需检查";
  const pillClass = run.state === "done" ? "on" : run.state === "running" ? "mid" : "bad";
  return (
    <div className="urun">
      <div className="urow"><strong>{label}</strong><span className={`pill ${pillClass}`}>{pill}</span></div>
      {run.state === "running" ? (
        <div className="uline faint">会重启网关，对话可能短暂中断；请保持此页面或稍后回来查看，更新不会因关闭页面而停止。</div>
      ) : (
        <div className="uline faint">
          {run.oldHead && run.newHead ? <>{run.oldHead} → {run.newHead}{run.changed ? "" : "（版本未变化）"} · </> : null}
          本地改动备份：<code>{run.backupDir ?? ""}</code>{run.patchBytes ? `（补丁 ${run.patchBytes} 字节）` : ""}
        </div>
      )}
      {run.state === "failed" || run.state === "interrupted" ? (
        <div className="uline warn">更新没有成功。上面是日志末尾；服务若不正常，先到「状态」页的「服务」里重启网关，再把日志发给我。</div>
      ) : null}
      {run.logTail ? <pre className="ulog">{run.logTail}</pre> : null}
    </div>
  );
}
