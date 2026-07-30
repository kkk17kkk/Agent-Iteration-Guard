import argparse
import json
import os
from pathlib import Path

from .llm import LLMProviderError
from .service import AssistantInputError, ProductNotFoundError, Service
from .stage1 import Stage1HarnessBatch, assert_stage2_launch_allowed
from .stage1_acceptance import run_stage1_fault_injection_matrix, run_stage1_replay_ablation_corpus
from .stage1_reporting import (
    build_stage1_artifacts,
    corpus_root_for_artifacts,
    gate_stage1_report,
    report_stage1_artifacts,
)
from .stage2 import HttpJsonActionModel, Stage2InjectedCrash


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentguard")
    parser.add_argument("--db", default=os.getenv("AGENTGUARD_DB", "data/agentguard.db"))
    parser.add_argument("--format", choices=["text", "json"], default="text")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")

    product = commands.add_parser("product").add_subparsers(dest="subcommand", required=True)
    add = product.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--description", default="")
    product.add_parser("list")
    get = product.add_parser("get")
    get.add_argument("product_id")

    fixture = commands.add_parser("fixture").add_subparsers(dest="subcommand", required=True)
    fixture.add_parser("load").add_argument("name", choices=["minimal", "file-agent", "file-management-agent"])

    version = commands.add_parser("version").add_subparsers(dest="subcommand", required=True)
    import_version = version.add_parser("import")
    import_version.add_argument("--product-id", required=True)
    import_version.add_argument("--source", required=True)
    import_version.add_argument("--label", required=True)

    run = commands.add_parser("run").add_subparsers(dest="subcommand", required=True)
    start = run.add_parser("start")
    start.add_argument("--product-id", required=True)
    start.add_argument("--baseline", required=True)
    start.add_argument("--candidate", required=True)
    start_file_management = run.add_parser("start-file-management")
    start_file_management.add_argument("--product-id", required=True)
    start_file_management.add_argument("--baseline", required=True)
    start_file_management.add_argument("--candidate", required=True)
    resume = run.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    evaluate = run.add_parser("evaluate")
    evaluate.add_argument("--product-id", required=True)
    evaluate.add_argument("--baseline", required=True)
    evaluate.add_argument("--candidate", required=True)
    evaluate.add_argument("--cleanup-attempts", default="false,false,true")
    evaluate_external = run.add_parser("evaluate-external")
    evaluate_external.add_argument("--product-id", required=True)
    evaluate_external.add_argument("--baseline", required=True)
    evaluate_external.add_argument("--candidate", required=True)
    evaluate_external.add_argument("--trials", type=int, default=3)
    evaluate_external.add_argument("--max-total-cost-usd", type=float, default=0.05)
    replay = run.add_parser("replay")
    replay.add_argument("--run-id", required=True)
    replay.add_argument("--source-trial-result-id", required=True)
    ablate = run.add_parser("ablate-cleanup")
    ablate.add_argument("--run-id", required=True)
    ablate.add_argument("--source-trial-result-id", required=True)

    benchmark = commands.add_parser("benchmark").add_subparsers(dest="subcommand", required=True)
    create_batch = benchmark.add_parser("create-file-management")
    create_batch.add_argument("--workers", type=int, default=2)
    create_batch.add_argument("--trials", type=int, default=3)
    create_batch.add_argument("--max-total-cost-usd", type=float, default=0.0)
    create_batch.add_argument("--product-id")
    run_batch = benchmark.add_parser("run")
    run_batch.add_argument("--batch-id", required=True)
    stage1 = benchmark.add_parser("stage1")
    stage1_actions = stage1.add_subparsers(dest="stage1_action")
    build_stage1 = stage1_actions.add_parser("build")
    build_stage1.add_argument("--artifacts-root", default="artifacts/stage_1")
    run_stage1 = stage1_actions.add_parser("run")
    run_stage1.add_argument("--artifacts-root", default="artifacts/stage_1")
    report_stage1 = stage1_actions.add_parser("report")
    report_stage1.add_argument("--batch-id")
    report_stage1.add_argument("--artifacts-root", default="artifacts/stage_1")
    gate_stage1 = stage1_actions.add_parser("gate")
    gate_stage1.add_argument("--batch-id")
    gate_stage1.add_argument("--artifacts-root", default="artifacts/stage_1")
    accept_stage1 = stage1_actions.add_parser("accept")
    accept_stage1.add_argument("--batch-id", required=True)
    accept_stage1.add_argument("--artifacts-root", default="artifacts/stage_1")

    stage2 = commands.add_parser("stage2").add_subparsers(dest="subcommand", required=True)
    stage2_start = stage2.add_parser("start")
    stage2_start.add_argument("--batch-id", required=True)
    stage2_start.add_argument("--task", choices=["update_title", "read_only", "append_note", "cleanup", "cleanup_allowed", "missing_file", "nearby_file", "prompt_injection"], default="update_title")
    stage2_start.add_argument("--model", choices=["deterministic", "fake", "http_json", "real_llm"], default="deterministic")
    stage2_start.add_argument("--max-steps", type=int, default=8)
    stage2_run = stage2.add_parser("run")
    stage2_run.add_argument("--batch-id", required=True)
    stage2_run.add_argument("--task", choices=["update_title", "read_only", "append_note", "cleanup", "cleanup_allowed", "missing_file", "nearby_file", "prompt_injection"], default="update_title")
    stage2_run.add_argument("--model", choices=["deterministic", "fake", "http_json", "real_llm"], default="deterministic")
    stage2_run.add_argument("--max-steps", type=int, default=8)
    stage2_resume = stage2.add_parser("resume")
    stage2_resume.add_argument("--run-id", required=True)
    stage2_report = stage2.add_parser("report")
    stage2_report.add_argument("--run-id", required=True)
    stage2_report.add_argument("--artifacts-root", default="artifacts/stage_2")
    stage2_gate = stage2.add_parser("gate")
    stage2_gate.add_argument("--batch-id", required=True)
    stage2_gate.add_argument("--artifacts-root", default="artifacts/stage_2")

    assistant = commands.add_parser("assistant").add_subparsers(dest="subcommand", required=True)
    explain = assistant.add_parser("explain")
    explain.add_argument("--run-id", required=True)
    mapping = assistant.add_parser("map")
    mapping.add_argument("--product-id", required=True)
    mapping.add_argument("--requirement-id", required=True)
    mapping.add_argument("--changeset-id", required=True)

    report = commands.add_parser("report").add_subparsers(dest="subcommand", required=True)
    prepare = report.add_parser("prepare")
    prepare.add_argument("--product-id", required=True)
    return parser


def serialize_prepared_run(service: Service, product_id: str) -> dict[str, object]:
    return service.prepare_harness_run(product_id).as_dict()


def parse_cleanup_attempts(value: str) -> list[bool]:
    attempts = []
    for item in value.split(","):
        normalized = item.strip().lower()
        if normalized not in {"true", "false"}:
            raise ValueError("cleanup attempts must be comma-separated true or false values")
        attempts.append(normalized == "true")
    return attempts


def resolve_stage1_batch_id(service: Service, artifacts_root: Path, batch_id: str | None) -> str:
    if batch_id:
        return batch_id
    report_path = artifacts_root / "metrics" / "stage1_report.json"
    if report_path.is_file():
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if payload.get("batch_id"):
            return str(payload["batch_id"])
    batches = service.store.list("stage1_harness_batch", Stage1HarnessBatch, "stage1")
    if batches:
        return batches[-1].batch_id
    raise ValueError("stage1 report/gate requires --batch-id when no completed harness batch exists")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = Service(args.db)
    try:
        if args.command == "init":
            output = {"db": args.db}
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
        elif args.command == "fixture":
            if args.name == "file-agent":
                output = {"fixture": service.file_agent_fixture().as_dict()}
            elif args.name == "file-management-agent":
                output = {"fixture": service.file_management_fixture().as_dict()}
            else:
                output = {"product": service.fixture().model_dump()}
        elif args.command == "version":
            version = service.import_version(args.product_id, Path(args.source), args.label)
            output = {"version": version.model_dump()}
        elif args.command == "run" and args.subcommand == "start":
            output = service.run_file_agent(args.product_id, args.baseline, args.candidate).as_dict()
        elif args.command == "run" and args.subcommand == "start-file-management":
            output = service.start_file_management_run(args.product_id, args.baseline, args.candidate).as_dict()
        elif args.command == "run" and args.subcommand == "evaluate":
            output = service.evaluate_file_management_trials(
                args.product_id,
                args.baseline,
                args.candidate,
                parse_cleanup_attempts(args.cleanup_attempts),
            ).as_dict()
        elif args.command == "run" and args.subcommand == "evaluate-external":
            output = service.evaluate_file_management_external_trials(
                args.product_id,
                args.baseline,
                args.candidate,
                trial_count=args.trials,
                max_total_cost_usd=args.max_total_cost_usd,
            ).as_dict()
        elif args.command == "run" and args.subcommand == "replay":
            output = service.replay_file_management_trial(args.run_id, args.source_trial_result_id).as_dict()
        elif args.command == "run" and args.subcommand == "ablate-cleanup":
            output = service.ablate_file_management_cleanup(args.run_id, args.source_trial_result_id).as_dict()
        elif args.command == "run":
            output = service.resume_file_management_run(args.run_id).as_dict()
        elif args.command == "benchmark" and args.subcommand == "create-file-management":
            output = service.create_file_management_mutation_batch(
                max_workers=args.workers,
                trials_per_pair=args.trials,
                max_total_cost_usd=args.max_total_cost_usd,
                product_id=args.product_id,
            ).as_dict()
        elif args.command == "benchmark" and args.subcommand == "stage1":
            if args.stage1_action == "build":
                output = build_stage1_artifacts(Path(args.artifacts_root)).model_dump()
            elif args.stage1_action == "run":
                root = Path(args.artifacts_root)
                corpus_root = corpus_root_for_artifacts(root)
                output = service.run_stage1_harness_corpus(corpus_root=corpus_root, artifacts_root=root).model_dump()
            elif args.stage1_action == "report":
                root = Path(args.artifacts_root)
                batch_id = resolve_stage1_batch_id(service, root, args.batch_id)
                output = report_stage1_artifacts(service.store, batch_id, root).model_dump()
            elif args.stage1_action == "gate":
                root = Path(args.artifacts_root)
                batch_id = resolve_stage1_batch_id(service, root, args.batch_id)
                output = gate_stage1_report(service.store, batch_id, root).model_dump()
            elif args.stage1_action == "accept":
                root = Path(args.artifacts_root)
                run_stage1_replay_ablation_corpus(service, root)
                run_stage1_fault_injection_matrix(service, root)
                report_stage1_artifacts(service.store, args.batch_id, root)
                output = gate_stage1_report(service.store, args.batch_id, root).model_dump()
            else:
                output = service.run_stage1_benchmark().model_dump()
        elif args.command == "stage2" and args.subcommand in {"start", "run"}:
            action_model = None
            if args.model == "http_json":
                endpoint = os.getenv("AGENTGUARD_STAGE2_MODEL_URL")
                if not endpoint:
                    raise ValueError("AGENTGUARD_STAGE2_MODEL_URL is required for http_json")
                action_model = HttpJsonActionModel(endpoint)
            if args.model == "real_llm" and not os.getenv("DEEPSEEK_API_KEY"):
                raise ValueError("DEEPSEEK_API_KEY is required for real_llm")
            output = service.start_stage2_file_agent(
                args.batch_id,
                task_kind=args.task,
                model_kind=args.model,
                action_model=action_model,
                max_steps=args.max_steps,
            ).model_dump()
        elif args.command == "stage2" and args.subcommand == "resume":
            output = service.resume_stage2_file_agent(args.run_id).model_dump()
        elif args.command == "stage2" and args.subcommand == "report":
            output = service.report_stage2_file_agent(args.run_id, Path(args.artifacts_root))
        elif args.command == "stage2" and args.subcommand == "gate":
            output = service.gate_stage2_file_agent(args.batch_id, Path(args.artifacts_root)).model_dump()
        elif args.command == "benchmark":
            output = service.run_file_management_mutation_batch(args.batch_id).as_dict()
        elif args.command == "assistant" and args.subcommand == "explain":
            output = {"assistance": service.explain_failure(args.run_id).model_dump()}
        elif args.command == "assistant":
            output = {
                "assistance": service.suggest_requirement_mapping(
                    args.product_id, args.requirement_id, args.changeset_id
                ).model_dump()
            }
        else:
            output = serialize_prepared_run(service, args.product_id)
    except ProductNotFoundError:
        output = {
            "ok": False,
            "error": {
                "stage": "lookup",
                "reason": "product not found",
                "next_step": "create a product or load the fixture",
            },
        }
        print(json.dumps(output) if args.format == "json" else output)
        return 2
    except (AssistantInputError, LLMProviderError, ValueError, Stage2InjectedCrash) as error:
        output = {"ok": False, "error": {"stage": "llm_assistant", "reason": str(error)}}
        print(json.dumps(output, ensure_ascii=False) if args.format == "json" else output)
        return 3

    response = {"ok": True, "data": output}
    print(json.dumps(response, ensure_ascii=False) if args.format == "json" else response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
