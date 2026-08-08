"""Common adapter boundary for heterogeneous Agent Evolution evaluations.

The layer intentionally knows nothing about HTML, Analyst prompts, or product
decisions.  Concrete adapters only normalize persisted evaluation artifacts
into the future common Immutable Evidence Bundle contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .evidence_bundle import EvaluationType, ImmutableEvidenceBundle

ArtifactT = TypeVar("ArtifactT")


class EvaluationAdapterError(ValueError):
    """Raised for an invalid adapter registration or adaptation request."""


class AdapterContext(BaseModel):
    """Stable context supplied by the evaluation orchestration layer."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    evaluation_name: str = Field(min_length=1)
    evaluation_type: EvaluationType
    component_name: str | None = None
    source_ref: str = Field(min_length=1)
    evaluation_request_id: str | None = None
    baseline_version: str | None = None
    candidate_version: str | None = None
    scope_id: str | None = None
    product_definition_ref: str | None = None
    evaluation_plan_id: str | None = None
    experiment_ids_by_condition: dict[str, str] = Field(default_factory=dict)
    scenario_ids_by_trial_ref: dict[str, str] = Field(default_factory=dict)


class EvaluationAdapter(Protocol, Generic[ArtifactT]):
    """Adapter contract for one evaluation type.

    An implementation may parse, normalize, hash, and classify evidence.  It
    must not call the Product Evaluation Analyst or decide product impact.
    """

    @property
    def evaluation_type(self) -> EvaluationType: ...

    def adapt(
        self,
        artifact: ArtifactT,
        *,
        context: AdapterContext,
    ) -> ImmutableEvidenceBundle: ...


@dataclass(frozen=True)
class RegisteredAdapter:
    evaluation_type: EvaluationType
    adapter: EvaluationAdapter[object]


class EvaluationAdapterLayer:
    """Explicit registry and dispatch boundary for Evaluation Adapters."""

    def __init__(self) -> None:
        self._adapters: dict[EvaluationType, RegisteredAdapter] = {}

    def register(self, adapter: EvaluationAdapter[object]) -> None:
        evaluation_type = adapter.evaluation_type
        if evaluation_type in self._adapters:
            raise EvaluationAdapterError(
                f"An Evaluation Adapter is already registered for {evaluation_type}."
            )
        self._adapters[evaluation_type] = RegisteredAdapter(evaluation_type, adapter)

    def supports(self, evaluation_type: str) -> bool:
        return evaluation_type in self._adapters

    def adapter_for(self, evaluation_type: str) -> EvaluationAdapter[object]:
        try:
            return self._adapters[evaluation_type].adapter
        except KeyError as error:
            raise EvaluationAdapterError(
                f"No Evaluation Adapter is registered for {evaluation_type}."
            ) from error

    def adapt(
        self,
        evaluation_type: str,
        artifact: object,
        *,
        context: AdapterContext,
    ) -> ImmutableEvidenceBundle:
        if context.evaluation_type != evaluation_type:
            raise EvaluationAdapterError(
                "Adapter context evaluation_type does not match the dispatch type."
            )
        adapter = self.adapter_for(evaluation_type)
        result = adapter.adapt(artifact, context=context)
        if not isinstance(result, ImmutableEvidenceBundle):
            raise EvaluationAdapterError(
                "Adapter must return the standard Immutable Evidence Bundle."
            )
        if result.evaluation_type != evaluation_type:
            raise EvaluationAdapterError(
                "Adapter returned a Bundle for a different evaluation type."
            )
        if not result.artifact_manifest_hash:
            raise EvaluationAdapterError(
                "Adapter returned an Immutable Evidence Bundle without an artifact hash."
            )
        return result

    def registered_types(self) -> tuple[EvaluationType, ...]:
        return tuple(sorted(self._adapters))


__all__ = [
    "AdapterContext",
    "EvaluationAdapter",
    "EvaluationAdapterError",
    "EvaluationAdapterLayer",
    "EvaluationType",
    "ImmutableEvidenceBundle",
]
