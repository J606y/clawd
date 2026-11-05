import { useEffect, useRef, useState } from "react";
import { useUpdateInfo, useUpdateRun } from "@/lib/queries";
import type { UpdateInfo, UpdateRun } from "@/lib/sidecar";
import { errText, Head } from "./bits";

export function UpdateTab() {
  const [force, setForce] = useState(false);
  const infoQ = useUpdateInfo(force);
  const [shown, setShown] = useState<UpdateInfo | null>(null);
  const [poll, setPoll] = useState(false);
  const runQ = useUpdateRun(poll);
  const knownRunning = useRef(false);
  const sawRunning = useRef(false);

  useEffect(() => {
    if (infoQ.isSuccess && infoQ.data) setShown(infoQ.data);
  }, [infoQ.isSuccess, infoQ.data]);

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
                {info.version || "clawd"}
                {info.releaseDate ? <> <small>{info.releaseDate}</small></> : null}
              </div>
              <div className="usub">提交 {info.commit || "—"}{info.branch ? ` · 分支 ${info.branch}` : ""}</div>
            </div>
            <div className="uact">
              {info.behindBy == null ? <span className="pill mid">未能检查</span> : info.behindBy === 0 ? <span className="pill on">已是最新</span> : <span className="pill mid">落后 {info.behindBy} 个提交</span>}
              <button type="button" className="btn" disabled={infoQ.isFetching || running} onClick={recheck}>{infoQ.isFetching ? "检查中…" : "重新检查"}</button>
            </div>
          </div>
          {info.behindBy != null && info.behindBy > 0 ? (
            <div className="uline warn">没有安全的进程内更新接口，不能从这里立即更新。此页只保留检查结果。</div>
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
          <div className="uline faint">{info.checkedAt ? `检查于 ${new Date(info.checkedAt * 1000).toLocaleTimeString()} · ` : ""}对比上游 main；数据来自 GitHub 比较接口</div>
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
