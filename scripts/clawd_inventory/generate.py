#!/usr/bin/env python3
"""离线依赖来源清单。只读调用方给出的文件，不下载、不扫描目录、不判定许可证。

验收入口: python3 scripts/clawd_inventory --offline
同等开关: --local-only
"network_audit": false
声明的 license 字符串只是元数据。缺字段保持 unknown，不套用核心 MIT。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path


SCHEMA_VERSION = 1
UNKNOWN = "unknown"

DEFAULT_RELATIVE_INPUTS = (
    "LICENSE",
    "NOTICE",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "apps/panel/package.json",
    "apps/panel/package-lock.json",
    "apps/panel/requirements-panel-core.txt",
    "ui-tui/package.json",
    "ui-tui/packages/hermes-ink/package.json",
    "apps/shared/package.json",
)

_FORBIDDEN_PARTS = frozenset({"node_modules", "venv", ".venv", "site-packages"})
_SPEC_OPS = ("===", "==", "!=", "<=", ">=", "~=", ">", "<")
_PROJECT_SOURCES = frozenset({"editable", "virtual"})
_NPM_GIT_PREFIXES = ("git+", "git://", "github:", "gitlab:", "bitbucket:")
_NPM_ROLE_FIELDS = (
    ("dependencies", "runtime"),
    ("devDependencies", "development"),
    ("optionalDependencies", "optional"),
    ("peerDependencies", "peer"),
)


def normalize_python_name(name: str) -> str:
    chars = []
    previous_dash = False
    for char in name.strip().lower():
        if char in "-_.":
            if not previous_dash:
                chars.append("-")
                previous_dash = True
        else:
            chars.append(char)
            previous_dash = False
    return "".join(chars).strip("-") or UNKNOWN


def split_pep508(text: str) -> dict[str, str]:
    raw = text.split("#", 1)[0].strip()
    if not raw:
        return {"name": UNKNOWN, "extras": UNKNOWN, "spec": UNKNOWN, "marker": UNKNOWN, "url": UNKNOWN}
    marker = UNKNOWN
    if ";" in raw:
        raw, marker_text = raw.split(";", 1)
        raw = raw.strip()
        marker = marker_text.strip() or UNKNOWN
    url = UNKNOWN
    if " @ " in raw:
        name_token, url_text = raw.split(" @ ", 1)
        return {
            "name": name_token.strip() or UNKNOWN,
            "extras": UNKNOWN,
            "spec": UNKNOWN,
            "marker": marker,
            "url": url_text.strip() or UNKNOWN,
        }
    spec = UNKNOWN
    for op in _SPEC_OPS:
        index = raw.find(op)
        if index != -1:
            spec = raw[index:].strip() or UNKNOWN
            raw = raw[:index].strip()
            break
    extras = UNKNOWN
    if "[" in raw:
        name, extra_text = raw.split("[", 1)
        extras = extra_text.strip().strip("[]") or UNKNOWN
        raw = name.strip()
    return {
        "name": raw or UNKNOWN,
        "extras": extras,
        "spec": spec,
        "marker": marker,
        "url": url,
    }


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _forbidden(path: Path) -> bool:
    text = path.as_posix()
    if "://" in text:
        return True
    return any(part in _FORBIDDEN_PARTS for part in path.parts)


def _input_record(path: Path, status: str, **extra: object) -> dict[str, object]:
    record: dict[str, object] = {"path": str(path), "status": status}
    record.update(extra)
    return record


def _read_bytes(path: Path) -> tuple[dict, bytes | None]:
    if _forbidden(path):
        return _input_record(path, "forbidden"), None
    try:
        if path.is_dir():
            return _input_record(path, "directory_refused"), None
        data = path.read_bytes()
    except FileNotFoundError:
        return _input_record(path, "absent"), None
    except OSError as exc:
        return _input_record(path, "unreadable", detail=type(exc).__name__), None
    return _input_record(path, "read", sha256=_sha256(data), bytes=len(data)), data


def _kind(path: Path) -> str:
    name = path.name
    if name in {"LICENSE", "NOTICE", "COPYING"}:
        return "notice"
    if name == "pyproject.toml":
        return "pyproject"
    if name == "uv.lock":
        return "uv_lock"
    if name == "package.json":
        return "npm_manifest"
    if name == "package-lock.json":
        return "npm_lock"
    if name == "requirements.txt":
        return "requirements"
    return UNKNOWN


def _license_field(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return UNKNOWN


def _requested_extras(value: object) -> list[str]:
    if not isinstance(value, str) or value in (UNKNOWN, ""):
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _dependency_row(**fields: object) -> dict:
    row = {
        "ecosystem": UNKNOWN,
        "name": UNKNOWN,
        "version": UNKNOWN,
        "relation": UNKNOWN,
        "install_class": UNKNOWN,
        "possible_install_classes": [],
        "scope_certainty": UNKNOWN,
        "packaged": UNKNOWN,
        "source": UNKNOWN,
        "source_type": UNKNOWN,
        "integrity": UNKNOWN,
        "license": UNKNOWN,
        "declared_license": UNKNOWN,
        "lock_path": UNKNOWN,
        "lock_file": UNKNOWN,
        "marker": UNKNOWN,
        "record_kind": UNKNOWN,
        "git_rev": UNKNOWN,
        "lock_dev": UNKNOWN,
        "lock_optional": UNKNOWN,
        "lock_peer": UNKNOWN,
        "artifacts": UNKNOWN,
        "lock_extras": UNKNOWN,
        "requested_extras": [],
    }
    row.update(fields)
    row["license"] = UNKNOWN
    row["packaged"] = UNKNOWN
    if row.get("declared_license") in ("", None):
        row["declared_license"] = UNKNOWN
    if row.get("marker") not in (UNKNOWN, "", None) and row.get("record_kind") == "manifest":
        role = row.get("install_class")
        if role not in (UNKNOWN, "", None):
            row["possible_install_classes"] = sorted(set(row.get("possible_install_classes") or []) | {str(role)})
        row["install_class"] = UNKNOWN
        row["scope_certainty"] = "marker_dependent"
    return row


def parse_requirements(text: str) -> list[dict]:
    logical: list[str] = []
    buffer = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            buffer = f"{buffer} {stripped[:-1]}".strip()
            continue
        buffer = f"{buffer} {stripped}".strip()
        logical.append(buffer)
        buffer = ""
    if buffer:
        logical.append(buffer)
    rows = []
    for item in logical:
        parts = item.split()
        if not parts or parts[0].startswith("-"):
            continue
        parsed = split_pep508(parts[0])
        hashes = [part.split("=", 1)[1] for part in parts if part.startswith("--hash=") and "=" in part]
        rows.append(
            _dependency_row(
                ecosystem="requirements",
                name=parsed["name"],
                normalized_name=normalize_python_name(parsed["name"]) if parsed["name"] != UNKNOWN else UNKNOWN,
                version=parsed["spec"].removeprefix("==") if parsed["spec"].startswith("==") else UNKNOWN,
                declared_spec=parsed["spec"],
                relation="direct",
                install_class="sidecar_pin",
                possible_install_classes=["sidecar_pin"],
                scope_certainty="declared",
                source=parsed["url"],
                source_type="requirements_pin",
                integrity=hashes or UNKNOWN,
                marker=parsed["marker"],
                record_kind="requirements",
            )
        )
    return rows


def parse_notice(text: str, path: Path) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    copyrights = [line for line in text.splitlines() if line.strip().lower().startswith("copyright")]
    return {
        "path": str(path),
        "heading": lines[0] if lines else UNKNOWN,
        "copyrights": copyrights,
        "applies_to_other_components": False,
    }


def _pyproject_declarations(data: dict, path: Path) -> tuple[list[dict], dict]:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    component = {
        "path": str(path),
        "distribution_name": project.get("name") or UNKNOWN,
        "manifest_license": _license_field(project.get("license")),
        "license": UNKNOWN,
        "authors": project.get("authors") if isinstance(project.get("authors"), list) else [],
    }
    rows: list[dict] = []

    def add(requirements: object, install_class: str) -> None:
        if not isinstance(requirements, list):
            return
        for requirement in requirements:
            if not isinstance(requirement, str):
                continue
            parsed = split_pep508(requirement)
            rows.append(
                _dependency_row(
                    ecosystem="pypi",
                    name=parsed["name"],
                    normalized_name=normalize_python_name(parsed["name"]) if parsed["name"] != UNKNOWN else UNKNOWN,
                    version=UNKNOWN,
                    declared_spec=parsed["spec"],
                    declared_requirement=requirement.strip(),
                    relation="direct",
                    install_class=install_class,
                    possible_install_classes=[install_class],
                    scope_certainty="declared",
                    source=parsed["url"],
                    source_type="manifest",
                    marker=parsed["marker"],
                    manifest=str(path),
                    record_kind="manifest",
                    requested_extras=_requested_extras(parsed["extras"]),
                )
            )

    add(project.get("dependencies"), "runtime")
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra, requirements in optional.items():
            before = len(rows)
            add(requirements, "optional")
            for row in rows[before:]:
                row["extra"] = str(extra)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group, requirements in groups.items():
            before = len(rows)
            add(requirements, "development")
            for row in rows[before:]:
                row["group"] = str(group)
    build = data.get("build-system")
    if isinstance(build, dict):
        add(build.get("requires"), "build")
    return rows, component


def _uv_extras(dep: dict) -> list[str]:
    raw = dep.get("extra", dep.get("extras"))
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str) and item.strip()]
    return []


def _uv_edge(dep: dict) -> dict:
    source = dep.get("source") if isinstance(dep.get("source"), dict) else {}
    source_type = ""
    source_value = ""
    if len(source) == 1:
        source_type = next(iter(source))
        value = source.get(source_type)
        source_value = value if isinstance(value, str) else ""
    name = dep.get("name") if isinstance(dep.get("name"), str) else ""
    return {
        "name": name,
        "norm": normalize_python_name(name) if name else "",
        "marker": dep.get("marker") or "",
        "extras": _uv_extras(dep),
        "version": dep.get("version") if isinstance(dep.get("version"), str) else "",
        "source_type": source_type,
        "source": source_value,
    }


def _uv_edge_table(value: object) -> dict[str, list[dict]]:
    if not isinstance(value, dict):
        return {}
    table: dict[str, list[dict]] = {}
    for key, items in value.items():
        if not isinstance(items, list):
            continue
        edges = [_uv_edge(item) for item in items if isinstance(item, dict) and isinstance(item.get("name"), str)]
        table[str(key)] = edges
    return table


def _git_rev(url: str) -> str:
    if not url:
        return UNKNOWN
    if "rev=" in url:
        rev = url.split("rev=", 1)[1].split("&", 1)[0].split("#", 1)[0]
        return rev or UNKNOWN
    if "#" in url:
        rev = url.rsplit("#", 1)[1]
        return rev or UNKNOWN
    return UNKNOWN


def _uv_source(source: dict) -> tuple[str, str]:
    if len(source) == 1:
        source_type = next(iter(source))
        value = source.get(source_type)
        if isinstance(value, str):
            return source_type, value
        return source_type, json.dumps(source, ensure_ascii=False, sort_keys=True)
    if not source:
        return UNKNOWN, UNKNOWN
    return UNKNOWN, json.dumps(source, ensure_ascii=False, sort_keys=True)


def _artifact(kind: str, item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url") if isinstance(item.get("url"), str) and item.get("url") else UNKNOWN
    digest = item.get("hash") if isinstance(item.get("hash"), str) and item.get("hash") else UNKNOWN
    if url == UNKNOWN and digest == UNKNOWN:
        return None
    return {"kind": kind, "url": url, "hash": digest}


def _uv_packages(data: dict) -> list[dict]:
    packages = data.get("package")
    if not isinstance(packages, list):
        return []
    rows = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        source = package.get("source") if isinstance(package.get("source"), dict) else {}
        source_type, source_value = _uv_source(source)
        hashes: list[str] = []
        artifacts: list[dict] = []
        sdist = _artifact("sdist", package.get("sdist"))
        if sdist:
            artifacts.append(sdist)
            if sdist["hash"] != UNKNOWN:
                hashes.append(sdist["hash"])
        wheels = package.get("wheels") if isinstance(package.get("wheels"), list) else []
        for wheel in wheels:
            artifact = _artifact("wheel", wheel)
            if not artifact:
                continue
            artifacts.append(artifact)
            if artifact["hash"] != UNKNOWN:
                hashes.append(artifact["hash"])
        deps = [
            _uv_edge(dep)
            for dep in (package.get("dependencies") or [])
            if isinstance(dep, dict) and isinstance(dep.get("name"), str)
        ]
        rows.append(
            {
                "name": package.get("name") or UNKNOWN,
                "norm": normalize_python_name(package["name"]) if isinstance(package.get("name"), str) else UNKNOWN,
                "version": package.get("version") or UNKNOWN,
                "source": source_value,
                "source_type": source_type,
                "integrity": hashes or UNKNOWN,
                "artifacts": artifacts or UNKNOWN,
                "git_rev": _git_rev(source_value) if source_type == "git" else UNKNOWN,
                "deps": deps,
                "optional_edges": _uv_edge_table(package.get("optional-dependencies")),
                "dev_edges": _uv_edge_table(package.get("dev-dependencies")),
                "resolution_markers": package.get("resolution-markers") or [],
                "editable": source_type in _PROJECT_SOURCES,
                "declared_license": _license_field(package.get("license")),
            }
        )
    return rows


def _reachability(packages: list[dict], seeds: dict[str, list[tuple[str, bool, tuple[str, ...]]]]) -> dict[str, dict]:
    runtime: dict[str, list[dict]] = {}
    optional: dict[str, dict[str, list[dict]]] = {}
    for package in packages:
        runtime.setdefault(package["norm"], []).extend(package.get("deps") or [])
        bucket = optional.setdefault(package["norm"], {})
        for extra, edges in (package.get("optional_edges") or {}).items():
            bucket.setdefault(extra, []).extend(edges)
    state: dict[str, dict[str, set[str]]] = {}
    seen: set[tuple[str, str, bool, tuple[str, ...]]] = set()
    stack: list[tuple[str, str, bool, tuple[str, ...]]] = []
    for scope, items in seeds.items():
        for norm, marked, extras in items:
            stack.append((norm, scope, bool(marked), tuple(extras)))
    while stack:
        norm, scope, marked, extras = stack.pop()
        key = (norm, scope, marked, extras)
        if not norm or key in seen:
            continue
        seen.add(key)
        entry = state.setdefault(norm, {"exact": set(), "marker": set()})
        entry["marker" if marked else "exact"].add(scope)
        for dep in runtime.get(norm, []):
            dep_norm = dep.get("norm") or ""
            if not dep_norm:
                continue
            stack.append((dep_norm, scope, marked or bool(dep.get("marker")), tuple(dep.get("extras") or [])))
        for extra in extras:
            for dep in optional.get(norm, {}).get(extra, []):
                dep_norm = dep.get("norm") or ""
                if not dep_norm:
                    continue
                stack.append((dep_norm, scope, marked or bool(dep.get("marker")), tuple(dep.get("extras") or [])))
    return state


def _class_from_reach(reach: dict | None, declared: set[str]) -> tuple[str, list[str], str]:
    classes = set(declared)
    certainty = "declared" if declared else UNKNOWN
    if reach:
        classes.update(reach["exact"])
        classes.update(reach["marker"])
        if reach["marker"] and not reach["exact"]:
            certainty = "marker_dependent"
        elif reach["marker"] and reach["exact"]:
            certainty = "marker_dependent"
        elif reach["exact"]:
            certainty = "exact"
    ordered = sorted(classes)
    if not ordered:
        return UNKNOWN, [], UNKNOWN
    if certainty == "marker_dependent":
        return UNKNOWN, ordered, certainty
    if len(ordered) == 1:
        return ordered[0], ordered, certainty
    return "mixed", ordered, certainty


def _add_seed(
    by_name: dict[str, set[str]],
    seeds: dict[str, list[tuple[str, bool, tuple[str, ...]]]],
    extra_names: dict[str, set[str]],
    norm: str,
    install_class: str,
    marked: bool,
    extras: list[str] | tuple[str, ...],
    extra_name: str = "",
) -> None:
    if not norm or norm == UNKNOWN or install_class in (UNKNOWN, "", None):
        return
    by_name.setdefault(norm, set()).add(install_class)
    seeds.setdefault(install_class, []).append((norm, marked, tuple(extras)))
    if extra_name:
        extra_names.setdefault(norm, set()).add(extra_name)


def _is_project_package(package: dict, project_norm: str) -> bool:
    if package.get("editable") or package.get("source_type") in _PROJECT_SOURCES:
        return True
    return bool(project_norm) and package.get("norm") == project_norm


def classify_python(declarations: list[dict], packages: list[dict], project_name: str) -> list[dict]:
    project_norm = normalize_python_name(project_name) if project_name and project_name != UNKNOWN else ""
    by_name: dict[str, set[str]] = {}
    seeds: dict[str, list[tuple[str, bool, tuple[str, ...]]]] = {}
    extra_names: dict[str, set[str]] = {}
    for row in declarations:
        norm = row.get("normalized_name")
        if not isinstance(norm, str) or norm == UNKNOWN:
            continue
        classes = {str(item) for item in (row.get("possible_install_classes") or []) if item not in (UNKNOWN, "", None)}
        install_class = row.get("install_class")
        if install_class not in (UNKNOWN, "", None):
            classes.add(str(install_class))
        classes.discard(UNKNOWN)
        marked = row.get("marker") not in (UNKNOWN, "", None)
        extras = tuple(row.get("requested_extras") or [])
        for role in classes:
            _add_seed(by_name, seeds, extra_names, norm, role, marked, extras)
    for package in packages:
        if not _is_project_package(package, project_norm):
            continue
        for edge in package.get("deps") or []:
            _add_seed(
                by_name,
                seeds,
                extra_names,
                edge.get("norm") or "",
                "runtime",
                bool(edge.get("marker")),
                edge.get("extras") or [],
            )
        for extra_name, edges in (package.get("optional_edges") or {}).items():
            for edge in edges:
                _add_seed(
                    by_name,
                    seeds,
                    extra_names,
                    edge.get("norm") or "",
                    "optional",
                    bool(edge.get("marker")),
                    edge.get("extras") or [],
                    extra_name,
                )
        for edges in (package.get("dev_edges") or {}).values():
            for edge in edges:
                _add_seed(
                    by_name,
                    seeds,
                    extra_names,
                    edge.get("norm") or "",
                    "development",
                    bool(edge.get("marker")),
                    edge.get("extras") or [],
                )
    reach = _reachability(packages, seeds) if packages else {}
    rows = []
    for package in packages:
        if _is_project_package(package, project_norm):
            continue
        declared = by_name.get(package["norm"], set())
        reached = reach.get(package["norm"])
        install_class, possible, certainty = _class_from_reach(reached, declared)
        if package.get("resolution_markers"):
            certainty = "marker_dependent"
            if install_class != UNKNOWN:
                possible = sorted(set(possible) | {install_class})
            install_class = UNKNOWN
        if declared:
            relation = "direct"
        elif reached:
            relation = "transitive"
        else:
            relation = UNKNOWN
        extras = sorted(extra_names.get(package["norm"], set()))
        rows.append(
            _dependency_row(
                ecosystem="pypi",
                name=package["name"],
                normalized_name=package["norm"],
                version=package["version"],
                relation=relation,
                install_class=install_class,
                possible_install_classes=possible,
                scope_certainty=certainty,
                source=package["source"],
                source_type=package["source_type"],
                integrity=package["integrity"],
                artifacts=package["artifacts"],
                git_rev=package["git_rev"],
                declared_license=package.get("declared_license") or UNKNOWN,
                resolution_markers=package["resolution_markers"],
                lock_extras=extras or UNKNOWN,
                record_kind="lock",
            )
        )
    return rows


def _npm_declarations(data: dict, path: Path) -> tuple[list[dict], dict]:
    component = {
        "path": str(path),
        "name": data.get("name") or UNKNOWN,
        "version": data.get("version") or UNKNOWN,
        "manifest_license": _license_field(data.get("license")),
        "license": UNKNOWN,
        "private": bool(data.get("private")),
    }
    rows = []
    for field, install_class in _NPM_ROLE_FIELDS:
        deps = data.get(field)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            spec_text = spec if isinstance(spec, str) else ""
            rows.append(
                _dependency_row(
                    ecosystem="npm",
                    name=str(name),
                    version=UNKNOWN,
                    declared_spec=spec_text or UNKNOWN,
                    relation="direct",
                    install_class=install_class,
                    possible_install_classes=[install_class],
                    scope_certainty="declared",
                    source=spec_text if spec_text.startswith(("file:", "git+", "git://", "github:")) else UNKNOWN,
                    source_type="manifest",
                    manifest=str(path),
                    record_kind="manifest",
                )
            )
    return rows, component


def _npm_source_type(resolved: str, linked: bool) -> str:
    if linked:
        return "link"
    if not resolved:
        return UNKNOWN
    lower = resolved.lower()
    if lower.startswith(_NPM_GIT_PREFIXES):
        return "git"
    if lower.startswith("file:"):
        return "file"
    if lower.startswith(("http://", "https://")):
        if "registry.npmjs.org/" in lower:
            return "registry"
        return "url"
    return UNKNOWN


def _merge_declared(*groups: dict[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for group in groups:
        for name, classes in group.items():
            merged.setdefault(name, set()).update(classes)
    return merged


def _npm_embedded_declared(packages: dict) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for key, meta in packages.items():
        if not isinstance(meta, dict):
            continue
        if key != "" and "node_modules/" in str(key):
            continue
        for field, install_class in _NPM_ROLE_FIELDS:
            deps = meta.get(field)
            if not isinstance(deps, dict):
                continue
            for name in deps:
                merged.setdefault(str(name), set()).add(install_class)
    return merged


def _npm_lock_rows(data: dict, declared_by_name: dict[str, set[str]], lock_file: str = UNKNOWN) -> list[dict]:
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return []
    declared = _merge_declared(declared_by_name, _npm_embedded_declared(packages))
    rows = []
    for key, meta in packages.items():
        if not isinstance(meta, dict) or key == "":
            continue
        if "node_modules/" not in key:
            continue
        name = meta.get("name") if isinstance(meta.get("name"), str) else ""
        if not name and "node_modules/" in key:
            name = key.rsplit("node_modules/", 1)[1]
        if not name:
            name = UNKNOWN
        linked = bool(meta.get("link"))
        resolved = meta.get("resolved") if isinstance(meta.get("resolved"), str) else ""
        source_type = _npm_source_type(resolved, linked)
        if linked:
            relation = "first_party"
        elif name in declared:
            relation = "direct"
        elif declared:
            relation = "transitive"
        else:
            relation = UNKNOWN
        classes = set(declared.get(name, set()))
        if meta.get("optional") is True:
            classes.add("optional")
        if meta.get("dev") is True:
            classes.add("development")
        if meta.get("peer") is True:
            classes.add("peer")
        if relation == "first_party":
            install_class, possible, certainty = UNKNOWN, [], UNKNOWN
        elif classes:
            install_class, possible, certainty = _class_from_reach(None, classes)
            if name not in declared:
                certainty = "lock_flag"
        else:
            install_class, possible, certainty = UNKNOWN, [], UNKNOWN
        dev = meta.get("dev")
        optional = meta.get("optional")
        peer = meta.get("peer")
        rows.append(
            _dependency_row(
                ecosystem="npm",
                name=name,
                version=meta.get("version") or UNKNOWN,
                relation=relation,
                install_class=install_class,
                possible_install_classes=possible,
                scope_certainty=certainty,
                lock_dev=dev if isinstance(dev, bool) else UNKNOWN,
                lock_optional=optional if isinstance(optional, bool) else UNKNOWN,
                lock_peer=peer if isinstance(peer, bool) else UNKNOWN,
                source=resolved or UNKNOWN,
                source_type=source_type,
                integrity=meta.get("integrity") or UNKNOWN,
                git_rev=_git_rev(resolved) if source_type == "git" else UNKNOWN,
                declared_license=_license_field(meta.get("license")),
                lock_path=key,
                lock_file=lock_file,
                record_kind="lock",
            )
        )
    return rows


def _apply_notice_license(components: list[dict], notices: list[dict]) -> None:
    parents = {
        Path(notice["path"]).parent
        for notice in notices
        if notice.get("heading") == "MIT License" and notice.get("copyrights")
    }
    for component in components:
        component["license"] = UNKNOWN
        if Path(component["path"]).parent in parents:
            component["license"] = "MIT"
            component["license_basis"] = "sibling_notice_file"


def _npm_declared_by_dir(rows: list[dict]) -> dict[Path, dict[str, set[str]]]:
    grouped: dict[Path, dict[str, set[str]]] = {}
    for row in rows:
        manifest = row.get("manifest")
        if not isinstance(manifest, str) or manifest in (UNKNOWN, ""):
            continue
        classes = {str(item) for item in (row.get("possible_install_classes") or []) if item not in (UNKNOWN, "", None)}
        install_class = row.get("install_class")
        if install_class not in (UNKNOWN, "", None):
            classes.add(str(install_class))
        classes.discard(UNKNOWN)
        if not classes:
            continue
        grouped.setdefault(Path(manifest).parent, {}).setdefault(str(row["name"]), set()).update(classes)
    return grouped


def build_inventory(paths: list[Path]) -> dict:
    inputs = []
    notices = []
    components = []
    py_declarations: list[dict] = []
    npm_declarations: list[dict] = []
    requirement_rows: list[dict] = []
    uv_locks: list[tuple[Path, list[dict]]] = []
    npm_locks: list[tuple[Path, dict]] = []
    project_name = UNKNOWN
    errors = False
    for path in paths:
        record, data = _read_bytes(path)
        record["kind"] = _kind(path)
        inputs.append(record)
        if record["status"] in {"forbidden", "directory_refused", "unreadable"}:
            errors = True
            continue
        if data is None:
            continue
        kind = record["kind"]
        try:
            if kind == "notice":
                notices.append(parse_notice(data.decode("utf-8"), path))
            elif kind == "pyproject":
                parsed = tomllib.loads(data.decode("utf-8"))
                rows, component = _pyproject_declarations(parsed, path)
                components.append(component)
                project_name = str(component.get("distribution_name") or UNKNOWN)
                py_declarations.extend(rows)
            elif kind == "uv_lock":
                uv_locks.append((path, _uv_packages(tomllib.loads(data.decode("utf-8")))))
            elif kind == "npm_manifest":
                parsed = json.loads(data.decode("utf-8"))
                rows, component = _npm_declarations(parsed, path)
                components.append(component)
                npm_declarations.extend(rows)
            elif kind == "npm_lock":
                npm_locks.append((path, json.loads(data.decode("utf-8"))))
            elif kind == "requirements":
                requirement_rows.extend(parse_requirements(data.decode("utf-8")))
            else:
                record["status"] = "unsupported"
        except (UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            record["status"] = "parse_error"
            record["detail"] = type(exc).__name__
            errors = True
    _apply_notice_license(components, notices)
    npm_by_dir = _npm_declared_by_dir(npm_declarations)
    dependencies = []
    dependencies.extend(py_declarations)
    for path, packages in uv_locks:
        for row in classify_python(py_declarations, packages, project_name):
            row["lock_file"] = str(path)
            dependencies.append(row)
    dependencies.extend(npm_declarations)
    for path, lock in npm_locks:
        dependencies.extend(_npm_lock_rows(lock, npm_by_dir.get(path.parent, {}), lock_file=str(path)))
    dependencies.extend(requirement_rows)
    dependencies.sort(
        key=lambda row: (
            str(row["ecosystem"]),
            str(row["name"]),
            str(row["version"]),
            str(row.get("lock_file", "")),
            str(row["lock_path"]),
        )
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "scripts/clawd_inventory/generate.py",
        "network_audit": False,
        "offline": True,
        "declared_license_is_metadata_only": True,
        "packaged_membership": UNKNOWN,
        "security_scan": False,
        "advisory_status": "not_scanned",
        "compliance_conclusion": "not_determined",
        "errors": errors,
        "inputs": inputs,
        "notices": notices,
        "components": components,
        "dependencies": dependencies,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线提取依赖名称、版本、来源与完整性。不下载，不扫描，不判定许可证。")
    parser.add_argument("--offline", "--local-only", action="store_true", help="只读本地文件。不联网。")
    parser.add_argument("--checkout", type=Path)
    parser.add_argument("--input", action="append", default=[], type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    paths = [Path(item) for item in args.input]
    if args.checkout:
        paths.extend(args.checkout / relative for relative in DEFAULT_RELATIVE_INPUTS)
    if args.offline and not paths:
        checkout = Path(__file__).resolve().parents[2]
        paths.extend(checkout / relative for relative in DEFAULT_RELATIVE_INPUTS)
    if not paths:
        parser.error("需要 --checkout、--offline 或至少一个 --input")
    if args.output and _forbidden(args.output):
        print("refusing output path", file=sys.stderr)
        return 2
    document = build_inventory(paths)
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 2 if document["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
