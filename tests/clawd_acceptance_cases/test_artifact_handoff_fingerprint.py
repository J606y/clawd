"""Trusted archive handoff into artifact hashes. Not executed by the authoring turn.

These tests must not import ``clawd_server.packaging_backend`` or build a wheel.
"""

from __future__ import annotations

import hashlib
import inspect
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "clawd_acceptance"))

import execute
import fingerprints
import stages
from isolation import Layout


REPO = Path(__file__).resolve().parents[2]
WHEEL_NAME = "clawd-0.0.0-py3-none-any.whl"
SDIST_NAME = "clawd-0.0.0.tar.gz"


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _checkout(root: Path) -> Path:
    repo = root / "checkout"
    (repo / "ui-tui" / "dist").mkdir(parents=True)
    (repo / "apps" / "panel" / "dist").mkdir(parents=True)
    (repo / "ui-tui" / "dist" / "entry.js").write_bytes(b"entry-js")
    (repo / "apps" / "panel" / "dist" / "index.html").write_bytes(b"<html>panel</html>")
    return repo


def _pair(parent: Path, wheel_bytes: bytes, sdist_bytes: bytes) -> tuple[Path, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    wheel = parent / WHEEL_NAME
    sdist = parent / SDIST_NAME
    wheel.write_bytes(wheel_bytes)
    sdist.write_bytes(sdist_bytes)
    return wheel, sdist


class ArtifactHandoffFingerprintTests(unittest.TestCase):
    def test_source_does_not_list_archives_and_assemble_forwards_wheel_dir(self) -> None:
        fingerprint_text = (REPO / "scripts" / "clawd_acceptance" / "fingerprints.py").read_text(encoding="utf-8")
        execute_text = (REPO / "scripts" / "clawd_acceptance" / "execute.py").read_text(encoding="utf-8")
        report_text = (REPO / "scripts" / "clawd_acceptance" / "report_build.py").read_text(encoding="utf-8")
        self.assertNotIn(".glob(", fingerprint_text)
        self.assertNotIn("rglob", fingerprint_text)
        self.assertNotIn(".glob(", inspect.getsource(fingerprints.artifact_report))
        self.assertNotIn("rglob", inspect.getsource(fingerprints._trusted_archives))
        self.assertNotIn("wheel_dir=layout.wheel", execute_text)
        self.assertIn("wheel_dir=_trusted_archive_handoff(layout, package_result)", execute_text)
        self.assertIn("wheel_dir=_trusted_archive_handoff(layout, None)", execute_text)
        self.assertIn("artifact_report(repo, wheel_dir=wheel_dir)", report_text)
        self.assertIn("content_hashed", inspect.getsource(fingerprints.artifact_report))

    def test_directory_argument_does_not_hash_old_or_new_archives(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _checkout(Path(raw))
            isolation = repo / "artifacts" / "final-acceptance" / "wheel"
            new_wheel, _new_sdist = _pair(isolation / "accept-build-new", b"new-wheel", b"new-sdist")
            old = isolation / "hermes_agent-0.0.0-py3-none-any.whl"
            old.write_bytes(b"old-wheel")
            (isolation / "clawd-old-py3-none-any.whl").write_bytes(b"old-clawd")
            report = fingerprints.artifact_report(repo, wheel_dir=isolation)
            blob = repr(report)
            self.assertFalse(report["complete"])
            self.assertIn("no trusted stage archive paths", " ".join(report["blockers"]))
            self.assertNotIn(_digest(b"new-wheel"), blob)
            self.assertNotIn(_digest(b"old-wheel"), blob)
            self.assertNotIn(_digest(b"old-clawd"), blob)
            self.assertNotIn(new_wheel.name, blob)
            self.assertTrue(any(item["path"] == "ui-tui/dist/entry.js" and item["content_hashed"] for item in report["items"]))

    def test_handoff_hashes_named_bytes_and_keeps_protections(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = _checkout(Path(raw))
            isolation = repo / "artifacts" / "final-acceptance" / "wheel"
            wheel, sdist = _pair(isolation / "accept-build-new", b"wheel-v1", b"sdist-v1")
            old = isolation / "hermes_agent-9-py3-none-any.whl"
            old.write_bytes(b"historical-bytes")
            handoff = fingerprints.TrustedArchiveHandoff(isolation, str(wheel), str(sdist))
            report = fingerprints.artifact_report(repo, wheel_dir=handoff)
            by_kind = {item["kind"]: item for item in report["items"] if item.get("kind")}
            self.assertEqual(by_kind["wheel"]["sha256"], _digest(b"wheel-v1"))
            self.assertEqual(by_kind["sdist"]["sha256"], _digest(b"sdist-v1"))
            self.assertTrue(by_kind["wheel"]["content_hashed"])
            self.assertNotEqual(by_kind["wheel"]["sha256"], _digest(b"historical-bytes"))
            self.assertTrue(report["complete"])
            self.assertIn("accept-build-new", by_kind["wheel"]["path"])

            wheel.write_bytes(b"wheel-v2")
            changed = fingerprints.artifact_report(repo, wheel_dir=handoff)
            changed_wheel = next(item for item in changed["items"] if item.get("kind") == "wheel")
            self.assertEqual(changed_wheel["sha256"], _digest(b"wheel-v2"))
            self.assertNotEqual(changed_wheel["sha256"], by_kind["wheel"]["sha256"])

            missing = fingerprints.artifact_report(
                repo,
                wheel_dir=fingerprints.TrustedArchiveHandoff(isolation, str(wheel), None),
            )
            self.assertFalse(missing["complete"])
            self.assertIn("one trusted archive path missing (sdist)", " ".join(missing["blockers"]))
            self.assertNotIn(_digest(b"wheel-v2"), repr(missing))

            historical_wheel = isolation / "accept-build-old" / "hermes_agent-0.0.0-py3-none-any.whl"
            historical_sdist = isolation / "accept-build-old" / "hermes_agent-0.0.0.tar.gz"
            historical_wheel.parent.mkdir()
            historical_wheel.write_bytes(b"fake-marker")
            historical_sdist.write_bytes(b"fake-sdist")
            refused = fingerprints.artifact_report(
                repo,
                wheel_dir=fingerprints.TrustedArchiveHandoff(isolation, str(historical_wheel), str(historical_sdist)),
            )
            self.assertFalse(refused["complete"])
            self.assertIn("historical", " ".join(refused["blockers"]))
            self.assertNotIn(_digest(b"fake-marker"), repr(refused))

            link = isolation / "accept-build-link"
            link.mkdir()
            target = link / "clawd-real-py3-none-any.whl"
            target.write_bytes(b"linked-wheel")
            linked = link / WHEEL_NAME
            linked.symlink_to(target.name)
            linked_sdist = link / SDIST_NAME
            linked_sdist.write_bytes(b"linked-sdist")
            symlink_report = fingerprints.artifact_report(
                repo,
                wheel_dir=fingerprints.TrustedArchiveHandoff(isolation, str(linked), str(linked_sdist)),
            )
            self.assertFalse(symlink_report["complete"])
            self.assertIn("symlink", " ".join(symlink_report["blockers"]).lower())
            self.assertNotIn(_digest(b"linked-wheel"), repr(symlink_report))

            real_dir = repo / "old-wheels"
            real_wheel, real_sdist = _pair(real_dir, b"parent-link-wheel", b"parent-link-sdist")
            parent_link = isolation / "accept-build-parent-link"
            parent_link.symlink_to(real_dir, target_is_directory=True)
            parent_report = fingerprints.artifact_report(
                repo,
                wheel_dir=fingerprints.TrustedArchiveHandoff(
                    isolation,
                    str(parent_link / real_wheel.name),
                    str(parent_link / real_sdist.name),
                ),
            )
            self.assertFalse(parent_report["complete"])
            self.assertIn("symlink", " ".join(parent_report["blockers"]).lower())
            self.assertNotIn(_digest(b"parent-link-wheel"), repr(parent_report))

            partial_wheel, partial_sdist = _pair(isolation / "accept-build-x.partial", b"partial-wheel", b"partial-sdist")
            partial_report = fingerprints.artifact_report(
                repo,
                wheel_dir=fingerprints.TrustedArchiveHandoff(isolation, str(partial_wheel), str(partial_sdist)),
            )
            self.assertFalse(partial_report["complete"])
            self.assertIn("resource staging", " ".join(partial_report["blockers"]))
            self.assertNotIn(_digest(b"partial-wheel"), repr(partial_report))

    def test_archives_without_dist_files_are_hashed_but_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw) / "checkout"
            repo.mkdir()
            isolation = repo / "wheel"
            wheel, sdist = _pair(isolation / "accept-build-new", b"only-wheel", b"only-sdist")
            report = fingerprints.artifact_report(
                repo,
                wheel_dir=fingerprints.TrustedArchiveHandoff(isolation, str(wheel), str(sdist)),
            )
            hashed = [item for item in report["items"] if item.get("kind")]
            self.assertEqual({item["sha256"] for item in hashed}, {_digest(b"only-wheel"), _digest(b"only-sdist")})
            self.assertTrue(all(item["content_hashed"] for item in hashed))
            self.assertFalse(report["complete"])
            self.assertTrue(report["blockers"])

    def test_run_all_passes_stage_paths_into_the_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "checkout"
            repo.mkdir()
            layout = Layout(repo / "artifacts" / "final-acceptance")
            layout.ensure()
            wheel, sdist = _pair(layout.wheel / "accept-build-new", b"stage-wheel", b"stage-sdist")
            old = layout.wheel / "hermes_agent-0.0.0-py3-none-any.whl"
            old.write_bytes(b"stale-stage")
            package_result = {
                "id": "package",
                "status": "blocked",
                "exit_code": 2,
                "log": "",
                "detail": "hooks returned",
                "checks": stages.package_build_checks(0, "", layout.wheel),
                "distribution_env": {
                    stages.DISTRIBUTION_WHEEL_ENV: str(wheel),
                    stages.DISTRIBUTION_SDIST_ENV: str(sdist),
                },
            }
            captured: dict = {}

            def fake_assemble(**kwargs):
                captured["wheel_dir"] = kwargs["wheel_dir"]
                return {"summary": {"exit_code": 2}}

            def fake_spawn(*_args, **_kwargs):
                return {"exit_code": 2, "timed_out": False, "pid": 3}

            with (
                unittest.mock.patch.object(execute, "gate_view", return_value={"run_allowed": True}),
                unittest.mock.patch.object(execute, "stage_catalog", return_value=[{"id": "package", "timeout_seconds": 600}]),
                unittest.mock.patch.object(execute, "execute_stage", return_value=package_result),
                unittest.mock.patch.object(execute, "spawn", side_effect=fake_spawn),
                unittest.mock.patch.object(execute, "assemble", side_effect=fake_assemble),
                unittest.mock.patch.object(execute, "coverage_checks", return_value=[]),
            ):
                execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
            handoff = captured["wheel_dir"]
            self.assertIsInstance(handoff, fingerprints.TrustedArchiveHandoff)
            self.assertNotIsInstance(handoff, Path)
            self.assertEqual(handoff.wheel, str(wheel))
            self.assertEqual(handoff.sdist, str(sdist))
            self.assertEqual(handoff.isolation, layout.wheel)
            self.assertNotIn(str(old), (handoff.wheel, handoff.sdist))
            hashed = fingerprints.artifact_report(repo, wheel_dir=handoff)
            digests = {item.get("sha256") for item in hashed["items"]}
            self.assertIn(_digest(b"stage-wheel"), digests)
            self.assertIn(_digest(b"stage-sdist"), digests)
            self.assertNotIn(_digest(b"stale-stage"), digests)

            empty = dict(package_result)
            empty["distribution_env"] = None
            with (
                unittest.mock.patch.object(execute, "gate_view", return_value={"run_allowed": True}),
                unittest.mock.patch.object(execute, "stage_catalog", return_value=[{"id": "package", "timeout_seconds": 600}]),
                unittest.mock.patch.object(execute, "execute_stage", return_value=empty),
                unittest.mock.patch.object(execute, "spawn", side_effect=fake_spawn),
                unittest.mock.patch.object(execute, "assemble", side_effect=fake_assemble),
                unittest.mock.patch.object(execute, "coverage_checks", return_value=[]),
            ):
                execute.run_all(repo, layout, {"run": True, "implementation_complete": True})
            self.assertIsNone(captured["wheel_dir"].wheel)
            self.assertIsNone(captured["wheel_dir"].sdist)
            untouched = fingerprints.artifact_report(repo, wheel_dir=captured["wheel_dir"])
            self.assertNotIn(_digest(b"stage-wheel"), {item.get("sha256") for item in untouched["items"]})
            self.assertIn("no trusted stage archive paths", " ".join(untouched["blockers"]))


if __name__ == "__main__":
    unittest.main()
