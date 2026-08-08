"""Persisted status envelope for one unified Evaluation execution."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .store import Store


RunStatus = Literal["running", "completed", "failed"]
RunStage = Literal["execution", "evidence", "analysis", "completed", "failed"]
RunEventStatus = Literal["running", "completed", "failed"]
FailureClassification = Literal[
    "validation_failure",
    "target_failure",
    "oracle_failure",
    "provider_failure",
    "infrastructure_failure",
    "report_failure",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_ref(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class EvaluationRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    stage: RunStage
    status: RunEventStatus
    detail: str = Field(min_length=1)
    created_at: str = Field(default_factory=_now)


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-run.v1"] = "aig.evaluation-run.v1"
    run_id: str = Field(default_factory=lambda: f"evaluation_run_{uuid4().hex}", min_length=1)
    evaluation_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    evaluation_request_id: str = Field(min_length=1)
    evaluation_plan_id: str = Field(min_length=1)
    execution_config_id: str | None = None
    scope_id: str = Field(min_length=16)
    status: RunStatus
    current_stage: RunStage = "execution"
    readiness_ref: str | None = None
    matrix_artifact_ref: str | None = None
    evidence_bundle_ref: str | None = None
    report_ref: str | None = None
    failure_classification: FailureClassification | None = None
    artifact: dict[str, object] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    events: list["EvaluationRunEvent"] = Field(default_factory=list)
    error: str | None = None
    started_at: str = Field(default_factory=_now)
    completed_at: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


def append_event(
    run: EvaluationRun,
    *,
    stage: RunStage,
    status: RunEventStatus,
    detail: str,
) -> EvaluationRun:
    event = EvaluationRunEvent(
        event_id=f"{run.run_id}:{len(run.events) + 1}",
        stage=stage,
        status=status,
        detail=detail,
    )
    return run.model_copy(update={"events": [*run.events, event]})


class EvaluationRunRepository:
    _KIND = "evaluation_run"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, run: EvaluationRun) -> EvaluationRun:
        existing = self.store.get(self._KIND, run.run_id, EvaluationRun)
        if existing:
            immutable_fields = (
                "run_id",
                "evaluation_id",
                "project_id",
                "evaluation_request_id",
                "evaluation_plan_id",
                "execution_config_id",
                "scope_id",
                "started_at",
                "created_at",
            )
            if any(getattr(existing, field) != getattr(run, field) for field in immutable_fields):
                raise ValueError(f"Evaluation Run {run.run_id} already exists with different identity.")
            if existing.artifact is not None and run.artifact != existing.artifact:
                raise ValueError(f"Evaluation Run {run.run_id} already exists with different execution evidence.")
            if existing.status == "failed" and run.status != "failed":
                raise ValueError(f"Failed Evaluation Run {run.run_id} cannot be reopened.")
            if existing.status == "completed" and run.status == "running":
                raise ValueError(f"Completed Evaluation Run {run.run_id} cannot return to running.")
        self.store.save(self._KIND, run.run_id, run.project_id, run)
        return run

    def get(self, project_id: str, run_id: str) -> EvaluationRun | None:
        run = self.store.get(self._KIND, run_id, EvaluationRun)
        if run is None or run.project_id != project_id:
            return None
        return run

    def list_for_request(self, project_id: str, evaluation_request_id: str) -> list[EvaluationRun]:
        return sorted(
            [
                item
                for item in self.store.list(self._KIND, EvaluationRun, project_id)
                if item.evaluation_request_id == evaluation_request_id
            ],
            key=lambda item: item.updated_at,
        )


__all__ = [
    "EvaluationRun",
    "EvaluationRunEvent",
    "EvaluationRunRepository",
    "FailureClassification",
    "RunStage",
    "RunStatus",
    "append_event",
    "content_ref",
]
