"""Explicit provider-config inspection. Values are not returned or logged.
"""

from __future__ import annotations

from pathlib import Path

from gate import forbidden_path


_PLACEHOLDERS = {
    "",
    "changeme",
    "your-api-key",
    "your_api_key",
    "todo",
    "placeholder",
    "sk-test",
    "xxx",
    "null",
    "none",
}
_KEY_NAMES = ("api_key", "apikey", "token", "secret", "key")


def inspect_provider(path: Path | None) -> dict:
    if path is None:
        return {"ok": False, "detail": "no --provider-config; production credentials were not read"}
    if path.is_symlink() or forbidden_path(path) or not path.is_file():
        return {"ok": False, "detail": "provider config missing, linked, or forbidden; not read"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"ok": False, "detail": "provider config unreadable; production credentials were not used"}
    if _has_real_credential(text):
        return {"ok": True, "detail": "explicit config has a non-placeholder credential; value not recorded", "values_recorded": False}
    return {"ok": False, "detail": "真实付费配置缺失或只有占位符；未当作通过"}


def _has_real_credential(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip().lower() not in _KEY_NAMES:
            continue
        cleaned = value.strip().strip("\"'").strip()
        if cleaned.lower() in _PLACEHOLDERS or len(cleaned) < 12:
            continue
        return True
    return False
