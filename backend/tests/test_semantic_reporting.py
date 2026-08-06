import json
from pathlib import Path

import pytest

from agentguard.domain import (
    ProviderBinding,
    SkillAblationEvidence,
    SkillAblationVerification,
    SkillContract,
    SkillTraceEvent,
    VerificationCriterion,
)
from agentguard.api import ProductEvaluationReportRequest, product_evaluation_report
from agentguard.evaluation_adapters import AdapterContext
from agentguard.evaluation_planning import EvaluationScenario, build_evolution_plan
from agentguard.evaluation_scenario_generator import ScenarioEvidenceRequirementsGenerator
from agentguard.provider_runtime import ProviderRuntimeError, ProviderToolCall, ProviderTurn
from agentguard.semantic_reporting import (
    ProductDefinition,
    ProductEvaluationAnalysis,
    assemble_product_evaluation_report,
    build_skill_ablation_analyst_input,
    build_skill_ablation_evidence_bundle,
    generate_product_evaluation_analysis_with_provider,
    load_skill_ablation_artifact,
    product_evaluation_api_payload,
    render_product_evaluation_html,
    render_product_evaluation_markdown,
    write_product_evaluation_outputs,
)
from agentguard.skill_ablation_adapter import (
    build_default_evaluation_adapter_layer,
    build_skill_ablation_change,
    build_skill_evaluation_target,
    skill_ablation_experiment_ids_by_condition,
)


class FakeProvider:
    def __init__(self, arguments: dict[str, object]) -> None:
        self.arguments = arguments

    def complete(self, messages, tools) -> ProviderTurn:
        assert messages[0]["role"] == "system"
        assert tools[0]["function"]["name"] == "submit_product_evaluation_analysis"
        return ProviderTurn(
            "request-semantic",
            "tool_calls",
            (ProviderToolCall("call-semantic", "submit_product_evaluation_analysis", self.arguments),),
            30,
            20,
            0,
            "request-fingerprint",
            "response-fingerprint",
        )


class FakeScenarioGenerator:
    def generate(self, target, change):
        return [
            EvaluationScenario(
                scenario_id=f"scenario_{index}",
                category=category,
                user_prompt=f"用户请求测试场景 {index}。",
                evaluation_goal=f"测试 {category}。",
                expected_success_behavior=["完成用户任务"],
                evidence_to_collect=["用户任务结果"],
            )
            for index, category in enumerate(("normal", "constraint_conflict", "boundary"), 1)
        ]


def _binding() -> ProviderBinding:
    return ProviderBinding(
        project_id="lighttable",
        role="control_plane",
        provider="vllm",
        base_url="http://127.0.0.1:8000/v1",
        model="local",
        expected_environment_variable="VLLM_API_KEY",
        credential_source_ref="test",
        batch_budget_usd=0,
        timeout_seconds=10,
        allowed_hosts=["127.0.0.1"],
        data_retention_policy="test",
    )


def _write_artifact(
    directory: Path,
    intervention: str,
    *,
    passed: bool,
    scenario_id: str | None = None,
) -> object:
    directory.mkdir(parents=True, exist_ok=True)
    contract = SkillContract(
        project_id="lighttable",
        evolution_case_id="recipe-case",
        skill_name="recipe_planning",
        kind="runtime_skill",
        trigger="recipe planning begins",
        execution="planner creates a meal plan",
        deliverable="structured meal plan",
        termination="plan ends",
        required_trace_event_types=["recipe_planning_generated"],
        status="approved",
    )
    evidence_ref = f"file:{directory / 'trial-evidence.json'}"
    trace = (
        [
            SkillTraceEvent(sequence=0, event_type="recipe_planning_generated", evidence_ref=f"{evidence_ref}#trace:0")
        ]
        if passed
        else []
    )
    evidence = SkillAblationEvidence(
        project_id="lighttable",
        evolution_case_id="recipe-case",
        skill_contract_id=contract.skill_contract_id,
        trial_ref=f"{intervention}-sample",
        intervention=intervention,
        scenario_id=scenario_id,
        trigger_event=trace[0] if trace else None,
        trace_events=trace,
        trace_complete=passed,
        deliverable={"plans": [{"name": "tofu"}]} if passed else {},
        deliverable_evidence_ref=evidence_ref if passed else None,
        target_criteria=[
            VerificationCriterion(
                name="target_response_shape",
                status="passed" if passed else "failed",
                detail="shape",
                evidence_refs=[evidence_ref],
            ),
            VerificationCriterion(
                name="generated_constraint_adherence",
                status="passed" if passed else "failed",
                detail="constraint",
                evidence_refs=[evidence_ref],
            ),
            VerificationCriterion(
                name="sqlite_write_boundary",
                status="passed",
                detail="boundary",
                evidence_refs=[evidence_ref],
            ),
        ],
        boundary_outcome="none",
        boundary_evidence_refs=[evidence_ref],
        fallback_used=not passed,
    )
    verification = SkillAblationVerification(
        project_id="lighttable",
        evolution_case_id="recipe-case",
        skill_contract_id=contract.skill_contract_id,
        skill_ablation_evidence_id=evidence.skill_ablation_evidence_id,
        status="passed" if passed else "failed",
        criteria=[
            VerificationCriterion(name="deliverable", status="passed" if passed else "failed", detail="deliverable", evidence_refs=[evidence_ref]),
            VerificationCriterion(name="target_response_shape", status="passed" if passed else "failed", detail="shape", evidence_refs=[evidence_ref]),
            VerificationCriterion(name="generated_constraint_adherence", status="passed" if passed else "failed", detail="constraint", evidence_refs=[evidence_ref]),
            VerificationCriterion(name="sqlite_write_boundary", status="passed", detail="boundary", evidence_refs=[evidence_ref]),
        ],
    )
    for name, model in (
        ("skill-contract.json", contract),
        ("skill-evidence.json", evidence),
        ("skill-verification.json", verification),
    ):
        (directory / name).write_text(json.dumps(model.model_dump()), encoding="utf-8")
    (directory / "trial-evidence.json").write_text(
        json.dumps({"request": {"goal": "maintain"}, "response": {"plans": [{"name": "tofu"}]}}),
        encoding="utf-8",
    )
    return load_skill_ablation_artifact("lighttable", directory)


def test_skill_ablation_adapter_matches_existing_bundle_builder(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "enabled", "enabled", passed=True)
    context = AdapterContext(
        project_id="lighttable",
        evaluation_name="Skill Ablation",
        evaluation_type="skill_ablation",
        source_ref=str((tmp_path / "enabled").resolve()),
    )
    adapted = build_default_evaluation_adapter_layer().adapt(
        "skill_ablation", [artifact], context=context
    )
    expected, _ = build_skill_ablation_evidence_bundle(
        "lighttable", [artifact], evaluation_name="Skill Ablation"
    )
    assert adapted.model_dump(mode="json") == expected.model_dump(mode="json")


def test_skill_ablation_adapter_attaches_plan_and_experiment_identity(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "enabled", "enabled", passed=True)
    definition = ProductDefinition(
            component_name="recipe_planning",
            description="structured meal planning",
            product_responsibility="help users create usable meal plans",
            user_job="obtain a constraint-aware meal plan",
        )
    target = build_skill_evaluation_target(artifact, definition)
    change = build_skill_ablation_change(artifact, evaluation_name="Skill Ablation")
    plan = build_evolution_plan(
        target,
        change,
        scenario_generator=FakeScenarioGenerator(),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )
    context = AdapterContext(
        project_id="lighttable",
        evaluation_name="Skill Ablation",
        evaluation_type="skill_ablation",
        source_ref=str((tmp_path / "enabled").resolve()),
        evaluation_plan_id=plan.plan_id,
        experiment_ids_by_condition=skill_ablation_experiment_ids_by_condition(plan),
        scenario_ids_by_trial_ref={artifact.evidence.trial_ref: "scenario_1"},
    )
    bundle = build_default_evaluation_adapter_layer().adapt(
        "skill_ablation", [artifact], context=context
    )
    assert bundle.evaluation_plan_id == plan.plan_id
    assert bundle.conditions[0].experiment_id == plan.experiment_for_kind("baseline").experiment_id
    assert bundle.conditions[0].scenario_id == "scenario_1"


def test_skill_ablation_adapter_preserves_artifact_persisted_scenario_identity(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "enabled", "enabled", passed=True, scenario_id="scenario_2")
    context = AdapterContext(
        project_id="lighttable",
        evaluation_name="Skill Ablation",
        evaluation_type="skill_ablation",
        source_ref=str((tmp_path / "enabled").resolve()),
    )
    bundle = build_default_evaluation_adapter_layer().adapt(
        "skill_ablation", [artifact], context=context
    )
    assert bundle.conditions[0].scenario_id == "scenario_2"


def test_skill_ablation_bundle_preserves_evaluation_request_and_version_identity(tmp_path: Path) -> None:
    artifact = _write_artifact(tmp_path / "enabled", "enabled", passed=True)
    context = AdapterContext(
        project_id="lighttable",
        evaluation_name="Skill Ablation",
        evaluation_type="skill_ablation",
        source_ref=str((tmp_path / "enabled").resolve()),
        evaluation_request_id="evaluation_request_test",
        baseline_version="git:baseline",
        candidate_version="git:candidate",
    )

    bundle = build_default_evaluation_adapter_layer().adapt(
        "skill_ablation", [artifact], context=context
    )

    assert bundle.evaluation_request_id == "evaluation_request_test"
    assert bundle.baseline_version == "git:baseline"
    assert bundle.candidate_version == "git:candidate"


def _analysis(analyst_input):
    refs = analyst_input.evidence.conditions[0].evidence_refs[:1]
    first, second = analyst_input.evidence.conditions
    return {
        "skill_overview": {
            "name": "recipe_planning",
            "product_role": "帮助用户获得符合饮食限制且可以执行的菜谱。",
            "user_value": ["减少用户手动检查和修改菜谱的成本。"],
            "expected_product_behavior": ["生成结构化菜谱并遵守饮食限制。"],
            "boundary_in_product_language": "不能通过通用路径绕过饮食限制。",
            "evidence_refs": refs,
        },
        "evaluation_goal": {
            "question": "验证 Skill 是否改善用户获得的菜谱质量。",
            "what_is_being_validated": ["饮食限制遵守", "输出可用性"],
            "why_it_matters": "格式完整不代表菜谱可以直接使用。",
            "scope_statement": "结论仅限当前受控任务。",
            "evidence_refs": refs,
        },
        "experiment_summary": {
            "tests": [
                {
                    "condition_id": first.condition_id,
                    "condition_label": first.label,
                    "observed_behavior": "生成结构化菜谱并遵守饮食限制。",
                    "product_meaning": "用户获得可直接采用的结果。",
                    "interpretation_status": "supported",
                    "evidence_refs": first.evidence_refs[:1],
                },
                {
                    "condition_id": second.condition_id,
                    "condition_label": second.label,
                    "observed_behavior": "仍生成结构化菜谱，但饮食限制没有得到遵守。",
                    "product_meaning": "输出形式完整但产品结果不可稳定使用。",
                    "interpretation_status": "supported",
                    "evidence_refs": second.evidence_refs[:1],
                },
            ]
        },
        "findings": [
            {
                "finding_id": "finding-constraint",
                "finding_type": "product_effect",
                "observation": "移除 Skill 后仍有结构化输出，但饮食限制违规。",
                "interpretation": "通用生成路径维持了输出形式，却没有继承领域约束。",
                "product_meaning": "Skill 影响的是菜谱是否适合用户，而不只是是否产生输出。",
                "impact_dimension": "output_usability",
                "direction": "degraded",
                "severity": "high",
                "claim_status": "supported",
                "causal_scope": "controlled_comparison_supported",
                "scope_statement": "仅限当前受控任务。",
                "evidence_refs": refs,
                "uncertainties": ["通用路径来源尚未完全定位。"],
            }
        ],
        "business_impact": {
            "affected_user_journey": "用户输入饮食限制后获得菜谱。",
            "user_consequence": "用户可能收到格式完整但无法采用的菜谱。",
            "product_value": ["启用 Skill 时提高约束满足可能性。"],
            "product_risks": ["通用路径可能绕过产品约束。"],
            "affected_capabilities": ["受约束的菜谱规划"],
            "severity": "high",
            "release_relevance": "requires_review",
            "evidence_refs": ["finding-constraint"],
        },
        "recommendation": [
            {
                "recommendation_id": "recommendation-guard",
                "priority": "high",
                "target": "通用菜谱路径",
                "action": "所有最终菜谱都必须经过饮食限制检查。",
                "reasoning": ["移除 Skill 后约束执行失败。"],
                "expected_product_effect": "阻止格式完整但不可用的菜谱进入用户结果。",
                "validation_plan": ["重跑启用、移除和替换实现测试。"],
                "evidence_refs": ["finding-constraint"],
            }
        ],
        "limitations": [
            {
                "statement": "样本仅覆盖当前受控任务。",
                "limitation_type": "sample",
                "evidence_refs": refs,
            }
        ],
    }


def test_product_semantic_report_preserves_evidence_and_projects_all_formats(tmp_path: Path) -> None:
    artifacts = [_write_artifact(tmp_path / "enabled", "enabled", passed=True), _write_artifact(tmp_path / "removed", "disabled", passed=False)]
    product_definition = ProductDefinition(
        component_name="recipe_planning",
        description="生成受约束菜谱",
        product_responsibility="帮助用户获得符合饮食限制的可执行菜谱",
        user_job="获得可直接使用的个性化菜谱",
        expected_behavior=["遵守饮食限制"],
        quality_dimensions=["constraint_adherence"],
        boundary=["不得绕过饮食限制"],
        definition_status="declared",
        evidence_refs=["product-contract-ref"],
    )
    analyst_input, raw_evidence = build_skill_ablation_analyst_input(
        "lighttable", artifacts, product_definition=product_definition
    )
    analysis = _analysis(analyst_input)
    run = generate_product_evaluation_analysis_with_provider(
        analyst_input,
        provider=FakeProvider(analysis),
        binding=_binding(),
        forbidden_tokens={"enabled-sample", "disabled-sample"},
    )
    report = assemble_product_evaluation_report(analyst_input, run)
    paths = write_product_evaluation_outputs(tmp_path / "output", report, raw_evidence)
    payload = product_evaluation_api_payload(report)
    assert report.schema_version == "aig.product-evaluation-report.v1"
    assert report.subject["product_name"] == "lighttable"
    assert report.evidence.artifact_manifest_hash == raw_evidence["evidence_manifest_sha256"]
    assert [item.condition for item in report.product_evaluation.experiment_summary.tests] == ["启用 Skill 测试", "移除 Skill 测试"]
    assert "enabled-sample" not in json.dumps(payload, ensure_ascii=False)
    assert "verifier passed" not in json.dumps(payload, ensure_ascii=False)
    assert "启用 Skill 测试" in paths["html"].read_text(encoding="utf-8")
    assert "移除 Skill 测试" in paths["markdown"].read_text(encoding="utf-8")
    assert paths["report"].is_file() and paths["evidence"].is_file()
    api_report = product_evaluation_report("lighttable", ProductEvaluationReportRequest(report=payload))
    assert api_report["report_id"] == report.report_id


def test_product_semantic_report_rejects_invalid_evidence_reference(tmp_path: Path) -> None:
    artifacts = [_write_artifact(tmp_path / "enabled", "enabled", passed=True), _write_artifact(tmp_path / "removed", "disabled", passed=False)]
    analyst_input, _ = build_skill_ablation_analyst_input("lighttable", artifacts)
    invalid = _analysis(analyst_input)
    invalid["findings"][0]["evidence_refs"] = ["invented-evidence"]
    with pytest.raises(ProviderRuntimeError, match="evidence"):
        generate_product_evaluation_analysis_with_provider(
            analyst_input,
            provider=FakeProvider(invalid),
            binding=_binding(),
        )


def test_product_semantic_report_rejects_internal_trial_text(tmp_path: Path) -> None:
    artifacts = [_write_artifact(tmp_path / "enabled", "enabled", passed=True), _write_artifact(tmp_path / "removed", "disabled", passed=False)]
    analyst_input, _ = build_skill_ablation_analyst_input("lighttable", artifacts)
    invalid = _analysis(analyst_input)
    invalid["findings"][0]["interpretation"] = "enabled-sample verifier passed"
    with pytest.raises(ProviderRuntimeError, match="internal"):
        generate_product_evaluation_analysis_with_provider(
            analyst_input,
            provider=FakeProvider(invalid),
            binding=_binding(),
            forbidden_tokens={"enabled-sample"},
        )
