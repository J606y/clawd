"""Retired shadow registry.

Constructing ``Store`` or calling any method raises before a database is
opened. ``PANEL_LIVE_TASK_DB`` is not read. Browser tools record targets they
create in the profile registry; ``clawd_server.browser_native.install`` is the
panel reader.
"""

from __future__ import annotations


class ShadowStoreRetired(RuntimeError):
    """The panel shadow task database is not a runtime ownership source."""


class Store:
    def __init__(self, *args, **kwargs) -> None:
        raise ShadowStoreRetired(
            "live_task_store is retired. Do not open PANEL_LIVE_TASK_DB. "
            "Browser tools register targets they create."
        )

    def __getattr__(self, name: str):
        raise ShadowStoreRetired(f"live_task_store.{name} is retired")
