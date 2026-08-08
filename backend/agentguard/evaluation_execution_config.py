from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .store import Store


OracleType = Literal["rule_based", "frozen_lookup", "structured_state"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationExecutionConfiguration(BaseModel):
    """Server-owned target and Oracle contract selected by a GUI Run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["aig.evaluation-execution-config.v1"] = "aig.evaluation-execution-config.v1"
    config_id: str = Field(default_factory=lambda: f"execution_config_{uuid4().hex}", min_length=1)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    manifest_path: str = Field(min_length=1)
    cache_root: str = Field(min_length=1)
    run_root_parent: str = Field(min_length=1)
    oracle_command: list[str] = Field(min_length=1, max_length=32)
    oracle_id: str = Field(min_length=1, max_length=160)
    oracle_type: OracleType = "rule_based"
    oracle_version: str = Field(default="1.0", min_length=1, max_length=80)
    oracle_cwd: str | None = Field(default=None, min_length=1)
    target_provider_binding_id: str | None = Field(default=None, min_length=1)
    runtime_draft_id: str | None = Field(default=None, min_length=1)
    snapshot_version: str | None = Field(default=None, min_length=1)
    snapshot_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    created_at: str = Field(default_factory=_now)


class EvaluationExecutionConfigurationMetadata(BaseModel):
    """Non-secret metadata exposed to the GUI; command and server paths stay private."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_id: str
    project_id: str
    name: str
    oracle_id: str
    oracle_type: OracleType
    oracle_version: str
    target_provider_binding_id: str | None = None
    runtime_draft_id: str | None = None
    snapshot_version: str | None = None
    status: Literal["ready", "stale"] = "ready"
    created_at: str


class EvaluationExecutionConfigurationRepository:
    _KIND = "evaluation_execution_configuration"

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, config: EvaluationExecutionConfiguration) -> EvaluationExecutionConfiguration:
        existing = self.store.get(self._KIND, config.config_id, EvaluationExecutionConfiguration)
        if existing and existing.model_dump(mode="json") != config.model_dump(mode="json"):
            raise ValueError(f"Evaluation Execution Configuration {config.config_id} already exists with different contents.")
        self.store.save(self._KIND, config.config_id, config.project_id, config)
        return config

    def get(self, project_id: str, config_id: str) -> EvaluationExecutionConfiguration | None:
        config = self.store.get(self._KIND, config_id, EvaluationExecutionConfiguration)
        if config is None or config.project_id != project_id:
            return None
        return config

    def list(self, project_id: str) -> list[EvaluationExecutionConfiguration]:
        return sorted(
            self.store.list(self._KIND, EvaluationExecutionConfiguration, project_id),
            key=lambda item: item.created_at,
        )


def metadata(config: EvaluationExecutionConfiguration) -> EvaluationExecutionConfigurationMetadata:
    return EvaluationExecutionConfigurationMetadata(
        config_id=config.config_id,
        project_id=config.project_id,
        name=config.name,
        oracle_id=config.oracle_id,
        oracle_type=config.oracle_type,
        oracle_version=config.oracle_version,
        target_provider_binding_id=config.target_provider_binding_id,
        runtime_draft_id=config.runtime_draft_id,
        snapshot_version=config.snapshot_version,
        created_at=config.created_at,
    )


__all__ = [
    "EvaluationExecutionConfiguration",
    "EvaluationExecutionConfigurationMetadata",
    "EvaluationExecutionConfigurationRepository",
    "OracleType",
    "metadata",
]
