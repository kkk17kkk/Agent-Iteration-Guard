import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agentguard.evaluation_memory import EvaluationKnowledge
from agentguard.evaluation_planning import EvaluationChange, build_evolution_evaluation_plan
from agentguard.evaluation_request import EvaluationRequest, EvaluationRequestValidationError
from agentguard.evaluation_scenario_generator import (
    LLMEvaluationScenarioGenerator,
    ScenarioEvidenceRequirementsGenerator,
)
from agentguard.project_scanner import ProjectScanRequest, ProjectScanner
from agentguard.provider_runtime import ProviderToolCall, ProviderTurn
from agentguard.semantic_reporting import ProductDefinition
from agentguard.service import Service
from agentguard.skill_pair_evaluation import build_skill_pair_evaluation_change, build_skill_pair_evaluation_target
from agentguard.domain import ProviderBinding


def _declaration() -> dict[str, object]:
    return {
        "agent_manifest": {
            "agent_name": "LightTable local scanner fixture",
            "purpose": "Plan meals and apply a nutrition constraint.",
            "source_kind": "repository",
            "source_ref": "manual",
            "available_components": ["recipe_planning", "nutrition_check", "recipe_planning_nutrition_check"],
            "capability_descriptions": {
                "recipe_planning": "Create a usable meal plan.",
                "nutrition_check": "Check the plan against a nutrition constraint.",
                "recipe_planning_nutrition_check": "Use nutrition feedback to revise the plan.",
            },
        },
        "capabilities": [
            {
                "component_type": "skill",
                "name": "recipe_planning",
                "responsibility": "Create a usable meal plan.",
                "boundary": ["Does not check nutrition independently."],
            },
            {
                "component_type": "skill",
                "name": "nutrition_check",
                "responsibility": "Check the plan against a nutrition constraint.",
                "boundary": ["Does not create a plan independently."],
            },
            {
                "component_type": "skill_pair",
                "name": "recipe_planning_nutrition_check",
                "responsibility": "Use feedback to revise the plan.",
                "dependencies": ["recipe_planning", "nutrition_check"],
            },
        ],
        "runtime_profile": {
            "entrypoint": "python main.py",
            "runtime_kind": "native_command",
            "dependencies": ["requirements.lock"],
            "model_configuration": {"provider": "configured-at-runtime", "model": "configured-at-runtime"},
            "execution_requirements": ["isolated state per trial", "trace is required"],
            "source_ref": "manual",
        },
    }


def _source(root: Path, *, runtime_kind: str = "native_command") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("print('agent')\n", encoding="utf-8")
    (root / "requirements.lock").write_text("pydantic==2.10.0\n", encoding="utf-8")
    payload = _declaration()
    payload["runtime_profile"]["runtime_kind"] = runtime_kind
    (root / "project-registration.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def _request(version: str = "candidate-v2") -> EvaluationRequest:
    return EvaluationRequest(
        request_id="scan-evaluation-request",
        project_id="lighttable-scan",
        component_type="skill_pair",
        component_name="recipe_planning_nutrition_check",
        change_type="modify",
        candidate_version=version,
        baseline_version="baseline-v1",
    )


class PairProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, messages, tools):
        name = tools[0]["function"]["name"]
        self.calls.append(name)
        if name == "submit_pair_relationship":
            arguments = {
                "relationship": "complementary",
                "rationale": "The two declarations address different steps of the same meal task.",
                "signals": ["shared user job", "distinct responsibilities"],
            }
        else:
            arguments = {
                "scenarios": [
                    {
                        "scenario_id": f"pair-{index}",
                        "category": category,
                        "user_prompt": f"Prepare a realistic meal task {index}.",
                        "evaluation_goal": f"Observe {category} behavior.",
                        "expected_behavior": {
                            "skill_a_only": "The planner handles its own task.",
                            "skill_b_only": "The checker handles its own task.",
                            "combined": "The combined flow serves the same user job.",
                        },
                        "evidence_to_collect": ["activation, handoff, output, and cost"],
                    }
                    for index, category in enumerate(("complementary", "synergy", "conflict", "boundary"), 1)
                ]
            }
        return ProviderTurn(
            request_id="local-control-plane-turn",
            finish_reason="tool_calls",
            tool_calls=(ProviderToolCall("call-1", name, arguments),),
            input_tokens=100,
            output_tokens=100,
            cache_hit_tokens=0,
            request_fingerprint="request-fingerprint",
            response_fingerprint="response-fingerprint",
        )


def _binding() -> ProviderBinding:
    return ProviderBinding(
        project_id="lighttable-scan",
        role="control_plane",
        provider="deepseek",
        base_url="http://127.0.0.1:8000/v1",
        model="test-control-plane",
        expected_environment_variable="TEST_KEY",
        credential_source_ref="test",
        batch_budget_usd=0.1,
        timeout_seconds=10,
        allowed_hosts=["127.0.0.1"],
        data_retention_policy="test",
    )


def test_repository_scanner_registers_snapshot_and_runtime_preflight(tmp_path: Path) -> None:
    source = _source(tmp_path / "agent")
    service = Service(str(tmp_path / "agentguard.db"))

    first = service.scan_project(ProjectScanRequest(
        project_id="lighttable-scan", source_kind="repository", source_ref=str(source), version="baseline-v1"
    ))
    second = service.scan_project(ProjectScanRequest(
        project_id="lighttable-scan", source_kind="repository", source_ref=str(source), version="candidate-v2"
    ))

    assert first.scan.status == "ready"
    assert second.scan.registered_snapshot_id
    intelligence = service.project_intelligence("lighttable-scan")
    assert intelligence is not None
    assert [item.version for item in intelligence.snapshot_history] == ["baseline-v1", "candidate-v2"]
    assert service.runtime_preflight("lighttable-scan", "candidate-v2", source_root=source).status == "passed"
    comparison = service.runtime_comparability("lighttable-scan", "baseline-v1", "candidate-v2")
    assert comparison.status == "comparable"

    created = service.create_evaluation_request(_request(), candidate_available=True)
    assert created.runtime_comparability is not None
    assert created.runtime_comparability.status == "comparable"


def test_api_scan_and_runtime_preflight_use_generic_scanner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path / "api-agent")
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    from agentguard.api import app

    client = TestClient(app)
    scan = client.post(
        "/api/v1/projects/api-scan/scan",
        json={
            "source_kind": "repository",
            "source_ref": str(source),
            "version": "v1",
        },
    )
    assert scan.status_code == 200
    assert scan.json()["scan"]["status"] == "ready"

    preflight = client.get(
        "/api/v1/projects/api-scan/runtime-preflight",
        params={"version": "v1", "source_root": str(source)},
    )
    assert preflight.status_code == 200
    assert preflight.json()["status"] == "passed"


def test_scanner_returns_unresolved_without_semantic_component_evidence(tmp_path: Path) -> None:
    source = tmp_path / "opaque-agent"
    source.mkdir()
    (source / "main.py").write_text("print('unknown')\n", encoding="utf-8")

    result = ProjectScanner().scan(ProjectScanRequest(
        project_id="opaque", source_kind="repository", source_ref=str(source), version="v1"
    ))

    assert result.scan.status == "unresolved"
    assert result.registration is None
    assert result.scan.unresolved_reasons


def test_package_archive_scanner_reads_project_neutral_declaration(tmp_path: Path) -> None:
    source = _source(tmp_path / "package-source")
    archive = tmp_path / "agent.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in source.rglob("*"):
            if path.is_file():
                handle.write(path, Path("agent") / path.relative_to(source))

    result = ProjectScanner().scan(ProjectScanRequest(
        project_id="package-agent", source_kind="package", source_ref=str(archive), version="v1"
    ))

    assert result.scan.status == "ready"
    assert result.registration is not None
    assert result.registration.runtime_profile.source_kind == "package"


def test_dockerfile_scan_is_ready_but_preflight_exposes_missing_image_digest(tmp_path: Path) -> None:
    source = _source(tmp_path / "docker-agent")
    (source / "Dockerfile").write_text("FROM python:3.11\nCMD [\"python\", \"main.py\"]\n", encoding="utf-8")
    result = ProjectScanner().scan(ProjectScanRequest(
        project_id="docker-agent", source_kind="docker_image", source_ref=str(source), version="v1"
    ))

    assert result.scan.status == "ready"
    assert result.registration is not None
    assert result.registration.runtime_profile.runtime_kind == "docker"
    assert result.registration.runtime_profile.image_digest is None


def test_runtime_change_blocks_evaluation_creation(tmp_path: Path) -> None:
    service = Service(str(tmp_path / "agentguard.db"))
    _source(tmp_path / "baseline")
    _source(tmp_path / "candidate", runtime_kind="package")
    service.scan_project(ProjectScanRequest(
        project_id="lighttable-scan", source_kind="repository", source_ref=str(tmp_path / "baseline"), version="baseline-v1"
    ))
    service.scan_project(ProjectScanRequest(
        project_id="lighttable-scan", source_kind="repository", source_ref=str(tmp_path / "candidate"), version="candidate-v2"
    ))

    with pytest.raises(EvaluationRequestValidationError) as error:
        service.create_evaluation_request(_request(), candidate_available=True)
    assert error.value.code == "E_RUNTIME_NOT_COMPARABLE"


def test_lighttable_knowledge_hit_flows_through_planner_to_scenario_generator(tmp_path: Path) -> None:
    source = _source(tmp_path / "lighttable")
    service = Service(str(tmp_path / "chain.db"))
    service.scan_project(ProjectScanRequest(
        project_id="lighttable-scan", source_kind="repository", source_ref=str(source), version="baseline-v1"
    ))
    knowledge = service.record_evaluation_knowledge(EvaluationKnowledge(
        project_id="lighttable-scan",
        component_pattern="skill_pair",
        common_risks=["constraint violation"],
        recommended_dimensions=["coordination", "conflict"],
        scenario_templates=["preference conflict", "resource constraint"],
        source_evaluation_ids=["lighttable-acceptance-1"],
        evidence_refs=["evidence:lighttable-acceptance-1"],
    ))
    intelligence = service.project_intelligence("lighttable-scan")
    assert intelligence is not None
    product = ProductDefinition(
        component_type="skill_pair",
        component_name="recipe_planning_nutrition_check",
        description="Use nutrition feedback to revise a meal plan.",
        product_responsibility="Produce a usable meal plan under nutrition constraints.",
        user_job="Choose a practical meal.",
        expected_behavior=["The final plan respects the declared constraint."],
        boundary=["Do not make medical claims."],
    )
    target = build_skill_pair_evaluation_target(intelligence, product.component_name, product).model_copy(update={
        "component_pattern": "skill_pair",
        "evaluation_knowledge": service.evaluation_knowledge_for_target(
            "lighttable-scan", component_pattern="skill_pair", component_type="skill_pair"
        ),
    })
    change = build_skill_pair_evaluation_change(_request(), evaluation_name="LightTable scanner chain")
    provider = PairProvider()
    plan = build_evolution_evaluation_plan(
        target,
        change,
        scenario_generator=LLMEvaluationScenarioGenerator(provider, _binding()),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )

    assert [item.knowledge_id for item in plan.evaluation_knowledge] == [knowledge.knowledge_id]
    assert provider.calls == ["submit_pair_relationship", "submit_pair_evaluation_scenarios"]
    assert [item.category for item in plan.scenarios] == ["complementary", "synergy", "conflict", "boundary"]
    assert all(item.scenario_provenance and item.scenario_provenance.frozen for item in plan.scenarios)
