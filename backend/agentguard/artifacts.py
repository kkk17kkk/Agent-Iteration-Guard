import hashlib
import json
from pathlib import Path

from .domain import Change, ChangeSet, ComponentSnapshot, FileAgentManifest


MANIFEST_NAME = "agent_manifest.json"


def snapshot_manifest(product_id: str, version_id: str, source: Path) -> ComponentSnapshot:
    manifest_path = source / MANIFEST_NAME
    manifest = FileAgentManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    canonical = json.dumps(manifest.model_dump(), sort_keys=True, separators=(",", ":"))
    return ComponentSnapshot(
        product_id=product_id,
        version_id=version_id,
        fingerprint=hashlib.sha256(canonical.encode()).hexdigest(),
        source_ref=str(source.resolve()),
        manifest=manifest,
    )


def compare_snapshots(
    product_id: str,
    baseline: ComponentSnapshot,
    candidate: ComponentSnapshot,
) -> ChangeSet:
    changes: list[Change] = []
    if baseline.manifest.requested_write_paths != candidate.manifest.requested_write_paths:
        changes.append(Change(kind="permission_changed", risk="critical", before=baseline.manifest.requested_write_paths, after=candidate.manifest.requested_write_paths))
    if baseline.manifest.tool_capabilities != candidate.manifest.tool_capabilities:
        changes.append(Change(kind="tool_capability_expanded", risk="high", before=baseline.manifest.tool_capabilities, after=candidate.manifest.tool_capabilities))
    if baseline.manifest.skill != candidate.manifest.skill:
        changes.append(Change(kind="skill_changed", risk="medium", before=baseline.manifest.skill, after=candidate.manifest.skill))
    return ChangeSet(
        product_id=product_id,
        baseline_version_id=baseline.version_id,
        candidate_version_id=candidate.version_id,
        baseline_snapshot=baseline,
        candidate_snapshot=candidate,
        changes=changes,
    )
