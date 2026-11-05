"""配套测试。交付时未运行。

日后只能在隔离临时目录运行，例如：
    python3 -m unittest scripts/clawd_cutover/tests/test_prepare.py
不要把路径指到生产 home。本文件被执行前，不能把结果写成通过。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
import guard
import manifest
import migration_iface
import mutate
import planbuild
import treewalk


ROOT = Path(__file__).resolve().parents[1]


class PolicyTests(unittest.TestCase):
    def test_banned_path_does_not_stat(self) -> None:
        original = Path.is_symlink

        def boom(self):
            raise AssertionError("should not stat")

        Path.is_symlink = boom
        try:
            with self.assertRaises(guard.GuardError):
                guard.assert_not_banned("/root/.hermes/state.db", kind="test")
        finally:
            Path.is_symlink = original

    def test_relative_tilde_and_broad_root_rejected(self) -> None:
        with self.assertRaises(guard.GuardError):
            guard.normalize_lexical("relative/path")
        with self.assertRaises(guard.GuardError):
            guard.normalize_lexical("~/data")
        with self.assertRaises(guard.GuardError):
            guard.assert_allow_root("/root")
        with self.assertRaises(guard.GuardError):
            guard.assert_allow_root("/etc/nginx")

    def test_secret_names(self) -> None:
        self.assertTrue(treewalk.is_secret_name("config.yaml"))
        self.assertTrue(treewalk.is_secret_name(".env.local"))
        self.assertTrue(treewalk.is_secret_name("api-server.key"))
        self.assertFalse(treewalk.is_secret_name("pyproject.toml"))

    def test_profile_siblings_are_not_named(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "profiles" / "one").mkdir(parents=True)
            (root / "profiles" / "two").mkdir(parents=True)
            (root / "profiles" / "one" / "note.txt").write_text("visible", encoding="utf-8")
            (root / "profiles" / "two" / "note.txt").write_text("hidden", encoding="utf-8")
            resources, summary = treewalk.iter_tree(root, treewalk.Policy())
            blob = json.dumps(resources)
            self.assertNotIn("one", blob)
            self.assertNotIn("two", blob)
            self.assertGreater(summary["profile_siblings_unnamed"], 0)

    def test_migration_is_explicit_and_not_implemented_here(self) -> None:
        skipped = migration_iface.migration_status("no")
        self.assertEqual(skipped["status"], "explicitly_skipped")
        self.assertFalse(skipped["blocks_apply"])
        pending = migration_iface.migration_status("yes")
        self.assertTrue(pending["blocks_apply"])
        self.assertFalse(pending["implemented_here"])
        self.assertEqual(pending["module"], "clawd_projects/migration.py")
        self.assertNotIn("folder_migration.py", pending["module"])
        self.assertIn("execute_folder_migration", pending["functions"])
        self.assertEqual(pending["status"], "interface_present_not_called")
        self.assertTrue(migration_iface.migration_status(None)["blocks_apply"])

    def test_manifest_cannot_claim_tests_or_service_apply(self) -> None:
        data = manifest.blank_manifest("plan")
        manifest.validate_manifest(data)
        data["verification"]["tool_tests"] = "passed"
        with self.assertRaises(guard.GuardError):
            manifest.validate_manifest(data)
        data = manifest.blank_manifest("plan")
        data["systemd_nginx"]["applied"] = True
        with self.assertRaises(guard.GuardError):
            manifest.validate_manifest(data)

    def test_confirm_token_is_exact(self) -> None:
        self.assertFalse(guard.confirm_has(["apply"], "apply"))
        self.assertTrue(guard.confirm_has(["APPLY"], "apply"))
        opts = argparse_namespace(copy_credentials=True, confirm=["APPLY"])
        self.assertFalse(planbuild.confirmed(opts, "apply"))
        opts.confirm = ["APPLY", "COPY_CREDENTIALS"]
        self.assertTrue(planbuild.confirmed(opts, "apply"))

    def test_execution_policy_does_not_read_secrets_by_default(self) -> None:
        opts = argparse_namespace()
        policy = planbuild.execution_policy(opts)
        self.assertFalse(policy.read_secret_bytes)
        self.assertIsNone(policy.profile)

    def test_report_rejects_secret_assignment(self) -> None:
        with self.assertRaises(guard.GuardError):
            guard.assert_report_clean({"note": "password=hunter2"})

    def test_help_has_no_default_home(self) -> None:
        help_text = cli.build_parser().format_help()
        self.assertNotIn("/root/.hermes", help_text)
        self.assertNotIn("~/.hermes", help_text)

    def test_static_policy(self) -> None:
        forbidden = ("shutil.rmtree", "os.rmdir", "os.removedirs", "subprocess", "os.system", "importlib")
        for path in ROOT.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for item in forbidden:
                self.assertNotIn(item, text, f"{path.name} contains {item}")
        self.assertNotIn("select.py", (ROOT / "treewalk.py").read_text(encoding="utf-8").split("Not named", 1)[0])

    def test_recover_renames_previous_back(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.mkdir()
            (target / "keep.txt").write_text("old", encoding="utf-8")
            previous = root / "target.clawd-previous-test"
            mutate._rename_onto_absent(target, previous)
            journal = {"steps": [{"op": "rename", "dst": str(previous)}], "staging": str(root / "staging")}
            self.assertEqual(mutate._recover_apply(journal, target, previous), "restored_previous")
            self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse(previous.exists())

    def test_backup_skips_secret_and_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            (source / "hello.txt").write_text("hello", encoding="utf-8")
            (source / "config.yaml").write_text("token: hidden", encoding="utf-8")
            backup = root / "backup"
            meta = mutate.backup_tree(source, backup, treewalk.Policy())
            self.assertFalse((backup / "files" / "config.yaml").exists())
            self.assertTrue((backup / "files" / "hello.txt").is_file())
            self.assertFalse(meta["credentials_copied"])
            with self.assertRaises(guard.GuardError):
                mutate.backup_tree(source, backup, treewalk.Policy())

    def test_restore_refuses_different_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            (source / "hello.txt").write_text("hello", encoding="utf-8")
            backup = root / "backup"
            mutate.backup_tree(source, backup, treewalk.Policy())
            target = root / "target"
            target.mkdir()
            (target / "hello.txt").write_text("changed", encoding="utf-8")
            with self.assertRaises(guard.GuardError):
                mutate.restore_tree(backup, target)
            self.assertEqual((target / "hello.txt").read_text(encoding="utf-8"), "changed")

    def test_plan_and_unconfirmed_apply_do_not_create_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            (source / "hello.txt").write_text("hello", encoding="utf-8")
            target = root / "target"
            out = root / "plan.json"
            code = cli.main([
                "plan",
                "--allow-root", str(root),
                "--source", str(source),
                "--target", str(target),
                "--migration-allowed", "no",
                "--allow-version-unknown",
                "--out", str(out),
            ])
            self.assertEqual(code, 0)
            self.assertFalse(target.exists())
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(saved["executed"])
            self.assertEqual(saved["verification"]["tool_tests"], "not_run")
            code = cli.main([
                "apply",
                "--allow-root", str(root),
                "--source", str(source),
                "--target", str(target),
                "--backup-dir", str(root / "backup"),
                "--migration-allowed", "no",
                "--allow-version-unknown",
                "--out", str(root / "apply-plan.json"),
            ])
            self.assertEqual(code, 0)
            self.assertFalse(target.exists())
            self.assertFalse((root / "backup").exists())


def argparse_namespace(**kwargs):
    import argparse

    base = dict(
        allow_roots=[],
        source=None,
        target=None,
        backup_dir=None,
        previous=None,
        journal=None,
        out=None,
        out_dir=None,
        migration_allowed=None,
        profile=None,
        copy_credentials=False,
        confirm=[],
        acceptance_report=None,
        allow_version_skew=False,
        allow_version_unknown=False,
        include_skipped=False,
        source_version=None,
        target_version=None,
        max_files=100000,
        max_file_bytes=32 * 1024 * 1024,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


if __name__ == "__main__":
    unittest.main()
