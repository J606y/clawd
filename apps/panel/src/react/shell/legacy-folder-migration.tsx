import { useEffect, useState } from "react";
import {
  folderMigrationApi,
  MigrationClientError,
  type MigrationPlan,
  type MigrationPreview,
  type MigrationReceipt,
} from "@/lib/projects.js";
import {
  migrationExecuteBody,
  migrationFailureText,
  migrationPlanBody,
  projectPathError,
  type MigrationMappingInput,
} from "@/lib/project-model.js";
import { ProjectPathField } from "./project-path-field";

/** 旧文件夹显式映射。不重排会话列表，不填猜测目录。 */
export function LegacyFolderMigration(props: { disabled: boolean; onMigrated: () => void }) {
  const [preview, setPreview] = useState<MigrationPreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [path, setPath] = useState("");
  const [createDirectory, setCreateDirectory] = useState(false);
  const [plan, setPlan] = useState<MigrationPlan | null>(null);
  const [receipt, setReceipt] = useState<MigrationReceipt | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [rollbackId, setRollbackId] = useState("");
  const [reuseConfirmed, setReuseConfirmed] = useState(false);

  const load = () => {
    setPreviewError("");
    void folderMigrationApi.preview().then((data) => {
      setPreview(data);
    }).catch((caught: unknown) => {
      setPreview(null);
      setPreviewError(migrationFailureText(caught));
    });
  };

  useEffect(() => {
    load();
  }, []);

  const needs = preview?.folders.filter((folder) => folder.status === "needs_explicit_path" && folder.primary_path == null && folder.guessed !== true) ?? [];
  const migrated = preview?.folders.filter((folder) => folder.status === "migrated") ?? [];
  const selected = needs.find((folder) => folder.folder_id === selectedId);
  const mapping: MigrationMappingInput | null = selected
    ? { folder_id: selected.folder_id, primary_path: path, create_directory: createDirectory }
    : null;
  const pathError = path ? projectPathError(path) : "项目必须绑定绝对目录";
  const planned = plan?.folders?.[0];
  const samePath = (typed: string, recorded: string) => {
    const left = typed.trim().replace(/\/+$/, "") || typed.trim();
    const right = recorded.trim().replace(/\/+$/, "") || recorded.trim();
    return left.length > 1 && left === right;
  };
  const offer = selected?.reuse_offer;
  const offerPath = typeof offer?.recorded_primary_path === "string" ? offer.recorded_primary_path : "";
  const reuseReady = Boolean(
    reuseConfirmed
    && offer?.requires_confirmation === true
    && offer.empty_proven === true
    && offer.migration_id
    && mapping
    && offerPath
    && samePath(mapping.primary_path, offerPath),
  );
  const planReady = Boolean(
    mapping
    && !pathError
    && plan
    && planned?.folder_id === mapping.folder_id
    && typeof planned.primary_path === "string"
    && samePath(mapping.primary_path, planned.primary_path)
    && planned.create_directory === mapping.create_directory,
  );

  const rememberFailure = (caught: unknown) => {
    setError(migrationFailureText(caught));
    if (caught instanceof MigrationClientError && caught.payload.receipt) setReceipt(caught.payload.receipt);
    else if (caught instanceof MigrationClientError && caught.payload.migration_id) setReceipt(caught.payload);
  };

  const runPlan = async () => {
    if (!mapping || pathError || props.disabled || busy) return;
    setBusy(true);
    setError("");
    setPlan(null);
    try {
      const body = migrationPlanBody(mapping);
      setPlan(await folderMigrationApi.plan(
        body.mappings[0],
        reuseReady && offer?.migration_id ? { migration_id: offer.migration_id } : undefined,
      ));
    } catch (caught) {
      rememberFailure(caught);
    } finally {
      setBusy(false);
    }
  };

  const runExecute = async () => {
    if (!mapping || !planReady || props.disabled || busy) return;
    setBusy(true);
    setError("");
    try {
      const next = await folderMigrationApi.execute(migrationExecuteBody({
        folder_id: mapping.folder_id,
        primary_path: planned?.primary_path || mapping.primary_path,
        create_directory: mapping.create_directory,
      }, reuseReady && offer?.migration_id ? { migration_id: offer.migration_id } : undefined));
      setReceipt(next);
      setSelectedId("");
      setPath("");
      setCreateDirectory(false);
      setReuseConfirmed(false);
      setPlan(null);
      props.onMigrated();
      load();
    } catch (caught) {
      rememberFailure(caught);
    } finally {
      setBusy(false);
    }
  };

  const runRollback = async (migrationId: string) => {
    if (!migrationId || props.disabled || busy) return;
    setBusy(true);
    setError("");
    try {
      const next = await folderMigrationApi.rollback(migrationId);
      setReceipt(next);
      setRollbackId("");
      props.onMigrated();
      load();
    } catch (caught) {
      rememberFailure(caught);
    } finally {
      setBusy(false);
    }
  };

  if (previewError) {
    return <div className="folder-confirm" role="alert">旧文件夹核心预览失败，未当作没有旧数据，也未迁移。{previewError}</div>;
  }
  if (!preview || preview.source_missing || (!needs.length && !migrated.length && !receipt)) return null;
  const disabled = props.disabled || busy;

  return (
    <div className="folder-confirm" role="region" aria-label="旧文件夹映射">
      <p>显式映射后才会迁移。不猜测目录，不删除旧文件夹、会话或记忆。失败不会显示为成功。归档不会释放目录。</p>
      {needs.map((folder) => (
        <button
          key={folder.folder_id}
          type="button"
          disabled={disabled}
          aria-pressed={selectedId === folder.folder_id}
          onClick={() => {
            setSelectedId(folder.folder_id);
            setPath("");
            setCreateDirectory(false);
            setReuseConfirmed(false);
            setPlan(null);
            setError("");
          }}
        >
          映射：{folder.name}（{folder.folder_id}，{folder.session_ids.length} 个会话）
        </button>
      ))}
      {selected ? (
        <form onSubmit={(event) => event.preventDefault()}>
          <ProjectPathField
            path={path}
            createDirectory={createDirectory}
            disabled={disabled}
            onPath={(next) => {
              setPath(next);
              setReuseConfirmed(false);
              setPlan(null);
            }}
            onCreateDirectory={(next) => {
              setCreateDirectory(next);
              setPlan(null);
            }}
          />
          {offer?.migration_id && offer.requires_confirmation === true && offer.empty_proven === true ? (
            <p>
              回执 {offer.migration_id} 已证明创建的空项目仍占用目录
              {offerPath ? `（${offerPath}）` : ""}。
              归档不会释放路径。不会自动并入。同一目录重试必须另行确认复用该回执，且目录需自行填写。
            </p>
          ) : null}
          {offer?.migration_id && offer.requires_confirmation === true && offer.empty_proven === true ? (
            <button
              type="button"
              disabled={disabled || !offerPath || !mapping || Boolean(pathError) || !samePath(mapping.primary_path, offerPath)}
              aria-pressed={reuseConfirmed}
              onClick={() => {
                setReuseConfirmed(true);
                setPlan(null);
              }}
            >
              确认复用此回执的空项目
            </button>
          ) : null}
          <button type="button" disabled={disabled || Boolean(pathError)} onClick={() => void runPlan()}>核对映射</button>
          <button type="button" disabled={disabled || !planReady} onClick={() => void runExecute()}>确认迁移此文件夹</button>
          {pathError ? <span role="status">{pathError}</span> : null}
          {planReady ? <span role="status">核对通过，尚未写入。确认后才迁移。</span> : null}
        </form>
      ) : null}
      {migrated.map((folder) => (
        <div key={folder.folder_id}>
          <span>{folder.name} 已有迁移回执{folder.recorded_primary_path ? `：${folder.recorded_primary_path}` : ""}。旧文件夹未删除。</span>
          {folder.not_migrated_session_ids?.length ? (
            <span> 另有 {folder.not_migrated_session_ids.length} 个会话未自动并入。</span>
          ) : null}
          {folder.migration_id ? (
            rollbackId === folder.migration_id ? (
              <button type="button" disabled={disabled} onClick={() => void runRollback(folder.migration_id || "")}>确认回滚归属</button>
            ) : (
              <button type="button" disabled={disabled} onClick={() => setRollbackId(folder.migration_id || "")}>回滚此次归属</button>
            )
          ) : null}
        </div>
      ))}
      {receipt?.migration_id ? (
        <div role="status">
          回执 {receipt.migration_id}：{receipt.success === true ? receipt.status : "未成功"}。
          {receipt.fully_restored === false ? " 有归属未恢复或已跳过。" : ""}
          {receipt.recovery ? ` ${receipt.recovery}` : ""}
        </div>
      ) : null}
      {error ? <div role="alert">{error}</div> : null}
    </div>
  );
}
