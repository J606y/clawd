"""PEP 517 backend that refuses to ship a wheel without staged panel resources.

Editable installs delegate to setuptools and do not require a built panel.
Direct ``setuptools.build_meta`` calls (uv2nix and some tests) skip this
module; ``setup.py`` must call ``stage_distribution`` and
``apply_distribution_boundary`` for that path. Wheel and sdist hooks here
still create a fresh stage directory so an in-tree ``build/lib`` or
``SOURCES.txt`` is not the build input. The hook deletes only that stage.
It does not exclude ``plugins.web`` and does not delete repository caches.
"""

from __future__ import annotations


def _stage() -> None:
    from pathlib import Path

    from clawd_server.resources import stage_distribution

    # Checkout panel wins. If apps/panel is absent, stage_distribution reads
    # CLAWD_PANEL_RESOURCE_ROOT. It does not keep a pre-placed bundled tree.
    stage_distribution(Path(__file__).resolve().parents[1])


def _hook(name: str):
    from setuptools import build_meta

    return getattr(build_meta, name)


def get_requires_for_build_wheel(config_settings=None):
    return _hook("get_requires_for_build_wheel")(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _hook("get_requires_for_build_sdist")(config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    return _hook("prepare_metadata_for_build_wheel")(metadata_directory, config_settings)


def prepare_metadata_for_build_sdist(metadata_directory, config_settings=None):
    return _hook("prepare_metadata_for_build_sdist")(metadata_directory, config_settings)


def get_requires_for_build_editable(config_settings=None):
    return _hook("get_requires_for_build_editable")(config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    return _hook("prepare_metadata_for_build_editable")(metadata_directory, config_settings)


def _run_isolated(builder):
    from clawd_server.resources import (
        begin_distribution_boundary_env,
        end_distribution_boundary_env,
    )

    token = begin_distribution_boundary_env()
    try:
        _stage()
        return builder()
    finally:
        end_distribution_boundary_env(token)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _run_isolated(
        lambda: _hook("build_wheel")(wheel_directory, config_settings, metadata_directory)
    )


def build_sdist(sdist_directory, config_settings=None):
    return _run_isolated(lambda: _hook("build_sdist")(sdist_directory, config_settings))


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _hook("build_editable")(wheel_directory, config_settings, metadata_directory)
