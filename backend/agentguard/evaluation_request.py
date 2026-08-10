"""Unified Evaluation Request and creation-time validation.

The request is the product-facing boundary between Project Intelligence and
an evaluation run.  It deliberately contains no execution implementation or
LLM output: those belong to the Planner, Runner, and Analyst layers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .project_intelligence import ProjectIntelligence
from .runtime_comparability import RuntimeComparabilityResult, compare_runtime_snapshots
from .evaluation_suite import ScenarioSuiteConfig
from .store import Store


RequestComponentType = Literal["skill", "skill_pair", "tool"]
RequestChangeType = Literal["add", "remove", "modify", "replace"]
RequestStatus = Literal["created", "validated"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationRequest(BaseModel):
    """A user-declared component evolution to be evaluated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-request.v1"] = "aig.evaluation-request.v1"
    request_id: str = Field(default_factory=lambda: f"evaluation_request_{uuid4().hex}", min_length=1)
    project_id: str = Field(min_length=1)
    component_type: RequestComponentType
    component_name: str = Field(min_length=1)
    pair_members: list[str] = Field(default_factory=list, max_length=2)
    scenario_suite: ScenarioSuiteConfig | None = None
    change_type: RequestChangeType
    candidate_version: str = Field(min_length=1)
    baseline_version: str = Field(min_length=1)
    status: RequestStatus = "created"
    runtime_comparability: RuntimeComparabilityResult | None = None
    evaluation_scope_id: str | None = Field(default=None, min_length=16)
    created_at: str = Field(default_factory=_now)


class EvaluationRequestValidationError(ValueError):
    """A stable, user-facing validation failure at Evaluation creation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def validate_evaluation_request(
    request: EvaluationRequest,
    intelligence: ProjectIntelligence,
    *,
    candidate_available: bool,
    candidate_component_name: str | None = None,
) -> EvaluationRequest:
    """Validate the request against the immutable Project Intelligence view.

    ``candidate_available`` is supplied by the caller that owns the candidate
    artifact or revision lookup.  The Project Intelligence boundary itself
    proves that the registered runtime is reproducible by requiring an
    entrypoint, source reference, and execution requirements.
    """

    if request.project_id != intelligence.project_id:
        raise EvaluationRequestValidationError(
            "E_PROJECT_MISMATCH",
            "EvaluationRequest does not belong to the registered project.",
        )

    snapshot_history = list(getattr(intelligence, "snapshot_history", []) or [])
    capability = next(
        (
            item
            for item in intelligence.capability_registry
            if item.component_type == request.component_type and item.name == request.component_name
        ),
        None,
    )
    if capability is None:
        capability = next(
            (
                item
                for snapshot in snapshot_history
                for item in snapshot.capability_registry
                if item.component_type == request.component_type and item.name == request.component_name
            ),
            None,
        )
    pair_members = list(request.pair_members)
    if request.component_type == "skill_pair":
        if pair_members:
            if len(pair_members) != 2 or len(set(pair_members)) != 2:
                raise EvaluationRequestValidationError(
                    "E_PAIR_MEMBERS_INVALID",
                    "A Skill Pair request must name exactly two distinct Skill members.",
                )
            registered_members = list(capability.dependencies) if capability and capability.component_type == "skill_pair" else None
            if registered_members is not None and set(registered_members) != set(pair_members):
                raise EvaluationRequestValidationError(
                    "E_PAIR_MEMBERS_MISMATCH",
                    "The supplied Skill Pair members do not match the registered Pair dependencies.",
                )
            if registered_members is not None:
                pair_members = registered_members
            available_skills = {
                item.name
                for item in intelligence.capability_registry
                if item.component_type == "skill"
            }
            available_skills.update(
                item.name
                for snapshot in snapshot_history
                for item in snapshot.capability_registry
                if item.component_type == "skill"
            )
            missing_members = sorted(set(pair_members) - available_skills)
            if missing_members:
                raise EvaluationRequestValidationError(
                    "E_PAIR_MEMBER_NOT_FOUND",
                    f"Skill Pair members were not discovered in this project's frozen snapshots: {missing_members}.",
                )
        elif capability is not None and capability.component_type == "skill_pair":
            pair_members = list(capability.dependencies)
        else:
            raise EvaluationRequestValidationError(
                "E_COMPONENT_NOT_FOUND",
                f"Temporary Skill Pair {request.component_name} requires exactly two discovered pair_members.",
            )
    if capability is None and request.component_type != "skill_pair":
        raise EvaluationRequestValidationError(
            "E_COMPONENT_NOT_FOUND",
            f"Component {request.component_type}/{request.component_name} is not registered for project {request.project_id}.",
        )

    if request.baseline_version != intelligence.baseline_snapshot.baseline_version:
        raise EvaluationRequestValidationError(
            "E_BASELINE_NOT_FOUND",
            f"Baseline version {request.baseline_version} is not the registered baseline {intelligence.baseline_snapshot.baseline_version}.",
        )

    known_snapshot_versions = {
        intelligence.baseline_snapshot.baseline_version,
        *(snapshot.version for snapshot in snapshot_history),
    }
    # A first import has one immutable snapshot.  It is valid to evaluate a
    # discovered capability inside that frozen runtime; this is a capability
    # evaluation, not a claim that a version-to-version regression was run.
    # Older artifact-owned callers may still provide candidate_available until
    # they are migrated to snapshot registration.
    if request.candidate_version not in known_snapshot_versions and not candidate_available:
        raise EvaluationRequestValidationError(
            "E_CANDIDATE_NOT_FOUND",
            f"Candidate version {request.candidate_version} is not available for evaluation.",
        )

    # Once Project Intelligence has more than its initial snapshot, the
    # candidate version is no longer an opaque caller flag: it must resolve to
    # one of the immutable uploaded snapshots and its component presence must
    # agree with the declared change type.  The single-baseline branch keeps
    # the existing artifact-owned Skill Ablation path compatible.
    runtime_comparability = None
    if len(snapshot_history) > 1:
        baseline_snapshot = next(
            (item for item in snapshot_history if item.version == request.baseline_version),
            None,
        )
        candidate_snapshot = next(
            (item for item in snapshot_history if item.version == request.candidate_version),
            None,
        )
        if baseline_snapshot is None:
            raise EvaluationRequestValidationError(
                "E_BASELINE_NOT_FOUND",
                f"Baseline snapshot {request.baseline_version} is not present in Project Intelligence history.",
            )
        if candidate_snapshot is None:
            raise EvaluationRequestValidationError(
                "E_CANDIDATE_NOT_FOUND",
                f"Candidate snapshot {request.candidate_version} is not present in Project Intelligence history.",
            )
        baseline_component = _component_in_snapshot(baseline_snapshot, request, pair_members=pair_members)
        candidate_component = _component_in_snapshot(candidate_snapshot, request, pair_members=pair_members)
        expected = {
            "add": (False, True),
            "remove": (True, False),
            "modify": (True, True),
            "replace": (True, True),
        }[request.change_type]
        observed = (baseline_component is not None, candidate_component is not None)
        if observed != expected:
            raise EvaluationRequestValidationError(
                "E_VERSION_NOT_COMPARABLE",
                f"Change type {request.change_type} expects baseline/candidate component presence {expected}, observed {observed}.",
            )
        runtime_comparability = compare_runtime_snapshots(baseline_snapshot, candidate_snapshot)
        # Snapshot/runtime differences remain evidence for later Readiness, but
        # a request only freezes the target and versions.  Requiring a runnable
        # runtime here makes first import impossible before runtime review.

    if candidate_component_name is not None and candidate_component_name != request.component_name:
        raise EvaluationRequestValidationError(
            "E_CANDIDATE_COMPONENT_MISMATCH",
            f"Candidate artifact targets {candidate_component_name}, not {request.component_name}.",
        )

    return request.model_copy(update={"pair_members": pair_members, "status": "validated", "runtime_comparability": runtime_comparability})


def _component_in_snapshot(snapshot, request: EvaluationRequest, *, pair_members: list[str] | None = None):
    if request.component_type == "skill_pair" and pair_members:
        member_names = set(pair_members)
        snapshot_skills = {
            item.name
            for item in snapshot.capability_registry
            if item.component_type == "skill"
        }
        return request if member_names.issubset(snapshot_skills) else None
    return next(
        (
            item
            for item in snapshot.capability_registry
            if item.component_type == request.component_type and item.name == request.component_name
        ),
        None,
    )


def validate_skill_artifacts(request: EvaluationRequest, artifacts: list[object]) -> None:
    """Validate the current Skill Ablation artifact matrix for this request."""

    if request.component_type != "skill":
        raise EvaluationRequestValidationError(
            "E_COMPONENT_TYPE_UNSUPPORTED",
            "Skill artifact validation only accepts component_type=skill.",
        )
    if request.change_type not in {"remove", "replace"}:
        raise EvaluationRequestValidationError(
            "E_CHANGE_TYPE_UNSUPPORTED",
            "Skill v1.0 execution supports remove and replace through the Skill Ablation matrix.",
        )
    if not artifacts:
        raise EvaluationRequestValidationError(
            "E_CANDIDATE_NOT_FOUND",
            "At least one persisted Skill Ablation artifact is required.",
        )

    project_names: set[str] = set()
    component_names: set[str] = set()
    interventions: set[str] = set()
    for artifact in artifacts:
        project_name = getattr(artifact, "project_name", None)
        contract = getattr(artifact, "contract", None)
        evidence = getattr(artifact, "evidence", None)
        project_names.add(str(project_name))
        component_names.add(str(getattr(contract, "skill_name", "")))
        interventions.add(str(getattr(evidence, "intervention", "")))

    if project_names != {request.project_id}:
        raise EvaluationRequestValidationError(
            "E_PROJECT_MISMATCH",
            "All Skill Ablation artifacts must belong to the requested project.",
        )
    if component_names != {request.component_name}:
        raise EvaluationRequestValidationError(
            "E_CANDIDATE_COMPONENT_MISMATCH",
            "All Skill Ablation artifacts must target the requested Skill.",
        )

    required = {"enabled", "disabled", "replacement"}
    missing = sorted(required - interventions)
    if missing:
        raise EvaluationRequestValidationError(
            "E_SKILL_MATRIX_INCOMPLETE",
            f"Skill Evaluation requires baseline, removal, and replacement evidence; missing {missing}.",
        )


class EvaluationRequestRepository:
    """Persistence boundary for validated Evaluation Requests."""

    _KIND = "evaluation_request"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, request: EvaluationRequest) -> EvaluationRequest:
        existing = self.store.get(self._KIND, request.request_id, EvaluationRequest)
        if existing and existing.model_dump(mode="json") != request.model_dump(mode="json"):
            raise EvaluationRequestValidationError(
                "E_REQUEST_CONFLICT",
                f"EvaluationRequest {request.request_id} already exists with different contents.",
            )
        self.store.save(self._KIND, request.request_id, request.project_id, request)
        return request

    def get(self, project_id: str, request_id: str) -> EvaluationRequest | None:
        request = self.store.get(self._KIND, request_id, EvaluationRequest)
        if request is None or request.project_id != project_id:
            return None
        return request

    def list(self, project_id: str) -> list[EvaluationRequest]:
        return sorted(
            self.store.list(self._KIND, EvaluationRequest, project_id),
            key=lambda item: item.created_at,
        )

    def bind_scope(self, request: EvaluationRequest, scope_id: str) -> EvaluationRequest:
        if request.evaluation_scope_id not in {None, scope_id}:
            raise EvaluationRequestValidationError(
                "E_SCOPE_CONFLICT",
                f"EvaluationRequest {request.request_id} is already bound to a different Evaluation Scope.",
            )
        bound = request.model_copy(update={"evaluation_scope_id": scope_id})
        self.store.save(self._KIND, bound.request_id, bound.project_id, bound)
        return bound


__all__ = [
    "EvaluationRequest",
    "EvaluationRequestRepository",
    "EvaluationRequestValidationError",
    "RequestChangeType",
    "RequestComponentType",
    "RequestStatus",
    "validate_evaluation_request",
    "validate_skill_artifacts",
]
