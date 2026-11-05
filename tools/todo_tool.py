"""Todo tool: in-memory, revisioned task list for multi-step work. State lives on the
AIAgent (one per session), is re-injected after context compression, and every write bumps
a monotonic revision so UI clients can reject stale updates. One ``todo_list`` tool: pass
``todos`` to write, omit to read; every call returns the full list. No system-prompt mutation."""

import json
from typing import Any, Dict, List, Optional

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
# Acceptance status stays parent-owned. These are display-only execution states.
EXECUTION_STATUSES = frozenset({
    "running", "stalled", "returned", "failed", "cancelled", "interrupted", "unknown",
})
_LINK_KEYS = ("delegation_id", "child_id")
MAX_TODO_LINKS = 32
MAX_TODO_LINK_CHARS = 256
# The list is re-read after every compression (format_for_injection), so unbounded
# content/count would defeat the compression it rides through. Caps apply equally to
# model-authored items and caller-replayed API history.
MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
# Max single todo tool-result payload accepted during history hydration, so a forged
# oversized result is dropped before parsing (AIAgent._hydrate_todo_store).
MAX_TODO_RESULT_CHARS = 512_000
_TRUNCATION_MARKER = "… [truncated]"
# Persisted as ordinary message content; ContextCompressor keys on this stable header to
# tell the synthetic post-compaction row from a real user message.
TODO_INJECTION_HEADER = "[Your active task list was preserved across context compression]"
_STATUS_MARKERS = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]", "cancelled": "[~]"}
_ACTIVE_STATUSES = {"pending", "in_progress"}


def _clean_link_id(value: Any) -> str:
    """Explicit id only. Empty, non-text, overlong, or multiline values are not links."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > MAX_TODO_LINK_CHARS or any(ch in text for ch in "\n\r\x00"):
        return ""
    return text


def _link_from_mapping(entry: Any) -> dict[str, str]:
    if not isinstance(entry, dict):
        return {}
    link: dict[str, str] = {}
    for key in _LINK_KEYS:
        text = _clean_link_id(entry.get(key))
        if text:
            link[key] = text
    return link


def _dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for link in links:
        key = (link.get("delegation_id", ""), link.get("child_id", ""))
        if key in seen or key == ("", ""):
            continue
        seen.add(key)
        out.append(dict(link))
        if len(out) >= MAX_TODO_LINKS:
            break
    return out


def _links_from_item(item: dict[str, Any]) -> tuple[bool, list[dict[str, str]]]:
    """``(specified, links)``. A present ``delegations`` key replaces scalars."""
    if "delegations" in item:
        raw = item.get("delegations")
        links = [_link_from_mapping(entry) for entry in raw] if isinstance(raw, list) else []
        return True, _dedupe_links([link for link in links if link])
    if "delegation_id" not in item and "child_id" not in item:
        return False, []
    link = _link_from_mapping(item)
    return True, ([link] if link else [])


def _upsert_link(existing: list[dict[str, str]], link: dict[str, str]) -> None:
    delegation_id = link.get("delegation_id", "")
    child_id = link.get("child_id", "")
    kept = [
        old for old in existing
        if not (
            (delegation_id and old.get("delegation_id") == delegation_id)
            or (child_id and old.get("child_id") == child_id)
        )
    ]
    kept.append(link)
    existing[:] = kept


def _merge_link_scalars(cur: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Update one explicit link. Several existing links plus one scalar is left unchanged."""
    provided: dict[str, str] = {}
    for key in _LINK_KEYS:
        if key not in incoming or not isinstance(incoming.get(key), str):
            continue
        provided[key] = str(incoming.get(key)).strip()
    if not provided:
        return
    existing = [dict(link) for link in cur.get("delegations") or []]
    if set(provided) == set(_LINK_KEYS):
        if not provided["delegation_id"] and not provided["child_id"]:
            cur.pop("delegations", None)
            return
        link = {key: _clean_link_id(value) for key, value in provided.items()}
        link = {key: value for key, value in link.items() if value}
        if not link:
            return
        _upsert_link(existing, link)
        cur["delegations"] = _dedupe_links(existing)
        return
    key, raw = next(iter(provided.items()))
    value = _clean_link_id(raw) if raw else ""
    if len(existing) > 1:
        return
    if not existing:
        if value:
            cur["delegations"] = [{key: value}]
        return
    if value:
        existing[0][key] = value
        cur["delegations"] = _dedupe_links(existing)
        return
    existing[0].pop(key, None)
    if existing[0]:
        cur["delegations"] = existing
    else:
        cur.pop("delegations", None)


def _copy_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    copied = {key: value for key, value in item.items() if key not in {"delegations", "execution"}}
    links = item.get("delegations")
    if links:
        copied["delegations"] = [dict(link) for link in links if isinstance(link, dict)]
    return copied


def _execution_text(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in record:
            return _clean_link_id(record.get(key))
    return ""


def _fold_execution(states: list[str]) -> Optional[str]:
    """One item, many children. Running wins; a failure never becomes acceptance."""
    if not states:
        return None
    if "running" in states:
        return "running"
    if "failed" in states:
        return "failed"
    if "interrupted" in states:
        return "interrupted"
    if "cancelled" in states:
        return "cancelled"
    if "missing" in states or "unknown" in states:
        return "unknown"
    if "stalled" in states:
        return "stalled"
    if all(state == "returned" for state in states):
        return "awaiting_acceptance"
    return "unknown"


def _pick_execution(matched: list[str]) -> Optional[str]:
    """Collapse records for one link. Display names such as awaiting_acceptance are not inputs."""
    if not matched:
        return None
    if "running" in matched:
        return "running"
    if "failed" in matched:
        return "failed"
    if "interrupted" in matched:
        return "interrupted"
    if "cancelled" in matched:
        return "cancelled"
    if "unknown" in matched:
        return "unknown"
    if "stalled" in matched:
        return "stalled"
    if all(state == "returned" for state in matched):
        return "returned"
    return "unknown"


def _match_execution(records: list[dict[str, str]], item_id: str, delegation_id: str, child_id: str) -> Optional[str]:
    matched: list[str] = []
    for record in records:
        if record.get("todo_id") and record["todo_id"] != item_id:
            continue
        if delegation_id and record.get("delegation_id") and record["delegation_id"] != delegation_id:
            continue
        if child_id and record.get("child_id") and record["child_id"] != child_id:
            continue
        by_delegation = bool(delegation_id and record.get("delegation_id") == delegation_id)
        by_child = bool(child_id and record.get("child_id") == child_id)
        if by_delegation or by_child:
            matched.append(record.get("status") or "unknown")
    return _pick_execution(matched)


def project_todo_execution(
    items: Optional[list[dict[str, Any]]],
    executions: Any,
    *,
    session_id: str,
    profile_key: str,
) -> list[dict[str, Any]]:
    """Overlay execution display. Does not write acceptance status or keep a cache.

    Records from another session or profile are ignored. An id claimed by more
    than one item matches nothing. Unlinked items stay as stored.
    """
    public = [_copy_todo_item(item) for item in items or [] if isinstance(item, dict)]
    if not isinstance(session_id, str) or not session_id.strip():
        return public
    if not isinstance(profile_key, str) or not profile_key.strip():
        return public
    if not isinstance(executions, list):
        return public
    session_id = session_id.strip()
    profile_key = profile_key.strip()
    claims_d: dict[str, set[str]] = {}
    claims_c: dict[str, set[str]] = {}
    for item in public:
        item_id = str(item.get("id") or "")
        for link in item.get("delegations") or []:
            if not isinstance(link, dict):
                continue
            delegation_id = _clean_link_id(link.get("delegation_id"))
            child_id = _clean_link_id(link.get("child_id"))
            if delegation_id:
                claims_d.setdefault(delegation_id, set()).add(item_id)
            if child_id:
                claims_c.setdefault(child_id, set()).add(item_id)
    ambiguous_d = {key for key, owners in claims_d.items() if len(owners) != 1}
    ambiguous_c = {key for key, owners in claims_c.items() if len(owners) != 1}
    scoped: list[dict[str, str]] = []
    for raw in executions:
        if not isinstance(raw, dict):
            continue
        rec_session = _execution_text(raw, "session_id", "sessionId")
        rec_profile = _execution_text(raw, "profile_key", "profileKey")
        if rec_session != session_id or rec_profile != profile_key:
            continue
        status = str(raw.get("executionStatus") or raw.get("status") or "").strip().lower()
        scoped.append({
            "delegation_id": _execution_text(raw, "delegation_id", "delegationId"),
            "child_id": _execution_text(raw, "child_id", "childId"),
            "todo_id": _execution_text(raw, "todo_id", "todoId"),
            "status": status if status in EXECUTION_STATUSES else "unknown",
        })
    for item in public:
        if item.get("status") in {"completed", "cancelled"}:
            continue
        links = [link for link in item.get("delegations") or [] if isinstance(link, dict)]
        if not links:
            continue
        states: list[str] = []
        item_id = str(item.get("id") or "")
        for link in links:
            delegation_id = _clean_link_id(link.get("delegation_id"))
            child_id = _clean_link_id(link.get("child_id"))
            if (delegation_id and delegation_id in ambiguous_d) or (child_id and child_id in ambiguous_c):
                states.append("unknown")
                continue
            states.append(_match_execution(scoped, item_id, delegation_id, child_id) or "missing")
        folded = _fold_execution(states)
        if folded:
            item["execution"] = folded
    return public


_LINK_SUFFIX_CAP = 512


def _link_suffix(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for link in item.get("delegations") or []:
        if not isinstance(link, dict):
            continue
        bits = []
        if link.get("delegation_id"):
            bits.append(f"delegation_id={link['delegation_id']}")
        if link.get("child_id"):
            bits.append(f"child_id={link['child_id']}")
        if bits:
            parts.append(" ".join(bits))
    if not parts:
        return ""
    suffix = " [" + "; ".join(parts) + "]"
    if len(suffix) > _LINK_SUFFIX_CAP:
        return suffix[:_LINK_SUFFIX_CAP - 1] + "…]"
    return suffix


class TodoStore:
    """In-memory todo list, one per AIAgent. List position is priority; items are
    ``{id, content, status, parent?, delegations?}``. ``parent`` nests a subtask.
    ``delegations`` is an explicit todo-to-child link, not an acceptance status."""

    def __init__(self):
        self._items: list[dict[str, str]] = []
        self._revision = 0

    def _fresh_items(self, todos: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Validate, dedupe and order a whole new list (replace / restore)."""
        return self._normalize_order([self._validate(t) for t in self._dedupe_by_id(todos)])

    def write(self, todos: list[dict[str, Any]], merge: bool = False) -> list[dict[str, str]]:
        """Replace the list (default) or merge by id; returns the full list after writing.
        Raises ValueError (leaving the list untouched) if any item is invalid."""
        before = self.read()
        try:
            if merge:
                self._merge(todos)
            else:
                self._items = self._fresh_items(todos)
        except ValueError:
            self._items = before  # rejected write must not leave a partial list behind
            raise
        del self._items[MAX_TODO_ITEMS:]  # keep the priority head; replays can't grow unbounded
        self._sanitize_parents(self._items)
        if self._items != before:
            self._revision += 1
        return self.read()

    def _merge(self, todos: list[dict[str, Any]]) -> None:
        """Update existing items only in the fields provided; append new ones (validated)."""
        existing = {item["id"]: item for item in self._items}
        for t in self._dedupe_by_id(todos):
            item_id = str(t.get("id", "")).strip()
            if not item_id:
                continue  # can't merge without an id
            cur = existing.get(item_id)
            if cur is None:
                validated = self._validate(t)
                existing[validated["id"]] = validated
                self._items.append(validated)
                continue
            if t.get("content"):
                cur["content"] = self._cap_content(str(t["content"]).strip())
            if t.get("status") and str(t["status"]).strip().lower() in VALID_STATUSES:
                cur["status"] = str(t["status"]).strip().lower()
            if "parent" in t:
                parent = str(t["parent"] or "").strip()
                if parent:
                    cur["parent"] = parent
                else:
                    cur.pop("parent", None)
            if "delegations" in t:
                _specified, links = _links_from_item({"delegations": t.get("delegations")})
                if links:
                    cur["delegations"] = links
                else:
                    cur.pop("delegations", None)
            elif "delegation_id" in t or "child_id" in t:
                _merge_link_scalars(cur, t)
        # Rebuild preserving original order for existing items (first occurrence wins).
        rebuilt = {item["id"]: existing.get(item["id"], item) for item in self._items}
        self._items = self._normalize_order(list(rebuilt.values()))

    def read(self) -> list[dict[str, Any]]:
        return [_copy_todo_item(item) for item in self._items]

    def has_items(self) -> bool:
        return bool(self._items)

    def snapshot(self) -> dict[str, Any]:
        """Full state clients can reconcile atomically."""
        return {"todos": self.read(), "revision": self._revision}

    def restore(self, todos: list[dict[str, Any]], *, revision: Any = 0) -> list[dict[str, str]]:
        """Restore a trusted snapshot without manufacturing a new revision."""
        self._items = self._fresh_items(todos)[:MAX_TODO_ITEMS]
        try:
            self._revision = max(0, int(revision or 0))
        except (TypeError, ValueError):
            self._revision = 0
        return self.read()

    def format_for_injection(self) -> Optional[str]:
        """Render the list for post-compression injection, or None if nothing active. Only
        pending/in_progress items are injected — finished ones make the model re-do work after
        compression. A parent is kept (with its real status marker) when any descendant is
        active so subtasks keep context."""
        if not self._items:
            return None
        children: dict[str, list[dict[str, str]]] = {}
        for item in self._items:
            if item.get("parent"):
                children.setdefault(item["parent"], []).append(item)

        def render(item: dict[str, str], depth: int, out: list[str]) -> bool:
            kid_lines: list[str] = []
            has_active_kid = False
            for kid in children.get(item["id"], []):
                has_active_kid |= render(kid, depth + 1, kid_lines)
            keep = item["status"] in _ACTIVE_STATUSES or has_active_kid
            if keep:
                marker = _STATUS_MARKERS.get(item["status"], "[?]")
                out.append(f"{'  ' * depth}- {marker} {item['id']}. "
                           f"{item['content']} ({item['status']}){_link_suffix(item)}")
                out.extend(kid_lines)
            return keep

        lines = [TODO_INJECTION_HEADER]
        for item in self._items:
            if not item.get("parent"):
                render(item, 0, lines)
        return "\n".join(lines) if len(lines) > 1 else None

    @staticmethod
    def _cap_content(content: str) -> str:
        """Truncate to MAX_TODO_CONTENT_CHARS keeping the head (the actionable part) + marker."""
        if len(content) > MAX_TODO_CONTENT_CHARS:
            return content[:MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
        return content

    @staticmethod
    def _validate(item: dict[str, Any]) -> dict[str, str]:
        """Normalize one item to ``{id, content, status, parent?}`` (placeholders when missing)."""
        if not isinstance(item, dict):
            return {"id": "?", "content": "(invalid item)", "status": "pending"}
        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip()
        if not content:
            raise ValueError(
                f"Todo item '{item_id}' has empty or missing content. "
                "Each item must have a non-empty description."
            )
        status = str(item.get("status", "pending")).strip().lower()
        result = {"id": item_id,
                  "content": TodoStore._cap_content(content),
                  "status": status if status in VALID_STATUSES else "pending"}
        parent = str(item.get("parent") or "").strip()
        if parent and parent != item_id:
            result["parent"] = parent
        _specified, links = _links_from_item(item)
        if links:
            result["delegations"] = links
        return result

    @staticmethod
    def _sanitize_parents(items: list[dict[str, str]]) -> None:
        """Drop dangling parent refs and break cycles in place (such items become roots)."""
        by_id = {item["id"]: item for item in items}
        for item in items:
            if item.get("parent") and item["parent"] not in by_id:
                item.pop("parent", None)
        for item in items:
            seen, node = {item["id"]}, item
            while node.get("parent"):
                if node["parent"] in seen:
                    item.pop("parent", None)
                    break
                seen.add(node["parent"])
                node = by_id[node["parent"]]

    @staticmethod
    def _dedupe_by_id(todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Collapse duplicate ids, keeping the last occurrence in its position."""
        last_index: dict[str, int] = {}
        for i, item in enumerate(todos):  # non-dicts get a synthetic key; _validate handles them
            key = str(item.get("id", "")).strip() if isinstance(item, dict) else f"__invalid_{i}"
            last_index[key or "?"] = i
        return [todos[i] for i in sorted(last_index.values())]

    @staticmethod
    def _normalize_order(items: list[dict[str, str]]) -> list[dict[str, str]]:
        """Lift the in_progress step ahead of any earlier pending placeholder. Nested lists
        keep authored order — reordering would tear a subtask from its siblings."""
        statuses = [item["status"] for item in items]
        if any(item.get("parent") for item in items) or "in_progress" not in statuses:
            return items
        active_index = statuses.index("in_progress")
        if "pending" not in statuses[:active_index]:
            return items
        normalized = items.copy()
        normalized.insert(statuses.index("pending"), normalized.pop(active_index))
        return normalized


def todo_tool(todos: Optional[list[dict[str, Any]]] = None, merge: bool = False,
              store: Optional[TodoStore] = None) -> str:
    """Write ``todos`` (replace, or ``merge`` by id) or read when None -> list + summary JSON."""
    if store is None:
        return tool_error("TodoStore not initialized")
    if todos is None:
        items = store.read()
    else:
        if not isinstance(todos, list):
            return tool_error(f"todos must be a list, got {type(todos).__name__}")
        try:
            items = store.write(todos, merge)
        except ValueError as e:
            return tool_error(str(e))
    summary = {"total": len(items)}
    for status in ("pending", "in_progress", "completed", "cancelled"):
        summary[status] = sum(1 for i in items if i["status"] == status)
    return json.dumps({"todos": items, "revision": store.snapshot()["revision"],
                       "summary": summary}, ensure_ascii=False)


def check_todo_requirements() -> bool:
    """Todo tool has no external requirements -- always available."""
    return True


# Behavioral guidance is baked into the (static, cached) description; item shape and merge
# semantics live ONLY in the parameter schema.
TODO_SCHEMA = {
    "name": "todo_list",
    "description": (
        # See #95681.
        "Track a task list for multi-step work (3+ steps). Use for complex tasks "
        "with 3+ steps or when the user provides multiple tasks. "
        "For 'all N items' tasks, enumerate every instance as its own checklist "
        "item so none are silently dropped. "
        "Call with no parameters to read the current list.\n"
        "List order is priority. When several subagents each do their own item, "
        "merge status in_progress on each of those ids; do not mark an unlinked sibling. "
        "A parent item may stay in_progress while its children remain pending until you "
        "update those child ids. Record delegation_id and/or child_id from the dispatch "
        "result on that same item id; never infer a link from goal or title. "
        "A returned child is not completed. "
        "Break large phases into subtasks via parent. "
        "Mark an item completed only after the work is verified done, never "
        "based on intent. If something fails, cancel it and add a revised "
        "item. Always returns the full current list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "Task items to write.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string"
                        },
                        "content": {
                            "type": "string",
                            "description": "Task description"
                        },
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed", "cancelled"]
                        },
                        "parent": {
                            "type": "string",
                            "description": "Optional id of another item, making this a nested subtask. Omit for top-level."
                        },
                        "delegation_id": {
                            "type": "string",
                            "description": "Explicit delegation id for the subagent doing THIS item. Omit on merge to keep the current link. Empty string clears a single link. Do not infer from text. Several existing links plus only this field are left unchanged."
                        },
                        "child_id": {
                            "type": "string",
                            "description": "Explicit child session id for the subagent doing THIS item. Same merge rules as delegation_id. This is not the subagent registry id unless that id is the session id."
                        },
                        "delegations": {
                            "type": "array",
                            "description": "Explicit links when several children share THIS item. If present, replaces delegation_id/child_id. Empty array clears every link. Each entry needs delegation_id and/or child_id.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "delegation_id": {"type": "string"},
                                    "child_id": {"type": "string"}
                                }
                            }
                        }
                    },
                    "required": ["id", "content", "status"]
                }
            },
            "merge": {
                "type": "boolean",
                "description": (
                    "true: update existing items by id, add new ones. "
                    "false (default): replace the entire list with a fresh plan."
                ),
                "default": False
            }
        },
        "required": []
    }
}

# Pre-rename names that replay as the Todo tool. model_tools._LEGACY_TOOL_ALIASES derives its todo
# entries from this, so the alias map and the transcript/TUI predicates below cannot drift.
TODO_LEGACY_ALIASES = ("todo",)
TODO_TOOL_NAMES = frozenset((TODO_SCHEMA["name"], *TODO_LEGACY_ALIASES))


def is_todo_tool_name(name: Any) -> bool:
    """True for the Todo tool's current name or a legacy alias (an already-unwrapped dispatch name)."""
    return isinstance(name, str) and name in TODO_TOOL_NAMES


def is_todo_tool_call(tool_call: Any) -> bool:
    """True when a transcript tool_call entry (dict or object) invoked the Todo tool.

    Covers the current name, legacy aliases, and the ``tool_call`` bridge (``todo_list`` is deferred by
    default, and the transcript keeps the bridge name). The bridge is peeled from the recorded arguments
    only, never live tool-search config, and must wrap exactly one call. Keep this module free of model_tools / agent.tool_executor
    imports: TUI resume and run_agent call this without loading either.
    """
    from agent.message_sanitization import _tc_field

    fn = _tc_field(tool_call, "function")
    name, raw_args = _tc_field(fn, "name") or "", _tc_field(fn, "arguments")
    if is_todo_tool_name(name):
        return True
    # Cheap heuristic before the bridge modules load: skip args without a literal "todo". Only a
    # unicode-escaped name slips past, which json.dumps never writes for ASCII.
    if isinstance(raw_args, str) and "todo" not in raw_args:
        return False
    from tools.tool_search_catalog import TOOL_CALL_NAME

    if name != TOOL_CALL_NAME:
        return False
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except json.JSONDecodeError:
        return False
    if not isinstance(args, dict):
        return False
    from tools.tool_search_validation import normalize_tool_call_entries

    entries, error = normalize_tool_call_entries(args)
    return not error and len(entries) == 1 and is_todo_tool_name(entries[0]["name"])


from tools.registry import registry, tool_error

registry.register(
    name="todo_list", toolset="todo", schema=TODO_SCHEMA, check_fn=check_todo_requirements,
    handler=lambda args, **kw: todo_tool(
        todos=args.get("todos"), merge=args.get("merge", False), store=kw.get("store")),
    emoji="📋")
