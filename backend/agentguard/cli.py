import argparse
import json
import os
from pathlib import Path

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
from .product_reporting import (
    build_product_evaluation_evidence,
    generate_product_report_analysis,
    load_skill_ablation_artifact,
    write_product_evaluation_report,
)
from .provider_runtime import ProviderRuntimeError, build_control_plane_client
from .service import AssistantInputError, ProductNotFoundError, Service
from .target_onboarding import TargetEnvironmentCache, initialize_target_manifest, inspect_target_manifest, target_golden_path
from .target_runtime import TargetRuntimeAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentguard")
    parser.add_argument("--db", default=os.getenv("AGENTGUARD_DB", "data/agentguard.db"))
    parser.add_argument("--format", choices=["text", "json"], default="text")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

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
    product_report.add_argument("--artifact-dir", action="append", required=True, help="Persisted Skill-ablation artifact directory; repeatable.")
    product_report.add_argument("--binding", required=True, help="Path to a non-secret control_plane ProviderBinding JSON.")
    product_report.add_argument("--output-dir", required=True)
    product_report.add_argument("--evaluation-name", default="Skill Ablation")

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


def _provider_from_binding(binding: ProviderBinding):
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parents[2] / ".env", override=True)
    api_key = os.getenv(binding.expected_environment_variable)
    if not api_key:
        raise ValueError(f"{binding.expected_environment_variable} is required at runtime")
    return build_control_plane_client(binding, api_key)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = Service(args.db)
    try:
        if args.command == "init":
            output = {"db": args.db}
        elif args.command == "target" and args.subcommand == "init":
            output = initialize_target_manifest(
                source=Path(args.source), output=Path(args.output), target_id=args.target_id, kind=args.kind,
                application=args.application, readiness_path=args.readiness_path, command=args.command_part,
                required_source_files=args.required_file, dependency_lock=args.dependency_lock, python_executable=args.python,
                runtime_requirements=[json.loads(item) for item in args.runtime_requirement or []],
                sut_provider=_load_json_object(Path(args.sut_provider)) if args.sut_provider else None,
                trace=_load_json_object(Path(args.trace)) if args.trace else None,
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
        elif args.command == "report":
            binding = ProviderBinding.model_validate({**_load_json_object(Path(args.binding)), "project_id": args.project_name})
            if binding.role != "control_plane":
                raise ValueError("Product report requires a control_plane ProviderBinding")
            artifacts = [load_skill_ablation_artifact(args.project_name, Path(path)) for path in args.artifact_dir]
            evidence = build_product_evaluation_evidence(args.project_name, artifacts, evaluation_name=args.evaluation_name)
            report = generate_product_report_analysis(evidence, binding=binding, api_key=_provider_api_key(binding))
            evidence_path, report_path, html_path = write_product_evaluation_report(Path(args.output_dir), evidence, report)
            output = {"evidence_path": str(evidence_path), "report_path": str(report_path), "html_path": str(html_path), "evidence_manifest_sha256": evidence["evidence_manifest_sha256"]}
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
        output = {"ok": False, "error": {"stage": "lookup", "reason": "product not found"}}
        print(json.dumps(output, ensure_ascii=False) if args.format == "json" else output)
        return 2
    except (AssistantInputError, EvolutionIntakeError, ProviderRuntimeError, ValueError) as error:
        stage = "target_onboarding" if args.command == "target" else "control_plane"
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
