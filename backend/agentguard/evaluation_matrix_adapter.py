"""Normalize a common scenario matrix into the immutable Evidence Bundle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .evaluation_adapters import AdapterContext
from .interaction_evaluation import InteractionRelationshipProfile
from .evaluation_planning import EvaluationScenario, scenario_hash_for
from .evidence_bundle import (
    EvidenceCondition,
    EvidenceFact,
    EvidenceIntegrity,
    EvidenceMetric,
    EvidenceRecord,
    ImmutableEvidenceBundle,
)
from .interaction_matrix import EvaluationMatrixArtifact, InteractionTrialResult
from .interaction_runner import IndependentOracleResult
from .scenario_contracts import verify_scenario_trace_contract


class EvaluationMatrixEvidenceAdapter:
    """Admission adapter for target-produced scenario matrix artifacts."""

    def __init__(
        self,
        evaluation_type: str,
        expected_condition_kinds: tuple[str, ...],
        *,
        fact_type: str = "evaluation_behavior",
        require_live_provider_metadata: bool = True,
    ) -> None:
        self.evaluation_type = evaluation_type
        self.expected_condition_kinds = expected_condition_kinds
        self.fact_type = fact_type
        self.require_live_provider_metadata = require_live_provider_metadata

    def adapt(self, artifact: object, *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError("Evaluation matrix adapter context type mismatch.")
        matrix = self._parse_artifact(artifact)
        if matrix.evaluation_type != self.evaluation_type:
            raise ValueError("Evaluation matrix artifact type does not match the adapter.")
        if matrix.evaluation_plan_id != context.evaluation_plan_id:
            raise ValueError("Evaluation matrix artifact does not match the Evaluation Plan.")
        if matrix.scope_id != context.scope_id:
            raise ValueError("Evaluation matrix scope does not match the immutable adapter context.")
        if tuple(matrix.condition_kinds) != self.expected_condition_kinds:
            raise ValueError(
                f"Evaluation matrix must declare conditions {list(self.expected_condition_kinds)}; "
                f"got {matrix.condition_kinds}."
            )
        if matrix.scenario_readiness.evaluation_plan_id != matrix.evaluation_plan_id:
            raise ValueError("Evaluation matrix readiness does not match the Evaluation Plan.")
        self._validate_hash(matrix)
        scenarios = self._parse_scenarios(matrix.scenarios)
        hypothesis = self._validate_interaction_hypothesis(matrix, scenarios)
        expected_keys = {
            (scenario.scenario_id, condition, repetition_index)
            for scenario in scenarios.values()
            for condition in self.expected_condition_kinds
            for repetition_index in range(1, scenario.repetition_count + 1)
        }
        observed_keys: set[tuple[str, str, int]] = set()
        conditions: list[EvidenceCondition] = []
        facts: list[EvidenceFact] = []
        records: list[EvidenceRecord] = []
        for index, raw in enumerate(matrix.conditions):
            result = InteractionTrialResult.model_validate(raw)
            key = (result.scenario_id, result.condition_kind, result.repetition_index)
            if key in observed_keys or key not in expected_keys:
                raise ValueError("Evaluation matrix contains a duplicate or unknown scenario condition.")
            if result.category != scenarios[result.scenario_id].category:
                raise ValueError("Evaluation matrix condition category does not match its scenario.")
            trace_violations = verify_scenario_trace_contract(
                scenarios[result.scenario_id].input_contract,
                result.trace,
                condition_kind=result.condition_kind,
            )
            if trace_violations:
                raise ValueError(
                    f"Evaluation matrix trace violates the scenario contract for {result.scenario_id}: "
                    + "; ".join(trace_violations)
                )
            provider_metadata_present = bool(result.provider_request_ids or result.usage)
            if provider_metadata_present and (not result.provider_request_ids or not result.usage):
                raise ValueError("Evaluation matrix Provider metadata must include both request IDs and usage.")
            if self.require_live_provider_metadata and (
                not result.provider_request_ids or not result.usage or not result.output_artifact_ref
            ):
                raise ValueError(
                    f"Evaluation matrix condition {result.scenario_id}/{result.condition_kind} lacks live target metadata."
                )
            oracle = IndependentOracleResult.model_validate(result.oracle)
            refs = [_opaque_ref(ref) for ref in [*result.evidence_refs, *oracle.evidence_refs]]
            refs = list(dict.fromkeys(refs))
            observations = {
                "scenario_id": result.scenario_id,
                "scenario_category": result.category,
                "condition_kind": result.condition_kind,
                "trace_event_count": len(result.trace),
                "output_recorded": True,
                "latency_ms": result.metrics["latency_ms"],
                "cost_usd": result.metrics["cost_usd"],
                "oracle_verified": True,
                "oracle_outcome": oracle.outcome,
                "oracle_type": oracle.oracle_type,
                "oracle_version": oracle.oracle_version,
                "oracle_verification_scopes": list(oracle.verification_scopes),
                "oracle_scope_limitations": list(oracle.scope_limitations),
                "oracle_failure_types_evaluated": list(oracle.failure_types_evaluated),
                "provider_request_ids": list(result.provider_request_ids),
                "usage": dict(result.usage),
                "output_artifact_ref": result.output_artifact_ref,
                **result.observations,
            }
            observed_keys.add(key)
            label = result.label
            conditions.append(EvidenceCondition(
                condition_id=_opaque_id("condition", f"{matrix.evaluation_name}:{result.scenario_id}:{result.condition_kind}:{result.repetition_index}"),
                scenario_id=result.scenario_id,
                repetition_id=result.repetition_id,
                repetition_index=result.repetition_index,
                experiment_id=context.experiment_ids_by_condition.get(result.condition_kind),
                label=label,
                observations=observations,
                evidence_refs=refs,
            ))
            facts.append(EvidenceFact(
                fact_id=_opaque_id("fact", f"{matrix.evaluation_name}:{result.scenario_id}:{result.condition_kind}:{result.repetition_index}"),
                label=f"{label} observed behavior",
                fact_type=self.fact_type,
                value=observations,
                evidence_level="verified",
                evidence_refs=refs,
            ))
            records.append(EvidenceRecord(
                record_id=_opaque_id("record", f"{matrix.evaluation_id}:{index}"),
                record_type="trial_result",
                source_ref=_opaque_ref(f"{matrix.evaluation_id}:{index}"),
                payload={
                    "scenario_id": result.scenario_id,
                    "condition_kind": result.condition_kind,
                    "repetition_id": result.repetition_id,
                    "repetition_index": result.repetition_index,
                    "observations": observations,
                    "trace": result.trace,
                    "output": result.output,
                    "metrics": result.metrics,
                    "oracle": result.oracle,
                    "provider_request_ids": result.provider_request_ids,
                    "usage": result.usage,
                    "output_artifact_ref": result.output_artifact_ref,
                },
                evidence_refs=refs,
            ))
        if observed_keys != expected_keys:
            raise ValueError(
                "Evaluation matrix must contain exactly one result for every scenario and declared condition."
            )
        metrics = [
            EvidenceMetric(metric_id=_opaque_id("metric", key), name=key, value=value, unit="reported")
            for key, value in matrix.metrics.items()
            if isinstance(value, (int, float, str))
        ]
        integrity = EvidenceIntegrity.model_validate(matrix.integrity)
        return ImmutableEvidenceBundle(
            evaluation_id=matrix.evaluation_id,
            project_id=context.project_id,
            evaluation_name=context.evaluation_name,
            evaluation_type=self.evaluation_type,
            evaluation_request_id=context.evaluation_request_id,
            baseline_version=context.baseline_version,
            candidate_version=context.candidate_version,
            scope_id=context.scope_id,
            evaluation_plan_id=context.evaluation_plan_id,
            artifact_manifest_hash=matrix.artifact_manifest_hash,
            conditions=conditions,
            facts=facts,
            records=records,
            metrics=metrics,
            summary={key: value for key, value in matrix.metrics.items() if isinstance(value, (int, float, str))},
            type_data={
                "evaluation_name": matrix.evaluation_name,
                "scenario_suite": matrix.scenario_suite,
                "suite_aggregate": matrix.suite_aggregate,
                "interaction_hypothesis": hypothesis.model_dump(mode="json") if hypothesis else None,
                "condition_kinds": list(matrix.condition_kinds),
                "scenario_ids": [item.scenario_id for item in scenarios.values()],
                "scenario_hashes": {item.scenario_id: item.scenario_hash for item in scenarios.values()},
                "scenario_provenance_sources": {
                    item.scenario_id: item.scenario_provenance.model_dump(mode="json")
                    for item in scenarios.values()
                    if item.scenario_provenance is not None
                },
                "scenario_categories": [item.category for item in scenarios.values()],
                "scenario_input_profiles": {
                    item.scenario_id: item.input_contract.profile_id for item in scenarios.values()
                },
                "scenario_input_requirements": {
                    item.scenario_id: bool(item.input_contract.requirements) for item in scenarios.values()
                },
                "scenario_trace_contracts": {
                    item.scenario_id: item.input_contract.trace.model_dump(mode="json")
                    for item in scenarios.values()
                },
                "interaction_model": "scenario_matrix" if hypothesis else None,
                "scenario_readiness_required": True,
                "scenario_readiness_status": matrix.scenario_readiness.status,
                "matrix_artifact_schema": matrix.schema_version,
            },
            integrity=integrity,
        )

    @staticmethod
    def _parse_artifact(artifact: object) -> EvaluationMatrixArtifact:
        if isinstance(artifact, EvaluationMatrixArtifact):
            return artifact
        if isinstance(artifact, Mapping):
            return EvaluationMatrixArtifact.model_validate(artifact)
        raise TypeError("Evaluation matrix adapter expects an EvaluationMatrixArtifact or JSON object.")

    @staticmethod
    def _parse_scenarios(raw_scenarios: list[dict[str, object]]) -> dict[str, EvaluationScenario]:
        result: dict[str, EvaluationScenario] = {}
        for raw in raw_scenarios:
            scenario = EvaluationScenario.model_validate(raw)
            if scenario.scenario_id in result:
                raise ValueError("Evaluation matrix cannot contain duplicate scenario IDs.")
            if not scenario.scenario_hash:
                raise ValueError(f"Evaluation scenario {scenario.scenario_id} is not frozen.")
            if scenario_hash_for(raw) != scenario.scenario_hash:
                raise ValueError(f"Evaluation scenario {scenario.scenario_id} hash does not match its content.")
            provenance = scenario.scenario_provenance
            if provenance is None or provenance.frozen is not True or provenance.scenario_hash != scenario.scenario_hash:
                raise ValueError(f"Evaluation scenario {scenario.scenario_id} provenance is not frozen.")
            result[scenario.scenario_id] = scenario
        return result

    def _validate_interaction_hypothesis(
        self,
        matrix: EvaluationMatrixArtifact,
        scenarios: dict[str, EvaluationScenario],
    ) -> InteractionRelationshipProfile | None:
        if self.evaluation_type != "skill_pair_evaluation":
            return None
        if matrix.interaction_hypothesis is None:
            raise ValueError("Skill Pair matrix requires an interaction hypothesis.")
        hypothesis = InteractionRelationshipProfile.model_validate(matrix.interaction_hypothesis)
        if not hypothesis.hypothesis_hash:
            raise ValueError("Skill Pair matrix interaction hypothesis must be content-addressed.")
        for scenario in scenarios.values():
            provenance = scenario.scenario_provenance
            if provenance is None or provenance.relationship_hypothesis_hash != hypothesis.hypothesis_hash:
                raise ValueError(
                    f"Evaluation scenario {scenario.scenario_id} is not bound to the interaction hypothesis."
                )
        return hypothesis

    @staticmethod
    def _validate_hash(matrix: EvaluationMatrixArtifact) -> None:
        payload = matrix.model_dump(mode="json")
        observed = payload.pop("artifact_manifest_hash")
        expected = "sha256:" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if observed != expected:
            raise ValueError("Evaluation matrix artifact hash does not match its contents.")


def _opaque_ref(raw_ref: str) -> str:
    return "evidence_" + hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()[:16]


def _opaque_id(prefix: str, raw_value: str) -> str:
    return prefix + "_" + hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]


__all__ = ["EvaluationMatrixEvidenceAdapter"]
