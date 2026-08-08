"""Static runtime discovery and reviewed runtime-configuration drafts.

The scanner never executes imported source.  This module turns its immutable
snapshot metadata into a user-reviewable proposal; saving the proposal is the
separate, explicit point at which a server-owned execution contract is made.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .project_intelligence import ProjectIntelligence, RuntimeProfile
from .store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RuntimeAdapterCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["interaction", "oracle"]
    path: str
    command: list[str]
    confidence: Literal["convention", "declared"] = "convention"


class RuntimeConfigurationDraft(BaseModel):
    """Non-secret, non-executable proposal tied to one immutable snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.runtime-configuration-draft.v1"] = "aig.runtime-configuration-draft.v1"
    draft_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    snapshot_version: str = Field(min_length=1)
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    source_fingerprint: str = Field(min_length=64, max_length=64)
    runtime_kind: str = Field(min_length=1)
    language: Literal["python", "node", "unknown"] = "unknown"
    working_directory: str = "."
    entrypoint: str | None = None
    package_manager: str | None = None
    dependency_files: list[str] = Field(default_factory=list)
    adapter_candidates: list[RuntimeAdapterCandidate] = Field(default_factory=list)
    suggested_interaction_command: list[str] = Field(default_factory=list)
    suggested_oracle_command: list[str] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    status: Literal["review_required", "ready_to_save"] = "review_required"
    created_at: str = Field(default_factory=_now)


class RuntimeDraftReview(BaseModel):
    """User-facing review payload. Server paths and interpreter paths are absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=160)
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    entrypoint: str = Field(min_length=1, max_length=240)
    interaction_command: list[str] = Field(min_length=1, max_length=32)
    oracle_command: list[str] = Field(min_length=1, max_length=32)
    oracle_id: str = Field(min_length=1, max_length=160)
    oracle_type: Literal["rule_based", "frozen_lookup", "structured_state"] = "rule_based"
    oracle_version: str = Field(default="1.0", min_length=1, max_length=80)


class RuntimeConfigurationDraftRepository:
    _KIND = "runtime_configuration_draft"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, draft: RuntimeConfigurationDraft) -> RuntimeConfigurationDraft:
        self.store.save(self._KIND, draft.draft_id, draft.project_id, draft)
        return draft

    def latest(self, project_id: str, snapshot_fingerprint: str) -> RuntimeConfigurationDraft | None:
        candidates = [
            item for item in self.store.list(self._KIND, RuntimeConfigurationDraft, project_id)
            if item.snapshot_fingerprint == snapshot_fingerprint
        ]
        return sorted(candidates, key=lambda item: item.created_at)[-1] if candidates else None


def build_runtime_draft(
    intelligence: ProjectIntelligence,
    *,
    snapshot_version: str,
    source_root: Path | None = None,
) -> RuntimeConfigurationDraft:
    snapshot = _snapshot_for_version(intelligence, snapshot_version)
    if snapshot is None:
        raise ValueError(f"Snapshot {snapshot_version} was not found.")
    runtime = snapshot.runtime_profile
    language = _language(runtime)
    package_manager = _package_manager(runtime)
    candidates = _adapter_candidates(source_root, language)
    interaction = next((item.command for item in candidates if item.kind == "interaction"), [])
    oracle = next((item.command for item in candidates if item.kind == "oracle"), [])
    unresolved = []
    if not runtime.entrypoint:
        unresolved.append("entrypoint")
    if not interaction:
        unresolved.append("evaluation interaction command")
    if not oracle:
        unresolved.append("independent oracle command")
    identity = {
        "project_id": intelligence.project_id,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
        "source_fingerprint": runtime.source_fingerprint,
    }
    return RuntimeConfigurationDraft(
        draft_id="runtime_draft_" + _fingerprint(identity)[:16],
        project_id=intelligence.project_id,
        snapshot_version=snapshot_version,
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        source_fingerprint=runtime.source_fingerprint or snapshot.snapshot_fingerprint,
        runtime_kind=runtime.runtime_kind,
        language=language,
        entrypoint=runtime.entrypoint,
        package_manager=package_manager,
        dependency_files=list(runtime.dependencies),
        adapter_candidates=candidates,
        suggested_interaction_command=interaction,
        suggested_oracle_command=oracle,
        unresolved_fields=unresolved,
        status="ready_to_save" if not unresolved else "review_required",
    )


def _snapshot_for_version(intelligence: ProjectIntelligence, version: str):
    for item in intelligence.snapshot_history:
        if item.version == version:
            return item
    if intelligence.baseline_snapshot.baseline_version == version:
        return intelligence.baseline_snapshot
    return None


def _language(runtime: RuntimeProfile) -> Literal["python", "node", "unknown"]:
    entrypoint = (runtime.entrypoint or "").lower()
    dependencies = {Path(item).name for item in runtime.dependencies}
    if ".py" in entrypoint or "pyproject.toml" in dependencies or "requirements.txt" in dependencies:
        return "python"
    if ".js" in entrypoint or ".ts" in entrypoint or "package.json" in dependencies:
        return "node"
    return "unknown"


def _package_manager(runtime: RuntimeProfile) -> str | None:
    dependencies = {Path(item).name for item in runtime.dependencies}
    if "pyproject.toml" in dependencies:
        return "pyproject"
    if "requirements.txt" in dependencies:
        return "pip"
    if "package.json" in dependencies:
        return "pnpm" if "pnpm-lock.yaml" in dependencies else "npm"
    return None


def _adapter_candidates(source_root: Path | None, language: str) -> list[RuntimeAdapterCandidate]:
    if source_root is None or not source_root.is_dir():
        return []
    ignored = {".git", ".venv", "node_modules", "__pycache__"}
    names = {
        "interaction": (
            "aig_eval.py", "evaluate.py", "eval.py", "interaction_adapter.py", "interaction.py",
            "aig_eval.js", "evaluate.js", "eval.js", "interaction_adapter.js", "interaction.js",
        ),
        "oracle": ("oracle.py", "verify.py", "evaluator.py", "oracle.js", "verify.js", "evaluator.js"),
    }
    candidates: list[RuntimeAdapterCandidate] = []
    for path in source_root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        relative = path.relative_to(source_root).as_posix()
        for kind, known_names in names.items():
            if path.name.lower() not in known_names:
                continue
            executable = "{python}" if path.suffix == ".py" else "{node}"
            candidates.append(RuntimeAdapterCandidate(kind=kind, path=relative, command=[executable, relative]))
    return sorted(candidates, key=lambda item: (item.kind, item.path))


__all__ = [
    "RuntimeAdapterCandidate",
    "RuntimeConfigurationDraft",
    "RuntimeConfigurationDraftRepository",
    "RuntimeDraftReview",
    "build_runtime_draft",
]
