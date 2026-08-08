"""Persisted ProductEvaluationReport reference bound to one Evaluation Run."""

from __future__ import annotations

from datetime import datetime, timezone

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationReportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "aig.evaluation-report-record.v1"
    report_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    source: Literal["run", "import"] = "run"
    run_id: str | None = Field(default=None, min_length=1)
    evaluation_plan_id: str | None = Field(default=None, min_length=1)
    scope_id: str | None = Field(default=None, min_length=16)
    report: dict[str, object]
    gate: dict[str, object] | None = None
    created_at: str = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_source_identity(self) -> "EvaluationReportRecord":
        if self.source == "run" and not self.run_id:
            raise ValueError("Run-backed Evaluation Report records require run_id.")
        return self


class EvaluationReportRepository:
    _KIND = "evaluation_report"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, record: EvaluationReportRecord) -> EvaluationReportRecord:
        existing = self.store.get(self._KIND, record.report_id, EvaluationReportRecord)
        if existing and existing.model_dump(mode="json") != record.model_dump(mode="json"):
            raise ValueError(f"Evaluation Report {record.report_id} already exists with different contents.")
        self.store.save(self._KIND, record.report_id, record.project_id, record)
        return record

    def get(self, project_id: str, report_id: str) -> EvaluationReportRecord | None:
        record = self.store.get(self._KIND, report_id, EvaluationReportRecord)
        if record is None or record.project_id != project_id:
            return None
        return record

    def by_run(self, project_id: str, run_id: str) -> EvaluationReportRecord | None:
        records = self.store.list(self._KIND, EvaluationReportRecord, project_id)
        return next((item for item in records if item.run_id == run_id), None)

    def list(self, project_id: str) -> list[EvaluationReportRecord]:
        return sorted(
            self.store.list(self._KIND, EvaluationReportRecord, project_id),
            key=lambda item: item.created_at,
        )


__all__ = ["EvaluationReportRecord", "EvaluationReportRepository"]
