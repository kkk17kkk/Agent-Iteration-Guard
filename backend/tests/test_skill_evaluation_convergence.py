from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from agentguard.evaluation_orchestration import adapt_evaluation_run_evidence
from agentguard.evaluation_planning import EvaluationPlan
from agentguard.evaluation_request import EvaluationRequest
from agentguard.evaluation_scope import EvaluationScope
from agentguard.product_evaluation_analyst import ProductAnalystInput, ProductEvaluationAnalyst
from agentguard.product_evaluation_report import assemble_product_evaluation_report
from agentguard.release_decision_gate import evaluate_release_decision
from agentguard.semantic_reporting import ProductDefinition
from agentguard.skill_ablation import execute_skill_ablation_matrix

from test_product_evaluation_analyst import FakeProvider, _analysis, _binding
from test_skill_ablation_matrix import SkillRunner, _plan, _readiness


def _with_evidence_refs(value: object, evidence_ref: str) -> object:
    if isinstance(value, dict):
        return {
            key: ([evidence_ref] if key == "evidence_refs" else _with_evidence_refs(child, evidence_ref))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_with_evidence_refs(child, evidence_ref) for child in value]
    return value


def _scoped_plan() -> tuple[EvaluationPlan, EvaluationRequest]:
    plan = _plan()
    scope = EvaluationScope(
        scope_id="s" * 64,
        project_id="demo",
        evaluation_request_id=plan.change_id,
        baseline_version="baseline",
        candidate_version="candidate",
        baseline_runtime_fingerprint="b" * 64,
        candidate_runtime_fingerprint="c" * 64,
        provider_binding_id="control-plane",
        provider="vllm",
        model="local",
        provider_binding_fingerprint="p" * 64,
        fixture_catalog_fingerprint="f" * 64,
        planned_trial_count=len(plan.scenarios) * 3,
        budget_usd=0.1,
        timeout_seconds=30,
        side_effect_policy="isolated_read",
        frozen_at="2026-08-06T00:00:00+00:00",
    )
    return plan.model_copy(update={"evaluation_scope": scope}), EvaluationRequest(
        request_id=plan.change_id,
        project_id="demo",
        component_type="skill",
        component_name=plan.component_name,
        change_type="remove",
        candidate_version="candidate",
        baseline_version="baseline",
    )


def _skill_analysis(plan: EvaluationPlan, evidence_ref: str) -> dict[str, object]:
    payload = deepcopy(_analysis())
    payload = _with_evidence_refs(payload, evidence_ref)
    assert isinstance(payload, dict)
    payload["experiment_overview"]["questions"].append({
        "name": "能力替换后的产品价值",
        "question": "替换实现后是否仍完成用户任务？",
        "purpose": "检查替换实现是否保持产品价值。",
        "evidence_refs": [evidence_ref],
    })
    payload["experiment_analysis"].append({
        "experiment_name": "能力替换后的产品价值",
        "purpose": "检查替换实现是否保持产品价值。",
        "design": "使用相同用户任务比较当前能力与替换后的实现。",
        "input_scenario": "用户提交一个结构化资料任务。",
        "observation": "三种条件都返回结构化结果。",
        "result": "覆盖样本中任务交付保持。",
        "product_meaning": "替换实现仍需在更多边界任务中验证。",
        "evidence_refs": [evidence_ref],
    })
    base = payload["scenario_stability"]["scenarios"][0]
    payload["scenario_stability"]["scenarios"] = [
        {
            **base,
            "scenario_id": scenario.scenario_id,
            "name": scenario.category,
            "user_prompt": scenario.user_prompt,
            "purpose": scenario.evaluation_goal,
            "evidence_refs": [evidence_ref],
        }
        for scenario in plan.scenarios
    ]
    return payload


def test_skill_run_evidence_analyst_report_and_gate_are_one_closed_loop(tmp_path: Path) -> None:
    plan, request = _scoped_plan()
    artifact = execute_skill_ablation_matrix(
        plan,
        evaluation_id="evaluation-skill-convergence",
        readiness=_readiness(plan),
        runner=SkillRunner(),
        run_root=tmp_path / "skill-convergence",
    )
    evidence = adapt_evaluation_run_evidence(
        plan,
        request,
        run_id="run-skill-convergence",
        scope_id=plan.evaluation_scope.scope_id,
        artifact=artifact.model_dump(mode="json"),
    )
    definition = ProductDefinition(
        component_type="skill",
        component_name=plan.component_name,
        description="Build a structured profile.",
        product_responsibility="Provide a usable profile.",
        user_job="Understand the application.",
    )
    analyst_input = ProductAnalystInput(
        project_id="demo",
        evaluation_name=plan.evaluation_name,
        evaluation_type=plan.evaluation_type,
        evaluation_question=plan.comparison_question,
        hypothesis=plan.hypothesis,
        product_definition=definition,
        evidence=evidence,
        evaluation_plan=plan,
    )
    analyst_result = ProductEvaluationAnalyst().analyze(
        analyst_input,
        provider=FakeProvider(_skill_analysis(plan, evidence.conditions[0].evidence_refs[0])),
        binding=_binding(),
    )
    report = assemble_product_evaluation_report(analyst_input, analyst_result)
    gate = evaluate_release_decision(report)

    assert artifact.evaluation_type == "skill_ablation"
    assert evidence.scope_id == plan.evaluation_scope.scope_id
    assert report.evaluation_type == "skill_ablation"
    assert report.evaluation_plan is not None and report.evaluation_plan.plan_id == plan.plan_id
    assert gate.decision == "review"
    assert gate.blocking_reasons == []
