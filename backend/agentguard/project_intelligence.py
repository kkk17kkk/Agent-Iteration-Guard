"""Project Intelligence Layer for registered Agent projects.

This module describes the evaluated project. It is not Agent Memory and is
not injected into the target Agent execution loop.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .store import Store
from .scenario_contracts import FixtureCatalog


ComponentType = Literal["skill", "skill_pair", "tool"]
SnapshotChangeStatus = Literal["added", "removed", "changed", "unchanged"]
CapabilityStatus = Literal["declared", "observed", "verified", "stale", "rejected"]
SourceKind = Literal["repository", "package", "docker_image"]
RuntimeKind = Literal["native_http", "native_command", "package", "docker"]
IntelligenceStatus = Literal["registered", "ready", "stale"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{_fingerprint(list(parts))[:16]}"


class AgentManifest(BaseModel):
    """Project identity and declared component surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.agent-manifest.v1"] = "aig.agent-manifest.v1"
    manifest_id: str = Field(default="", min_length=0)
    project_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    source_kind: SourceKind
    source_ref: str = Field(min_length=1)
    available_components: list[str] = Field(min_length=1)
    capability_descriptions: dict[str, str] = Field(min_length=1)
    status: IntelligenceStatus = "registered"
    created_at: str = Field(default_factory=_now)


class CapabilityRecord(BaseModel):
    """One component that can be selected by an EvaluationRequest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.capability-record.v1"] = "aig.capability-record.v1"
    capability_id: str = Field(default="", min_length=0)
    project_id: str = Field(min_length=1)
    component_type: ComponentType
    name: str = Field(min_length=1)
    responsibility: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    boundary: list[str] = Field(default_factory=list)
    status: CapabilityStatus = "declared"
    source_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class RuntimeProfile(BaseModel):
    """Non-secret instructions required to reproduce a target runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.runtime-profile.v1"] = "aig.runtime-profile.v1"
    profile_id: str = Field(default="", min_length=0)
    project_id: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    runtime_kind: RuntimeKind
    environment: dict[str, str] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    model_configuration: dict[str, str] = Field(default_factory=dict)
    execution_requirements: list[str] = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_kind: SourceKind | None = None
    source_fingerprint: str | None = Field(default=None, min_length=16, max_length=200)
    runtime_version: str | None = Field(default=None, min_length=1)
    dependency_lock_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    scanner_ref: str | None = Field(default=None, min_length=1)
    preflight_contract_ref: str | None = Field(default=None, min_length=1)
    trace_contract_ref: str | None = None
    reset_contract_ref: str | None = None
    trace_event_types: list[str] = Field(default_factory=list, max_length=100)
    fixture_catalog: FixtureCatalog = Field(default_factory=FixtureCatalog)
    created_at: str = Field(default_factory=_now)


class BaselineSnapshot(BaseModel):
    """Immutable initial project state used by later Evaluation Requests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.baseline-snapshot.v1"] = "aig.baseline-snapshot.v1"
    snapshot_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    baseline_version: str = Field(min_length=1)
    agent_manifest_id: str = Field(min_length=1)
    agent_manifest_fingerprint: str = Field(min_length=64, max_length=64)
    capability_snapshot: list[CapabilityRecord] = Field(min_length=1)
    capability_snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    runtime_snapshot: RuntimeProfile
    runtime_snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    initial_evaluation_history: list[str] = Field(default_factory=list)
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    created_at: str = Field(default_factory=_now)


class AgentSnapshot(BaseModel):
    """Immutable inventory of one uploaded Agent project version.

    This is project context, not a memory injected into the target Agent.  It
    keeps enough of the manifest, capability registry, and runtime profile to
    compare a later upload without rewriting the original baseline.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.agent-snapshot.v1"] = "aig.agent-snapshot.v1"
    snapshot_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    agent_manifest: AgentManifest
    capability_registry: list[CapabilityRecord] = Field(min_length=1)
    runtime_profile: RuntimeProfile
    parent_snapshot_id: str | None = None
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    created_at: str = Field(default_factory=_now)


class SnapshotComponentChange(BaseModel):
    """A deterministic component-level comparison between two snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.snapshot-component-change.v1"] = "aig.snapshot-component-change.v1"
    component_type: ComponentType
    component_name: str = Field(min_length=1)
    status: SnapshotChangeStatus
    baseline_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    candidate_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    changed_fields: list[str] = Field(default_factory=list)


class ProjectSnapshotDiff(BaseModel):
    """Reviewable suggestions for the components affected by a new upload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.project-snapshot-diff.v1"] = "aig.project-snapshot-diff.v1"
    diff_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    baseline_snapshot_id: str = Field(min_length=1)
    candidate_snapshot_id: str = Field(min_length=1)
    component_changes: list[SnapshotComponentChange] = Field(min_length=1)
    manifest_changed: bool
    runtime_changed: bool
    diff_fingerprint: str = Field(min_length=64, max_length=64)
    created_at: str = Field(default_factory=_now)


class ProjectSnapshotRegistrationResult(BaseModel):
    """Result returned when a new project version is registered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: AgentSnapshot
    diff: ProjectSnapshotDiff
    intelligence: "ProjectIntelligence"


class ProjectIntelligence(BaseModel):
    """Queryable aggregate assembled from the four persisted objects."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.project-intelligence.v1"] = "aig.project-intelligence.v1"
    project_id: str = Field(min_length=1)
    status: IntelligenceStatus
    agent_manifest: AgentManifest
    capability_registry: list[CapabilityRecord] = Field(min_length=1)
    runtime_profile: RuntimeProfile
    baseline_snapshot: BaselineSnapshot
    latest_snapshot: AgentSnapshot | None = None
    snapshot_history: list[AgentSnapshot] = Field(default_factory=list)
    latest_diff: ProjectSnapshotDiff | None = None
    intelligence_fingerprint: str = Field(min_length=64, max_length=64)


class ProjectIntelligenceRegistration(BaseModel):
    """Input accepted by the registration boundary."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    agent_manifest: AgentManifest
    capabilities: list[CapabilityRecord] = Field(min_length=1)
    runtime_profile: RuntimeProfile
    baseline_version: str = Field(min_length=1)
    snapshot_version: str | None = Field(default=None, min_length=1)
    initial_evaluation_history: list[str] = Field(default_factory=list)

    @property
    def version_label(self) -> str:
        return self.snapshot_version or self.baseline_version


class ProjectIntelligenceError(ValueError):
    """Invalid or conflicting Project Intelligence registration."""


class ProjectIntelligenceRepository:
    """Persist and query Project Intelligence without changing legacy records."""

    _MANIFEST_KIND = "project_intelligence_manifest"
    _CAPABILITY_KIND = "project_intelligence_capability"
    _RUNTIME_KIND = "project_intelligence_runtime"
    _BASELINE_KIND = "project_intelligence_baseline"
    _SNAPSHOT_KIND = "project_agent_snapshot"
    _DIFF_KIND = "project_snapshot_diff"

    def __init__(self, store: Store) -> None:
        self.store = store

    def register(self, registration: ProjectIntelligenceRegistration) -> ProjectIntelligence:
        manifest, capabilities, runtime = self._normalize(registration)
        self._validate(registration, manifest, capabilities, runtime)
        baseline = self._build_snapshot(registration, manifest, capabilities, runtime)
        existing = self.get(registration.project_id)
        if existing:
            if existing.baseline_snapshot.snapshot_fingerprint != baseline.snapshot_fingerprint:
                raise ProjectIntelligenceError(
                    "Project Intelligence already exists with a different immutable baseline."
                )
            return existing

        agent_snapshot = self._build_agent_snapshot(
            registration, manifest, capabilities, runtime, parent_snapshot_id=None
        )

        self.store.save_many([
            (self._MANIFEST_KIND, manifest.manifest_id, registration.project_id, manifest),
            *[
                (self._CAPABILITY_KIND, capability.capability_id, registration.project_id, capability)
                for capability in capabilities
            ],
            (self._RUNTIME_KIND, runtime.profile_id, registration.project_id, runtime),
            (self._BASELINE_KIND, baseline.snapshot_id, registration.project_id, baseline),
            (self._SNAPSHOT_KIND, agent_snapshot.snapshot_id, registration.project_id, agent_snapshot),
        ])
        return self._aggregate(
            manifest,
            capabilities,
            runtime,
            baseline,
            [agent_snapshot],
            None,
        )

    def register_snapshot(
        self, registration: ProjectIntelligenceRegistration
    ) -> ProjectSnapshotRegistrationResult:
        """Register a later immutable upload and return component diff suggestions."""

        manifest, capabilities, runtime = self._normalize(registration)
        self._validate(registration, manifest, capabilities, runtime)
        existing = self.get(registration.project_id)
        if existing is None:
            raise ProjectIntelligenceError(
                "Project Intelligence must be registered before a candidate snapshot."
            )
        if registration.version_label == existing.baseline_snapshot.baseline_version:
            raise ProjectIntelligenceError(
                "A candidate snapshot must use a version different from the immutable baseline."
            )

        history = existing.snapshot_history
        candidate = self._build_agent_snapshot(
            registration,
            manifest,
            capabilities,
            runtime,
            parent_snapshot_id=existing.latest_snapshot.snapshot_id,
        )
        same_version = next((item for item in history if item.version == candidate.version), None)
        if same_version is not None:
            if same_version.snapshot_fingerprint != candidate.snapshot_fingerprint:
                raise ProjectIntelligenceError(
                    f"Agent Snapshot {candidate.version!r} already exists with different contents."
                )
            diff = self._diff_for_candidate(existing, same_version)
            return ProjectSnapshotRegistrationResult(
                snapshot=same_version,
                diff=diff,
                intelligence=existing,
            )

        diff = self._build_diff(existing.latest_snapshot, candidate)
        self.store.save_many([
            (self._SNAPSHOT_KIND, candidate.snapshot_id, registration.project_id, candidate),
            (self._DIFF_KIND, diff.diff_id, registration.project_id, diff),
        ])
        history = [*history, candidate]
        intelligence = self._aggregate(
            candidate.agent_manifest,
            candidate.capability_registry,
            candidate.runtime_profile,
            existing.baseline_snapshot,
            history,
            diff,
        )
        return ProjectSnapshotRegistrationResult(snapshot=candidate, diff=diff, intelligence=intelligence)

    def get(self, project_id: str) -> ProjectIntelligence | None:
        manifest = self.store.get(self._MANIFEST_KIND, _stable_id("manifest", project_id), AgentManifest)
        runtime = self.store.get(self._RUNTIME_KIND, _stable_id("runtime", project_id), RuntimeProfile)
        snapshots = self.store.list(self._BASELINE_KIND, BaselineSnapshot, project_id)
        agent_snapshots = sorted(
            self.store.list(self._SNAPSHOT_KIND, AgentSnapshot, project_id),
            key=lambda item: item.created_at,
        )
        diffs = self.store.list(self._DIFF_KIND, ProjectSnapshotDiff, project_id)
        capabilities = sorted(
            self.store.list(self._CAPABILITY_KIND, CapabilityRecord, project_id),
            key=lambda item: (item.component_type, item.name),
        )
        if not manifest and not runtime and not snapshots and not capabilities and not agent_snapshots:
            return None
        if not manifest or not runtime or len(snapshots) != 1 or not capabilities:
            raise ProjectIntelligenceError(
                "Project Intelligence records are incomplete or contain multiple baselines."
            )
        baseline = snapshots[0]
        if not agent_snapshots:
            agent_snapshots = [self._legacy_agent_snapshot(manifest, capabilities, runtime, baseline)]
        latest = agent_snapshots[-1]
        latest_diff = next(
            (item for item in diffs if item.candidate_snapshot_id == latest.snapshot_id),
            None,
        )
        return self._aggregate(
            latest.agent_manifest,
            latest.capability_registry,
            latest.runtime_profile,
            baseline,
            agent_snapshots,
            latest_diff,
        )

    def _normalize(
        self,
        registration: ProjectIntelligenceRegistration,
    ) -> tuple[AgentManifest, list[CapabilityRecord], RuntimeProfile]:
        project_id = registration.project_id
        manifest = registration.agent_manifest.model_copy(
            update={"manifest_id": _stable_id("manifest", project_id), "project_id": project_id}
        )
        capabilities = [
            capability.model_copy(
                update={
                    "capability_id": _stable_id("capability", project_id, capability.component_type, capability.name),
                    "project_id": project_id,
                }
            )
            for capability in registration.capabilities
        ]
        capabilities = sorted(capabilities, key=lambda item: (item.component_type, item.name))
        runtime = registration.runtime_profile.model_copy(
            update={"profile_id": _stable_id("runtime", project_id), "project_id": project_id}
        )
        return manifest, capabilities, runtime

    @staticmethod
    def _validate(
        registration: ProjectIntelligenceRegistration,
        manifest: AgentManifest,
        capabilities: list[CapabilityRecord],
        runtime: RuntimeProfile,
    ) -> None:
        if (
            registration.agent_manifest.project_id != registration.project_id
            or registration.runtime_profile.project_id != registration.project_id
            or any(capability.project_id != registration.project_id for capability in registration.capabilities)
        ):
            raise ProjectIntelligenceError("All Project Intelligence objects must belong to the same project.")
        if manifest.project_id != registration.project_id or runtime.project_id != registration.project_id:
            raise ProjectIntelligenceError("All Project Intelligence objects must belong to the same project.")
        names = [capability.name for capability in capabilities]
        if len(names) != len(set(names)):
            raise ProjectIntelligenceError("Capability Registry contains duplicate component names.")
        if set(manifest.available_components) != set(names):
            raise ProjectIntelligenceError("Agent Manifest available_components must match the Capability Registry.")
        if set(manifest.capability_descriptions) != set(names):
            raise ProjectIntelligenceError("Agent Manifest capability_descriptions must match the Capability Registry.")
        by_name = {capability.name: capability for capability in capabilities}
        for capability in capabilities:
            if capability.project_id != registration.project_id:
                raise ProjectIntelligenceError("Capability Registry contains a foreign project record.")
            if capability.component_type == "skill_pair":
                if len(capability.dependencies) != 2 or len(set(capability.dependencies)) != 2:
                    raise ProjectIntelligenceError("A skill_pair must declare exactly two distinct skill dependencies.")
                if any(
                    dependency not in by_name or by_name[dependency].component_type != "skill"
                    for dependency in capability.dependencies
                ):
                    raise ProjectIntelligenceError("A skill_pair dependency must reference registered skills.")

    def _build_snapshot(
        self,
        registration: ProjectIntelligenceRegistration,
        manifest: AgentManifest,
        capabilities: list[CapabilityRecord],
        runtime: RuntimeProfile,
    ) -> BaselineSnapshot:
        capability_payload = [self._semantic_model(capability) for capability in capabilities]
        runtime_payload = self._semantic_model(runtime)
        manifest_fingerprint = _fingerprint(self._semantic_model(manifest))
        capability_fingerprint = _fingerprint(capability_payload)
        runtime_fingerprint = _fingerprint(runtime_payload)
        snapshot_fingerprint = _fingerprint({
            "project_id": registration.project_id,
            "baseline_version": registration.baseline_version,
            "agent_manifest_fingerprint": manifest_fingerprint,
            "capability_snapshot_fingerprint": capability_fingerprint,
            "runtime_snapshot_fingerprint": runtime_fingerprint,
            "initial_evaluation_history": registration.initial_evaluation_history,
        })
        return BaselineSnapshot(
            snapshot_id=_stable_id("baseline", registration.project_id, registration.baseline_version),
            project_id=registration.project_id,
            baseline_version=registration.baseline_version,
            agent_manifest_id=manifest.manifest_id,
            agent_manifest_fingerprint=manifest_fingerprint,
            capability_snapshot=capabilities,
            capability_snapshot_fingerprint=capability_fingerprint,
            runtime_snapshot=runtime,
            runtime_snapshot_fingerprint=runtime_fingerprint,
            initial_evaluation_history=registration.initial_evaluation_history,
            snapshot_fingerprint=snapshot_fingerprint,
        )

    def _build_agent_snapshot(
        self,
        registration: ProjectIntelligenceRegistration,
        manifest: AgentManifest,
        capabilities: list[CapabilityRecord],
        runtime: RuntimeProfile,
        *,
        parent_snapshot_id: str | None,
    ) -> AgentSnapshot:
        manifest_fingerprint = _fingerprint(self._semantic_model(manifest))
        capability_fingerprint = _fingerprint(
            [self._semantic_model(capability) for capability in capabilities]
        )
        runtime_fingerprint = _fingerprint(self._semantic_model(runtime))
        version = registration.version_label
        snapshot_fingerprint = _fingerprint({
            "project_id": registration.project_id,
            "version": version,
            "agent_manifest_fingerprint": manifest_fingerprint,
            "capability_registry_fingerprint": capability_fingerprint,
            "runtime_profile_fingerprint": runtime_fingerprint,
        })
        return AgentSnapshot(
            snapshot_id=_stable_id("agent-snapshot", registration.project_id, version),
            project_id=registration.project_id,
            version=version,
            agent_manifest=manifest,
            capability_registry=capabilities,
            runtime_profile=runtime,
            parent_snapshot_id=parent_snapshot_id,
            snapshot_fingerprint=snapshot_fingerprint,
        )

    @classmethod
    def _legacy_agent_snapshot(
        cls,
        manifest: AgentManifest,
        capabilities: list[CapabilityRecord],
        runtime: RuntimeProfile,
        baseline: BaselineSnapshot,
    ) -> AgentSnapshot:
        return AgentSnapshot(
            snapshot_id=baseline.snapshot_id,
            project_id=baseline.project_id,
            version=baseline.baseline_version,
            agent_manifest=manifest,
            capability_registry=capabilities,
            runtime_profile=runtime,
            snapshot_fingerprint=_fingerprint({
                "project_id": baseline.project_id,
                "version": baseline.baseline_version,
                "agent_manifest_fingerprint": baseline.agent_manifest_fingerprint,
                "capability_registry_fingerprint": baseline.capability_snapshot_fingerprint,
                "runtime_profile_fingerprint": baseline.runtime_snapshot_fingerprint,
            }),
        )

    def _build_diff(self, baseline: AgentSnapshot, candidate: AgentSnapshot) -> ProjectSnapshotDiff:
        baseline_components = {
            (item.component_type, item.name): item for item in baseline.capability_registry
        }
        candidate_components = {
            (item.component_type, item.name): item for item in candidate.capability_registry
        }
        component_changes: list[SnapshotComponentChange] = []
        for key in sorted(set(baseline_components) | set(candidate_components)):
            before = baseline_components.get(key)
            after = candidate_components.get(key)
            if before is None and after is not None:
                component_changes.append(SnapshotComponentChange(
                    component_type=key[0],
                    component_name=key[1],
                    status="added",
                    candidate_fingerprint=_fingerprint(self._semantic_model(after)),
                ))
                continue
            if after is None and before is not None:
                component_changes.append(SnapshotComponentChange(
                    component_type=key[0],
                    component_name=key[1],
                    status="removed",
                    baseline_fingerprint=_fingerprint(self._semantic_model(before)),
                ))
                continue
            assert before is not None and after is not None
            before_model = self._semantic_model(before)
            after_model = self._semantic_model(after)
            changed_fields = sorted({
                field
                for field in set(before_model) | set(after_model)
                if before_model.get(field) != after_model.get(field)
            })
            component_changes.append(SnapshotComponentChange(
                component_type=key[0],
                component_name=key[1],
                status="changed" if changed_fields else "unchanged",
                baseline_fingerprint=_fingerprint(before_model),
                candidate_fingerprint=_fingerprint(after_model),
                changed_fields=changed_fields,
            ))

        manifest_changed = self._semantic_model(baseline.agent_manifest) != self._semantic_model(candidate.agent_manifest)
        runtime_changed = self._semantic_model(baseline.runtime_profile) != self._semantic_model(candidate.runtime_profile)
        diff_id = _stable_id("snapshot-diff", baseline.project_id, baseline.snapshot_id, candidate.snapshot_id)
        diff_payload = {
            "project_id": baseline.project_id,
            "baseline_snapshot_id": baseline.snapshot_id,
            "candidate_snapshot_id": candidate.snapshot_id,
            "component_changes": [item.model_dump(mode="json") for item in component_changes],
            "manifest_changed": manifest_changed,
            "runtime_changed": runtime_changed,
        }
        return ProjectSnapshotDiff(
            diff_id=diff_id,
            project_id=baseline.project_id,
            baseline_snapshot_id=baseline.snapshot_id,
            candidate_snapshot_id=candidate.snapshot_id,
            component_changes=component_changes,
            manifest_changed=manifest_changed,
            runtime_changed=runtime_changed,
            diff_fingerprint=_fingerprint(diff_payload),
        )

    def _diff_for_candidate(
        self, intelligence: ProjectIntelligence, candidate: AgentSnapshot
    ) -> ProjectSnapshotDiff:
        stored = self.store.get(self._DIFF_KIND, _stable_id(
            "snapshot-diff",
            candidate.project_id,
            candidate.parent_snapshot_id or intelligence.baseline_snapshot.snapshot_id,
            candidate.snapshot_id,
        ), ProjectSnapshotDiff)
        if stored is not None:
            return stored
        parent = next(
            (item for item in intelligence.snapshot_history if item.snapshot_id == candidate.parent_snapshot_id),
            None,
        ) or intelligence.latest_snapshot
        if parent is None:
            raise ProjectIntelligenceError("Cannot compute a snapshot diff without a parent snapshot.")
        return self._build_diff(parent, candidate)

    @classmethod
    def _aggregate(
        cls,
        manifest: AgentManifest,
        capabilities: list[CapabilityRecord],
        runtime: RuntimeProfile,
        snapshot: BaselineSnapshot,
        snapshot_history: list[AgentSnapshot],
        latest_diff: ProjectSnapshotDiff | None,
    ) -> ProjectIntelligence:
        latest_snapshot = snapshot_history[-1]
        return ProjectIntelligence(
            project_id=manifest.project_id,
            status="ready",
            agent_manifest=manifest,
            capability_registry=capabilities,
            runtime_profile=runtime,
            baseline_snapshot=snapshot,
            latest_snapshot=latest_snapshot,
            snapshot_history=snapshot_history,
            latest_diff=latest_diff,
            intelligence_fingerprint=cls._intelligence_fingerprint(
                manifest, capabilities, runtime, snapshot, latest_snapshot
            ),
        )

    @classmethod
    def _intelligence_fingerprint(
        cls,
        manifest: AgentManifest,
        capabilities: list[CapabilityRecord],
        runtime: RuntimeProfile,
        snapshot: BaselineSnapshot,
        latest_snapshot: AgentSnapshot | None = None,
    ) -> str:
        return _fingerprint({
            "manifest": cls._semantic_model(manifest),
            "capabilities": [cls._semantic_model(item) for item in capabilities],
            "runtime": cls._semantic_model(runtime),
            "snapshot": cls._semantic_model(snapshot),
            "latest_snapshot": cls._semantic_model(latest_snapshot) if latest_snapshot else None,
        })

    @staticmethod
    def _semantic_model(model: BaseModel) -> dict[str, object]:
        ignored = {
            "manifest_id", "capability_id", "profile_id", "snapshot_id", "created_at",
            "intelligence_fingerprint", "snapshot_fingerprint",
        }

        def strip(value: object) -> object:
            if isinstance(value, dict):
                return {key: strip(item) for key, item in value.items() if key not in ignored}
            if isinstance(value, list):
                return [strip(item) for item in value]
            return value

        result = strip(model.model_dump(mode="json"))
        if not isinstance(result, dict):
            raise TypeError("Project Intelligence semantic model must be an object.")
        return result


__all__ = [
    "AgentSnapshot",
    "AgentManifest",
    "BaselineSnapshot",
    "CapabilityRecord",
    "CapabilityStatus",
    "ComponentType",
    "IntelligenceStatus",
    "ProjectIntelligence",
    "ProjectIntelligenceError",
    "ProjectIntelligenceRegistration",
    "ProjectIntelligenceRepository",
    "ProjectSnapshotDiff",
    "ProjectSnapshotRegistrationResult",
    "RuntimeProfile",
    "SnapshotChangeStatus",
    "SnapshotComponentChange",
]
