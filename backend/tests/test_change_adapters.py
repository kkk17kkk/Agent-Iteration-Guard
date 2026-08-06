import pytest

from agentguard.change_adapters import MemoryEvolutionEvaluationAdapter, PromptChangeEvaluationAdapter, ReleaseSummaryEvaluationAdapter, SkillPairEvaluationAdapter, ToolRegressionEvaluationAdapter, build_full_evaluation_adapter_layer
from agentguard.evaluation_adapters import AdapterContext
from agentguard.evaluation_planning import scenario_hash_for


def _context(evaluation_type: str = "tool_regression") -> AdapterContext:
    return AdapterContext(
        project_id="demo",
        evaluation_name="Tool Regression",
        evaluation_type=evaluation_type,
        source_ref="artifact:tool-regression",
    )


def _artifact() -> dict[str, object]:
    return {
        "evaluation_id": "evaluation-tool",
        "evaluation_type": "tool_regression",
        "artifact_manifest_hash": "sha256:tool1234567890",
        "tool_name": "calendar_lookup",
        "baseline_metrics": {
            "tool_call_success": True,
            "argument_correct": True,
            "downstream_task_success": True,
            "latency_ms": 80,
            "cost_usd": 0.002,
        },
        "candidate_metrics": {
            "tool_call_success": True,
            "argument_correct": True,
            "downstream_task_success": True,
            "latency_ms": 85,
            "cost_usd": 0.002,
        },
        "baseline_trace": [{"event_type": "tool_call_completed"}],
        "candidate_trace": [{"event_type": "tool_call_completed"}],
        "baseline_output": {"events": ["A"]},
        "candidate_output": {"events": ["A", "B"]},
        "oracle": {"status": "verified", "evidence_refs": ["oracle:tool-state"]},
        "metrics": {"case_count": 2, "changed_case_count": 1},
        "evidence_refs": ["file:tool-baseline.json", "file:tool-candidate.json"],
    }


def _interaction_artifact() -> dict[str, object]:
    scenarios = [
        {"scenario_id": "scenario_1", "category": "synergy"},
        {"scenario_id": "scenario_2", "category": "conflict"},
        {
            "scenario_id": "scenario_3",
            "category": "boundary",
            "input_contract": {
                "profile_id": "missing-input",
                "requirements": [{
                    "input_id": "task-data",
                    "fixture_id": "behavior-data-absent",
                    "availability": "absent",
                    "description": "Boundary case without behavior data",
                }],
            },
        },
    ]
    interaction_hypothesis = {
        "relationship": "complementary",
        "rationale": "The capabilities address adjacent responsibilities.",
        "signals": ["shared user task"],
        "hypothesis_source": {
            "kind": "eval_engineering",
            "inputs": ["description", "responsibility", "dependency", "boundary"],
            "status": "hypothesis",
        },
        "provider_metadata": {
            "provider": "test",
            "model": "relationship-model",
            "request_fingerprint": "request-fingerprint",
            "response_fingerprint": "response-fingerprint",
        },
        "hypothesis_hash": "sha256:" + "b" * 64,
    }
    for scenario in scenarios:
        frozen_hash = scenario_hash_for(scenario)
        scenario["scenario_hash"] = frozen_hash
        scenario["scenario_provenance"] = {
            "hypothesis_source": "eval_engineering.relationship_hypothesis",
            "relationship_hypothesis_hash": interaction_hypothesis["hypothesis_hash"],
            "provider_metadata": interaction_hypothesis["provider_metadata"],
            "scenario_hash": frozen_hash,
            "frozen": True,
        }
    conditions = []
    for scenario in scenarios:
        for condition_kind in ("a_only", "b_only", "combined"):
            ref = f"trial:{scenario['scenario_id']}:{condition_kind}"
            conditions.append({
                "scenario_id": scenario["scenario_id"],
                "category": scenario["category"],
                "condition_kind": condition_kind,
                "label": f"{scenario['scenario_id']} {condition_kind}",
                "observations": {"task_success": condition_kind == "combined"},
                "trace": [{"event_type": f"{condition_kind}_completed"}],
                "output": {"condition": condition_kind},
                "metrics": {"latency_ms": 10, "cost_usd": 0.001},
                "oracle": {
                    "oracle_type": "rule_based",
                    "oracle_version": "1.0",
                    "validation_input": {"scenario_id": scenario["scenario_id"], "condition": condition_kind},
                    "status": "verified",
                    "evidence_refs": [f"oracle:{ref}"],
                },
                "evidence_refs": [ref],
            })
    return {
        "evaluation_id": "evaluation-interaction",
        "evaluation_type": "skill_pair_evaluation",
        "artifact_manifest_hash": "sha256:interaction123456",
        "interaction_name": "recipe_planning_and_nutrition_check",
        "interaction_hypothesis": interaction_hypothesis,
        "scenarios": scenarios,
        "conditions": conditions,
        "metrics": {"condition_count": len(conditions)},
        "evidence_refs": ["artifact:interaction"],
    }


def test_tool_regression_adapter_normalizes_heterogeneous_shape() -> None:
    bundle = ToolRegressionEvaluationAdapter().adapt(_artifact(), context=_context())
    assert bundle.evaluation_type == "tool_regression"
    assert bundle.project_id == "demo"
    assert [condition.label for condition in bundle.conditions] == ["保留 Tool 实现测试", "替换 Tool 实现测试"]
    assert bundle.type_data == {"tool_name": "calendar_lookup"}
    assert bundle.metrics[0].name == "case_count"
    assert bundle.artifact_manifest_hash == "sha256:tool1234567890"


def test_tool_regression_adapter_rejects_missing_evidence() -> None:
    artifact = _artifact()
    artifact.pop("evidence_refs")
    with pytest.raises(ValueError, match="evidence_refs"):
        ToolRegressionEvaluationAdapter().adapt(artifact, context=_context())


def test_tool_regression_adapter_rejects_wrong_context_type() -> None:
    with pytest.raises(ValueError, match="context type mismatch"):
        ToolRegressionEvaluationAdapter().adapt(_artifact(), context=_context("memory_evolution"))


def test_skill_pair_adapter_requires_and_preserves_three_conditions() -> None:
    from agentguard.change_adapters import SkillPairEvaluationAdapter

    artifact = {
        "evaluation_id": "evaluation-pair",
        "evaluation_type": "skill_pair_evaluation",
        "artifact_manifest_hash": "sha256:pair1234567890",
        "pair_name": "planning_and_delivery",
        "conditions": [
            {"condition_kind": "a_only", "label": "task_planning only", "observations": {"task_success": True}, "trace": [{"event_type": "a_completed"}], "output": {"plan": True}, "metrics": {"latency_ms": 10, "cost_usd": 0.001}, "oracle": {"oracle_type": "rule_based", "oracle_version": "1.0", "validation_input": {"condition": "a_only"}, "status": "verified", "evidence_refs": ["oracle:a"]}, "evidence_refs": ["trial:a"]},
            {"condition_kind": "b_only", "label": "result_delivery only", "observations": {"task_success": False}, "trace": [{"event_type": "b_completed"}], "output": {"delivered": False}, "metrics": {"latency_ms": 11, "cost_usd": 0.001}, "oracle": {"oracle_type": "rule_based", "oracle_version": "1.0", "validation_input": {"condition": "b_only"}, "status": "verified", "evidence_refs": ["oracle:b"]}, "evidence_refs": ["trial:b"]},
            {"condition_kind": "combined", "label": "planning_and_delivery", "observations": {"task_success": True, "synergy": True}, "trace": [{"event_type": "combined_completed"}], "output": {"plan": True, "delivered": True}, "metrics": {"latency_ms": 14, "cost_usd": 0.002}, "oracle": {"oracle_type": "rule_based", "oracle_version": "1.0", "validation_input": {"condition": "combined"}, "status": "verified", "evidence_refs": ["oracle:combined"]}, "evidence_refs": ["trial:combined"]},
        ],
        "metrics": {"condition_count": 3},
        "evidence_refs": ["trial:a", "trial:b", "trial:combined"],
    }

    bundle = SkillPairEvaluationAdapter().adapt(
        artifact,
        context=AdapterContext(
            project_id="demo",
            evaluation_name="Skill Pair Evaluation",
            evaluation_type="skill_pair_evaluation",
            source_ref="artifact:pair",
            experiment_ids_by_condition={
                "a_only": "experiment-a",
                "b_only": "experiment-b",
                "combined": "experiment-combined",
            },
        ),
    )

    assert bundle.evaluation_type == "skill_pair_evaluation"
    assert bundle.type_data["condition_kinds"] == ["a_only", "b_only", "combined"]
    assert [condition.experiment_id for condition in bundle.conditions] == [
        "experiment-a", "experiment-b", "experiment-combined"
    ]


def test_skill_pair_adapter_rejects_missing_trace_oracle_and_runtime_metrics() -> None:
    from agentguard.change_adapters import SkillPairEvaluationAdapter

    artifact = {
        "evaluation_id": "evaluation-pair",
        "evaluation_type": "skill_pair_evaluation",
        "artifact_manifest_hash": "sha256:pair1234567890",
        "pair_name": "planning_and_delivery",
        "conditions": [
            {"condition_kind": "a_only", "label": "A", "observations": {}, "evidence_refs": ["trial:a"]},
            {"condition_kind": "b_only", "label": "B", "observations": {}, "evidence_refs": ["trial:b"]},
            {"condition_kind": "combined", "label": "A+B", "observations": {}, "evidence_refs": ["trial:combined"]},
        ],
        "metrics": {},
        "evidence_refs": ["trial:a", "trial:b", "trial:combined"],
    }

    with pytest.raises(ValueError, match="structured trace"):
        SkillPairEvaluationAdapter().adapt(
            artifact,
            context=AdapterContext(
                project_id="demo",
                evaluation_name="Skill Pair Evaluation",
                evaluation_type="skill_pair_evaluation",
                source_ref="artifact:pair",
            ),
        )


def test_interaction_adapter_normalizes_scenario_matrix_for_pair_reuse() -> None:
    bundle = SkillPairEvaluationAdapter().adapt(
        _interaction_artifact(),
        context=AdapterContext(
            project_id="demo",
            evaluation_name="Skill Pair Evaluation",
            evaluation_type="skill_pair_evaluation",
            component_name="recipe_planning_and_nutrition_check",
            source_ref="artifact:interaction",
            experiment_ids_by_condition={
                "a_only": "experiment-a",
                "b_only": "experiment-b",
                "combined": "experiment-combined",
            },
        ),
    )

    assert len(bundle.conditions) == 9
    assert {condition.scenario_id for condition in bundle.conditions} == {
        "scenario_1", "scenario_2", "scenario_3"
    }
    assert bundle.type_data["interaction_model"] == "scenario_matrix"
    assert bundle.conditions[-1].observations["scenario_category"] == "boundary"
    assert bundle.conditions[-1].experiment_id == "experiment-combined"


def test_interaction_adapter_rejects_incomplete_scenario_matrix() -> None:
    artifact = _interaction_artifact()
    artifact["conditions"] = artifact["conditions"][:-1]

    with pytest.raises(ValueError, match="exactly one A-only"):
        SkillPairEvaluationAdapter().adapt(
            artifact,
            context=AdapterContext(
                project_id="demo",
                evaluation_name="Skill Pair Evaluation",
                evaluation_type="skill_pair_evaluation",
                source_ref="artifact:interaction",
            ),
        )


def test_interaction_adapter_enforces_scenario_specific_trace_contract() -> None:
    artifact = _interaction_artifact()
    boundary = artifact["scenarios"][2]
    assert isinstance(boundary, dict)
    boundary["input_contract"]["trace"] = {
        "provider_usage": "forbidden",
        "required_event_types": ["clarification_requested"],
    }
    updated_hash = scenario_hash_for(boundary)
    boundary["scenario_hash"] = updated_hash
    boundary["scenario_provenance"]["scenario_hash"] = updated_hash

    with pytest.raises(ValueError, match="violates the scenario contract"):
        SkillPairEvaluationAdapter().adapt(
            artifact,
            context=AdapterContext(
                project_id="demo",
                evaluation_name="Skill Pair Evaluation",
                evaluation_type="skill_pair_evaluation",
                source_ref="artifact:interaction",
            ),
        )


def test_memory_evolution_adapter_normalizes_entry_snapshots() -> None:
    artifact = {
        "evaluation_id": "evaluation-memory",
        "evaluation_type": "memory_evolution",
        "artifact_manifest_hash": "sha256:memory123456789",
        "memory_name": "dietary_preferences",
        "baseline_entries": [{"key": "avoid", "value": "egg"}],
        "candidate_entries": [{"key": "avoid", "value": "egg"}, {"key": "budget", "value": "20"}],
        "metrics": {"entry_count_delta": 1},
        "evidence_refs": ["file:memory-baseline.json"],
    }
    bundle = MemoryEvolutionEvaluationAdapter().adapt(artifact, context=_context("memory_evolution"))
    assert bundle.evaluation_type == "memory_evolution"
    assert [item.label for item in bundle.conditions] == ["原有 Memory 测试", "更新 Memory 测试"]
    assert bundle.conditions[1].observations["entry_count"] == 2


def test_prompt_change_adapter_preserves_prompt_hash_identity() -> None:
    artifact = {
        "evaluation_id": "evaluation-prompt",
        "evaluation_type": "prompt_change",
        "artifact_manifest_hash": "sha256:prompt123456789",
        "prompt_name": "recipe_system_prompt",
        "baseline_prompt_hash": "sha256:baseline-prompt",
        "candidate_prompt_hash": "sha256:candidate-prompt",
        "metrics": {"case_count": 4},
        "evidence_refs": ["file:prompt-regression.json"],
    }
    bundle = PromptChangeEvaluationAdapter().adapt(artifact, context=_context("prompt_change"))
    assert bundle.evaluation_type == "prompt_change"
    assert bundle.type_data["prompt_name"] == "recipe_system_prompt"
    assert bundle.records[0].payload["candidate_prompt_hash"] == "sha256:candidate-prompt"


def test_release_summary_adapter_records_regressions_without_release_decision() -> None:
    artifact = {
        "evaluation_id": "evaluation-release",
        "evaluation_type": "release_summary",
        "artifact_manifest_hash": "sha256:release123456789",
        "baseline_version": "v1.0.0",
        "candidate_version": "v1.1.0",
        "regression_results": [{"case": "recipe", "status": "recorded"}],
        "metrics": {"regression_case_count": 1},
        "evidence_refs": ["file:release-regression.json"],
    }
    bundle = ReleaseSummaryEvaluationAdapter().adapt(artifact, context=_context("release_summary"))
    assert bundle.evaluation_type == "release_summary"
    assert bundle.conditions[0].observations["regression_case_count"] == 1
    assert "release_status" not in bundle.summary


def test_full_adapter_registry_exposes_all_evolution_change_types() -> None:
    assert build_full_evaluation_adapter_layer().registered_types() == (
        "memory_evolution",
        "prompt_change",
        "release_summary",
        "skill_ablation",
        "skill_pair_evaluation",
        "tool_regression",
    )


def test_v1_adapter_registry_exposes_only_supported_component_types() -> None:
    from agentguard.change_adapters import build_v1_evaluation_adapter_layer

    assert build_v1_evaluation_adapter_layer().registered_types() == (
        "skill_ablation",
        "skill_pair_evaluation",
        "tool_regression",
    )
