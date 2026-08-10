import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .benchmark_evidence import BenchmarkEvidence
from .evaluation_memory import EvaluationKnowledge
from .domain import (
    HistoricalReplayEvidence,
    NativeHarnessContract,
    ProviderBinding,
    RuntimeEnvironmentContract,
    RuntimeEnvironmentPreflight,
    SkillAblationEvidence,
    SkillContract,
    TaskVerifierContract,
)
from .evolution import EvolutionIntakeError
from .integrations.native_http import HttpOperation
from .product_reporting import load_skill_ablation_artifact
from .evaluation_adapters import AdapterContext
from .evaluation_request import EvaluationRequest
from .evaluation_execution import (
    build_evaluation_execution_mapping,
    parse_execution_scenario_mapping,
)
from .evaluation_execution_config import EvaluationExecutionConfiguration, metadata as execution_configuration_metadata
from .change_adapters import build_v1_evaluation_adapter_layer
from .evaluation_planning import EvaluationPlan, bind_evaluation_scope, build_evolution_evaluation_plan
from .evaluation_dispatch import build_evaluation_plan_for_request
from .evaluation_orchestration import (
    adapt_evaluation_run_evidence,
    build_product_evaluation_report,
    execute_evaluation_run,
    planned_trial_count,
    resolve_evaluation_execution_inputs,
)
from .evaluation_failures import classify_report_failure, classify_run_failure
from .evaluation_run import EvaluationRun, content_ref
from .evaluation_report import EvaluationReportRecord
from .evaluation_scope import freeze_evaluation_scope
from .evaluation_scenario_generator import LLMEvaluationScenarioGenerator, ScenarioEvidenceRequirementsGenerator
from .interaction_matrix import PAIR_INTERACTION_CONDITIONS, execute_evaluation_matrix
from .interaction_runner import ManifestInteractionTrialRunner, SubprocessInteractionOracle
from .product_evaluation_analyst import ProductAnalystInput, ProductEvaluationAnalyst
from .product_evaluation_report import assemble_product_evaluation_report
from .product_evaluation_report import ProductEvaluationReport
from .product_evaluation_renderers import write_product_evaluation_outputs
from .product_report_template import load_product_report_template
from .project_intelligence import ProjectIntelligenceRegistration
from .project_scanner import ProjectScanRequest
from .release_decision_gate import evaluate_release_decision
from .scenario_contracts import check_evaluation_plan_readiness
from .skill_pair_evaluation import build_skill_pair_evaluation_change, build_skill_pair_evaluation_target, skill_pair_experiment_ids_by_condition
from .tool_regression import validate_tool_regression_artifact
from .semantic_reporting import (
    ProductDefinition,
    build_skill_ablation_analyst_input,
    product_definition_from_skill_artifact,
)
from .skill_ablation_adapter import (
    build_skill_ablation_change,
    build_skill_evaluation_target,
    skill_ablation_experiment_ids_by_condition,
)
from .provider_runtime import ProviderRuntimeError, build_control_plane_client
from .service import AssistantInputError, ProductNotFoundError, Service
from .target_onboarding import TargetEnvironmentCache, initialize_target_manifest, inspect_target_manifest, target_golden_path
from .target_runtime import TargetRuntimeAdapter
from .targets import TargetInfrastructureError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentguard")
    parser.add_argument("--db", default=os.getenv("AGENTGUARD_DB", "data/agentguard.db"))
    parser.add_argument("--format", choices=["text", "json"], default="text")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

    project = commands.add_parser("project").add_subparsers(dest="subcommand", required=True)
    project_register = project.add_parser("register")
    project_register.add_argument("--project-id", required=True)
    project_register.add_argument("--input", required=True, help="Project Intelligence registration JSON; secrets are not accepted.")
    project_register.add_argument("--benchmark-result", action="append", help="Optional external benchmark result JSON; repeatable and never executed by AIG.")
    project_snapshot = project.add_parser("snapshot")
    project_snapshot.add_argument("--project-id", required=True)
    project_snapshot.add_argument("--input", required=True, help="New immutable Agent Snapshot registration JSON.")
    project_scan = project.add_parser("scan")
    project_scan.add_argument("--project-id", required=True)
    project_scan.add_argument("--source", required=True, help="Local repository/package directory, archive, Dockerfile directory, or image reference.")
    project_scan.add_argument("--source-kind", choices=["repository", "package", "docker_image"], required=True)
    project_scan.add_argument("--version", required=True)
    project_scan.add_argument("--entrypoint")
    project_scan.add_argument("--runtime-kind", choices=["native_http", "native_command", "package", "docker"])
    project_scan.add_argument("--declaration-file", help="Optional project-neutral declaration path inside the source.")
    project_preflight = project.add_parser("runtime-preflight")
    project_preflight.add_argument("--project-id", required=True)
    project_preflight.add_argument("--version", required=True)
    project_preflight.add_argument("--source-root")
    project_compare = project.add_parser("runtime-compare")
    project_compare.add_argument("--project-id", required=True)
    project_compare.add_argument("--baseline-version", required=True)
    project_compare.add_argument("--candidate-version", required=True)
    project_compare.add_argument("--baseline-source-root")
    project_compare.add_argument("--candidate-source-root")
    project_get = project.add_parser("get")
    project_get.add_argument("project_id")

    evaluation = commands.add_parser("evaluation").add_subparsers(dest="subcommand", required=True)
    evaluation_create = evaluation.add_parser("create")
    evaluation_create.add_argument("--input", required=True, help="EvaluationRequest JSON; candidate availability is checked before persistence.")
    evaluation_create.add_argument("--candidate-artifact-dir", action="append", help="Persisted Skill Ablation artifact directory; repeatable.")
    evaluation_create.add_argument("--candidate-available", action="store_true", help="Use when a non-Skill candidate revision is available outside the artifact loader.")
    evaluation_get = evaluation.add_parser("get")
    evaluation_get.add_argument("project_id")
    evaluation_get.add_argument("request_id")
    evaluation_execution_config = evaluation.add_parser("execution-config").add_subparsers(
        dest="execution_config_subcommand", required=True
    )
    execution_config_register = evaluation_execution_config.add_parser("register")
    execution_config_register.add_argument("--project-id", required=True)
    execution_config_register.add_argument("--input", required=True, help="Server-owned target and Oracle contract JSON.")
    execution_config_list = evaluation_execution_config.add_parser("list")
    execution_config_list.add_argument("--project-id", required=True)
    evaluation_plan = evaluation.add_parser("plan")
    evaluation_plan.add_argument("--project-id", required=True)
    evaluation_plan.add_argument("--evaluation-request-id", required=True)
    evaluation_plan.add_argument("--binding", required=True, help="Non-secret control_plane ProviderBinding JSON.")
    evaluation_plan.add_argument("--product-definition", required=True)
    evaluation_plan.add_argument("--evaluation-name", default="Evaluation")
    evaluation_plan.add_argument("--knowledge-pattern")
    evaluation_plan.add_argument("--target-binding", help="Optional target-native ProviderBinding JSON frozen into the Scope.")
    evaluation_plan.add_argument("--output")
    evaluation_readiness = evaluation.add_parser("readiness")
    evaluation_readiness.add_argument("--project-id", required=True)
    evaluation_readiness.add_argument("--plan", required=True, help="Persisted EvaluationPlan JSON.")
    evaluation_readiness.add_argument("--fixture-root", help="Root for project-declared file and directory fixtures.")
    evaluation_run = evaluation.add_parser("run")
    evaluation_run.add_argument("--project-id", required=True)
    evaluation_run.add_argument("--plan", required=True, help="Persisted frozen EvaluationPlan JSON.")
    evaluation_run.add_argument("--execution-config-id", required=True, help="Reviewed server-owned target and Oracle contract.")
    evaluation_run.add_argument("--fixture-root")
    evaluation_run.add_argument("--evaluation-id")
    evaluation_run.add_argument("--output", help="Optional completed EvaluationRun JSON output.")
    evaluation_evidence = evaluation.add_parser("evidence")
    evaluation_evidence.add_argument("--project-id", required=True)
    evaluation_evidence.add_argument("--run-id", required=True)
    evaluation_report = evaluation.add_parser("report")
    evaluation_report.add_argument("--project-id", required=True)
    evaluation_report.add_argument("--run-id", required=True)
    evaluation_report.add_argument("--binding", required=True, help="Non-secret control_plane ProviderBinding JSON.")
    evaluation_report.add_argument("--product-definition", required=True)
    evaluation_report.add_argument("--output")

    memory = commands.add_parser("memory").add_subparsers(dest="subcommand", required=True)
    memory_list = memory.add_parser("list")
    memory_list.add_argument("--project-id", required=True)
    memory_list.add_argument("--component-pattern")
    memory_record = memory.add_parser("record")
    memory_record.add_argument("--project-id", required=True)
    memory_record.add_argument("--input", required=True, help="Evaluation Knowledge JSON.")
    memory_from_report = memory.add_parser("from-report")
    memory_from_report.add_argument("--project-id", required=True)
    memory_from_report.add_argument("--report", required=True)
    memory_from_report.add_argument("--component-pattern", required=True)

    benchmark = commands.add_parser("benchmark").add_subparsers(dest="subcommand", required=True)
    benchmark_import = benchmark.add_parser("import")
    benchmark_import.add_argument("--project-id", required=True)
    benchmark_import.add_argument("--input", required=True, help="External benchmark result JSON; AIG imports only its summary.")
    benchmark_import.add_argument("--source-ref")
    benchmark_list = benchmark.add_parser("list")
    benchmark_list.add_argument("--project-id", required=True)

    evaluation_matrix = evaluation.add_parser("interaction-matrix")
    evaluation_matrix.add_argument("--project-id", required=True)
    evaluation_matrix.add_argument("--plan", required=True, help="Persisted EvaluationPlan JSON.")
    evaluation_matrix.add_argument("--manifest", required=True, help="Target manifest declaring an interaction command.")
    evaluation_matrix.add_argument("--cache-root", required=True, help="Imported target environment cache root.")
    evaluation_matrix.add_argument("--fixture-root", help="Root for project-declared file and directory fixtures.")
    evaluation_matrix.add_argument("--run-root", required=True, help="Root for one immutable matrix run.")
    evaluation_matrix.add_argument("--output", required=True, help="Interaction matrix artifact JSON output path.")
    evaluation_matrix.add_argument("--interaction-name", required=True)
    evaluation_matrix.add_argument("--evaluation-id", required=True)
    evaluation_matrix.add_argument("--oracle-command-part", action="append", required=True)
    evaluation_matrix.add_argument("--oracle-id", required=True)
    evaluation_matrix.add_argument("--oracle-type", choices=["rule_based", "frozen_lookup", "structured_state"], default="rule_based")
    evaluation_matrix.add_argument("--oracle-version", default="1.0")
    evaluation_matrix.add_argument("--oracle-cwd")
    evaluation_matrix.add_argument("--binding", help="Optional target-native ProviderBinding JSON.")

    target = commands.add_parser("target").add_subparsers(dest="subcommand", required=True)
    target_init = target.add_parser("init")
    target_init.add_argument("--source", required=True)
    target_init.add_argument("--target-id", required=True)
    target_init.add_argument("--kind", choices=["native_http", "native_command"], default="native_http")
    target_init.add_argument("--application")
    target_init.add_argument("--readiness-path")
    target_init.add_argument("--command-part", action="append")
    target_init.add_argument("--required-file", action="append", required=True)
    target_init.add_argument("--dependency-lock")
    target_init.add_argument("--python")
    target_init.add_argument("--runtime-requirement", action="append", help="JSON runtime requirement; repeatable.")
    target_init.add_argument("--sut-provider", help="Path to a non-secret target Provider mapping JSON.")
    target_init.add_argument("--trace", help="Path to a non-secret target trace contract JSON.")
    target_init.add_argument("--interaction", help="Path to a non-secret target Interaction command contract JSON.")
    target_init.add_argument("--output", required=True)
    target_inspect = target.add_parser("inspect")
    target_inspect.add_argument("--manifest", required=True)
    target_cache = target.add_parser("cache").add_subparsers(dest="cache_action", required=True)
    target_cache_import = target_cache.add_parser("import")
    target_cache_import.add_argument("--manifest", required=True)
    target_cache_import.add_argument("--environment", required=True)
    target_cache_import.add_argument("--cache-root", required=True)
    target_preflight = target.add_parser("preflight")
    target_preflight.add_argument("--manifest", required=True)
    target_preflight.add_argument("--cache-root", required=True)
    target_probe = target.add_parser("probe")
    target_probe.add_argument("--manifest", required=True)
    target_probe.add_argument("--cache-root", required=True)
    target_probe.add_argument("--state-path", required=True)
    target_probe.add_argument("--log-path", required=True)
    target_golden = target.add_parser("golden-path")
    target_golden.add_argument("--manifest", required=True)
    target_golden.add_argument("--cache-root", required=True)

    product = commands.add_parser("product").add_subparsers(dest="subcommand", required=True)
    add = product.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--description", default="")
    product.add_parser("list")
    get = product.add_parser("get")
    get.add_argument("product_id")
    version = commands.add_parser("version").add_subparsers(dest="subcommand", required=True)
    import_version = version.add_parser("import")
    import_version.add_argument("--product-id", required=True)
    import_version.add_argument("--source", required=True)
    import_version.add_argument("--label", required=True)

    report = commands.add_parser("report").add_subparsers(dest="subcommand", required=True)
    product_report = report.add_parser("product")
    product_report.add_argument("--project-name", required=True)
    product_report.add_argument("--evaluation-request-id", help="Validated EvaluationRequest ID; binds the report to Project Intelligence and versions.")
    product_report.add_argument("--artifact-dir", action="append", required=True, help="Persisted Skill-ablation artifact directory; repeatable.")
    product_report.add_argument("--binding", required=True, help="Path to a non-secret control_plane ProviderBinding JSON.")
    product_report.add_argument(
        "--output-dir",
        default=str(Path(__file__).parents[2] / "examples" / "reports"),
        help="Directory for ProductEvaluationReport projections; defaults to the repository examples/reports directory.",
    )
    product_report.add_argument("--evaluation-name", default="Skill Ablation")
    product_report.add_argument("--knowledge-pattern", help="Optional reusable Evaluation Knowledge pattern key.")
    product_report.add_argument("--product-definition", help="Path to a declared product component definition JSON.")
    product_report.add_argument(
        "--scenario-map",
        help="Optional JSON mapping of persisted trial_ref values to Evaluation Plan scenario_id values.",
    )
    product_report.add_argument(
        "--evaluation-plan",
        help="Optional persisted EvaluationPlan JSON, or a ProductEvaluationReport JSON containing evaluation_plan.",
    )
    product_report.add_argument(
        "--report-template",
        help="Optional ProductReportTemplate JSON; defaults to the repository product-evaluation template.",
    )
    product_report.add_argument("--benchmark-evidence", action="append", help="Optional imported BenchmarkEvidence JSON; repeatable.")
    pair_report = report.add_parser("pair")
    pair_report.add_argument("--project-name", required=True)
    pair_report.add_argument("--evaluation-request-id", required=True)
    pair_report.add_argument("--artifact", required=True, help="Skill Pair evidence artifact JSON.")
    pair_report.add_argument("--binding", required=True)
    pair_report.add_argument("--product-definition", required=True)
    pair_report.add_argument("--output-dir", required=True)
    pair_report.add_argument("--evaluation-name", default="Skill Pair Evaluation")
    pair_report.add_argument("--knowledge-pattern", help="Optional reusable Evaluation Knowledge pattern key.")
    pair_report.add_argument("--evaluation-plan")
    pair_report.add_argument("--report-template")
    pair_report.add_argument("--benchmark-evidence", action="append", help="Optional imported BenchmarkEvidence JSON; repeatable.")
    tool_report = report.add_parser("tool")
    tool_report.add_argument("--project-name", required=True)
    tool_report.add_argument("--evaluation-request-id", required=True)
    tool_report.add_argument("--artifact", required=True, help="Tool Regression evidence artifact JSON.")
    tool_report.add_argument("--binding", required=True)
    tool_report.add_argument("--product-definition", required=True)
    tool_report.add_argument("--output-dir", required=True)
    tool_report.add_argument("--evaluation-name", default="Tool Regression")
    tool_report.add_argument("--knowledge-pattern", help="Optional reusable Evaluation Knowledge pattern key.")
    tool_report.add_argument("--report-template")
    tool_report.add_argument("--benchmark-evidence", action="append", help="Optional imported BenchmarkEvidence JSON; repeatable.")

    release = commands.add_parser("release").add_subparsers(dest="subcommand", required=True)
    release_gate = release.add_parser("gate")
    release_gate.add_argument("--report", dest="report_path", required=True, help="Completed ProductEvaluationReport JSON.")
    release_gate.add_argument("--output", help="Optional path for the deterministic Release Decision result JSON.")

    evolution = commands.add_parser("evolution").add_subparsers(dest="subcommand", required=True)
    intake = evolution.add_parser("intake")
    intake.add_argument("--project-id", required=True)
    intake.add_argument("--source", required=True)
    intake.add_argument("--baseline", required=True)
    intake.add_argument("--candidate", required=True)
    intake.add_argument("--repository-url")
    intake.add_argument("--entrypoint")
    stale = evolution.add_parser("propagate-stale")
    stale.add_argument("--project-id", required=True)
    stale.add_argument("--changeset-id", required=True)
    evolution_report = evolution.add_parser("report")
    evolution_report.add_argument("--project-id", required=True)
    evolution_report.add_argument("--report-id", required=True)
    register = evolution.add_parser("register")
    register.add_argument("--project-id", required=True)
    register.add_argument("--case-id", required=True)
    register.add_argument("--kind", required=True, choices=["native-harness", "environment", "task-verifier", "preflight", "replay-evidence"])
    register.add_argument("--input", required=True, help="Path to a JSON contract/evidence payload; secrets are not accepted.")
    assess = evolution.add_parser("assess")
    assess.add_argument("--project-id", required=True)
    assess.add_argument("--case-id", required=True)
    bind_provider = evolution.add_parser("bind-provider")
    bind_provider.add_argument("--project-id", required=True)
    bind_provider.add_argument("--input", required=True, help="Path to a non-secret ProviderBinding JSON payload.")
    control_plane_smoke = evolution.add_parser("control-plane-smoke")
    control_plane_smoke.add_argument("--project-id", required=True)
    control_plane_smoke.add_argument("--binding-id", required=True)
    control_plane_smoke.add_argument("--evidence", required=True, help="Path to approved non-secret evidence JSON.")
    control_plane_smoke.add_argument("--evidence-ref", required=True)
    control_plane_smoke.add_argument("--objective", default="Read approved evidence and submit an evidence-linked hypothesis or insufficient evidence. Do not issue a release verdict.")
    compare = evolution.add_parser("compare")
    compare.add_argument("--project-id", required=True)
    compare.add_argument("--case-id", required=True)
    build_manifest = evolution.add_parser("build-manifest")
    build_manifest.add_argument("--project-id", required=True)
    build_manifest.add_argument("--case-id", required=True)
    build_manifest.add_argument("--run-id", required=True)
    report_agent = evolution.add_parser("report-agent")
    report_agent.add_argument("--project-id", required=True)
    report_agent.add_argument("--manifest-id", required=True)
    report_agent.add_argument("--binding-id", required=True)
    report_agent.add_argument("--output-dir", required=True)
    report_agent.add_argument("--objective", default="Read the immutable ReportManifest and submit a concise fact-linked narrative. Do not issue a release verdict.")
    report_result = evolution.add_parser("report-result")
    report_result.add_argument("--project-id", required=True)
    report_result.add_argument("--narrative-id", required=True)
    verify_skill_ablation = evolution.add_parser("verify-skill-ablation")
    verify_skill_ablation.add_argument("--project-id", required=True)
    verify_skill_ablation.add_argument("--case-id", required=True)
    verify_skill_ablation.add_argument("--contract", required=True)
    verify_skill_ablation.add_argument("--evidence", required=True)
    analyze_skill_ablation = evolution.add_parser("analyze-skill-ablation")
    analyze_skill_ablation.add_argument("--project-id", required=True)
    analyze_skill_ablation.add_argument("--binding-id", required=True)
    analyze_skill_ablation.add_argument("--contract-id", required=True)
    analyze_skill_ablation.add_argument("--evidence-id", required=True)
    analyze_skill_ablation.add_argument("--objective", default="Read immutable target evidence and submit an evidence-linked Skill analysis. Do not issue a verifier verdict.")
    return parser


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _load_benchmark_evidence(path: Path) -> BenchmarkEvidence:
    payload = _load_json_object(path)
    raw = payload.get("data", payload)
    if isinstance(raw, dict) and "benchmark_evidence" in raw:
        raw = raw["benchmark_evidence"]
    if isinstance(raw, list):
        if len(raw) != 1:
            raise ValueError(f"{path} must contain exactly one BenchmarkEvidence object.")
        raw = raw[0]
    if not isinstance(raw, dict):
        raise ValueError(f"{path} does not contain a BenchmarkEvidence object.")
    return BenchmarkEvidence.model_validate(raw)


def _supplementary_evidence(service: Service, project_id: str, paths: list[str] | None) -> list[BenchmarkEvidence]:
    records = {item.evidence_id: item for item in service.benchmark_evidence(project_id)}
    for path in paths or []:
        evidence = _load_benchmark_evidence(Path(path))
        if evidence.project_id != project_id:
            raise ValueError("Benchmark evidence project_id does not match the report project.")
        records[evidence.evidence_id] = evidence
    return [records[key] for key in sorted(records)]


def _load_evaluation_plan(path: Path) -> EvaluationPlan:
    payload = _load_json_object(path)
    raw_plan = payload.get("evaluation_plan", payload)
    if not isinstance(raw_plan, dict):
        raise ValueError(f"{path} does not contain an EvaluationPlan object.")
    return EvaluationPlan.model_validate(raw_plan)


def _validate_evaluation_plan_identity(plan: EvaluationPlan, target, change) -> None:
    expected = {
        "project_id": target.project_id,
        "target_id": target.target_id,
        "change_id": change.change_id,
        "component_type": target.component_type,
        "component_name": target.name,
        "evaluation_type": change.evaluation_type,
    }
    mismatches = {
        key: (getattr(plan, key), value)
        for key, value in expected.items()
        if getattr(plan, key) != value
    }
    if mismatches:
        raise ValueError(f"Persisted EvaluationPlan does not match the current artifacts: {mismatches}")


def _provider_from_binding(binding: ProviderBinding):
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    api_key = os.getenv(binding.expected_environment_variable)
    if not api_key:
        raise ValueError(f"{binding.expected_environment_variable} is required at runtime")
    return build_control_plane_client(binding, api_key)


def _validated_request_for_report(service: Service, project_id: str, request_id: str, component_type: str):
    request = service.evaluation_request(project_id, request_id)
    if request is None:
        raise ValueError(f"EvaluationRequest {request_id} was not found for project {project_id}.")
    if request.component_type != component_type:
        raise ValueError(
            f"EvaluationRequest {request_id} targets {request.component_type}, not {component_type}."
        )
    return service.create_evaluation_request(request, candidate_available=True)


def _report_output(report, args) -> dict[str, object]:
    report_template = load_product_report_template(Path(args.report_template)) if args.report_template else None
    paths = write_product_evaluation_outputs(Path(args.output_dir), report, report_template)
    return {
        **{f"{name}_path": str(path) for name, path in paths.items()},
        "evidence_manifest_sha256": report.evidence.artifact_manifest_hash,
        "report": report.model_dump(mode="json"),
    }


def _run_skill_pair_report(args, service: Service) -> dict[str, object]:
    request = _validated_request_for_report(service, args.project_name, args.evaluation_request_id, "skill_pair")
    intelligence = service.project_intelligence(args.project_name)
    if intelligence is None:
        raise ProductNotFoundError(args.project_name)
    product_definition = ProductDefinition.model_validate(_load_json_object(Path(args.product_definition)))
    target = build_skill_pair_evaluation_target(
        intelligence,
        request.component_name,
        product_definition,
        request.pair_members,
    )
    target = target.model_copy(update={
        "component_pattern": args.knowledge_pattern or target.component_type,
        "evaluation_knowledge": service.evaluation_knowledge_for_target(
            args.project_name,
            component_pattern=args.knowledge_pattern or target.component_type,
            component_type=target.component_type,
        )
    })
    change = build_skill_pair_evaluation_change(request, evaluation_name=args.evaluation_name)
    binding = ProviderBinding.model_validate({**_load_json_object(Path(args.binding)), "project_id": args.project_name})
    if binding.role != "control_plane":
        raise ValueError("Skill Pair report requires a control_plane ProviderBinding")
    provider = build_control_plane_client(binding, _provider_api_key(binding))
    plan = (
        _load_evaluation_plan(Path(args.evaluation_plan))
        if args.evaluation_plan
        else build_evolution_evaluation_plan(
            target,
            change,
            scenario_generator=LLMEvaluationScenarioGenerator(provider, binding),
            evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
        )
    )
    _validate_evaluation_plan_identity(plan, target, change)
    artifact = _load_json_object(Path(args.artifact))
    context = AdapterContext(
                project_id=args.project_name,
                evaluation_name=args.evaluation_name,
                evaluation_type="skill_pair_evaluation",
                component_name=request.component_name,
                source_ref=str(Path(args.artifact).resolve()),
        evaluation_request_id=request.request_id,
        baseline_version=request.baseline_version,
        candidate_version=request.candidate_version,
        evaluation_plan_id=plan.plan_id,
        experiment_ids_by_condition=skill_pair_experiment_ids_by_condition(plan),
    )
    evidence = build_v1_evaluation_adapter_layer().adapt(
        "skill_pair_evaluation", artifact, context=context
    )
    analyst_input = ProductAnalystInput(
        project_id=args.project_name,
        evaluation_name=args.evaluation_name,
        evaluation_type="skill_pair_evaluation",
        evaluation_question=plan.comparison_question,
        hypothesis=plan.hypothesis,
        product_definition=product_definition,
        evidence=evidence,
        evaluation_plan=plan,
    )
    analyst_result = ProductEvaluationAnalyst().analyze(
        analyst_input,
        provider=provider,
        binding=binding,
        forbidden_tokens=set(),
    )
    return _report_output(
        assemble_product_evaluation_report(
            analyst_input,
            analyst_result,
            supplementary_evidence=_supplementary_evidence(service, args.project_name, args.benchmark_evidence),
        ),
        args,
    )


def _run_tool_regression_report(args, service: Service) -> dict[str, object]:
    request = _validated_request_for_report(service, args.project_name, args.evaluation_request_id, "tool")
    intelligence = service.project_intelligence(args.project_name)
    if intelligence is None:
        raise ProductNotFoundError(args.project_name)
    product_definition = ProductDefinition.model_validate(_load_json_object(Path(args.product_definition)))
    if product_definition.component_type != "tool" or product_definition.component_name != request.component_name:
        raise ValueError("Tool product definition and EvaluationRequest component do not match.")
    capability = next(
        (
            item for item in intelligence.capability_registry
            if item.component_type == "tool" and item.name == request.component_name
        ),
        None,
    )
    if capability is None:
        raise ValueError(f"Tool {request.component_name} is not registered for project {args.project_name}.")
    artifact = _load_json_object(Path(args.artifact))
    validate_tool_regression_artifact(artifact, expected_tool_name=request.component_name)
    binding = ProviderBinding.model_validate({**_load_json_object(Path(args.binding)), "project_id": args.project_name})
    if binding.role != "control_plane":
        raise ValueError("Tool Regression report requires a control_plane ProviderBinding")
    provider = build_control_plane_client(binding, _provider_api_key(binding))
    context = AdapterContext(
        project_id=args.project_name,
        evaluation_name=args.evaluation_name,
        evaluation_type="tool_regression",
        component_name=request.component_name,
        source_ref=str(Path(args.artifact).resolve()),
        evaluation_request_id=request.request_id,
        baseline_version=request.baseline_version,
        candidate_version=request.candidate_version,
    )
    evidence = build_v1_evaluation_adapter_layer().adapt(
        "tool_regression", artifact, context=context
    )
    analyst_input = ProductAnalystInput(
        project_id=args.project_name,
        evaluation_name=args.evaluation_name,
        evaluation_type="tool_regression",
        evaluation_question="Did the Tool change preserve product task success while keeping call correctness, latency, and cost acceptable?",
        hypothesis="The candidate Tool may change downstream task success even when the Tool call itself succeeds.",
        product_definition=product_definition,
        evidence=evidence,
    )
    analyst_result = ProductEvaluationAnalyst().analyze(
        analyst_input,
        provider=provider,
        binding=binding,
        forbidden_tokens=set(),
    )
    return _report_output(
        assemble_product_evaluation_report(
            analyst_input,
            analyst_result,
            supplementary_evidence=_supplementary_evidence(service, args.project_name, args.benchmark_evidence),
        ),
        args,
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(Path(__file__).parents[2] / ".env", override=False)
    args = build_parser().parse_args(argv)
    service = Service(args.db)
    try:
        if args.command == "init":
            output = {"db": args.db}
        elif args.command == "project" and args.subcommand == "register":
            payload = _load_json_object(Path(args.input))
            payload["project_id"] = args.project_id
            registration = ProjectIntelligenceRegistration.model_validate(payload)
            output = service.register_project_intelligence(registration).model_dump(mode="json")
            if args.benchmark_result:
                output["benchmark_evidence"] = [
                    service.import_benchmark_evidence(
                        args.project_id,
                        _load_json_object(Path(path)),
                        source_ref=str(Path(path).resolve()),
                        source_bytes=Path(path).read_bytes(),
                    ).model_dump(mode="json")
                    for path in args.benchmark_result
                ]
        elif args.command == "project" and args.subcommand == "snapshot":
            payload = _load_json_object(Path(args.input))
            payload["project_id"] = args.project_id
            registration = ProjectIntelligenceRegistration.model_validate(payload)
            output = service.register_project_snapshot(registration).model_dump(mode="json")
        elif args.command == "project" and args.subcommand == "scan":
            output = service.scan_project(ProjectScanRequest(
                project_id=args.project_id,
                source_kind=args.source_kind,
                source_ref=args.source,
                version=args.version,
                entrypoint=args.entrypoint,
                runtime_kind=args.runtime_kind,
                declaration_file=args.declaration_file,
            )).model_dump(mode="json")
        elif args.command == "project" and args.subcommand == "runtime-preflight":
            output = service.runtime_preflight(
                args.project_id,
                args.version,
                source_root=Path(args.source_root) if args.source_root else None,
            ).model_dump(mode="json")
        elif args.command == "project" and args.subcommand == "runtime-compare":
            output = service.runtime_comparability(
                args.project_id,
                args.baseline_version,
                args.candidate_version,
                baseline_source_root=Path(args.baseline_source_root) if args.baseline_source_root else None,
                candidate_source_root=Path(args.candidate_source_root) if args.candidate_source_root else None,
            ).model_dump(mode="json")
        elif args.command == "project" and args.subcommand == "get":
            intelligence = service.project_intelligence(args.project_id)
            if intelligence is None:
                raise ProductNotFoundError(args.project_id)
            output = intelligence.model_dump(mode="json")
        elif args.command == "memory" and args.subcommand == "list":
            output = {
                "knowledge": [
                    item.model_dump(mode="json")
                    for item in service.evaluation_knowledge(args.project_id, args.component_pattern)
                ]
            }
        elif args.command == "memory" and args.subcommand == "record":
            payload = _load_json_object(Path(args.input))
            payload["project_id"] = args.project_id
            output = service.record_evaluation_knowledge(
                EvaluationKnowledge.model_validate(payload)
            ).model_dump(mode="json")
        elif args.command == "memory" and args.subcommand == "from-report":
            report = ProductEvaluationReport.model_validate(_load_json_object(Path(args.report)))
            output = service.record_evaluation_knowledge_from_report(
                args.project_id, report, component_pattern=args.component_pattern
            ).model_dump(mode="json")
        elif args.command == "benchmark" and args.subcommand == "import":
            source_path = Path(args.input)
            output = service.import_benchmark_evidence(
                args.project_id,
                _load_json_object(source_path),
                source_ref=args.source_ref or str(source_path.resolve()),
                source_bytes=source_path.read_bytes(),
            ).model_dump(mode="json")
        elif args.command == "benchmark" and args.subcommand == "list":
            output = {
                "benchmark_evidence": [
                    item.model_dump(mode="json") for item in service.benchmark_evidence(args.project_id)
                ]
            }
        elif args.command == "evaluation" and args.subcommand == "create":
            request = EvaluationRequest.model_validate(_load_json_object(Path(args.input)))
            artifact_dirs = [Path(path) for path in (args.candidate_artifact_dir or [])]
            artifacts = [load_skill_ablation_artifact(request.project_id, path) for path in artifact_dirs]
            candidate_component_name = None
            if artifacts:
                candidate_component_name = artifacts[0].contract.skill_name
            output = service.create_evaluation_request(
                request,
                candidate_available=bool(artifacts) or args.candidate_available,
                candidate_component_name=candidate_component_name,
            ).model_dump(mode="json")
        elif args.command == "evaluation" and args.subcommand == "get":
            request = service.evaluation_request(args.project_id, args.request_id)
            if request is None:
                raise ProductNotFoundError(args.request_id)
            output = request.model_dump(mode="json")
        elif args.command == "evaluation" and args.subcommand == "plan":
            intelligence = service.project_intelligence(args.project_id)
            if intelligence is None:
                raise ProductNotFoundError(args.project_id)
            request = service.evaluation_request(args.project_id, args.evaluation_request_id)
            if request is None:
                raise ProductNotFoundError(args.evaluation_request_id)
            product_definition = ProductDefinition.model_validate(_load_json_object(Path(args.product_definition)))
            pattern = args.knowledge_pattern or request.component_type
            binding = ProviderBinding.model_validate({
                **_load_json_object(Path(args.binding)),
                "project_id": args.project_id,
            })
            if binding.role != "control_plane":
                raise ValueError("Evaluation Plan generation requires a control_plane ProviderBinding.")
            target_binding = None
            if args.target_binding:
                target_payload = _load_json_object(Path(args.target_binding))
                if not target_payload.get("provider_binding_id"):
                    raise ValueError(
                        "Target ProviderBinding JSON must declare provider_binding_id when it is frozen into Evaluation Scope."
                    )
                target_binding = ProviderBinding.model_validate({
                    **target_payload,
                    "project_id": args.project_id,
                })
                if target_binding.role != "sut_native":
                    raise ValueError("Evaluation Scope target binding requires a sut_native ProviderBinding.")
            plan = build_evaluation_plan_for_request(
                request,
                intelligence,
                product_definition,
                evaluation_name=args.evaluation_name,
                scenario_generator=LLMEvaluationScenarioGenerator(_provider_from_binding(binding), binding),
                evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
                knowledge_pattern=pattern,
                evaluation_knowledge=service.evaluation_knowledge_for_target(
                    args.project_id, component_pattern=pattern, component_type=request.component_type
                ),
            )
            plan = bind_evaluation_scope(
                plan,
                freeze_evaluation_scope(
                    request,
                    intelligence,
                    binding,
                    planned_trial_count=planned_trial_count(plan),
                    target_binding=target_binding,
                ),
            )
            service.bind_evaluation_request_scope(request, plan.evaluation_scope.scope_id)
            service.save_evaluation_plan(plan)
            if args.output:
                output_path = Path(args.output).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            output = {
                "plan": plan.model_dump(mode="json"),
                "knowledge_hit_ids": [item.knowledge_id for item in plan.evaluation_knowledge],
                "scenario_categories": [item.category for item in plan.scenarios],
                "scenario_hashes": [item.scenario_hash for item in plan.scenarios],
                "output": str(Path(args.output).resolve()) if args.output else None,
            }
        elif args.command == "evaluation" and args.subcommand == "execution-config" and args.execution_config_subcommand == "register":
            payload = _load_json_object(Path(args.input))
            config = EvaluationExecutionConfiguration.model_validate({
                **payload,
                "project_id": args.project_id,
            })
            if config.target_provider_binding_id:
                binding = service.provider_binding(args.project_id, config.target_provider_binding_id)
                if binding.role != "sut_native":
                    raise ValueError("Execution Configuration target binding must be sut_native.")
            output = execution_configuration_metadata(
                service.save_evaluation_execution_configuration(config)
            ).model_dump(mode="json")
        elif args.command == "evaluation" and args.subcommand == "execution-config" and args.execution_config_subcommand == "list":
            output = {
                "execution_configurations": [
                    execution_configuration_metadata(item).model_dump(mode="json")
                    for item in service.evaluation_execution_configurations(args.project_id)
                ]
            }
        elif args.command == "evaluation" and args.subcommand == "readiness":
            intelligence = service.project_intelligence(args.project_id)
            if intelligence is None:
                raise ProductNotFoundError(args.project_id)
            plan = _load_evaluation_plan(Path(args.plan))
            if plan.project_id != args.project_id:
                raise ValueError("Evaluation Plan project_id does not match --project-id.")
            readiness = check_evaluation_plan_readiness(
                plan,
                intelligence.runtime_profile.fixture_catalog,
                fixture_root=Path(args.fixture_root) if args.fixture_root else None,
            )
            output = readiness.model_dump(mode="json")
        elif args.command == "evaluation" and args.subcommand == "run":
            intelligence = service.project_intelligence(args.project_id)
            if intelligence is None:
                raise ProductNotFoundError(args.project_id)
            plan = _load_evaluation_plan(Path(args.plan))
            if plan.project_id != args.project_id:
                raise ValueError("Evaluation Plan project_id does not match --project-id.")
            if plan.evaluation_scope is None:
                raise ValueError("Evaluation execution requires a frozen Evaluation Scope.")
            request = service.evaluation_request(args.project_id, plan.change_id)
            if request is None:
                raise ProductNotFoundError(plan.change_id)
            execution_config = service.evaluation_execution_configuration(args.project_id, args.execution_config_id)
            if execution_config is None:
                raise ProductNotFoundError(args.execution_config_id)
            evaluation_id = args.evaluation_id or f"evaluation_{plan.plan_id}"
            execution = resolve_evaluation_execution_inputs(plan, execution_config, evaluation_id=evaluation_id)
            target_binding = None
            if execution.target_provider_binding_id:
                target_binding = service.provider_binding(args.project_id, execution.target_provider_binding_id)
                if target_binding.role != "sut_native":
                    raise ValueError("Target execution requires a sut_native ProviderBinding.")
            run = EvaluationRun(
                evaluation_id=evaluation_id,
                project_id=args.project_id,
                evaluation_request_id=request.request_id,
                evaluation_plan_id=plan.plan_id,
                execution_config_id=execution_config.config_id,
                scope_id=plan.evaluation_scope.scope_id,
                status="running",
            )
            service.save_evaluation_run(run)
            try:
                artifact = execute_evaluation_run(
                    plan,
                    intelligence,
                    manifest_path=execution.manifest_path,
                    cache_root=execution.cache_root,
                    fixture_root=Path(args.fixture_root) if args.fixture_root else None,
                    run_root=execution.run_root,
                    evaluation_id=run.evaluation_id,
                    oracle_command=execution.oracle_command,
                    oracle_id=execution.oracle_id,
                    oracle_type=execution.oracle_type,
                    oracle_version=execution.oracle_version,
                    oracle_cwd=execution.oracle_cwd,
                    target_binding=target_binding,
                )
                artifact_payload = artifact.model_dump(mode="json")
                evidence = adapt_evaluation_run_evidence(
                    plan,
                    request,
                    run_id=run.run_id,
                    scope_id=run.scope_id,
                    artifact=artifact_payload,
                )
                run = service.save_evaluation_run(run.model_copy(update={
                    "status": "completed",
                    "current_stage": "evidence",
                    "readiness_ref": content_ref(artifact_payload["scenario_readiness"]),
                    "matrix_artifact_ref": str(artifact_payload["artifact_manifest_hash"]),
                    "evidence_bundle_ref": content_ref(evidence.model_dump(mode="json")),
                    "artifact": artifact_payload,
                    "evidence_refs": list(artifact.evidence_refs),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }))
            except (ValueError, TargetInfrastructureError, OSError) as error:
                service.save_evaluation_run(run.model_copy(update={
                    "status": "failed",
                    "current_stage": "failed",
                    "failure_classification": classify_run_failure(error),
                    "error": str(error),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }))
                raise
            if args.output:
                output_path = Path(args.output).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            output = run.model_dump(mode="json")
        elif args.command == "evaluation" and args.subcommand == "evidence":
            run = service.evaluation_run(args.project_id, args.run_id)
            if run is None:
                raise ProductNotFoundError(args.run_id)
            plan = service.evaluation_plan(args.project_id, run.evaluation_plan_id)
            request = service.evaluation_request(args.project_id, run.evaluation_request_id)
            if plan is None or request is None or run.artifact is None:
                raise ValueError("Completed Evaluation Run, Plan, and Request are required for evidence.")
            output = adapt_evaluation_run_evidence(
                plan,
                request,
                run_id=run.run_id,
                scope_id=run.scope_id,
                artifact=run.artifact,
            ).model_dump(mode="json")
        elif args.command == "evaluation" and args.subcommand == "report":
            run = service.evaluation_run(args.project_id, args.run_id)
            if run is None:
                raise ProductNotFoundError(args.run_id)
            plan = service.evaluation_plan(args.project_id, run.evaluation_plan_id)
            request = service.evaluation_request(args.project_id, run.evaluation_request_id)
            if plan is None or request is None or run.artifact is None:
                raise ValueError("Completed Evaluation Run, Plan, and Request are required for report.")
            try:
                binding = ProviderBinding.model_validate({
                    **_load_json_object(Path(args.binding)),
                    "project_id": args.project_id,
                })
                product_definition = ProductDefinition.model_validate(_load_json_object(Path(args.product_definition)))
                evidence = adapt_evaluation_run_evidence(
                    plan,
                    request,
                    run_id=run.run_id,
                    scope_id=run.scope_id,
                    artifact=run.artifact,
                )
                report = build_product_evaluation_report(
                    plan,
                    product_definition,
                    evidence,
                    provider=_provider_from_binding(binding),
                    binding=binding,
                    forbidden_tokens={run.run_id},
                )
                report_payload = report.model_dump(mode="json")
                existing_report = service.evaluation_report(args.project_id, report.report_id)
                if existing_report is None:
                    service.save_evaluation_report(EvaluationReportRecord(
                        report_id=report.report_id,
                        project_id=args.project_id,
                        run_id=run.run_id,
                        evaluation_plan_id=plan.plan_id,
                        scope_id=run.scope_id,
                        report=report_payload,
                    ))
                elif (
                    existing_report.run_id != run.run_id
                    or existing_report.scope_id != run.scope_id
                    or existing_report.report != report_payload
                ):
                    raise ValueError("Persisted Evaluation Report does not match this Evaluation Run.")
                service.save_evaluation_run(run.model_copy(update={
                    "current_stage": "completed",
                    "report_ref": report.report_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }))
                if args.output:
                    output_path = Path(args.output).resolve()
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                output = report_payload
            except (AssistantInputError, EvolutionIntakeError, ProviderRuntimeError, ValueError) as error:
                service.save_evaluation_run(run.model_copy(update={
                    "status": "failed",
                    "current_stage": "failed",
                    "failure_classification": classify_report_failure(error),
                    "error": str(error),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }))
                raise
        elif args.command == "evaluation" and args.subcommand == "interaction-matrix":
            intelligence = service.project_intelligence(args.project_id)
            if intelligence is None:
                raise ProductNotFoundError(args.project_id)
            plan = _load_evaluation_plan(Path(args.plan))
            if plan.project_id != args.project_id:
                raise ValueError("Evaluation Plan project_id does not match --project-id.")
            fixture_root = Path(args.fixture_root) if args.fixture_root else None
            readiness = check_evaluation_plan_readiness(
                plan,
                intelligence.runtime_profile.fixture_catalog,
                fixture_root=fixture_root,
            )
            target = TargetRuntimeAdapter(Path(args.manifest), Path(args.cache_root))
            binding = None
            if args.binding:
                binding = ProviderBinding.model_validate(_load_json_object(Path(args.binding)))
                if binding.project_id != args.project_id:
                    raise ValueError("Target ProviderBinding project_id does not match --project-id.")
            oracle = SubprocessInteractionOracle(
                tuple(args.oracle_command_part),
                verifier_id=args.oracle_id,
                oracle_type=args.oracle_type,
                oracle_version=args.oracle_version,
                working_directory=Path(args.oracle_cwd) if args.oracle_cwd else None,
            )
            runner = ManifestInteractionTrialRunner(
                target,
                fixture_catalog=intelligence.runtime_profile.fixture_catalog,
                fixture_root=fixture_root,
                oracle=oracle,
                binding=binding,
            )
            artifact = execute_evaluation_matrix(
                plan,
                evaluation_name=args.interaction_name,
                evaluation_id=args.evaluation_id,
                readiness=readiness,
                runner=runner,
                condition_kinds=PAIR_INTERACTION_CONDITIONS,
                run_root=Path(args.run_root),
                output_path=Path(args.output),
            )
            output = artifact.model_dump(mode="json")
        elif args.command == "target" and args.subcommand == "init":
            output = initialize_target_manifest(
                source=Path(args.source), output=Path(args.output), target_id=args.target_id, kind=args.kind,
                application=args.application, readiness_path=args.readiness_path, command=args.command_part,
                required_source_files=args.required_file, dependency_lock=args.dependency_lock, python_executable=args.python,
                runtime_requirements=[json.loads(item) for item in args.runtime_requirement or []],
                sut_provider=_load_json_object(Path(args.sut_provider)) if args.sut_provider else None,
                trace=_load_json_object(Path(args.trace)) if args.trace else None,
                interaction=_load_json_object(Path(args.interaction)) if args.interaction else None,
            ).model_dump()
        elif args.command == "target" and args.subcommand == "inspect":
            output = inspect_target_manifest(Path(args.manifest))
        elif args.command == "target" and args.subcommand == "cache":
            output = TargetEnvironmentCache(Path(args.cache_root)).import_environment(Path(args.manifest), Path(args.environment)).model_dump()
        elif args.command == "target" and args.subcommand == "preflight":
            output = TargetEnvironmentCache(Path(args.cache_root)).preflight(Path(args.manifest))
        elif args.command == "target" and args.subcommand == "probe":
            adapter = TargetRuntimeAdapter(Path(args.manifest), Path(args.cache_root))
            readiness_path = adapter.manifest.runtime.readiness_path
            if not readiness_path:
                raise ValueError("target probe requires a manifest runtime readiness_path")
            handle = adapter.start_service(state_path=Path(args.state_path), log_path=Path(args.log_path))
            try:
                status, body = adapter.execute_http(handle, HttpOperation("readiness_probe", "GET", readiness_path, timeout_seconds=10))
            finally:
                handle.close()
            output = {"status": status, "body": body, "readiness_path": readiness_path}
        elif args.command == "target":
            output = target_golden_path(Path(args.manifest), Path(args.cache_root))
        elif args.command == "product" and args.subcommand == "add":
            product, version = service.create(args.name, args.description)
            output = {"product": product.model_dump(), "version": version.model_dump()}
        elif args.command == "product" and args.subcommand == "list":
            output = {"products": [product.model_dump() for product in service.products()]}
        elif args.command == "product":
            product = service.product(args.product_id)
            if not product:
                raise ProductNotFoundError(args.product_id)
            output = {"product": product.model_dump()}
        elif args.command == "version":
            output = {"version": service.import_version(args.product_id, Path(args.source), args.label).model_dump()}
        elif args.command == "report" and args.subcommand == "pair":
            output = _run_skill_pair_report(args, service)
        elif args.command == "report" and args.subcommand == "tool":
            output = _run_tool_regression_report(args, service)
        elif args.command == "release" and args.subcommand == "gate":
            report = ProductEvaluationReport.model_validate(_load_json_object(Path(args.report_path)))
            decision = evaluate_release_decision(report)
            output = decision.model_dump(mode="json")
            if args.output:
                output_path = Path(args.output).resolve()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                output["output_path"] = str(output_path)
        elif args.command == "report" and args.subcommand == "product":
            binding = ProviderBinding.model_validate({**_load_json_object(Path(args.binding)), "project_id": args.project_name})
            if binding.role != "control_plane":
                raise ValueError("Product report requires a control_plane ProviderBinding")
            artifacts = [load_skill_ablation_artifact(args.project_name, Path(path)) for path in args.artifact_dir]
            evaluation_request = None
            if args.evaluation_request_id:
                evaluation_request = service.evaluation_request(args.project_name, args.evaluation_request_id)
                if evaluation_request is None:
                    raise ValueError(f"EvaluationRequest {args.evaluation_request_id} was not found for project {args.project_name}.")
                service.create_evaluation_request(
                    evaluation_request,
                    candidate_available=True,
                    candidate_component_name=artifacts[0].contract.skill_name,
                    skill_artifacts=artifacts,
                )
            product_definition = ProductDefinition.model_validate(_load_json_object(Path(args.product_definition))) if args.product_definition else None
            if product_definition is None:
                product_definition = product_definition_from_skill_artifact(artifacts[0])
            target = build_skill_evaluation_target(artifacts[0], product_definition)
            target = target.model_copy(update={
                "component_pattern": args.knowledge_pattern or target.component_type,
                "evaluation_knowledge": service.evaluation_knowledge_for_target(
                    args.project_name,
                    component_pattern=args.knowledge_pattern or target.component_type,
                    component_type=target.component_type,
                ),
            })
            change = build_skill_ablation_change(artifacts[0], evaluation_name=args.evaluation_name)
            control_plane = build_control_plane_client(binding, _provider_api_key(binding))
            evaluation_plan = (
                _load_evaluation_plan(Path(args.evaluation_plan))
                if args.evaluation_plan
                else build_evolution_evaluation_plan(
                    target,
                    change,
                    scenario_generator=LLMEvaluationScenarioGenerator(control_plane, binding),
                    evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
                )
            )
            _validate_evaluation_plan_identity(evaluation_plan, target, change)
            scenario_ids_by_trial_ref = {
                artifact.evidence.trial_ref: artifact.evidence.scenario_id
                for artifact in artifacts
                if artifact.evidence.scenario_id is not None
            }
            if args.scenario_map:
                scenario_ids_by_trial_ref = parse_execution_scenario_mapping(
                    _load_json_object(Path(args.scenario_map))
                )
            execution_mapping = None
            if scenario_ids_by_trial_ref:
                execution_mapping = build_evaluation_execution_mapping(
                    evaluation_plan,
                    [artifact.evidence.trial_ref for artifact in artifacts],
                    scenario_ids_by_trial_ref,
                )
            adapter_context = AdapterContext(
                project_id=args.project_name,
                evaluation_name=args.evaluation_name,
                evaluation_type="skill_ablation",
                component_name=artifacts[0].contract.skill_name,
                source_ref=",".join(str(Path(path).resolve()) for path in args.artifact_dir),
                evaluation_request_id=evaluation_request.request_id if evaluation_request else None,
                baseline_version=evaluation_request.baseline_version if evaluation_request else None,
                candidate_version=evaluation_request.candidate_version if evaluation_request else None,
                product_definition_ref=str(Path(args.product_definition).resolve()) if args.product_definition else None,
                evaluation_plan_id=evaluation_plan.plan_id,
                experiment_ids_by_condition=skill_ablation_experiment_ids_by_condition(evaluation_plan),
                scenario_ids_by_trial_ref=execution_mapping.by_trial_ref() if execution_mapping else {},
            )
            evidence_bundle = build_v1_evaluation_adapter_layer().adapt(
                "skill_ablation",
                artifacts,
                context=adapter_context,
            )
            legacy_input, _raw_evidence = build_skill_ablation_analyst_input(
                args.project_name,
                artifacts,
                product_definition=product_definition,
                evaluation_name=args.evaluation_name,
                evidence_bundle=evidence_bundle,
            )
            analyst_input = ProductAnalystInput(
                project_id=legacy_input.project_id,
                evaluation_name=legacy_input.evaluation_name,
                evaluation_type="skill_ablation",
                evaluation_question=evaluation_plan.comparison_question,
                hypothesis=evaluation_plan.hypothesis,
                product_definition=legacy_input.product_definition,
                evidence=legacy_input.evidence,
                evaluation_plan=evaluation_plan,
            )
            analyst_run = ProductEvaluationAnalyst().analyze(
                analyst_input,
                provider=control_plane,
                binding=binding,
                forbidden_tokens={artifact.evidence.trial_ref for artifact in artifacts},
            )
            report = assemble_product_evaluation_report(
                analyst_input,
                analyst_run,
                supplementary_evidence=_supplementary_evidence(
                    service, args.project_name, args.benchmark_evidence
                ),
            )
            report_template = load_product_report_template(Path(args.report_template)) if args.report_template else None
            paths = write_product_evaluation_outputs(Path(args.output_dir), report, report_template)
            output = {
                **{f"{name}_path": str(path) for name, path in paths.items()},
                "evidence_manifest_sha256": report.evidence.artifact_manifest_hash,
                "report": report.model_dump(mode="json"),
            }
        elif args.command == "evolution" and args.subcommand == "intake":
            output = service.intake_agent_evolution(args.project_id, Path(args.source), args.baseline, args.candidate, repository_url=args.repository_url, declared_entrypoint=args.entrypoint).as_dict()
        elif args.command == "evolution" and args.subcommand == "propagate-stale":
            output = service.propagate_evolution_stale(args.project_id, args.changeset_id).model_dump()
        elif args.command == "evolution" and args.subcommand == "register":
            payload = _load_json_object(Path(args.input))
            payload.update({"project_id": args.project_id, "evolution_case_id": args.case_id})
            models = {
                "native-harness": (NativeHarnessContract, service.record_native_harness_contract),
                "environment": (RuntimeEnvironmentContract, service.record_runtime_environment_contract),
                "task-verifier": (TaskVerifierContract, service.record_task_verifier_contract),
                "preflight": (RuntimeEnvironmentPreflight, service.record_runtime_preflight),
                "replay-evidence": (HistoricalReplayEvidence, service.record_historical_replay_evidence),
            }
            model, recorder = models[args.kind]
            output = recorder(args.project_id, model.model_validate(payload)).model_dump()
        elif args.command == "evolution" and args.subcommand == "assess":
            output = service.assess_evolution_admission(args.project_id, args.case_id).as_dict()
        elif args.command == "evolution" and args.subcommand == "bind-provider":
            output = service.record_provider_binding(args.project_id, ProviderBinding.model_validate({"project_id": args.project_id, **_load_json_object(Path(args.input))})).model_dump()
        elif args.command == "evolution" and args.subcommand == "control-plane-smoke":
            binding = service.provider_binding(args.project_id, args.binding_id)
            output = service.run_evolution_control_plane_smoke(project_id=args.project_id, provider_binding_id=args.binding_id, objective=args.objective, evidence_ref=args.evidence_ref, evidence=_load_json_object(Path(args.evidence)), provider=_provider_from_binding(binding)).model_dump()
        elif args.command == "evolution" and args.subcommand == "compare":
            output = service.recompute_evolution_comparison(args.project_id, args.case_id).model_dump()
        elif args.command == "evolution" and args.subcommand == "build-manifest":
            output = service.build_evolution_report_manifest(args.project_id, args.case_id, args.run_id).model_dump()
        elif args.command == "evolution" and args.subcommand == "report-agent":
            binding = service.provider_binding(args.project_id, args.binding_id)
            api_key = _provider_api_key(binding)
            run, narrative = service.run_evolution_report_agent(project_id=args.project_id, report_manifest_id=args.manifest_id, provider_binding_id=args.binding_id, objective=args.objective, output_dir=Path(args.output_dir), provider_factory=lambda bounded: build_control_plane_client(bounded, api_key))
            output = {"run": run.model_dump(), "narrative": narrative.model_dump()}
        elif args.command == "evolution" and args.subcommand == "report-result":
            output = service.report_narrative(args.project_id, args.narrative_id).model_dump()
        elif args.command == "evolution" and args.subcommand == "verify-skill-ablation":
            contract = SkillContract.model_validate({"project_id": args.project_id, "evolution_case_id": args.case_id, **_load_json_object(Path(args.contract))})
            evidence = SkillAblationEvidence.model_validate({"project_id": args.project_id, "evolution_case_id": args.case_id, **_load_json_object(Path(args.evidence))})
            output = service.verify_skill_ablation(args.project_id, contract, evidence).model_dump()
        elif args.command == "evolution" and args.subcommand == "analyze-skill-ablation":
            binding = service.provider_binding(args.project_id, args.binding_id)
            output = service.run_skill_ablation_analysis(project_id=args.project_id, skill_contract_id=args.contract_id, skill_ablation_evidence_id=args.evidence_id, provider_binding_id=args.binding_id, objective=args.objective, provider=_provider_from_binding(binding)).model_dump()
        else:
            output = service.evolution_report(args.project_id, args.report_id).model_dump()
    except ProductNotFoundError:
        reason = "project not found" if args.command in {"project", "evaluation"} else "product not found"
        output = {"ok": False, "error": {"stage": "lookup", "reason": reason}}
        print(json.dumps(output, ensure_ascii=False) if args.format == "json" else output)
        return 2
    except (AssistantInputError, EvolutionIntakeError, ProviderRuntimeError, ValueError) as error:
        if args.command == "target":
            stage = "target_onboarding"
        elif args.command == "project":
            stage = "project_intelligence"
        elif args.command == "evaluation":
            stage = "evaluation_validation"
        elif args.command == "release":
            stage = "release_gate"
        else:
            stage = "control_plane"
        print(json.dumps({"ok": False, "error": {"stage": stage, "reason": str(error)}}, ensure_ascii=False) if args.format == "json" else str(error))
        return 3
    print(json.dumps({"ok": True, "data": output}, ensure_ascii=False) if args.format == "json" else {"ok": True, "data": output})
    return 0


def _provider_api_key(binding: ProviderBinding) -> str:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    api_key = os.getenv(binding.expected_environment_variable)
    if not api_key:
        raise ValueError(f"{binding.expected_environment_variable} is required at runtime")
    return api_key


if __name__ == "__main__":
    raise SystemExit(main())
