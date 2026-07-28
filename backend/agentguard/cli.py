import argparse
import json
import os
from pathlib import Path

from .llm import LLMProviderError
from .service import AssistantInputError, ProductNotFoundError, Service


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
        elif args.command == "run":
            output = service.resume_file_management_run(args.run_id).as_dict()
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
    except (AssistantInputError, LLMProviderError) as error:
        output = {"ok": False, "error": {"stage": "llm_assistant", "reason": str(error)}}
        print(json.dumps(output, ensure_ascii=False) if args.format == "json" else output)
        return 3

    response = {"ok": True, "data": output}
    print(json.dumps(response, ensure_ascii=False) if args.format == "json" else response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
