"""Composite memory store combining Global and Project memory stores.

- USER facts always go to global store (USER.md).
- When a project is active:
  - Default 'memory' writes go to project store (CLAWD_HOME/projects/<id>/memory/MEMORY.md).
  - Explicit target='global_memory' can target global notes if needed.
- System prompt format: combines Global Memory, Project Memory, and User Profile into frozen snapshots.
- Thread-safe and process-locked via underlying MemoryStore instances.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.memory_tool_store import ENTRY_DELIMITER, MemoryStore


class MemoryWriteDenied(RuntimeError):
    """A read-only composite refused a mutation. Not an approval stage."""

    def __init__(self, code: str = "subagent_readonly") -> None:
        self.code = code
        super().__init__(code)


class ProjectMemoryStore:
    """Manages both global memory and project-specific memory for a project-bound session."""

    def __init__(
        self,
        global_store: MemoryStore,
        project_store: MemoryStore,
        project_id: str,
        project_name: str = "",
    ):
        self.global_store = global_store
        self.project_store = project_store
        self.project_id = project_id
        self.project_name = project_name or project_id
        self.profile_home_key = getattr(project_store, "profile_home_key", None)
        self.memory_writes_allowed = True
        self._snapshot_sealed = False
        self._consolidation_failures = 0
        self._system_prompt_snapshot: dict[str, str] = {"memory": "", "user": ""}

    def _require_writable(self) -> None:
        if self.memory_writes_allowed is False:
            raise MemoryWriteDenied("subagent_readonly")

    @property
    def memory_enabled(self) -> bool:
        return self.project_store.memory_enabled or self.global_store.memory_enabled

    @property
    def user_profile_enabled(self) -> bool:
        return self.global_store.user_profile_enabled

    def accepted_targets(self) -> tuple[str, ...]:
        """Targets this composite store actually writes. ``memory``/``project`` are project notes."""
        targets: list[str] = []
        if self.project_store.memory_enabled:
            targets.extend(("memory", "project"))
        if self.global_store.user_profile_enabled:
            targets.append("user")
        if self.global_store.memory_enabled:
            targets.append("global_memory")
        return tuple(targets)

    def target_enabled(self, target: str) -> bool:
        return target in self.accepted_targets()

    @property
    def memory_entries(self) -> list[str]:
        return self.project_store.memory_entries

    @property
    def user_entries(self) -> list[str]:
        return self.global_store.user_entries

    def reset_consolidation_failures(self) -> None:
        self.global_store.reset_consolidation_failures()
        self.project_store.reset_consolidation_failures()
        self._consolidation_failures = 0

    def load_from_disk(self) -> None:
        self.global_store.load_from_disk()
        self.project_store.load_from_disk()
        if self._snapshot_sealed:
            return
        self._system_prompt_snapshot = {
            "user": self.global_store.format_for_system_prompt("user") or "",
            "memory": self._build_frozen_memory_snapshot() or "",
        }
        self._snapshot_sealed = True

    def _build_frozen_memory_snapshot(self) -> Optional[str]:
        blocks = []
        g_block = self.global_store.format_for_system_prompt("memory")
        if g_block:
            blocks.append(g_block)
        p_block = self._render_project_block()
        if p_block:
            blocks.append(p_block)
        return "\n\n".join(blocks) if blocks else None

    def _render_project_block(self) -> str:
        """Render frozen Project Memory snapshot."""
        entries = self.project_store.memory_entries
        if not entries:
            return ""
        content = ENTRY_DELIMITER.join(entries)
        sep = "═" * 46
        header = f"PROJECT MEMORY (notes for project '{self.project_name}')"
        usage = self.project_store._usage_pct("memory", len(content))
        return f"{sep}\n{header} [{usage}]\n{sep}\n{content}"

    def format_for_system_prompt(self, kind: str) -> Optional[str]:
        """Frozen load-time snapshot (NOT live state — mid-session writes don't touch
        it, preserving the prefix cache); None if empty."""
        return self._system_prompt_snapshot.get(kind, "") or None

    def _delegate_target(self, target: str) -> tuple[MemoryStore, str]:
        """Map an accepted target to (store, internal_target). Unknown targets are not written."""
        if target not in self.accepted_targets():
            raise ValueError(f"unsupported memory target: {target}")
        if target == "user":
            return self.global_store, "user"
        if target == "global_memory":
            return self.global_store, "memory"
        return self.project_store, "memory"

    def replace(self, target: str, old_text: str, new_content: str, matched_entry: Optional[str] = None) -> dict[str, Any]:
        self._require_writable()
        store, actual_target = self._delegate_target(target)
        return store.replace(actual_target, old_text, new_content, matched_entry)

    def _char_limit(self, target: str) -> int:
        store, actual_target = self._delegate_target(target)
        return store._char_limit(actual_target)

    def _usage_pct(self, target: str, current: int) -> str:
        store, actual_target = self._delegate_target(target)
        return store._usage_pct(actual_target, current)

    def add(self, target: str, content: str) -> dict[str, Any]:
        self._require_writable()
        store, actual_target = self._delegate_target(target)
        return store.add(actual_target, content)

    def remove(self, target: str, old_text: str, matched_entry: Optional[str] = None) -> dict[str, Any]:
        self._require_writable()
        store, actual_target = self._delegate_target(target)
        return store.remove(actual_target, old_text, matched_entry)

    def patch(self, target: str, old_text: str, new_text: str, matched_entry: Optional[str] = None) -> dict[str, Any]:
        self._require_writable()
        store, actual_target = self._delegate_target(target)
        return store.patch(actual_target, old_text, new_text, matched_entry)

    def apply_batch(self, target: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        self._require_writable()
        store, actual_target = self._delegate_target(target)
        return store.apply_batch(actual_target, operations)

    def resolve_entry(self, target: str, old_text: str, verb: str) -> dict[str, Any]:
        store, actual_target = self._delegate_target(target)
        return store.resolve_entry(actual_target, old_text, verb)

    def resolve_batch_entries(self, target: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        store, actual_target = self._delegate_target(target)
        return store.resolve_batch_entries(actual_target, operations)

    def _entries_for(self, target: str) -> list[str]:
        store, actual_target = self._delegate_target(target)
        return store._entries_for(actual_target)

    def _usage(self, target: str) -> str:
        store, actual_target = self._delegate_target(target)
        return store._usage(actual_target)
