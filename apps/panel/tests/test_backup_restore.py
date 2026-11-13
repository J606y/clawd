"""Tests for local update backup and restore (F03).
Exercises synthetic git repository only; NEVER touches real Hermes install or update.
"""
import sys as _sys
from pathlib import Path as _Path
_REPO_ROOT = _Path(__file__).resolve().parents[3]
_TESTS_DIR = _Path(__file__).resolve().parent
if str(_TESTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_TESTS_DIR))
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))

from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from clawd_server import panel_core as core_mod

spec_restore = None
try:
    import importlib.util
    spec_r = importlib.util.spec_from_file_location("restore_script", REPO_ROOT / "scripts" / "restore-local-backup.py")
    if spec_r and spec_r.loader:
        restore_mod = importlib.util.module_from_spec(spec_r)
        spec_r.loader.exec_module(restore_mod)
except Exception:
    restore_mod = None


class TestBackupRestore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self.tmp.name)
        self.repo = self.tmp_dir / "synthetic-repo"
        self.backup_dir = self.tmp_dir / "backup"
        self.repo.mkdir()
        self.backup_dir.mkdir()

        def git(*args):
            return subprocess.run(
                ["git", "-c", "safe.directory=*", "-C", str(self.repo), *args],
                capture_output=True,
                text=True,
                check=True,
            ).stdout

        git("init", "-q")
        git("config", "user.email", "audit@example.invalid")
        git("config", "user.name", "Audit Tester")
        (self.repo / "tracked.txt").write_text("v1\n")
        (self.repo / "executable.sh").write_text("#!/bin/sh\necho ok\n")
        (self.repo / "executable.sh").chmod(0o755)
        git("add", "tracked.txt", "executable.sh")
        git("commit", "-qm", "initial commit")

    def tearDown(self):
        self.tmp.cleanup()

    def git_repo(self, *args):
        return subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def test_f03_backup_captures_staged_worktree_and_all_untracked(self):
        # 1. Modify tracked in index (staged)
        (self.repo / "tracked.txt").write_text("staged v2\n")
        # 2. Add new staged file
        (self.repo / "new-staged.txt").write_text("new staged file\n")
        self.git_repo("add", "tracked.txt", "new-staged.txt")

        # 3. Modify tracked in worktree (unstaged)
        (self.repo / "tracked.txt").write_text("unstaged v3\n")

        # 4. Create 505 untracked files (> 500 boundary)
        for i in range(505):
            (self.repo / f"untracked-{i:03d}.txt").write_text(f"untracked {i}\n")

        # Run _backup_local_changes
        with patch.object(core_mod, "HERMES_INSTALL", self.repo):
            core_mod._backup_local_changes(self.backup_dir)

        # Assertions on backup files
        index_patch = self.backup_dir / "index.patch"
        worktree_patch = self.backup_dir / "worktree.patch"
        untracked_tar = self.backup_dir / "untracked.tar.gz"

        self.assertTrue(index_patch.exists(), "index.patch must be created")
        self.assertGreater(index_patch.stat().st_size, 0, "index.patch must contain staged changes")

        self.assertTrue(worktree_patch.exists(), "worktree.patch must be created")
        self.assertGreater(worktree_patch.stat().st_size, 0, "worktree.patch must contain worktree changes")

        self.assertTrue(untracked_tar.exists(), "untracked.tar.gz must be created")
        import tarfile
        with tarfile.open(untracked_tar, "r:gz") as tar:
            members = tar.getnames()
        self.assertEqual(len(members), 505, "All 505 untracked files must be archived without truncation")

    def test_f03_boundaries_symlinks_modes_and_traversal(self):
        # 1. Staged binary file & mode change
        (self.repo / "binary.bin").write_bytes(bytes([0, 1, 2, 255, 254]))
        (self.repo / "executable.sh").chmod(0o644)
        self.git_repo("add", "binary.bin", "executable.sh")

        # 2. Tracked deletion & rename
        (self.repo / "to_delete.txt").write_text("delete me\\n")
        (self.repo / "to_rename.txt").write_text("rename me\\n")
        self.git_repo("add", "to_delete.txt", "to_rename.txt")
        self.git_repo("commit", "-qm", "add files for delete/rename")
        self.git_repo("rm", "to_delete.txt")
        self.git_repo("mv", "to_rename.txt", "renamed.txt")

        # 3. Untracked ordinary symlink and dangling symlink
        (self.repo / "untracked_sym_normal").symlink_to("tracked.txt")
        (self.repo / "untracked_sym_dangling").symlink_to("nonexistent_target.txt")

        # 4. Untracked file with newline in name
        newline_file = self.repo / "newline\\nfile.txt"
        newline_file.write_text("newline content\\n")

        # Backup
        with patch.object(core_mod, "HERMES_INSTALL", self.repo):
            core_mod._backup_local_changes(self.backup_dir)

        # Verify archive contains symlinks and newline file
        import tarfile
        with tarfile.open(self.backup_dir / "untracked.tar.gz", "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("untracked_sym_normal", names)
            self.assertIn("untracked_sym_dangling", names)
            self.assertIn("newline\\nfile.txt", names)

        # Reset repo to clean state
        self.git_repo("reset", "--hard", "HEAD")
        self.git_repo("clean", "-fd")

        # Restore
        self.assertIsNotNone(restore_mod)
        res = restore_mod.restore_backup(self.repo, self.backup_dir)
        self.assertGreater(res["untracked_count"], 0)

        # Verify restored symlinks
        restored_normal = self.repo / "untracked_sym_normal"
        restored_dangling = self.repo / "untracked_sym_dangling"
        self.assertTrue(restored_normal.is_symlink())
        self.assertTrue(restored_dangling.is_symlink())
        self.assertEqual(os.readlink(restored_normal), "tracked.txt")
        self.assertEqual(os.readlink(restored_dangling), "nonexistent_target.txt")

        # Verify binary bytes preserved
        self.assertEqual((self.repo / "binary.bin").read_bytes(), bytes([0, 1, 2, 255, 254]))

        # Verify tar traversal refusal
        traversal_dir = self.tmp_dir / "traversal_backup"
        traversal_dir.mkdir()
        bad_tar = traversal_dir / "untracked.tar.gz"
        with tarfile.open(bad_tar, "w:gz") as tar:
            ti = tarfile.TarInfo("../../evil.txt")
            ti.size = 4
            import io
            tar.addfile(ti, io.BytesIO(b"evil"))
        with self.assertRaises(RuntimeError) as cm:
            restore_mod.restore_backup(self.repo, traversal_dir)
        self.assertIn("traversal", str(cm.exception).lower())

        # Verify existing target collision refusal during untracked extraction
        collision_dir = self.tmp_dir / "collision_backup"
        collision_dir.mkdir()
        col_tar = collision_dir / "untracked.tar.gz"
        with tarfile.open(col_tar, "w:gz") as tar:
            ti = tarfile.TarInfo("tracked.txt")
            ti.size = 11
            tar.addfile(ti, io.BytesIO(b"collide_val"))
        with self.assertRaises(RuntimeError) as cm_col:
            restore_mod.restore_backup(self.repo, collision_dir)
        self.assertIn("exists", str(cm_col.exception).lower())

    def test_f03_restore_synthetic_repo_full_verification(self):
        # 1. Create staged, worktree, and untracked changes
        (self.repo / "tracked.txt").write_text("staged content\n")
        (self.repo / "staged-new.txt").write_text("staged new\n")
        self.git_repo("add", "tracked.txt", "staged-new.txt")
        (self.repo / "tracked.txt").write_text("worktree content\n")
        (self.repo / "untracked.txt").write_text("untracked content\n")

        # Backup
        with patch.object(core_mod, "HERMES_INSTALL", self.repo):
            core_mod._backup_local_changes(self.backup_dir)

        # Reset repo to clean HEAD
        self.git_repo("reset", "--hard", "HEAD")
        self.git_repo("clean", "-fd")
        self.assertEqual((self.repo / "tracked.txt").read_text(), "v1\n")
        self.assertFalse((self.repo / "staged-new.txt").exists())
        self.assertFalse((self.repo / "untracked.txt").exists())

        # Restore using restore-local-backup.py
        self.assertIsNotNone(restore_mod)
        assert restore_mod is not None
        res = restore_mod.restore_backup(self.repo, self.backup_dir)
        self.assertGreater(res["index_bytes"], 0)
        self.assertGreater(res["worktree_bytes"], 0)
        self.assertEqual(res["untracked_count"], 1)

        # Verify staged diff, worktree diff, content and permissions
        cached_diff = self.git_repo("diff", "--cached")
        self.assertIn("staged content", cached_diff)
        self.assertIn("staged-new.txt", cached_diff)

        wt_diff = self.git_repo("diff")
        self.assertIn("worktree content", wt_diff)

        self.assertEqual((self.repo / "tracked.txt").read_text(), "worktree content\n")
        self.assertEqual((self.repo / "staged-new.txt").read_text(), "staged new\n")
        self.assertEqual((self.repo / "untracked.txt").read_text(), "untracked content\n")


if __name__ == "__main__":
    unittest.main()
