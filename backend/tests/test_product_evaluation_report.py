from agentguard.api import GenericProductEvaluationReportRequest, generic_product_evaluation_report
from agentguard.benchmark_evidence import BenchmarkEvidence, BenchmarkMetric, recompute_integrity_hash
from agentguard.evidence_bundle import ImmutableEvidenceBundle
from agentguard.product_evaluation_analyst import (
    AnalystLimitation,
    DimensionEvaluation,
    EvaluationContext,
    EvaluationContextItem,
    EvidenceExplorer,
    EvidenceExplorerEntry,
    ExecutiveFinding,
    ExecutiveSummary,
    ExperimentAnalysis,
    ExperimentMapQuestion,
    ExperimentOverview,
    ProductAnalystInput,
    ProductAnalystResult,
    ProductEvidenceStatement,
    ProductFinding,
    ProductImpactInterpretation,
    ProductOverview,
    ProductRecommendation,
    ProductSemanticAnalysis,
    InteractionAnalysis,
    InteractionDimensionConclusion,
    InteractionScenarioComparison,
    ScenarioStability,
    ScenarioStabilityScenario,
)
from agentguard.product_evaluation_report import (
    ProductEvaluationReport,
    assemble_product_evaluation_report,
    product_evaluation_report_api_payload,
)
from agentguard.product_evaluation_renderers import (
    render_product_evaluation_html,
    render_product_evaluation_markdown,
    write_product_evaluation_outputs,
)
from agentguard.product_report_template import default_product_report_template
from agentguard.semantic_reporting import ProductDefinition


def _input_and_result() -> tuple[ProductAnalystInput, ProductAnalystResult]:
    evidence = ImmutableEvidenceBundle(
        evaluation_id="evaluation-report",
        project_id="demo",
        evaluation_name="Memory Evolution",
        evaluation_type="memory_evolution",
        artifact_manifest_hash="sha256:1234567890abcdef",
        conditions=[{"condition_id": "condition-1", "label": "记忆能力验证", "evidence_refs": ["ref-1"]}],
        facts=[{"fact_id": "fact-1", "label": "memory fact", "fact_type": "machine", "evidence_level": "verified", "evidence_refs": ["ref-1"]}],
        records=[{"record_id": "record-1", "record_type": "trace", "source_ref": "trace-1", "payload": {"step": "store"}, "evidence_refs": ["ref-1"]}],
        integrity={"status": "complete"},
    )
    definition = ProductDefinition(
        component_type="memory",
        component_name="preference_memory",
        description="保存用户偏好",
        product_responsibility="帮助 Agent 在后续任务中保持用户偏好",
        user_job="减少重复说明偏好的成本",
        expected_behavior=["正确记住偏好"],
        quality_dimensions=["continuity"],
        boundary=["不得保存无关隐私"],
        definition_status="declared",
    )
    analyst_input = ProductAnalystInput(
        project_id="demo",
        evaluation_name="Memory Evolution",
        evaluation_type="memory_evolution",
        evaluation_question="记忆变化是否改善连续使用体验？",
        hypothesis="更准确的记忆可能减少用户重复说明。",
        product_definition=definition,
        evidence=evidence,
    )
    dimensions = [
        DimensionEvaluation(dimension="trigger", conclusion="能识别需要延续的偏好", explanation="后续任务使用了此前保存的信息。", status="supported", evidence_refs=["ref-1"]),
        DimensionEvaluation(dimension="execution", conclusion="完成了偏好保持流程", explanation="记忆在后续任务中被正确读取。", status="supported", evidence_refs=["ref-1"]),
        DimensionEvaluation(dimension="delivery", conclusion="减少了用户重复说明", explanation="后续回答保留了用户已经表达的偏好。", status="supported", evidence_refs=["ref-1"]),
        DimensionEvaluation(dimension="boundary", conclusion="隐私边界仍需验证", explanation="当前样本没有覆盖无关信息和遗忘场景。", status="unresolved", evidence_refs=["ref-1"]),
    ]
    analysis = ProductSemanticAnalysis(
        product_overview=ProductOverview(name="preference_memory", product_role="在后续任务中保持用户偏好", why_it_exists="让用户不必反复说明已经表达过的偏好", user_problem="减少跨任务重复说明和体验中断", ideal_behavior=["记住相关偏好", "不保存无关隐私"], boundary="只处理与用户任务相关的偏好", evidence_refs=["ref-1"]),
        evaluation_context=EvaluationContext(items=[EvaluationContextItem(label="用户任务", value="跨任务保持饮食偏好", evidence_refs=["ref-1"])], evidence_refs=["ref-1"]),
        executive_summary=ExecutiveSummary(final_conclusion="当前证据支持记忆改善了连续体验，但覆盖范围仍有限。", status="partially_supported", dimensions=dimensions, main_findings=[ExecutiveFinding(finding_type="capability_value", title="能力价值", statement="后续任务可以使用此前表达的偏好。", evidence_refs=["ref-1"])], product_recommendation="保留当前能力并补充边界验证。", follow_up_priorities=["增加跨会话和遗忘边界任务"], evidence_refs=["ref-1"]),
        experiment_overview=ExperimentOverview(summary="本次评估通过一类实验回答记忆是否改善连续体验。", questions=[ExperimentMapQuestion(name="完整能力验证", question="用户是否可以少重复说明？", purpose="建立连续体验基线。", evidence_refs=["ref-1"])], evidence_refs=["ref-1"]),
        experiment_analysis=[ExperimentAnalysis(experiment_name="完整能力验证", purpose="确认记忆能力正常时连续体验是否改善。", design="先表达用户偏好，再发起相关后续任务。", input_scenario="用户先说明饮食偏好，随后请求相关方案。", observation="Agent 在后续任务中使用了此前表达的偏好。", result="连续体验得到改善。", product_meaning="该记忆能力对减少用户负担有直接价值。", evidence_refs=["ref-1"])],
        scenario_stability=ScenarioStability(summary="当前只有一个受控任务场景。", coverage_conclusion="证据不足以支持跨场景稳定性结论。", status="insufficient_evidence", scenarios=[ScenarioStabilityScenario(name="场景 1", user_prompt="记住用户偏好并完成后续任务", purpose="测试连续体验。", observation="后续任务保留偏好。", result="用户减少重复说明。", status="supported", evidence_refs=["ref-1"])], evidence_refs=["ref-1"]),
        evidence_explorer=EvidenceExplorer(product_evidence=[ProductEvidenceStatement(label="连续体验", statement="偏好在后续任务中被使用。", evidence_refs=["ref-1"])], experiment_evidence=[EvidenceExplorerEntry(experiment_name="完整能力验证", input_task="记住用户偏好并完成后续任务", reference_label="参考结果", reference_result="保留偏好", changed_label="变化后结果", changed_result="仍保留偏好", difference="当前样本未发现差异", evidence_refs=["ref-1"])]),
        findings=[ProductFinding(finding_id="finding-1", finding_type="product_effect", observation="偏好在后续任务中被使用", product_meaning="连续体验有改善迹象，但仍需覆盖更多会话。", impact_dimension="continuity", direction="improved", severity="low", interpretation_status="supported", evidence_refs=["ref-1"])],
        business_impact=ProductImpactInterpretation(affected_user_journey="连续使用", user_consequence="用户可能减少重复说明，但隐私边界仍需要单独验证。", affected_capabilities=["偏好保持"], severity="low", release_relevance="informational", evidence_refs=["ref-1"]),
        recommendations=[ProductRecommendation(recommendation_id="recommendation-1", priority="low", target="偏好记忆", action="补充跨会话和遗忘边界任务。", reasoning="当前样本尚未覆盖隐私和失效场景。", validation_plan=["增加跨会话任务"], evidence_refs=["ref-1"])],
        limitations=[AnalystLimitation(statement="结论仅覆盖当前受控任务。", evidence_refs=["ref-1"])],
    )
    return analyst_input, ProductAnalystResult(analysis, "deepseek", "deepseek-v4-flash", "request-report")


def test_generic_report_is_type_neutral_and_portable() -> None:
    analyst_input, result = _input_and_result()
    report = assemble_product_evaluation_report(analyst_input, result)
    payload = product_evaluation_report_api_payload(report)
    assert isinstance(report, ProductEvaluationReport)
    assert report.schema_version == "aig.product-evaluation-report.v4"
    assert report.evaluation_type == "memory_evolution"
    assert payload["evaluation_context"]["items"][0]["label"] == "用户任务"
    assert payload["scenario_stability"]["status"] == "insufficient_evidence"


def test_report_records_analyst_evidence_retrieval_provenance() -> None:
    analyst_input, result = _input_and_result()
    result = ProductAnalystResult(
        result.analysis,
        result.provider,
        result.model,
        "request-report-final",
        ("request-report-read", "request-report-final"),
        ("ref-1",),
    )

    report = assemble_product_evaluation_report(analyst_input, result)

    assert report.provenance.analyst_request_ids == ["request-report-read", "request-report-final"]
    assert report.provenance.analyst_retrieved_evidence_refs == ["ref-1"]
    assert report.recompute_report_hash() == report.report_hash


def test_report_binds_optional_external_benchmark_evidence_into_hash() -> None:
    analyst_input, analyst_result = _input_and_result()
    evidence = BenchmarkEvidence(
        evidence_id="benchmark-1",
        project_id=analyst_input.project_id,
        benchmark_name="custom-eval",
        source_ref="upload.json",
        source_sha256="a" * 64,
        metrics=[BenchmarkMetric(
            metric_name="success", unit="ratio", baseline_value=0.70, candidate_value=0.75
        )],
        evidence_refs=["benchmark:benchmark-1"],
        integrity_hash="0" * 64,
    )
    evidence = evidence.model_copy(update={"integrity_hash": recompute_integrity_hash(evidence)})

    report = assemble_product_evaluation_report(
        analyst_input,
        analyst_result,
        supplementary_evidence=[evidence],
    )

    assert report.supplementary_evidence == [evidence]
    assert report.recompute_report_hash() == report.report_hash


def test_report_hash_survives_browser_json_number_normalization() -> None:
    analyst_input, analyst_result = _input_and_result()
    evidence = analyst_input.evidence.model_copy(update={"summary": {"failure_rate": 0.0}})
    report = assemble_product_evaluation_report(
        analyst_input.model_copy(update={"evidence": evidence}),
        analyst_result,
    )
    payload = product_evaluation_report_api_payload(report)

    def browser_json_numbers(value):
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, dict):
            return {key: browser_json_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [browser_json_numbers(item) for item in value]
        return value

    restored = ProductEvaluationReport.model_validate(browser_json_numbers(payload))
    assert restored.recompute_report_hash() == restored.report_hash


def test_generic_report_api_validates_project_binding() -> None:
    analyst_input, result = _input_and_result()
    report = assemble_product_evaluation_report(analyst_input, result)
    payload = product_evaluation_report_api_payload(report)
    returned = generic_product_evaluation_report("demo", GenericProductEvaluationReportRequest(report=payload))
    assert returned["report_hash"] == payload["report_hash"]


def test_renderers_show_context_executive_summary_and_three_evidence_panels(tmp_path) -> None:
    analyst_input, result = _input_and_result()
    report = assemble_product_evaluation_report(analyst_input, result)
    html = render_product_evaluation_html(report)
    markdown = render_product_evaluation_markdown(report)
    paths = write_product_evaluation_outputs(tmp_path / "outputs", report)
    assert "Evaluation Context" in html and "Executive Summary" in html
    assert "Product Evidence" in html and "Experiment Evidence" in html and "Technical Evidence" in html
    assert "用户任务" in markdown and "Scenario Stability" in markdown
    main = html.split("<div class='narratives'>", 1)[1].split("</div><aside", 1)[0]
    assert "trace" not in main and "runtime" not in main and "verifier" not in main
    assert paths["report"].is_file() and paths["html"].is_file() and paths["markdown"].is_file()


def test_renderers_consume_external_product_report_template() -> None:
    analyst_input, result = _input_and_result()
    report = assemble_product_evaluation_report(analyst_input, result)
    base = default_product_report_template()
    custom_sections = [
        section.model_copy(update={"title": "CUSTOM PRODUCT OVERVIEW"})
        if section.section_id == "capability_overview"
        else section
        for section in base.sections
    ]
    custom = base.model_copy(update={"sections": custom_sections})
    html = render_product_evaluation_html(report, custom)
    markdown = render_product_evaluation_markdown(report, custom)
    assert "CUSTOM PRODUCT OVERVIEW" in html and "CUSTOM PRODUCT OVERVIEW" in markdown
    assert "这个组件为用户完成什么工作？" not in html


def test_pair_report_renders_scenario_comparison_and_interaction_dimensions() -> None:
    analyst_input, result = _input_and_result()
    report = assemble_product_evaluation_report(analyst_input, result)
    interaction = InteractionAnalysis(
        summary="A+B improves constrained meal planning in selected scenarios but adds runtime cost.",
        capability_contribution="The checker adds a visible nutrition constraint outcome.",
        composition_gain="The pair appends a check result after planning in the covered task.",
        synergy_gain="The combined flow revises the plan using checker feedback.",
        coordination="The planner hands off one draft and the checker returns one revision.",
        conflict="No conflict is observed in ordinary tasks; boundary tasks still need review.",
        reliability_cost="The combined flow succeeds more often but adds latency and model calls.",
        outcome_gain_status="no_observed_pair_gain",
        observed_outcome="Pair Gain is 0 percentage points on matched resolved support.",
        mechanism_status="mechanistic_coordination_observed",
        observed_mechanism="Checker feedback changed the downstream plan in the combined condition.",
        dimension_conclusions=[
            InteractionDimensionConclusion(
                dimension=dimension,
                conclusion="The covered evidence supports this bounded conclusion.",
                status="supported",
                evidence_refs=["ref-1"],
            )
            for dimension in (
                "capability_contribution",
                "composition_gain",
                "synergy_gain",
                "coordination",
                "conflict",
                "reliability_cost",
            )
        ],
        scenario_comparisons=[
            InteractionScenarioComparison(
                scenario_id=f"scenario_{index}",
                category="synergy" if index == 1 else "boundary",
                scenario_name=f"Meal task {index}",
                user_prompt="Plan a constrained meal.",
                a_only="A produces a plan.",
                b_only="B checks the constraint.",
                combined="A revises the plan after B's check.",
                product_meaning="The combined result is more useful.",
                reliability_cost="Latency increases by 20%.",
                evidence_refs=["ref-1"],
            )
            for index in range(1, 4)
        ],
        evidence_refs=["ref-1"],
    )
    report = report.model_copy(update={"interaction_analysis": interaction})

    html = render_product_evaluation_html(report)
    markdown = render_product_evaluation_markdown(report)

    assert "Skill Pair Scenario Comparison" in html
    assert "A Only" in html and "A+B" in html and "Synergy Gain" in html
    assert "no_observed_pair_gain" in html and "mechanistic_coordination_observed" in html
    assert "Capability Contribution" in markdown and "Conflict / Interference" in markdown
