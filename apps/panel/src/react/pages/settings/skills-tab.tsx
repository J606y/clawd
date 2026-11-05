import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { renderMarkdown } from "@/lib/markdown";
import {
  useSaveSkill,
  useSetSkillEnabled,
  useSkill,
  useSkillBackup,
  useSkillBackups,
  useSkills,
} from "@/lib/queries";
import type { Skill } from "@/lib/sidecar";
import { errText, Head, LoadError, Loading, Notice, Switch } from "./bits";

function stripFront(content: string): string {
  return content.replace(/^---\n[\s\S]*?\n---\n/, "");
}

export function SkillsTab() {
  const skills = useSkills();
  const toggle = useSetSkillEnabled();
  const saveSkill = useSaveSkill();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeBad, setNoticeBad] = useState(false);
  const [viewing, setViewing] = useState<Skill | null>(null);
  const [editing, setEditing] = useState(false);
  const [raw, setRaw] = useState("");
  const [baseModifiedAt, setBaseModifiedAt] = useState(0);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [backupId, setBackupId] = useState("");
  const skillQ = useSkill(viewing?.path ?? "", viewing !== null);
  const backups = useSkillBackups(viewing?.path ?? "", editing && viewing !== null);
  const backup = useSkillBackup(viewing?.path ?? "", backupId, editing && backupId.length > 0);
  const dirty = editing && draft !== raw;

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  useEffect(() => {
    if (!backup.data || backup.data.id !== backupId) return;
    setDraft(backup.data.content);
    setEditError("");
  }, [backup.data, backupId]);

  useEffect(() => {
    if (backup.isError) setEditError(errText(backup.error, "读取历史版本失败"));
  }, [backup.isError, backup.error]);

  function confirmDiscard(): boolean {
    return !dirty || window.confirm("有未保存的修改，确定放弃吗？");
  }

  function open(skill: Skill) {
    if (!confirmDiscard()) return;
    setEditing(false);
    setEditError("");
    setConflict(false);
    setBackupId("");
    setViewing(skill);
  }

  async function flip(skill: Skill) {
    if (busy) return;
    const next = !skill.enabled;
    setBusy(skill.name);
    setNotice("");
    setNoticeBad(false);
    try {
      try {
        await toggle.mutateAsync({ name: skill.name, enabled: next });
      } catch (error) {
        setNoticeBad(true);
        setNotice(errText(error, "切换失败"));
        try {
          await skills.refetch();
        } catch {
          // 保留服务端切换错误，不用回读失败盖住它。
        }
        return;
      }
      try {
        await skills.refetch();
      } catch {
        setNoticeBad(true);
        setNotice("已提交，但列表未能刷新。开关仍显示上次读到的状态，本进程未热加载");
        return;
      }
      setNoticeBad(false);
      setNotice(`「${skill.name}」已保存为${next ? "启用" : "停用"}，新对话生效。本进程未热加载`);
    } finally {
      setBusy("");
    }
  }

  function startEdit() {
    setDraft(raw);
    setEditing(true);
    setEditError("");
    setConflict(false);
    setBackupId("");
  }

  function cancelEdit() {
    if (!confirmDiscard()) return;
    setEditing(false);
    setEditError("");
    setConflict(false);
  }

  function closeViewer() {
    if (!confirmDiscard()) return;
    setEditing(false);
    setViewing(null);
  }

  async function save() {
    if (!viewing || saving || !dirty) return;
    setSaving(true);
    setEditError("");
    try {
      const result = await saveSkill.mutateAsync({ path: viewing.path, content: draft, baseModifiedAt });
      const saved = draft.replace(/\r\n/g, "\n");
      const nextRaw = saved.endsWith("\n") ? saved : `${saved}\n`;
      setRaw(nextRaw);
      setBaseModifiedAt(result.modifiedAt);
      setEditing(false);
      setNoticeBad(false);
      setNotice(result.unchanged ? `「${viewing.name}」内容没有变化` : `「${viewing.name}」已保存（旧版已备份），新对话生效`);
    } catch (error) {
      setConflict(error instanceof ApiError && error.status === 409);
      setEditError(errText(error, "保存失败"));
    } finally {
      setSaving(false);
    }
  }

  async function reloadLatest() {
    if (!viewing) return;
    const next = await skillQ.refetch();
    if (!next.data) {
      setEditError(errText(next.error, "重新载入失败"));
      return;
    }
    setRaw(next.data.content);
    setBaseModifiedAt(next.data.modifiedAt);
    setDraft(next.data.content);
    setConflict(false);
    setEditError("");
  }

  function pickBackup(id: string) {
    if (!id) return;
    if (draft !== raw && !window.confirm("会用这个历史版本替换编辑框里的内容，确定吗？")) return;
    if (id === backupId) void backup.refetch();
    else setBackupId(id);
  }

  const list = skills.data ?? [];
  const q = query.trim().toLowerCase();
  const filtered = q ? list.filter((skill) => `${skill.name} ${skill.description} ${skill.category}`.toLowerCase().includes(q)) : list;
  const groups = new Map<string, Skill[]>();
  for (const skill of filtered) {
    const cat = skill.category || "其他";
    groups.set(cat, [...(groups.get(cat) ?? []), skill]);
  }
  const grouped = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  const off = list.filter((skill) => !skill.enabled).length;
  const viewContent = skillQ.isError
    ? `**加载失败**：${errText(skillQ.error, "未知错误")}`
    : skillQ.data && viewing && skillQ.data.path === viewing.path
      ? stripFront(skillQ.data.content)
      : null;

  useEffect(() => {
    if (!skillQ.data || !viewing || skillQ.data.path !== viewing.path || editing) return;
    setRaw(skillQ.data.content);
    setBaseModifiedAt(skillQ.data.modifiedAt);
  }, [skillQ.data, viewing, editing]);

  return (
    <div className="skills-root">
      <Head title="技能" />
      {list.length ? <p className="sub">共 {list.length} 个{off ? `，已停用 ${off} 个` : ""}。停用的技能不会出现在新对话里。</p> : null}
      <Notice text={notice} bad={noticeBad} />
      {viewing ? (
        <div className="card viewer">
          <div className="bar">
            <h2>
              {viewing.name}
              {dirty ? <small className="warn"> · 未保存</small> : null}
            </h2>
            {editing ? (
              <>
                <button type="button" className="btn" disabled={saving} onClick={cancelEdit}>取消</button>
                <button type="button" className="btn primary" disabled={saving || !dirty} onClick={() => void save()}>{saving ? "保存中…" : "保存"}</button>
              </>
            ) : (
              <>
                <button type="button" className="btn" disabled={viewContent === null} onClick={startEdit}>编辑</button>
                <button type="button" className="btn" onClick={closeViewer}>关闭</button>
              </>
            )}
          </div>
          {editing ? (
            <>
              <textarea
                className="editor"
                spellCheck={false}
                aria-label={`编辑 ${viewing.name} 的 SKILL.md`}
                disabled={saving}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
                    event.preventDefault();
                    void save();
                  }
                }}
              />
              <div className="edit-tools">
                <span>{new Blob([draft]).size.toLocaleString()} 字节 · Ctrl/⌘ + S 保存 · 开头的头信息（name 等）要保留，技能名不能在这里改</span>
                <span className="grow" />
                {backups.data?.length ? (
                  <label>
                    历史版本
                    <select
                      value=""
                      onChange={(event) => {
                        pickBackup(event.target.value);
                        event.target.value = "";
                      }}
                    >
                      <option value="">选择…</option>
                      {backups.data.map((item) => (
                        <option key={item.id} value={item.id}>{new Date(item.at * 1000).toLocaleString()}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
              </div>
              {editError ? (
                <div className="error edit-err" role="alert">
                  {editError}
                  {conflict ? <button type="button" className="link" onClick={() => void reloadLatest()}>放弃我的修改并重新载入</button> : null}
                </div>
              ) : null}
            </>
          ) : viewContent === null ? (
            <div className="muted">加载中…</div>
          ) : (
            <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(viewContent) }} />
          )}
        </div>
      ) : null}
      <div className="search">
        <input type="search" placeholder="搜索技能" aria-label="搜索技能" value={query} onChange={(event) => setQuery(event.target.value)} />
      </div>
      {skills.isError ? (
        <LoadError message={errText(skills.error, "加载失败")} onRetry={() => void skills.refetch()} />
      ) : skills.isLoading && !list.length ? (
        <Loading />
      ) : grouped.map(([cat, items]) => (
        <div key={cat}>
          <h2>{cat}</h2>
          <div className="card list">
            {items.map((skill) => (
              <div key={skill.path} className="skill" data-off={skill.enabled ? undefined : ""}>
                <button type="button" className="info" title={`查看 ${skill.name}`} onClick={() => void open(skill)}>
                  <b>{skill.name}{skill.version ? <small>v{skill.version}</small> : null}</b>
                  <span>{skill.description}</span>
                </button>
                <Switch checked={skill.enabled} disabled={busy.length > 0} label={`${skill.enabled ? "停用" : "启用"} ${skill.name}`} onClick={() => void flip(skill)} />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
