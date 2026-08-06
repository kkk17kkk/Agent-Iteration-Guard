import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agentguard.api import app
from agentguard.cli import main
from agentguard.evaluation_request import (
    EvaluationRequest,
    EvaluationRequestValidationError,
    validate_evaluation_request,
    validate_skill_artifacts,
)
from agentguard.project_intelligence import (
    AgentManifest,
    CapabilityRecord,
    ProjectIntelligenceRegistration,
    ProjectIntelligenceRepository,
    RuntimeProfile,
)
from agentguard.service import Service
from agentguard.store import Store


def _registration() -> ProjectIntelligenceRegistration:
    project_id = "generic-agent"
    names = ["task_planning", "result_delivery"]
    return ProjectIntelligenceRegistration(
        project_id=project_id,
        agent_manifest=AgentManifest(
            project_id=project_id,
            agent_name="Generic Agent",
            purpose="Complete bounded user tasks.",
            source_kind="repository",
            source_ref="repo:generic@baseline",
            available_components=names,
            capability_descriptions={name: f"Capability {name}" for name in names},
        ),
        capabilities=[
            CapabilityRecord(
                project_id=project_id,
                component_type="skill",
                name="task_planning",
                responsibility="Plan the user task.",
            ),
            CapabilityRecord(
                project_id=project_id,
                component_type="skill",
                name="result_delivery",
                responsibility="Deliver the result.",
            ),
        ],
        runtime_profile=RuntimeProfile(
            project_id=project_id,
            entrypoint="python -m generic_agent",
            runtime_kind="native_command",
            execution_requirements=["isolated working directory", "reset before each trial"],
            source_ref="repo:generic@baseline",
        ),
        baseline_version="git:baseline",
    )


def _request(**updates: object) -> EvaluationRequest:
    payload = {
        "request_id": "evaluation_request_test",
        "project_id": "generic-agent",
        "component_type": "skill",
        "component_name": "task_planning",
        "change_type": "remove",
        "candidate_version": "git:candidate",
        "baseline_version": "git:baseline",
    }
    return EvaluationRequest.model_validate({**payload, **updates})


def test_evaluation_request_validates_against_project_intelligence(tmp_path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "agentguard.db")))
    intelligence = repository.register(_registration())

    validated = validate_evaluation_request(
        _request(), intelligence, candidate_available=True, candidate_component_name="task_planning"
    )

    assert validated.status == "validated"
    assert validated.baseline_version == "git:baseline"


def test_evaluation_request_uses_snapshot_history_for_component_presence(tmp_path) -> None:
    repository = ProjectIntelligenceRepository(Store(str(tmp_path / "versioned.db")))
    repository.register(_registration())
    candidate = _registration().model_copy(update={
        "snapshot_version": "git:candidate",
        "agent_manifest": _registration().agent_manifest.model_copy(update={
            "available_components": ["result_delivery"],
            "capability_descriptions": {"result_delivery": "Deliver the result."},
        }),
        "capabilities": [
            item for item in _registration().capabilities if item.name == "result_delivery"
        ],
    })
    repository.register_snapshot(candidate)
    intelligence = repository.get("generic-agent")
    assert intelligence is not None

    validated = validate_evaluation_request(
        _request(candidate_version="git:candidate"),
        intelligence,
        candidate_available=True,
    )
    assert validated.status == "validated"

    with pytest.raises(EvaluationRequestValidationError) as error:
        validate_evaluation_request(
            _request(change_type="modify", candidate_version="git:candidate"),
            intelligence,
            candidate_available=True,
        )
    assert error.value.code == "E_VERSION_NOT_COMPARABLE"


@pytest.mark.parametrize(
    ("updates", "candidate_available", "code"),
    [
        ({"component_name": "missing_skill"}, True, "E_COMPONENT_NOT_FOUND"),
        ({"baseline_version": "git:old"}, True, "E_BASELINE_NOT_FOUND"),
        ({}, False, "E_CANDIDATE_NOT_FOUND"),
        ({}, True, "E_CANDIDATE_COMPONENT_MISMATCH"),
    ],
)
def test_evaluation_request_rejects_invalid_creation_inputs(tmp_path, updates, candidate_available, code) -> None:
    intelligence = ProjectIntelligenceRepository(Store(str(tmp_path / f"{code}.db"))).register(_registration())

    with pytest.raises(EvaluationRequestValidationError) as error:
        validate_evaluation_request(
            _request(**updates),
            intelligence,
            candidate_available=candidate_available,
            candidate_component_name=("other_skill" if code == "E_CANDIDATE_COMPONENT_MISMATCH" else "task_planning"),
        )

    assert error.value.code == code


def test_skill_request_requires_the_complete_ablation_matrix() -> None:
    def artifact(intervention: str):
        return SimpleNamespace(
            project_name="generic-agent",
            contract=SimpleNamespace(skill_name="task_planning"),
            evidence=SimpleNamespace(intervention=intervention),
        )

    with pytest.raises(EvaluationRequestValidationError, match="E_SKILL_MATRIX_INCOMPLETE"):
        validate_skill_artifacts(_request(), [artifact("enabled"), artifact("disabled")])

    validate_skill_artifacts(
        _request(), [artifact("enabled"), artifact("disabled"), artifact("replacement")]
    )


def test_service_persists_only_validated_evaluation_requests(tmp_path) -> None:
    service = Service(str(tmp_path / "agentguard.db"))
    service.register_project_intelligence(_registration())

    created = service.create_evaluation_request(
        _request(), candidate_available=True, candidate_component_name="task_planning"
    )
    loaded = service.evaluation_request("generic-agent", created.request_id)

    assert loaded == created
    assert loaded is not None and loaded.status == "validated"


def test_cli_reports_missing_component_during_evaluation_creation(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(_request(component_name="missing_skill").model_dump(mode="json")),
        encoding="utf-8",
    )
    service = Service(str(tmp_path / "agentguard.db"))
    service.register_project_intelligence(_registration())

    code = main([
        "--db", str(tmp_path / "agentguard.db"), "--format", "json", "evaluation", "create",
        "--input", str(request_path), "--candidate-available",
    ])
    output = json.loads(capsys.readouterr().out)

    assert code == 3
    assert output["error"]["stage"] == "evaluation_validation"
    assert "E_COMPONENT_NOT_FOUND" in output["error"]["reason"]


def test_api_creates_and_rejects_evaluation_requests_at_the_creation_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENTGUARD_DB", str(tmp_path / "api.db"))
    client = TestClient(app)
    registration = _registration().model_dump(mode="json")

    assert client.post(
        "/api/v1/projects/generic-agent/intelligence", json={
            "agent_manifest": registration["agent_manifest"],
            "capabilities": registration["capabilities"],
            "runtime_profile": registration["runtime_profile"],
            "baseline_version": registration["baseline_version"],
        }
    ).status_code == 200

    created = client.post(
        "/api/v1/projects/generic-agent/evaluations",
        json={
            "component_type": "skill",
            "component_name": "task_planning",
            "change_type": "remove",
            "candidate_version": "git:candidate",
            "baseline_version": "git:baseline",
            "candidate_available": True,
        },
    )
    assert created.status_code == 200
    request_id = created.json()["request_id"]
    assert client.get(f"/api/v1/projects/generic-agent/evaluations/{request_id}").status_code == 200

    rejected = client.post(
        "/api/v1/projects/generic-agent/evaluations",
        json={
            "component_type": "skill",
            "component_name": "missing_skill",
            "change_type": "remove",
            "candidate_version": "git:candidate",
            "baseline_version": "git:baseline",
            "candidate_available": True,
        },
    )
    assert rejected.status_code == 422
    assert "E_COMPONENT_NOT_FOUND" in rejected.json()["detail"]
