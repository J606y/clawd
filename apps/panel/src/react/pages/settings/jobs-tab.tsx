import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { ApiError, type CronJob, type CronJobInput, type SessionSummary } from "@/lib/api";
import {
  queryKeys,
  useCreateJob,
  useCronSessions,
  useDeleteJob,
  useJobs,
  usePauseJob,
  useResumeJob,
  useRunJob,
  useUpdateJob,
} from "@/lib/queries";
import { relativeTime } from "@/lib/time";
import { Empty, errText, Head, LoadError, Loading, Notice, openSession, useNow } from "./bits";

const SCHEDULE_HINT = "例：every 30m、every monday 9am、weekdays at 9am、0 9 * * *（cron）";
const DELIVER_MAX = 500;
const SKILL_MAX = 128;
const SKILLS_MAX = 50;
const REPEAT_RE = /^[1-9][0-9]{0,8}$/;
const CONTROL_RE = /[\u0000-\u001f\u007f]/;

interface Draft {
  id: string;
  name: string;
  schedule: string;
  prompt: string;
  deliver: string;
  skills: string;
  repeat: string;
}

interface JobWrite {
  name: string;
  schedule: string;
  prompt: string;
  deliver?: string;
  skills?: string[];
  repeat?: number | null;
}

const EMPTY_DRAFT: Draft = { id: "", name: "", schedule: "", prompt: "", deliver: "", skills: "", repeat: "" };

function asJobInput(body: JobWrite): CronJobInput {
  // CronJobInput 的可选字段由公共类型 owner 补。运行时对象仍带这些键，JSON.stringify 会送出。
  return body as CronJobInput;
}

function cleanDeliver(raw: string): string {
  return raw.split(",").map((part) => part.trim()).filter(Boolean).map((part) => {
    const lower = part.toLowerCase();
    return lower === "local" || lower === "origin" || lower === "all" ? lower : part;
  }).join(",");
}

function cleanSkills(raw: string): string[] {
  const seen = new Set<string>();
  const skills: string[] = [];
  for (const part of raw.split(/[,，、\n]/)) {
    const text = part.trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    skills.push(text);
  }
  return skills;
}

function parseDeliver(raw: string): { ok: true; value: string } | { ok: false; message: string } {
  if (CONTROL_RE.test(raw)) return { ok: false, message: "投递目标不能包含控制字符" };
  const value = cleanDeliver(raw);
  if (value.length > DELIVER_MAX) return { ok: false, message: "投递目标不能超过 500 个字符" };
  return { ok: true, value };
}

function parseSkills(raw: string): { ok: true; value: string[] } | { ok: false; message: string } {
  if (CONTROL_RE.test(raw)) return { ok: false, message: "技能名不能包含控制字符" };
  const value = cleanSkills(raw);
  if (value.length > SKILLS_MAX) return { ok: false, message: "技能一次最多 50 个" };
  if (value.some((item) => item.length > SKILL_MAX)) return { ok: false, message: "单个技能名不能超过 128 个字符" };
  return { ok: true, value };
}

function parseRepeat(raw: string): { ok: true; value: number | null } | { ok: false; message: string } {
  const text = raw.trim();
  if (!text) return { ok: true, value: null };
  if (!REPEAT_RE.test(text)) return { ok: false, message: "重复次数须为 1 及以上的整数，或留空表示不限" };
  return { ok: true, value: Number(text) };
}

const epoch = (iso?: string | null): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : t / 1000;
};

function untilText(iso: string | null | undefined, now: number): string {
  const t = epoch(iso);
  if (t == null) return "—";
  const diff = t - now / 1000;
  if (diff <= 60) return "即将";
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟后`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时后`;
  return new Date(t * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function JobsTab() {
  const jobs = useJobs();
  const client = useQueryClient();
  const createJob = useCreateJob();
  const updateJob = useUpdateJob();
  const deleteJob = useDeleteJob();
  const pauseJob = usePauseJob();
  const resumeJob = useResumeJob();
  const runJob = useRunJob();
  const navigate = useNavigate();
  const now = useNow();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");
  const [openRuns, setOpenRuns] = useState("");
  const [notice, setNotice] = useState("");
  const runs = useCronSessions(openRuns, openRuns.length > 0);

  useEffect(() => {
    if (!runs.isError) return;
    setNotice(runs.error instanceof ApiError ? `运行记录加载失败：${runs.error.message}` : "运行记录加载失败");
  }, [runs.isError, runs.error]);

  function snapshot(): CronJob[] | undefined {
    return client.getQueryData<CronJob[]>(queryKeys.jobs);
  }

  function replaceJob(id: string, patch: (job: CronJob) => CronJob) {
    client.setQueryData<CronJob[]>(queryKeys.jobs, (list) => list?.map((job) => (job.id === id ? patch(job) : job)));
  }

  async function act(job: CronJob, label: string, fn: () => Promise<unknown>) {
    setBusy(job.id);
    setNotice("");
    try {
      await fn();
      setNotice(`「${job.name}」${label}`);
    } catch (error) {
      setNotice(error instanceof ApiError ? `${label}失败：${error.message}` : `${label}失败`);
    } finally {
      setBusy("");
    }
  }

  async function togglePaused(job: CronJob, paused: boolean) {
    const prev = snapshot();
    const before = prev?.find((item) => item.id === job.id);
    replaceJob(job.id, (item) => (paused
      ? { ...item, enabled: true, state: item.state === "paused" ? "scheduled" : item.state }
      : { ...item, enabled: false, state: "paused" }));
    setBusy(job.id);
    setNotice("");
    try {
      if (paused) await resumeJob.mutateAsync(job.id);
      else await pauseJob.mutateAsync(job.id);
      setNotice(`「${job.name}」${paused ? "已恢复" : "已暂停"}`);
    } catch (error) {
      if (before) replaceJob(job.id, () => before);
      setNotice(error instanceof ApiError ? `${paused ? "恢复" : "暂停"}失败：${error.message}` : `${paused ? "恢复" : "暂停"}失败`);
    } finally {
      setBusy("");
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft || saving) return;
    const name = draft.name.trim();
    const schedule = draft.schedule.trim();
    const prompt = draft.prompt.trim();
    if (!name || !schedule || !prompt) {
      setFormError("名称、频率、任务内容都要填");
      return;
    }
    const deliver = parseDeliver(draft.deliver);
    if (!deliver.ok) {
      setFormError(deliver.message);
      return;
    }
    const skills = parseSkills(draft.skills);
    if (!skills.ok) {
      setFormError(skills.message);
      return;
    }
    const repeat = parseRepeat(draft.repeat);
    if (!repeat.ok) {
      setFormError(repeat.message);
      return;
    }
    setSaving(true);
    setFormError("");
    try {
      if (draft.id) {
        const patch: JobWrite = {
          name,
          schedule,
          prompt,
          deliver: deliver.value || "local",
          skills: skills.value,
          repeat: repeat.value,
        };
        await updateJob.mutateAsync({ id: draft.id, patch: asJobInput(patch) });
      } else {
        const body: JobWrite = { name, schedule, prompt };
        if (deliver.value) body.deliver = deliver.value;
        if (skills.value.length) body.skills = skills.value;
        if (repeat.value != null) body.repeat = repeat.value;
        await createJob.mutateAsync(asJobInput(body));
      }
      setNotice(draft.id ? `「${name}」已保存` : `「${name}」已创建`);
      setDraft(null);
    } catch (error) {
      setFormError(errText(error, "保存失败"));
    } finally {
      setSaving(false);
    }
  }

  async function remove(job: CronJob) {
    await act(job, "已删除", () => deleteJob.mutateAsync(job.id));
    setConfirmDelete("");
  }

  const list = jobs.data ?? [];

  return (
    <div className="jobs-root">
      <Head title="定时任务">
        <button type="button" className="btn primary" onClick={() => { setFormError(""); setDraft({ ...EMPTY_DRAFT }); }}>新建</button>
      </Head>
      <Notice text={notice} />
      {draft ? (
        <form className="card" onSubmit={(event) => void save(event)}>
          <h2>{draft.id ? "编辑任务" : "新建定时任务"}</h2>
          <label htmlFor="jn">名称</label>
          <input id="jn" type="text" maxLength={200} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          <label htmlFor="js">频率</label>
          <input id="js" type="text" placeholder={SCHEDULE_HINT} value={draft.schedule} onChange={(event) => setDraft({ ...draft, schedule: event.target.value })} />
          <label htmlFor="jp">任务内容（发给 clawd 的指令）</label>
          <textarea id="jp" maxLength={5000} value={draft.prompt} onChange={(event) => setDraft({ ...draft, prompt: event.target.value })} />
          <label htmlFor="jd">投递（可选）</label>
          <input id="jd" type="text" maxLength={DELIVER_MAX} autoComplete="off" spellCheck={false} placeholder="local / origin / all / 平台名 / 平台:chat_id，逗号分隔" value={draft.deliver} onChange={(event) => setDraft({ ...draft, deliver: event.target.value })} />
          <label htmlFor="jk">技能（可选，逗号分隔）</label>
          <input id="jk" type="text" maxLength={2000} autoComplete="off" spellCheck={false} placeholder="例：github、weather" value={draft.skills} onChange={(event) => setDraft({ ...draft, skills: event.target.value })} />
          <label htmlFor="jr">重复次数（可选）</label>
          <input id="jr" type="text" inputMode="numeric" maxLength={9} autoComplete="off" spellCheck={false} placeholder="正整数，留空为不限次数" value={draft.repeat} onChange={(event) => setDraft({ ...draft, repeat: event.target.value })} />
          <div className="muted tiny">创建时投递留空不传该字段，网关默认 local。次数留空为不限；一次性时间点由核心改成 1 次。origin 是本次 API 来源，不一定是聊天频道。编辑时清空投递会写成 local，清空次数会写成不限。</div>
          <div className="formacts">
            <button className="btn primary" type="submit" disabled={saving}>{saving ? "保存中…" : "保存"}</button>
            <button className="btn" type="button" onClick={() => setDraft(null)}>取消</button>
            {formError ? <span className="error">{formError}</span> : null}
          </div>
        </form>
      ) : null}
      {jobs.isError ? (
        <LoadError message={errText(jobs.error, "加载失败")} onRetry={() => void jobs.refetch()} />
      ) : list.length > 0 ? (
        <div className="card list">
          {list.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              now={now}
              busy={busy === job.id}
              confirm={confirmDelete === job.id}
              runsOpen={openRuns === job.id}
              runs={openRuns === job.id ? runs.data : undefined}
              runsLoading={openRuns === job.id && runs.isLoading}
              onRun={() => void act(job, "已触发运行", () => runJob.mutateAsync(job.id))}
              onEdit={() => {
                setFormError("");
                setDraft({
                  id: job.id,
                  name: job.name,
                  schedule: job.schedule_display || job.schedule?.display || "",
                  prompt: job.prompt,
                  deliver: job.deliver ?? "",
                  skills: job.skills?.join(", ") ?? "",
                  repeat: job.repeat?.times != null ? String(job.repeat.times) : "",
                });
              }}
              onTogglePause={(paused) => void togglePaused(job, paused)}
              onToggleRuns={() => setOpenRuns((current) => (current === job.id ? "" : job.id))}
              onAskDelete={() => setConfirmDelete(job.id)}
              onCancelDelete={() => setConfirmDelete("")}
              onDelete={() => void remove(job)}
              onOpen={(event, session) => openSession(event.currentTarget, session, navigate)}
            />
          ))}
        </div>
      ) : jobs.isPending ? (
        <Loading />
      ) : (
        <Empty>还没有定时任务</Empty>
      )}
    </div>
  );
}

function JobCard({
  job,
  now,
  busy,
  confirm,
  runsOpen,
  runs,
  runsLoading,
  onRun,
  onEdit,
  onTogglePause,
  onToggleRuns,
  onAskDelete,
  onCancelDelete,
  onDelete,
  onOpen,
}: {
  job: CronJob;
  now: number;
  busy: boolean;
  confirm: boolean;
  runsOpen: boolean;
  runs: SessionSummary[] | undefined;
  runsLoading: boolean;
  onRun: () => void;
  onEdit: () => void;
  onTogglePause: (paused: boolean) => void;
  onToggleRuns: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
  onOpen: (event: React.MouseEvent<HTMLButtonElement>, session: SessionSummary) => void;
}) {
  const failing = (job.failure_streak ?? 0) > 0 || job.last_status === "error" || job.last_status === "failed";
  const paused = !job.enabled || job.state === "paused";
  return (
    <div className="job">
      <div className="top">
        <h3 title={job.name}>{job.name}</h3>
        {paused ? <span className="pill off">已暂停</span> : failing ? <span className="pill bad">最近失败</span> : <span className="pill on">运行中</span>}
      </div>
      <div className="meta">
        <span>{job.schedule_display || job.schedule?.display || "—"}</span>
        <span>上次 {relativeTime(epoch(job.last_run_at), now) || "从未"}</span>
        {paused ? null : <span>下次 {untilText(job.next_run_at, now)}</span>}
        {job.deliver ? <span title={job.deliver}>投递 {job.deliver}</span> : null}
        {job.skills?.length ? <span title={job.skills.join(", ")}>技能 {job.skills.join("、")}</span> : null}
        {job.repeat?.times != null ? <span>限 {job.repeat.times} 次</span> : null}
        {job.repeat?.completed ? <span>累计 {job.repeat.completed} 次</span> : null}
      </div>
      <div className="prompt">{job.prompt}</div>
      {failing && job.last_error ? <div className="lasterr" title={job.last_error}>{job.last_error}</div> : null}
      <div className="acts">
        <button type="button" className="btn" disabled={busy} onClick={onRun}>立即运行</button>
        {paused ? (
          <button type="button" className="btn" disabled={busy} onClick={() => onTogglePause(true)}>恢复</button>
        ) : (
          <button type="button" className="btn" disabled={busy} onClick={() => onTogglePause(false)}>暂停</button>
        )}
        <button type="button" className="btn" disabled={busy} onClick={onEdit}>编辑</button>
        <button type="button" className="btn" onClick={onToggleRuns}>{runsOpen ? "收起记录" : "运行记录"}</button>
        {confirm ? (
          <>
            <button type="button" className="btn danger" disabled={busy} onClick={onDelete}>确认删除</button>
            <button type="button" className="btn" onClick={onCancelDelete}>取消</button>
          </>
        ) : (
          <button type="button" className="btn danger" disabled={busy} onClick={onAskDelete}>删除</button>
        )}
      </div>
      {runsOpen ? (
        <div className="runs">
          {runsLoading ? <div className="muted tiny">加载中…</div> : !runs?.length ? <div className="muted tiny">最近没有运行记录</div> : runs.map((session) => (
            <button key={session.id} type="button" onClick={(event) => onOpen(event, session)}>
              <span>{session.title || session.id}</span>
              <span className="muted">{relativeTime(session.last_active ?? session.started_at, now)} · {session.message_count} 条</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
