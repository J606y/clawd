import { useCallback, useEffect, useRef, useState } from "react";
import { migrateLegacyFoldersOnce } from "@/lib/projects.js";
import { autoMigrationApplied, autoMigrationHint, type AutoMigrationResult } from "@/lib/project-model.js";

/**
 * 项目列表加载后调用一次 POST /api/projects/migrations/auto。
 * 不展示路径、模式或 folder_id。pending 和失败都只给短句加手动重试，不轮询。
 */
export function LegacyFolderMigration(props: {
  active: boolean;
  disabled: boolean;
  onMigrated: () => void;
}) {
  const started = useRef(false);
  const onMigrated = useRef(props.onMigrated);
  onMigrated.current = props.onMigrated;
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);

  const apply = useCallback((result: AutoMigrationResult) => {
    if (autoMigrationApplied(result)) {
      setHint("");
      onMigrated.current();
      return;
    }
    setHint(autoMigrationHint(result));
    if (result.success === true && result.pending === false) onMigrated.current();
  }, []);

  const run = useCallback(async (force: boolean) => {
    setBusy(true);
    try {
      apply(await migrateLegacyFoldersOnce(force));
    } catch {
      setHint("旧文件夹迁移失败，可重试");
    } finally {
      setBusy(false);
    }
  }, [apply]);

  useEffect(() => {
    if (!props.active || started.current) return;
    started.current = true;
    void run(false);
  }, [props.active, run]);

  if (!hint) return null;
  return (
    <div role="alert">
      {hint}
      <button type="button" disabled={props.disabled || busy} onClick={() => void run(true)}>重试</button>
    </div>
  );
}
