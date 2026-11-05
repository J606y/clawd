"""会话后台进程：只读 Hermes 的 processes.json，并用 /proc 核实进程仍是登记的那一个。"""
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def _start_ticks(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    return int(stat.rpartition(")")[2].split()[19])


@unittest.skipUnless(Path("/proc/self/stat").exists(), "needs Linux /proc")
class SessionBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = importlib.import_module("sidecar.hermes_panel_sidecar")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        home = patch.object(self.m, "HERMES_HOME", self.home)
        home.start()
        self.addCleanup(home.stop)
        self.pid = os.getpid()
        self.ticks = _start_ticks(self.pid)

    def entry(self, **over):
        base = {"session_id": "proc_synthetic", "command": "python3 farm.py", "pid": self.pid, "pid_scope": "host",
                "host_start_time": self.ticks, "started_at": 1791573917.42, "parent_session_id": "api_1_parent",
                "owner_task_id": "api_1_parent"}
        base.update(over)
        return base

    def write(self, entries):
        (self.home / "processes.json").write_text(json.dumps(entries), encoding="utf-8")

    def test_missing_registry_means_nothing_running(self):
        self.assertEqual(self.m.get_session_background("api_1_parent"), {"processes": []})

    def test_live_process_of_this_session_is_listed(self):
        self.write([self.entry(), self.entry(session_id="proc_other", parent_session_id="api_2_other", owner_task_id="api_2_other")])
        self.assertEqual(self.m.get_session_background("api_1_parent"), {"processes": [
            {"id": "proc_synthetic", "command": "python3 farm.py", "startedAt": 1791573917.42}]})

    def test_owner_falls_back_to_owner_task_id(self):
        self.write([self.entry(parent_session_id=None)])
        self.assertEqual(len(self.m.get_session_background("api_1_parent")["processes"]), 1)

    def test_reused_pid_exited_pid_and_unverifiable_scopes_are_not_listed(self):
        self.write([
            self.entry(host_start_time=self.ticks + 1),  # 进程号被别的进程复用
            self.entry(pid=2 ** 22 + 7),                 # 已退出
            self.entry(pid_scope="docker"),              # 不是本机进程号，无法核实
            self.entry(pid="123"),                       # 类型不对
            "not an object",
        ])
        self.assertEqual(self.m.get_session_background("api_1_parent"), {"processes": []})

    def test_command_is_bounded_and_bad_timestamps_are_dropped(self):
        self.write([self.entry(command="x" * 1000, started_at="yesterday")])
        listed = self.m.get_session_background("api_1_parent")["processes"][0]
        self.assertEqual(len(listed["command"]), self.m.PROCESS_COMMAND_MAX_CHARS)
        self.assertIsNone(listed["startedAt"])

    def test_unreadable_or_foreign_registry_is_unavailable_not_empty(self):
        for raw in ["{not json", json.dumps({"processes": []})]:
            (self.home / "processes.json").write_text(raw, encoding="utf-8")
            with self.assertRaises(self.m.ApiError) as ctx:
                self.m.get_session_background("api_1_parent")
            self.assertEqual(ctx.exception.status, 503)

    def test_oversized_registry_is_not_parsed(self):
        (self.home / "processes.json").write_bytes(b" " * (self.m.PROCESS_REGISTRY_MAX_BYTES + 1))
        with self.assertRaises(self.m.ApiError) as ctx:
            self.m.get_session_background("api_1_parent")
        self.assertEqual(ctx.exception.status, 503)

    def test_invalid_session_id_is_rejected_before_reading(self):
        for bad in ["", "../etc", "a" * 200, None]:
            with self.assertRaises(self.m.ApiError) as ctx:
                self.m.get_session_background(bad)
            self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
