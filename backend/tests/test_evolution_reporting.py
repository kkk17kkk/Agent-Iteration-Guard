from pathlib import Path

from agentguard.domain import (
    AgentEvolutionCase,
    EvolutionAgentRun,
    EvolutionComparison,
    EvolutionTrial,
    EvolutionVerification,
    ReportFact,
    ReportManifest,
    ReportNarrative,
    VerificationCriterion,
)
from agentguard.evolution_runtime import EvolutionAgentRuntime
from agentguard.provider_runtime import ProviderRuntimeError
from agentguard.reporting import (
    ReportNarrativeAdapter,
    _criterion_summary,
    build_report_manifest,
    record_blocked_report,
    render_report_html,
)
from agentguard.store import Store

from test_evolution_runtime import ScriptedProvider, binding, turn


def manifest() -> ReportManifest:
    facts = [
        ReportFact(fact_id="fact_baseline", category="baseline_verifier", statement="Baseline failed.", evidence_level="verified", evidence_refs=["v:b"]),
        ReportFact(fact_id="fact_candidate", category="candidate_verifier", statement="Candidate passed.", evidence_level="verified", evidence_refs=["v:c"]),
        ReportFact(fact_id="fact_pair", category="pair_equivalence", statement="Inputs match.", evidence_level="verified", evidence_refs=["t:b", "t:c"]),
    ]
    return ReportManifest(
        project_id="project_runtime",
        evolution_case_id="case",
        comparison_id="comparison",
        baseline_revision_id="baseline",
        candidate_revision_id="candidate",
        environment_fingerprint="e" * 64,
        control_plane_run_id="run",
        facts=facts,
        metrics={"paired_trials": 1},
        evaluation_gate="candidate_behavior_supported",
        evidence_refs=["comparison"],
        manifest_fingerprint="m" * 64,
    )


def test_manifest_criterion_summary_is_target_neutral() -> None:
    verification = EvolutionVerification(
        project_id="project",
        evolution_case_id="case",
        evolution_trial_id="trial",
        status="passed",
        criteria=[VerificationCriterion(
            name="invalid_input_diagnostic",
            status="passed",
            detail="Actionable native diagnostic observed.",
        )],
    )
    summary = _criterion_summary(verification)
    assert "invalid_input_diagnostic=passed" in summary
    assert "constraint_adherence" not in summary


def test_manifest_recomputes_all_comparison_pairs(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "manifest.db"))
    project, case_id = "project_runtime", "case_multi"
    case = AgentEvolutionCase(
        evolution_case_id=case_id,
        project_id=project,
        source_id="source",
        baseline_revision_id="revision_baseline",
        candidate_revision_id="revision_candidate",
        evolution_changeset_id="changeset",
    )
    fixed_binding = binding(project_id=project)
    run = EvolutionAgentRun(
        project_id=project,
        evolution_case_id=case_id,
        provider_binding_id=fixed_binding.provider_binding_id,
        objective="Evaluate both declared pairs.",
        status="completed",
        allowed_tools=["read_case_contracts"],
        terminal_reason="hypothesis",
    )
    records: list[tuple[str, str, str, object]] = [
        ("agent_evolution_case", case_id, project, case),
        ("provider_binding", fixed_binding.provider_binding_id, project, fixed_binding),
        ("evolution_agent_run", run.evolution_agent_run_id, project, run),
    ]
    verification_ids: list[str] = []
    for index in (1, 2):
        for role, status in (("baseline", "failed"), ("candidate", "passed")):
            trial = EvolutionTrial(
                project_id=project,
                evolution_case_id=case_id,
                revision_id=f"revision_{role}",
                revision_role=role,
                trial_index=index,
                status="completed",
                environment_fingerprint="e" * 64,
                reset_evidence_ref=f"reset:{index}:{role}",
                request_fingerprint="r" * 64,
                response_evidence_ref=f"response:{index}:{role}",
                trace_evidence_ref=f"trace:{index}:{role}",
                initial_state_ref="sha256:" + "i" * 64,
                final_state_ref=f"sha256:{index}:{role}",
                terminal_reason="completed",
            )
            verification = EvolutionVerification(
                project_id=project,
                evolution_case_id=case_id,
                evolution_trial_id=trial.evolution_trial_id,
                status=status,
                criteria=[VerificationCriterion(
                    name="behavior", status="passed" if status == "passed" else "failed",
                    detail=f"{role} trial {index}",
                )],
                evidence_refs=[f"evidence:{index}:{role}"],
            )
            verification_ids.append(verification.evolution_verification_id)
            records.extend([
                ("evolution_trial", trial.evolution_trial_id, project, trial),
                ("evolution_verification", verification.evolution_verification_id, project, verification),
            ])
    comparison = EvolutionComparison(
        project_id=project,
        evolution_case_id=case_id,
        status="compared",
        conclusion="Two deterministic pairs support the candidate.",
        evidence_ids=verification_ids,
    )
    records.append(("evolution_comparison", comparison.evolution_comparison_id, project, comparison))
    store.save_many(records)  # type: ignore[arg-type]

    result = build_report_manifest(store, project, case_id, run.evolution_agent_run_id)
    assert result.metrics["paired_trials"] == 2
    assert result.metrics["baseline_verification_passes"] == 0
    assert result.metrics["candidate_verification_passes"] == 2
    assert result.evaluation_gate == "candidate_behavior_supported"
    assert len(result.evidence_refs) == 6


def test_report_agent_cites_manifest_and_cannot_modify_gate(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "report.db"))
    fixed = manifest()
    adapter = ReportNarrativeAdapter(store, fixed, tmp_path / "html")
    provider = ScriptedProvider([
        turn(1, "read_report_manifest", {}),
        turn(2, "submit_report_narrative", {
            "title": "LightTable 约束守卫演进评估",
            "executive_summary": "已保存事实支持一个边界明确的候选行为改善结论。",
            "sections": [{
                "heading": "配对证据",
                "fact_refs": ["fact_baseline", "fact_candidate", "fact_pair"],
                "interpretation": "在输入匹配的前提下，候选版本改变了本案例所验证的行为。",
            }],
            "limitations": ["单个确定性版本对不能证明随机行为的稳定性。"],
        }),
    ])
    run = EvolutionAgentRuntime(store, binding(), provider, adapter).start(
        project_id="project_runtime", evolution_case_id="case", objective="Read the manifest and write a report."
    )
    assert run.status == "completed"
    narrative = store.get("report_narrative", run.terminal_artifact_id or "", ReportNarrative)
    assert narrative and narrative.evaluation_gate_snapshot == fixed.evaluation_gate
    assert narrative.locale == "zh-CN"
    assert narrative.html_evidence_ref
    html = next((tmp_path / "html").glob("*.html")).read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in html
    assert "候选行为得到支持" in html
    assert "candidate_behavior_supported" in html
    assert "不等于发布批准" in html


def test_report_rejects_non_chinese_narrative(tmp_path: Path) -> None:
    adapter = ReportNarrativeAdapter(Store(str(tmp_path / "report.db")), manifest(), tmp_path / "html")
    adapter.execute("read_report_manifest", {})
    try:
        adapter.execute("submit_report_narrative", {
            "title": "Evolution report",
            "executive_summary": "English only.",
            "sections": [{
                "heading": "Evidence",
                "fact_refs": ["fact_baseline", "fact_candidate", "fact_pair"],
                "interpretation": "The candidate changed behavior.",
            }],
            "limitations": ["One pair only."],
        })
    except ValueError as error:
        assert "zh-CN" in str(error)
    else:
        raise AssertionError("English report narrative must fail visibly")


def test_sample_banner_is_explicit() -> None:
    fixed = manifest()
    narrative = ReportNarrative(
        project_id=fixed.project_id,
        evolution_case_id=fixed.evolution_case_id,
        report_manifest_id=fixed.report_manifest_id,
        report_agent_run_id="sample_run",
        status="completed",
        locale="zh-CN",
        title="中文报告输出示例",
        executive_summary="该页面只验证展示契约。",
        sections=[{
            "heading": "证据摘要",
            "fact_refs": ["fact_baseline", "fact_candidate", "fact_pair"],
            "interpretation": "事实索引保持不可变。",
        }],
        limitations=["不代表新的模型运行。"],
        fact_refs=["fact_baseline", "fact_candidate", "fact_pair"],
        evaluation_gate_snapshot=fixed.evaluation_gate,
    )
    html = render_report_html(fixed, narrative, sample=True)
    assert "界面输出示例" in html
    assert "不构成新的 Provider 运行或发布结论" in html


def test_report_provider_failure_is_visibly_blocked(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "blocked.db"))
    fixed = manifest()
    run = EvolutionAgentRuntime(
        store, binding(), ScriptedProvider([ProviderRuntimeError("offline")]),
        ReportNarrativeAdapter(store, fixed, tmp_path / "html"),
    ).start(project_id="project_runtime", evolution_case_id="case", objective="Write the report.")
    assert run.status == "infrastructure_blocked"
    narrative = record_blocked_report(store, fixed, run)
    assert narrative.status == "blocked"
    assert narrative.html_evidence_ref is None
    assert narrative.evaluation_gate_snapshot == fixed.evaluation_gate
