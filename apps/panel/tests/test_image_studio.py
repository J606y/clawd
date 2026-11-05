"""生图模式：模型白名单、请求体、落盘和删除边界。不访问真实上游。"""

from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sidecar import image_studio as studio


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SECRET = "sk-testsecretvalue123456"


def config() -> dict:
    return {
        "providers": {
            "sub2api-codex": {"base_url": "https://api.example.test/v1", "key_env": "SUB2API_CODEX_API_KEY"},
            "sub2api-gemini": {"base_url": "https://api.example.test/v1", "key_env": "SUB2API_GEMINI_API_KEY"},
            "sub2api-grok": {"base_url": "https://api.example.test/v1", "key_env": "SUB2API_GROK_API_KEY"},
        }
    }


def secrets() -> dict[str, str]:
    return {
        "SUB2API_CODEX_API_KEY": SECRET,
        "SUB2API_GEMINI_API_KEY": SECRET,
        "SUB2API_GROK_API_KEY": SECRET,
    }


class CatalogTests(unittest.TestCase):
    def test_lists_only_configured_image_models_and_hides_secrets(self) -> None:
        listed = studio.available_models(config(), secrets())
        ids = [item["id"] for item in listed]
        self.assertEqual(ids, [
            "gpt-image-2.5-sunburst",
            "gemini-3.1-flash-image",
            "grok-imagine-image-2.0",
        ])
        self.assertNotIn("gpt-image-2", ids)
        self.assertNotIn("grok-imagine-image", ids)
        self.assertNotIn("grok-imagine-image-quality", ids)
        blob = str(listed)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("base_url", blob)
        self.assertNotIn("key_env", blob)

    def test_omits_provider_without_key_or_https_base(self) -> None:
        cfg = config()
        cfg["providers"]["sub2api-grok"]["base_url"] = "http://insecure.example/v1"
        listed = studio.available_models(cfg, {"SUB2API_CODEX_API_KEY": SECRET})
        ids = {item["id"] for item in listed}
        self.assertEqual(ids, {"gpt-image-2.5-sunburst"})

    def test_env_file_does_not_override_process_env_and_ignores_other_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("SUB2API_CODEX_API_KEY=from-file\nOTHER_SECRET=do-not-read\n", encoding="utf-8")
            found = studio.read_named_secrets(path, {"SUB2API_CODEX_API_KEY"}, {"SUB2API_CODEX_API_KEY": "from-env"})
        self.assertEqual(found, {"SUB2API_CODEX_API_KEY": "from-env"})


class RequestTests(unittest.TestCase):
    def test_sunburst_sends_quality_and_weaker_ids_are_absent(self) -> None:
        sunburst = studio._spec("gpt-image-2.5-sunburst")
        sun_path, sun_body = studio.build_request(sunburst, "一只猫", "landscape")
        self.assertEqual(sun_path, "/images/generations")
        self.assertEqual(sun_body["quality"], "auto")
        self.assertEqual(sun_body["size"], "1536x1024")
        with self.assertRaises(studio.ImageStudioError):
            studio._spec("gpt-image-2")
        with self.assertRaises(studio.ImageStudioError):
            studio._spec("grok-imagine-image-quality")

    def test_grok_uses_images_api_and_gemini_uses_chat(self) -> None:
        grok_path, grok_body = studio.build_request(studio._spec("grok-imagine-image-2.0"), "山", "square")
        gemini_path, gemini_body = studio.build_request(studio._spec("gemini-3.1-flash-image"), "山", "portrait")
        self.assertEqual(grok_path, "/images/generations")
        self.assertEqual(grok_body["aspect_ratio"], "1:1")
        self.assertEqual(grok_body["response_format"], "b64_json")
        self.assertNotIn("quality", grok_body)
        self.assertEqual(gemini_path, "/chat/completions")
        self.assertEqual(gemini_body["messages"][0]["content"], "山\n画面比例：9:16。")
        self.assertNotIn("/images/", gemini_path)

    def test_unknown_model_and_aspect_rejected(self) -> None:
        with self.assertRaises(studio.ImageStudioError) as unknown:
            studio._spec("gpt-image-2.5-flare")
        self.assertEqual(unknown.exception.status, 400)
        with self.assertRaises(studio.ImageStudioError):
            studio.build_request(studio._spec("gpt-image-2.5-sunburst"), "山", "wide")


class ParseTests(unittest.TestCase):
    def test_extracts_b64_and_gemini_data_uri_and_rejects_text(self) -> None:
        encoded = base64.b64encode(PNG).decode()
        found = studio.extract_image("openai-images", {"data": [{"b64_json": encoded}]})
        self.assertEqual(found, (PNG, "png"))
        chat = studio.extract_image("gemini-chat", {"choices": [{"message": {"content": [
            {"type": "text", "text": "好的"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
        ]}}]})
        self.assertEqual(chat[1], "png")
        self.assertIsNone(studio.extract_image("gemini-chat", {"choices": [{"message": {"content": "没有图"}}]}))
        self.assertIsNone(studio.extract_image("openai-images", {"data": [{"b64_json": base64.b64encode(b"<html>").decode()}] }))

    def test_https_image_url_only(self) -> None:
        self.assertEqual(studio.image_url({"data": [{"url": "https://cdn.example/a.png"}]}), "https://cdn.example/a.png")
        self.assertEqual(studio.image_url({"data": [{"url": "http://cdn.example/a.png"}]}), "")
        self.assertEqual(studio.image_url({"data": [{"url": "https://user:pass@cdn.example/a.png"}]}), "")

    def test_upstream_auth_failure_is_not_panel_401(self) -> None:
        denied = studio._http_error(401, "Bearer super-secret")
        self.assertEqual(denied.status, 502)
        self.assertNotIn("super-secret", denied.message)
        rejected = studio._http_error(400, "bad Bearer super-secret")
        self.assertEqual(rejected.status, 400)
        self.assertIn("[redacted]", rejected.message)
        self.assertNotIn("super-secret", rejected.message)

    def test_sanitize_strips_bearer(self) -> None:
        self.assertNotIn("super-secret", studio.sanitize("nope Bearer super-secret tail"))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.uploads = Path(self.tmp.name) / "uploads"
        self.uploads.mkdir()
        self.db = Path(self.tmp.name) / "history.db"
        self.runtime = studio.Runtime(config(), secrets(), self.uploads, self.db)

    def tearDown(self) -> None:
        if studio._GEN_LOCK.locked():
            studio._GEN_LOCK.release()
        self.tmp.cleanup()

    def test_generate_saves_under_studio_and_response_has_no_secret(self) -> None:
        encoded = base64.b64encode(PNG).decode()
        seen: dict[str, object] = {}

        class Fake:
            def post_json(self, url, headers, payload, timeout):
                seen["url"] = url
                seen["auth"] = headers["Authorization"]
                seen["payload"] = payload
                return {"data": [{"b64_json": encoded}]}

            def get_bytes(self, url, timeout, limit):
                raise AssertionError("b64 response must not download a url")

        result = studio.generate(
            {"model": "gpt-image-2.5-sunburst", "prompt": "一只猫", "aspect": "square"},
            self.runtime,
            transport=Fake(),
            now=lambda: 1_700_000_000,
            new_id=lambda: "img_" + "ab" * 8,
            day="2026-10-29",
        )
        item = result["item"]
        self.assertTrue(item["absPath"].endswith("/uploads/image-studio/2026-10-29/img_abababababababab.png"))
        self.assertTrue(Path(item["absPath"]).is_file())
        self.assertEqual(Path(item["absPath"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(seen["url"], "https://api.example.test/v1/images/generations")
        self.assertEqual(seen["payload"]["quality"], "auto")
        self.assertNotIn(SECRET, str(result))
        self.assertEqual(studio.list_history(self.db)[0]["id"], item["id"])

    def test_url_download_does_not_send_key(self) -> None:
        seen: dict[str, object] = {}

        class Fake:
            def post_json(self, url, headers, payload, timeout):
                return {"data": [{"url": "https://cdn.example/pic.png"}]}

            def get_bytes(self, url, timeout, limit):
                seen["url"] = url
                seen["called"] = True
                return PNG

        studio.generate(
            {"model": "grok-imagine-image-2.0", "prompt": "海", "aspect": "landscape"},
            self.runtime,
            transport=Fake(),
            now=lambda: 1_700_000_000,
            new_id=lambda: "img_" + "cd" * 8,
            day="2026-10-29",
        )
        self.assertEqual(seen["url"], "https://cdn.example/pic.png")

    def test_upstream_failure_is_recorded_and_unknown_model_is_not(self) -> None:
        class Fake:
            def post_json(self, url, headers, payload, timeout):
                raise studio.ImageStudioError(404, "model_unavailable", "当前分组不支持这个模型")

        with self.assertRaises(studio.ImageStudioError):
            studio.generate(
                {"model": "gpt-image-2.5-sunburst", "prompt": "海", "aspect": "square"},
                self.runtime,
                transport=Fake(),
                now=lambda: 10,
                new_id=lambda: "img_" + "ef" * 8,
                day="2026-10-29",
            )
        self.assertEqual(studio.list_history(self.db)[0]["status"], "error")
        with self.assertRaises(studio.ImageStudioError):
            studio.generate({"model": "not-a-model", "prompt": "海", "aspect": "square"}, self.runtime, transport=Fake())
        self.assertEqual(len(studio.list_history(self.db)), 1)

    def test_delete_refuses_escape_and_removes_only_studio_file(self) -> None:
        encoded = base64.b64encode(PNG).decode()

        class Fake:
            def post_json(self, url, headers, payload, timeout):
                return {"data": [{"b64_json": encoded}]}

        result = studio.generate(
            {"model": "gpt-image-2.5-sunburst", "prompt": "树", "aspect": "square"},
            self.runtime,
            transport=Fake(),
            now=lambda: 20,
            new_id=lambda: "img_" + "11" * 8,
            day="2026-10-29",
        )
        path = Path(result["item"]["absPath"])
        outside = self.uploads / "keep.txt"
        outside.write_text("keep", encoding="utf-8")
        with self.assertRaises(studio.ImageStudioError):
            studio.unlink_studio_file(self.uploads, str(outside))
        with self.assertRaises(studio.ImageStudioError):
            studio.unlink_studio_file(self.uploads, str(self.uploads / "image-studio" / ".." / "keep.txt"))
        self.assertTrue(outside.exists())
        studio.delete_history(self.db, self.uploads, result["item"]["id"])
        self.assertFalse(path.exists())
        self.assertEqual(studio.list_history(self.db), [])
        self.assertTrue(outside.exists())

    def test_second_generate_is_busy(self) -> None:
        self.assertTrue(studio._GEN_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(studio.ImageStudioError) as busy:
                studio.generate(
                    {"model": "gpt-image-2.5-sunburst", "prompt": "海", "aspect": "square"},
                    self.runtime,
                    transport=object(),
                )
            self.assertEqual(busy.exception.status, 429)
        finally:
            studio._GEN_LOCK.release()

    def test_symlink_entry_is_removed_without_deleting_its_target(self) -> None:
        target = self.uploads / "secret.png"
        target.write_bytes(PNG)
        folder = self.uploads / "image-studio" / "2026-10-29"
        folder.mkdir(parents=True)
        link = folder / "img_2222222222222222.png"
        link.symlink_to(target)
        studio.unlink_studio_file(self.uploads, str(link))
        self.assertFalse(link.exists())
        self.assertFalse(link.is_symlink())
        self.assertTrue(target.exists())


class RouteTests(unittest.TestCase):
    def test_sidecar_registers_image_routes(self) -> None:
        source = Path(__file__).parents[1].joinpath("sidecar", "hermes_panel_sidecar.py").read_text(encoding="utf-8")
        for path in ("/image/models", "/image/history", "/image/generate", "/image/delete"):
            self.assertIn(path, source)

    def test_isolated_python_loads_sibling_image_studio(self) -> None:
        sidecar = Path(__file__).parents[1].joinpath("sidecar", "hermes_panel_sidecar.py")
        probe = r'''
import ast, sys
from pathlib import Path
source = Path(sys.argv[1]).read_text(encoding="utf-8")
tree = ast.parse(source)
fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_image_studio")
ns = {"ApiError": type("ApiError", (Exception,), {}), "__file__": sys.argv[1], "__package__": None}
exec(compile(ast.Module(body=[fn], type_ignores=[]), sys.argv[1], "exec"), ns)
loaded = ns["_image_studio"]()
if not hasattr(loaded, "available_models"):
    raise SystemExit("missing available_models")
'''
        completed = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(sidecar)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
