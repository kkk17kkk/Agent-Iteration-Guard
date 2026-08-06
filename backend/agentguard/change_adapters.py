"""Adapters for non-Skill Agent Evolution change artifacts.

Each adapter accepts its own persisted raw shape and emits only the common
Level 1 Immutable Evidence Bundle.  These adapters do not infer product impact
and do not decide whether a release is safe.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Protocol

from .evaluation_adapters import AdapterContext, EvaluationAdapterLayer
from .evaluation_planning import ScenarioProvenance, scenario_hash_for
from .evidence_bundle import (
    EvidenceCondition,
    EvidenceFact,
    EvidenceIntegrity,
    EvidenceMetric,
    EvidenceRecord,
    ImmutableEvidenceBundle,
)
from .skill_ablation_adapter import SkillAblationEvaluationAdapter
from .interaction_evaluation import InteractionRelationshipProfile
from .scenario_contracts import (
    EvaluationReadinessResult,
    ScenarioInputContract,
    verify_scenario_trace_contract,
)
from .tool_regression import tool_condition_observations, validate_tool_regression_artifact


class ChangeArtifactAdapter(Protocol):
    evaluation_type: str

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle: ...


def _required_text(artifact: Mapping[str, object], key: str) -> str:
    value = artifact.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required for {artifact.get('evaluation_type', 'change')} evaluation artifact.")
    return value


def _required_refs(artifact: Mapping[str, object]) -> list[str]:
    refs = artifact.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs):
        raise ValueError("change evaluation artifact requires non-empty evidence_refs.")
    return [_opaque_ref(ref) for ref in refs]


def _validate_independent_oracle(oracle: Mapping[str, object]) -> None:
    if oracle.get("status") != "verified":
        raise ValueError("Each interaction condition requires a verified independent oracle.")
    if oracle.get("oracle_type") not in {"rule_based", "frozen_lookup", "structured_state"}:
        raise ValueError("Interaction Oracle must declare a deterministic oracle_type.")
    if not isinstance(oracle.get("oracle_version"), str) or not str(oracle["oracle_version"]).strip():
        raise ValueError("Interaction Oracle must declare oracle_version.")
    validation_input = oracle.get("validation_input")
    if not isinstance(validation_input, Mapping) or not validation_input:
        raise ValueError("Interaction Oracle must record non-empty validation_input.")
    oracle_refs = oracle.get("evidence_refs")
    if not isinstance(oracle_refs, list) or not oracle_refs or any(not isinstance(ref, str) or not ref for ref in oracle_refs):
        raise ValueError("Each Interaction Oracle requires evidence_refs.")


def _validate_frozen_interaction_scenario(
    scenario: Mapping[str, object],
    *,
    hypothesis_hash: str,
) -> ScenarioProvenance:
    scenario_id = scenario.get("scenario_id")
    scenario_hash = scenario.get("scenario_hash")
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError("Each interaction scenario requires scenario_id.")
    if not isinstance(scenario_hash, str) or not scenario_hash.strip():
        raise ValueError(f"Interaction scenario {scenario_id} must be frozen with scenario_hash.")
    expected_hash = scenario_hash_for(scenario)
    if scenario_hash != expected_hash:
        raise ValueError(f"Interaction scenario {scenario_id} scenario_hash does not match its content.")
    raw_provenance = scenario.get("scenario_provenance")
    if not isinstance(raw_provenance, Mapping):
        raise ValueError(f"Interaction scenario {scenario_id} requires scenario_provenance.")
    try:
        provenance = ScenarioProvenance.model_validate(raw_provenance)
    except ValueError as error:
        raise ValueError(f"Interaction scenario {scenario_id} has invalid scenario_provenance.") from error
    if provenance.scenario_hash != scenario_hash or provenance.frozen is not True:
        raise ValueError(f"Interaction scenario {scenario_id} provenance is not frozen to its content.")
    if provenance.relationship_hypothesis_hash != hypothesis_hash:
        raise ValueError(f"Interaction scenario {scenario_id} is not bound to the interaction hypothesis.")
    return provenance


def _required_mapping(artifact: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = artifact.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object in a change evaluation artifact.")
    return value


def _manifest(artifact: Mapping[str, object]) -> str:
    value = _required_text(artifact, "artifact_manifest_hash")
    if len(value) < 16:
        raise ValueError("artifact_manifest_hash must be content-addressed and at least 16 characters.")
    return value


def _bundle(
    *,
    artifact: Mapping[str, object],
    context: AdapterContext,
    conditions: list[EvidenceCondition],
    facts: list[EvidenceFact],
    records: list[EvidenceRecord],
    type_data: dict[str, object],
) -> ImmutableEvidenceBundle:
    metrics_raw = artifact.get("metrics", {})
    if not isinstance(metrics_raw, Mapping):
        raise ValueError("metrics must be an object in a change evaluation artifact.")
    metrics = [
        EvidenceMetric(metric_id=_opaque_id("metric", str(key)), name=str(key), value=value, unit="reported")
        for key, value in metrics_raw.items()
        if isinstance(value, (int, float, str))
    ]
    integrity_raw = artifact.get("integrity", {"status": "complete"})
    if not isinstance(integrity_raw, Mapping):
        raise ValueError("integrity must be an object in a change evaluation artifact.")
    integrity = EvidenceIntegrity.model_validate(integrity_raw)
    return ImmutableEvidenceBundle(
        evaluation_id=_required_text(artifact, "evaluation_id"),
        project_id=context.project_id,
        evaluation_name=context.evaluation_name,
        evaluation_type=context.evaluation_type,
        evaluation_request_id=context.evaluation_request_id,
        baseline_version=context.baseline_version,
        candidate_version=context.candidate_version,
        evaluation_plan_id=context.evaluation_plan_id,
        artifact_manifest_hash=_manifest(artifact),
        conditions=conditions,
        facts=facts,
        records=records,
        metrics=metrics,
        summary={key: value for key, value in metrics_raw.items() if isinstance(value, (int, float, str))},
        type_data=type_data,
        integrity=integrity,
    )


def _record(artifact: Mapping[str, object], record_type: str, payload: Mapping[str, object], refs: list[str], index: int = 0) -> EvidenceRecord:
    source_ref = str(payload.get("source_ref", f"{record_type}:{index}"))
    return EvidenceRecord(
        record_id=_opaque_id("record", f"{record_type}:{index}:{source_ref}"),
        record_type=record_type,
        source_ref=_opaque_ref(source_ref),
        payload=dict(payload),
        evidence_refs=refs,
    )


class _LegacyToolRegressionEvaluationAdapter:
    evaluation_type = "tool_regression"

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError("Tool Regression adapter context type mismatch.")
        tool_name = _required_text(artifact, "tool_name")
        baseline = _required_mapping(artifact, "baseline_output")
        candidate = _required_mapping(artifact, "candidate_output")
        refs = _required_refs(artifact)
        conditions = [
            EvidenceCondition(condition_id=_opaque_id("condition", "tool:baseline"), label="保留 Tool 实现测试", observations={"output_recorded": bool(baseline)}, evidence_refs=refs),
            EvidenceCondition(condition_id=_opaque_id("condition", "tool:candidate"), label="替换 Tool 实现测试", observations={"output_recorded": bool(candidate)}, evidence_refs=refs),
        ]
        facts = [EvidenceFact(fact_id=_opaque_id("fact", tool_name), label="Tool 行为观察", fact_type="tool_behavior", value={"tool_name": tool_name, "baseline_output_recorded": bool(baseline), "candidate_output_recorded": bool(candidate)}, evidence_level="verified", evidence_refs=refs)]
        records = [_record(artifact, "artifact", {"tool_name": tool_name, "baseline_output": dict(baseline), "candidate_output": dict(candidate)}, refs)]
        return _bundle(artifact=artifact, context=context, conditions=conditions, facts=facts, records=records, type_data={"tool_name": tool_name})


class ToolRegressionEvaluationAdapter(_LegacyToolRegressionEvaluationAdapter):
    """Expose explicit v1 Tool metrics when the artifact provides them."""

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        bundle = super().adapt(artifact, context=context)
        if "baseline_metrics" not in artifact and "candidate_metrics" not in artifact:
            return bundle
        validate_tool_regression_artifact(
            artifact,
            expected_tool_name=str(artifact.get("tool_name") or ""),
        )
        baseline = tool_condition_observations(artifact, "baseline")
        candidate = tool_condition_observations(artifact, "candidate")
        conditions = [
            bundle.conditions[0].model_copy(update={"observations": baseline}),
            bundle.conditions[1].model_copy(update={"observations": candidate}),
        ]
        facts = [bundle.facts[0].model_copy(update={
            "value": {"tool_name": artifact["tool_name"], "baseline": baseline, "candidate": candidate},
        })]
        records = [bundle.records[0].model_copy(update={
            "payload": {
                **bundle.records[0].payload,
                "baseline_metrics": baseline,
                "candidate_metrics": candidate,
            },
        })]
        return bundle.model_copy(update={"conditions": conditions, "facts": facts, "records": records})


class InteractionEvaluationAdapter:
    """Normalize a scenario-aware A-only/B-only/A+B interaction matrix.

    The adapter is deliberately not tied to Skill Ablation.  Skill Pair is
    registered as one instance today; a future Tool + Skill evaluation can
    instantiate the same adapter with another evaluation type and component
    vocabulary.
    """

    def __init__(self, evaluation_type: str = "skill_pair_evaluation") -> None:
        self.evaluation_type = evaluation_type

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError(f"{self.evaluation_type} Interaction adapter context type mismatch.")
        interaction_name = artifact.get("interaction_name") or artifact.get("pair_name")
        if not isinstance(interaction_name, str) or not interaction_name.strip():
            raise ValueError("Interaction artifact requires interaction_name.")
        if context.component_name is not None and interaction_name != context.component_name:
            raise ValueError("Interaction artifact does not match the requested component.")
        if "scenarios" in artifact:
            return self._adapt_scenario_matrix(artifact, context=context, interaction_name=interaction_name)
        raw_conditions = artifact.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise ValueError("Interaction artifact requires a non-empty conditions list.")
        expected = {"a_only", "b_only", "combined"}
        observed: set[str] = set()
        conditions: list[EvidenceCondition] = []
        facts: list[EvidenceFact] = []
        records: list[EvidenceRecord] = []
        for index, raw in enumerate(raw_conditions):
            if not isinstance(raw, Mapping):
                raise ValueError("Each Interaction condition must be an object.")
            condition_kind = raw.get("condition_kind")
            if not isinstance(condition_kind, str) or condition_kind not in expected:
                raise ValueError("Interaction conditions must use a_only, b_only, or combined.")
            if condition_kind in observed:
                raise ValueError("Interaction artifact cannot contain duplicate condition kinds.")
            observed.add(condition_kind)
            label = raw.get("label", condition_kind)
            if not isinstance(label, str) or not label.strip():
                raise ValueError("Interaction condition label is required.")
            refs = raw.get("evidence_refs")
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs):
                raise ValueError("Each Interaction condition requires non-empty evidence_refs.")
            observations = raw.get("observations", raw.get("outcome", {}))
            if not isinstance(observations, Mapping):
                raise ValueError("Interaction condition observations must be an object.")
            trace = raw.get("trace")
            if not isinstance(trace, list) or not trace or any(not isinstance(item, Mapping) for item in trace):
                raise ValueError("Each Interaction condition requires a non-empty structured trace.")
            if any(not isinstance(item.get("event_type"), str) or not str(item["event_type"]).strip() for item in trace):
                raise ValueError("Each Interaction trace event requires event_type.")
            if "output" not in raw:
                raise ValueError("Each Interaction condition requires an output.")
            metrics = raw.get("metrics")
            if not isinstance(metrics, Mapping):
                raise ValueError("Each Interaction condition requires metrics.")
            latency_ms = metrics.get("latency_ms")
            cost_usd = metrics.get("cost_usd")
            if (
                not isinstance(latency_ms, (int, float)) or isinstance(latency_ms, bool) or latency_ms < 0
                or not isinstance(cost_usd, (int, float)) or isinstance(cost_usd, bool) or cost_usd < 0
            ):
                raise ValueError("Each Interaction condition requires non-negative latency_ms and cost_usd.")
            oracle = raw.get("oracle")
            if not isinstance(oracle, Mapping):
                raise ValueError("Each Interaction condition requires a verified independent oracle.")
            _validate_independent_oracle(oracle)
            oracle_refs = oracle["evidence_refs"]
            if not isinstance(oracle_refs, list) or not oracle_refs or any(not isinstance(ref, str) or not ref for ref in oracle_refs):
                raise ValueError("Each Interaction oracle requires evidence_refs.")
            normalized_refs = [_opaque_ref(ref) for ref in refs]
            normalized_refs.extend(_opaque_ref(ref) for ref in oracle_refs if _opaque_ref(ref) not in normalized_refs)
            normalized_observations = {
                "condition_kind": condition_kind,
                "trace_event_count": len(trace),
                "output_recorded": True,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "oracle_verified": True,
                "oracle_outcome": oracle.get("outcome", "unrecorded"),
                "oracle_type": oracle["oracle_type"],
                "oracle_version": oracle["oracle_version"],
                **dict(observations),
            }
            conditions.append(EvidenceCondition(
                condition_id=_opaque_id("condition", f"{interaction_name}:{condition_kind}"),
                experiment_id=context.experiment_ids_by_condition.get(condition_kind),
                label=label,
                observations=normalized_observations,
                evidence_refs=normalized_refs,
            ))
            facts.append(EvidenceFact(
                fact_id=_opaque_id("fact", f"{interaction_name}:{condition_kind}"),
                label=f"{label} observed behavior",
                fact_type="interaction_behavior",
                value=normalized_observations,
                evidence_level="verified",
                evidence_refs=normalized_refs,
            ))
            records.append(_record(
                artifact,
                "trial_result",
                {
                    "interaction_name": interaction_name,
                    "condition_kind": condition_kind,
                    "observations": normalized_observations,
                    "trace": list(trace),
                    "output": raw["output"],
                    "oracle": dict(oracle),
                },
                normalized_refs,
                index,
            ))
        if observed != expected:
            raise ValueError(f"Interaction artifact must cover exactly {sorted(expected)}; got {sorted(observed)}.")
        _required_refs(artifact)
        return _bundle(
            artifact=artifact,
            context=context,
            conditions=conditions,
            facts=facts,
            records=records,
            type_data={"interaction_name": interaction_name, "condition_kinds": sorted(observed), "legacy_matrix": True},
        )

    def _adapt_scenario_matrix(
        self,
        artifact: Mapping[str, object],
        *,
        context: AdapterContext,
        interaction_name: str,
    ) -> ImmutableEvidenceBundle:
        raw_scenarios = artifact.get("scenarios")
        if not isinstance(raw_scenarios, list) or not 3 <= len(raw_scenarios) <= 5:
            raise ValueError("Interaction artifact requires 3 to 5 generated scenarios.")
        raw_hypothesis = artifact.get("interaction_hypothesis")
        if not isinstance(raw_hypothesis, Mapping):
            raise ValueError("Interaction artifact requires the Eval Engineering interaction hypothesis.")
        try:
            hypothesis = InteractionRelationshipProfile.model_validate(raw_hypothesis)
        except ValueError as error:
            raise ValueError("Interaction artifact contains an invalid interaction hypothesis.") from error
        if not hypothesis.hypothesis_hash:
            raise ValueError("Interaction artifact interaction hypothesis must have a content hash.")
        scenario_by_id: dict[str, Mapping[str, object]] = {}
        scenario_input_profiles: dict[str, str] = {}
        scenario_input_requirements: dict[str, bool] = {}
        scenario_trace_contracts: dict[str, dict[str, object]] = {}
        for raw in raw_scenarios:
            if not isinstance(raw, Mapping):
                raise ValueError("Each interaction scenario must be an object.")
            scenario_id = raw.get("scenario_id")
            category = raw.get("category")
            if not isinstance(scenario_id, str) or not scenario_id.strip():
                raise ValueError("Each interaction scenario requires scenario_id.")
            if scenario_id in scenario_by_id:
                raise ValueError("Interaction artifact cannot contain duplicate scenario_id values.")
            if category not in {"complementary", "synergy", "conflict", "single_skill_dominant", "boundary"}:
                raise ValueError("Interaction scenarios use a declared Pair category.")
            _validate_frozen_interaction_scenario(raw, hypothesis_hash=hypothesis.hypothesis_hash)
            input_contract = ScenarioInputContract.model_validate(
                raw.get("input_contract", ScenarioInputContract.no_input().model_dump(mode="json"))
            )
            if category == "boundary" and not input_contract.requirements:
                raise ValueError("Boundary interaction scenarios require an input_contract.")
            scenario_by_id[scenario_id] = raw
            scenario_input_profiles[scenario_id] = input_contract.profile_id
            scenario_input_requirements[scenario_id] = bool(input_contract.requirements)
            scenario_trace_contracts[scenario_id] = input_contract.trace.model_dump(mode="json")
        raw_conditions = artifact.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            raise ValueError("Interaction artifact requires scenario conditions.")
        expected = {(scenario_id, kind) for scenario_id in scenario_by_id for kind in ("a_only", "b_only", "combined")}
        observed: set[tuple[str, str]] = set()
        conditions: list[EvidenceCondition] = []
        facts: list[EvidenceFact] = []
        records: list[EvidenceRecord] = []
        for index, raw in enumerate(raw_conditions):
            if not isinstance(raw, Mapping):
                raise ValueError("Each interaction condition must be an object.")
            scenario_id = raw.get("scenario_id")
            if not isinstance(scenario_id, str) or scenario_id not in scenario_by_id:
                raise ValueError("Each interaction condition must reference a generated scenario.")
            condition_kind = raw.get("condition_kind")
            if condition_kind not in {"a_only", "b_only", "combined"}:
                raise ValueError("Interaction conditions must use a_only, b_only, or combined.")
            key = (scenario_id, condition_kind)
            if key in observed:
                raise ValueError("Interaction artifact cannot contain duplicate scenario conditions.")
            observed.add(key)
            declared_category = scenario_by_id[scenario_id]["category"]
            if raw.get("category", declared_category) != declared_category:
                raise ValueError("Interaction condition category does not match its generated scenario.")
            label = raw.get("label", f"{scenario_id}:{condition_kind}")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("Interaction condition label is required.")
            refs = raw.get("evidence_refs")
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs):
                raise ValueError("Each interaction condition requires evidence_refs.")
            observations = raw.get("observations", {})
            if not isinstance(observations, Mapping):
                raise ValueError("Interaction condition observations must be an object.")
            trace = raw.get("trace")
            if not isinstance(trace, list) or not trace or any(not isinstance(item, Mapping) for item in trace):
                raise ValueError("Each interaction condition requires a structured trace.")
            if any(not isinstance(item.get("event_type"), str) or not str(item["event_type"]).strip() for item in trace):
                raise ValueError("Each interaction trace event requires event_type.")
            trace_violations = verify_scenario_trace_contract(
                ScenarioInputContract.model_validate(
                    scenario_by_id[scenario_id].get(
                        "input_contract", ScenarioInputContract.no_input().model_dump(mode="json")
                    )
                ),
                [dict(item) for item in trace],
                condition_kind=condition_kind,
            )
            if trace_violations:
                raise ValueError(
                    f"Interaction trace violates the scenario contract for {scenario_id}: "
                    + "; ".join(trace_violations)
                )
            if "output" not in raw:
                raise ValueError("Each interaction condition requires an output.")
            metrics = raw.get("metrics")
            if not isinstance(metrics, Mapping):
                raise ValueError("Each interaction condition requires metrics.")
            latency_ms = metrics.get("latency_ms")
            cost_usd = metrics.get("cost_usd")
            if (
                not isinstance(latency_ms, (int, float)) or isinstance(latency_ms, bool) or latency_ms < 0
                or not isinstance(cost_usd, (int, float)) or isinstance(cost_usd, bool) or cost_usd < 0
            ):
                raise ValueError("Each interaction condition requires non-negative latency_ms and cost_usd.")
            oracle = raw.get("oracle")
            if not isinstance(oracle, Mapping):
                raise ValueError("Each interaction condition requires a verified independent oracle.")
            _validate_independent_oracle(oracle)
            oracle_refs = oracle["evidence_refs"]
            if not isinstance(oracle_refs, list) or not oracle_refs or any(not isinstance(ref, str) or not ref for ref in oracle_refs):
                raise ValueError("Each interaction oracle requires evidence_refs.")
            normalized_refs = [_opaque_ref(ref) for ref in refs]
            normalized_refs.extend(_opaque_ref(ref) for ref in oracle_refs if _opaque_ref(ref) not in normalized_refs)
            normalized_observations = {
                "scenario_id": scenario_id,
                "scenario_category": declared_category,
                "condition_kind": condition_kind,
                "trace_event_count": len(trace),
                "output_recorded": True,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "oracle_verified": True,
                "oracle_type": oracle["oracle_type"],
                "oracle_version": oracle["oracle_version"],
                **dict(observations),
            }
            conditions.append(EvidenceCondition(
                condition_id=_opaque_id("condition", f"{interaction_name}:{scenario_id}:{condition_kind}"),
                scenario_id=scenario_id,
                experiment_id=context.experiment_ids_by_condition.get(condition_kind),
                label=label,
                observations=normalized_observations,
                evidence_refs=normalized_refs,
            ))
            facts.append(EvidenceFact(
                fact_id=_opaque_id("fact", f"{interaction_name}:{scenario_id}:{condition_kind}"),
                label=f"{scenario_id} {condition_kind} observed behavior",
                fact_type="interaction_behavior",
                value=normalized_observations,
                evidence_level="verified",
                evidence_refs=normalized_refs,
            ))
            records.append(_record(
                artifact,
                "trial_result",
                {
                    "interaction_name": interaction_name,
                    "scenario_id": scenario_id,
                    "condition_kind": condition_kind,
                    "observations": normalized_observations,
                    "trace": list(trace),
                    "output": raw["output"],
                    "oracle": dict(oracle),
                },
                normalized_refs,
                index,
            ))
        if observed != expected:
            raise ValueError(
                "Interaction artifact must contain exactly one A-only, B-only, and combined condition for every scenario."
            )
        _required_refs(artifact)
        raw_readiness = artifact.get("scenario_readiness")
        readiness_status = "not_recorded"
        if raw_readiness is not None:
            if not isinstance(raw_readiness, Mapping):
                raise ValueError("scenario_readiness must be an object when provided.")
            readiness = EvaluationReadinessResult.model_validate(raw_readiness)
            if readiness.evaluation_plan_id != context.evaluation_plan_id and context.evaluation_plan_id:
                raise ValueError("Scenario readiness does not match the Evaluation Plan.")
            if {item.scenario_id for item in readiness.scenarios} != set(scenario_by_id):
                raise ValueError("Scenario readiness must cover exactly the generated scenarios.")
            readiness_status = readiness.status
        readiness_required = any(scenario_input_requirements.values())
        return _bundle(
            artifact=artifact,
            context=context,
            conditions=conditions,
            facts=facts,
            records=records,
            type_data={
                "interaction_name": interaction_name,
                "interaction_hypothesis": hypothesis.model_dump(mode="json"),
                "scenario_ids": list(scenario_by_id),
                "scenario_hashes": {
                    scenario_id: scenario_by_id[scenario_id]["scenario_hash"]
                    for scenario_id in scenario_by_id
                },
                "scenario_provenance_sources": {
                    scenario_id: scenario_by_id[scenario_id]["scenario_provenance"]
                    for scenario_id in scenario_by_id
                },
                "scenario_categories": [scenario_by_id[item]["category"] for item in scenario_by_id],
                "condition_kinds": ["a_only", "b_only", "combined"],
                "interaction_model": "scenario_matrix",
                "scenario_input_profiles": scenario_input_profiles,
                "scenario_input_requirements": scenario_input_requirements,
                "scenario_trace_contracts": scenario_trace_contracts,
                "scenario_readiness_required": readiness_required,
                "scenario_readiness_status": readiness_status,
            },
        )


class SkillPairEvaluationAdapter(InteractionEvaluationAdapter):
    """v1 registration of the reusable interaction adapter for Skill Pairs."""

    def __init__(self) -> None:
        super().__init__(evaluation_type="skill_pair_evaluation")


class MemoryEvolutionEvaluationAdapter:
    evaluation_type = "memory_evolution"

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError("Memory Evolution adapter context type mismatch.")
        memory_name = _required_text(artifact, "memory_name")
        baseline = artifact.get("baseline_entries")
        candidate = artifact.get("candidate_entries")
        if not isinstance(baseline, list) or not isinstance(candidate, list):
            raise ValueError("Memory Evolution artifact requires baseline_entries and candidate_entries lists.")
        refs = _required_refs(artifact)
        conditions = [
            EvidenceCondition(condition_id=_opaque_id("condition", "memory:baseline"), label="原有 Memory 测试", observations={"entry_count": len(baseline)}, evidence_refs=refs),
            EvidenceCondition(condition_id=_opaque_id("condition", "memory:candidate"), label="更新 Memory 测试", observations={"entry_count": len(candidate)}, evidence_refs=refs),
        ]
        facts = [EvidenceFact(fact_id=_opaque_id("fact", memory_name), label="Memory 行为观察", fact_type="memory_behavior", value={"memory_name": memory_name, "baseline_entry_count": len(baseline), "candidate_entry_count": len(candidate)}, evidence_level="verified", evidence_refs=refs)]
        records = [_record(artifact, "artifact", {"memory_name": memory_name, "baseline_entries": baseline, "candidate_entries": candidate}, refs)]
        return _bundle(artifact=artifact, context=context, conditions=conditions, facts=facts, records=records, type_data={"memory_name": memory_name})


class PromptChangeEvaluationAdapter:
    evaluation_type = "prompt_change"

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError("Prompt Change adapter context type mismatch.")
        prompt_name = _required_text(artifact, "prompt_name")
        baseline_hash = _required_text(artifact, "baseline_prompt_hash")
        candidate_hash = _required_text(artifact, "candidate_prompt_hash")
        refs = _required_refs(artifact)
        conditions = [
            EvidenceCondition(condition_id=_opaque_id("condition", "prompt:baseline"), label="原有 Prompt 测试", observations={"prompt_hash_present": bool(baseline_hash)}, evidence_refs=refs),
            EvidenceCondition(condition_id=_opaque_id("condition", "prompt:candidate"), label="更新 Prompt 测试", observations={"prompt_hash_present": bool(candidate_hash)}, evidence_refs=refs),
        ]
        facts = [EvidenceFact(fact_id=_opaque_id("fact", prompt_name), label="Prompt 行为观察", fact_type="prompt_behavior", value={"prompt_name": prompt_name, "baseline_prompt_hash": baseline_hash, "candidate_prompt_hash": candidate_hash}, evidence_level="verified", evidence_refs=refs)]
        records = [_record(artifact, "artifact", {"prompt_name": prompt_name, "baseline_prompt_hash": baseline_hash, "candidate_prompt_hash": candidate_hash}, refs)]
        return _bundle(artifact=artifact, context=context, conditions=conditions, facts=facts, records=records, type_data={"prompt_name": prompt_name})


class ReleaseSummaryEvaluationAdapter:
    evaluation_type = "release_summary"

    def adapt(self, artifact: Mapping[str, object], *, context: AdapterContext) -> ImmutableEvidenceBundle:
        if context.evaluation_type != self.evaluation_type:
            raise ValueError("Release Summary adapter context type mismatch.")
        baseline_version = _required_text(artifact, "baseline_version")
        candidate_version = _required_text(artifact, "candidate_version")
        results = artifact.get("regression_results")
        if not isinstance(results, list) or not results:
            raise ValueError("Release Summary artifact requires regression_results.")
        refs = _required_refs(artifact)
        conditions = [EvidenceCondition(condition_id=_opaque_id("condition", "release:candidate"), label="候选版本回归测试", observations={"regression_case_count": len(results)}, evidence_refs=refs)]
        facts = [EvidenceFact(fact_id=_opaque_id("fact", candidate_version), label="Release 行为观察", fact_type="release_behavior", value={"baseline_version": baseline_version, "candidate_version": candidate_version, "regression_case_count": len(results)}, evidence_level="verified", evidence_refs=refs)]
        records = [_record(artifact, "artifact", {"baseline_version": baseline_version, "candidate_version": candidate_version, "regression_results": results}, refs)]
        return _bundle(artifact=artifact, context=context, conditions=conditions, facts=facts, records=records, type_data={"baseline_version": baseline_version, "candidate_version": candidate_version})


def build_full_evaluation_adapter_layer() -> EvaluationAdapterLayer:
    layer = EvaluationAdapterLayer()
    for adapter in (
        SkillAblationEvaluationAdapter(),
        SkillPairEvaluationAdapter(),
        ToolRegressionEvaluationAdapter(),
        MemoryEvolutionEvaluationAdapter(),
        PromptChangeEvaluationAdapter(),
        ReleaseSummaryEvaluationAdapter(),
    ):
        layer.register(adapter)
    return layer


def build_v1_evaluation_adapter_layer() -> EvaluationAdapterLayer:
    """Registry exposed by the converged AIG v1.0 main path."""

    layer = EvaluationAdapterLayer()
    for adapter in (SkillAblationEvaluationAdapter(), SkillPairEvaluationAdapter(), ToolRegressionEvaluationAdapter()):
        layer.register(adapter)
    return layer


def _opaque_ref(raw_ref: str) -> str:
    return "evidence_" + hashlib.sha256(raw_ref.encode("utf-8")).hexdigest()[:16]


def _opaque_id(prefix: str, raw_value: str) -> str:
    return prefix + "_" + hashlib.sha256(raw_value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "InteractionEvaluationAdapter",
    "MemoryEvolutionEvaluationAdapter",
    "PromptChangeEvaluationAdapter",
    "ReleaseSummaryEvaluationAdapter",
    "SkillPairEvaluationAdapter",
    "ToolRegressionEvaluationAdapter",
    "build_full_evaluation_adapter_layer",
    "build_v1_evaluation_adapter_layer",
]
