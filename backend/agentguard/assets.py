"""Resolve repository assets without binding the application to a host path."""

from __future__ import annotations

import os
from pathlib import Path


def project_asset_root() -> Path:
    """Return the configured asset root, or the source checkout root."""

    configured = os.getenv("AGENTGUARD_ASSET_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def asset_path(*parts: str) -> Path:
    return project_asset_root().joinpath(*parts)
