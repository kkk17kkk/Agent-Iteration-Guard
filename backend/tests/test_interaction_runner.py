import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentguard.domain import ProviderBinding
from agentguard.evaluation_planning import (
    EvaluationChange,
    EvaluationScenario,
    EvaluationTarget,
    PairScenarioExpectedBehavior,
    ScenarioProvenance,
    build_evolution_evaluation_plan,
    scenario_hash_for,
)
from agentguard.evaluation_scenario_generator import ScenarioEvidenceRequirementsGenerator
from agentguard.evaluation_suite import ScenarioSuiteConfig
from agentguard.interaction_evaluation import (
    InteractionHypothesisSource,
    InteractionRelationshipProfile,
    PlanningCallMetadata,
)
from agentguard.interaction_matrix import PAIR_INTERACTION_CONDITIONS, execute_evaluation_matrix
from agentguard.interaction_runner import (
    IndependentOracleResult,
    InteractionRunnerError,
    ManifestInteractionTrialRunner,
    SubprocessInteractionOracle,
)
from agentguard.scenario_contracts import (
    FixtureCatalog,
    FixtureDescriptor,
    ScenarioInputContract,
    ScenarioInputRequirement,
    ScenarioTraceContract,
    check_evaluation_plan_readiness,
)
from agentguard.target_onboarding import TargetEnvironmentCache, initialize_target_manifest
from agentguard.target_runtime import TargetRuntimeAdapter


def _run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _target_and_oracle(tmp_path: Path, *, oracle_outcome: str = "passed") -> tuple[TargetRuntimeAdapter, Path, Path]:
    target_source = tmp_path / "target"
    target_source.mkdir()
    _run(["git", "init"], target_source)
    _run(["git", "config", "user.email", "test@example.invalid"], target_source)
    _run(["git", "config", "user.name", "AgentGuard Test"], target_source)
    (target_source / "run_interaction.py").write_text(
        """
import json
import os
import sys
from pathlib import Path

request = json.load(sys.stdin)
trace_path = os.environ["TRACE_PATH"]
Path(trace_path).write_text(json.dumps({"event_type": "skill_output", "condition_kind": request["condition_kind"]}) + "\\n", encoding="utf-8")
print(json.dumps({
    "schema_version": "aig.interaction-observation.v1",
    "output": {"condition": request["condition_kind"], "prompt_seen": bool(request["user_prompt"])},
    "trace": [],
    "observations": {"target_completed": True},
    "metrics": {"latency_ms": 1, "cost_usd": 0.002},
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "output_artifact_ref": "sha256:declared-output"
}))
""",
        encoding="utf-8",
    )
    _run(["git", "add", "run_interaction.py"], target_source)
    _run(["git", "commit", "-m", "interaction target"], target_source)

    manifest_path = tmp_path / "target.json"
    manifest = initialize_target_manifest(
        source=target_source,
        output=manifest_path,
        target_id="portable-interaction-target",
        kind="native_command",
        command=["{python}", "run_interaction.py"],
        required_source_files=["run_interaction.py"],
        sut_provider={
            "api_key_variable": "TARGET_API_KEY",
            "model_variable": "TARGET_MODEL",
            "base_url_variable": "TARGET_BASE_URL",
        },
        trace={
            "trace_path_variable": "TRACE_PATH",
            "required_event_types": ["skill_output"],
            "provider_event_types": [],
            "requires_provider_usage": False,
        },
        interaction={
            "command": ["{python}", "run_interaction.py"],
            "timeout_seconds": 20,
            "required_exit_code": 0,
        },
    )
    cache_root = tmp_path / "cache"
    TargetEnvironmentCache(cache_root).import_environment(manifest_path, Path(sys.executable))
    target = TargetRuntimeAdapter(manifest_path, cache_root)

    oracle_source = tmp_path / "oracle"
    oracle_source.mkdir()
    (oracle_source / "verify.py").write_text(
        f"""
import json
import sys

payload = json.load(sys.stdin)
assert payload["observation"]["output"]["prompt_seen"] is True
print(json.dumps({{
    "verifier_id": "independent-test-oracle",
    "oracle_type": "rule_based",
    "oracle_version": "1.0",
    "validation_input": {{"scenario_id": payload["request"]["scenario_id"], "condition": payload["request"]["condition_kind"]}},
    "status": "verified",
    "outcome": {oracle_outcome!r},
    "assertions": [{{"name": "prompt_seen", "status": "{'passed' if oracle_outcome == 'passed' else 'failed'}", "detail": "checked outside the target process"}}],
    "evidence_refs": ["oracle-assertion:prompt_seen"],
    "summary": "Independent assertion completed."
}}))
""",
        encoding="utf-8",
    )
    return target, oracle_source, manifest_path


def _scenario(trace: ScenarioTraceContract | None = None) -> EvaluationScenario:
    return EvaluationScenario(
        scenario_id="scenario_1",
        category="synergy",
        user_prompt="Complete the declared user task.",
        evaluation_goal="Observe the interaction.",
        expected_success_behavior=["complete the task"],
        evidence_to_collect=["trace", "output", "cost"],
        input_contract=ScenarioInputContract(profile_id="no_input", trace=trace or ScenarioTraceContract()),
    )


def _runner(tmp_path: Path, *, outcome: str = "passed"):
    target, oracle_source, manifest_path = _target_and_oracle(tmp_path, oracle_outcome=outcome)
    oracle = SubprocessInteractionOracle(
        (sys.executable, str(oracle_source / "verify.py")),
        verifier_id="independent-test-oracle",
        working_directory=oracle_source,
    )
    runner = ManifestInteractionTrialRunner(
        target,
        fixture_catalog=FixtureCatalog(),
        fixture_root=None,
        oracle=oracle,
    )
    return runner, manifest_path


def test_manifest_runner_binds_target_observation_and_independent_oracle(tmp_path: Path) -> None:
    runner, manifest_path = _runner(tmp_path)

    result = runner.run(_scenario(), "combined", trial_root=tmp_path / "trial")

    assert result.oracle["status"] == "verified"
    assert result.oracle["outcome"] == "passed"
    assert result.metrics["latency_ms"] >= 0
    assert result.trace[0]["event_type"] == "skill_output"
    assert len(result.evidence_refs) == 3
    request = json.loads((tmp_path / "trial" / "interaction-request.json").read_text(encoding="utf-8"))
    assert request["condition_kind"] == "combined"
    assert "expected_success_behavior" not in request
    assert manifest_path.is_file()


def test_failed_product_outcome_is_verified_evidence_not_runner_failure(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path, outcome="failed")

    result = runner.run(_scenario(), "a_only", trial_root=tmp_path / "trial")

    assert result.oracle["status"] == "verified"
    assert result.oracle["outcome"] == "failed"


def test_runner_requires_live_provider_metadata_when_target_binding_is_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_TARGET_API_KEY", "test-secret")
    target, oracle_source, _ = _target_and_oracle(tmp_path)
    oracle = SubprocessInteractionOracle(
        (sys.executable, str(oracle_source / "verify.py")),
        verifier_id="independent-test-oracle",
        working_directory=oracle_source,
    )
    runner = ManifestInteractionTrialRunner(
        target,
        fixture_catalog=FixtureCatalog(),
        fixture_root=None,
        oracle=oracle,
        binding=ProviderBinding(
            project_id="demo",
            role="sut_native",
            provider="openai",
            base_url="https://api.example.invalid/v1",
            model="target-model",
            expected_environment_variable="TEST_TARGET_API_KEY",
            credential_source_ref="test-env",
            batch_budget_usd=1,
            timeout_seconds=30,
            allowed_hosts=["api.example.invalid"],
            data_retention_policy="test",
        ),
    )

    with pytest.raises(InteractionRunnerError, match="did not record provider request IDs"):
        runner.run(
            _scenario(ScenarioTraceContract(provider_usage="required")),
            "combined",
            trial_root=tmp_path / "trial",
        )


def test_runner_allows_bound_zero_provider_boundary_with_output_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_TARGET_API_KEY", "test-secret")
    target, oracle_source, _ = _target_and_oracle(tmp_path)
    runner = ManifestInteractionTrialRunner(
        target,
        fixture_catalog=FixtureCatalog(),
        fixture_root=None,
        oracle=SubprocessInteractionOracle(
            (sys.executable, str(oracle_source / "verify.py")),
            verifier_id="independent-test-oracle",
            working_directory=oracle_source,
        ),
        binding=ProviderBinding(
            project_id="demo",
            role="sut_native",
            provider="openai",
            base_url="https://api.example.invalid/v1",
            model="target-model",
            expected_environment_variable="TEST_TARGET_API_KEY",
            credential_source_ref="test-env",
            batch_budget_usd=1,
            timeout_seconds=30,
            allowed_hosts=["api.example.invalid"],
            data_retention_policy="test",
        ),
    )

    result = runner.run(
        _scenario(ScenarioTraceContract(provider_usage="optional")),
        "combined",
        trial_root=tmp_path / "trial",
    )

    assert result.provider_request_ids == []
    assert result.output_artifact_ref == "sha256:declared-output"


def test_runner_rejects_scenario_trace_contract_violation(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path)
    scenario = _scenario(ScenarioTraceContract(required_event_types=["required_but_missing"]))

    with pytest.raises(InteractionRunnerError, match="Scenario trace contract failed"):
        runner.run(scenario, "b_only", trial_root=tmp_path / "trial")


def test_oracle_result_model_keeps_verification_and_outcome_separate() -> None:
    result = IndependentOracleResult.model_validate({
        "verifier_id": "oracle",
        "oracle_type": "rule_based",
        "oracle_version": "1.0",
        "validation_input": {"scenario_id": "scenario_1", "condition": "combined"},
        "status": "verified",
        "outcome": "unresolved",
        "assertions": [{"name": "task", "status": "unresolved", "detail": "needs review"}],
        "evidence_refs": ["oracle:evidence"],
        "summary": "The independent verifier completed but could not resolve the product outcome.",
    })

    assert result.status == "verified"
    assert result.outcome == "unresolved"


def test_oracle_result_requires_declared_typed_failure_support_and_preserves_scope() -> None:
    payload = {
        "verifier_id": "oracle",
        "oracle_type": "structured_state",
        "oracle_version": "1.0",
        "validation_input": {"scenario_id": "scenario_1"},
        "status": "verified",
        "outcome": "failed",
        "assertions": [{"name": "contradiction_check", "status": "failed", "detail": "conflict", "failure_type": "contradiction"}],
        "failure_types_evaluated": ["contradiction"],
        "verification_scopes": ["behavioral"],
        "scope_limitations": ["Domain correctness was not evaluated."],
        "evidence_refs": ["oracle:evidence"],
        "summary": "Behavioral contradiction detected.",
    }

    result = IndependentOracleResult.model_validate(payload)

    assert result.failure_types_evaluated == ["contradiction"]
    assert result.verification_scopes == ["behavioral"]
    with pytest.raises(ValueError, match="exactly one typed assertion"):
        IndependentOracleResult.model_validate({**payload, "failure_types_evaluated": []})


class _MatrixGenerator:
    def analyze_pair_relationship(self, target, change):
        return InteractionRelationshipProfile(
            relationship="complementary",
            rationale="The two capabilities address one user job.",
            signals=["shared user job"],
            hypothesis_source=InteractionHypothesisSource(
                inputs=["description", "responsibility", "dependency", "boundary"]
            ),
            provider_metadata=PlanningCallMetadata(
                provider="test",
                model="relationship-model",
                request_fingerprint="request-fingerprint",
                response_fingerprint="response-fingerprint",
            ),
            hypothesis_hash="sha256:" + "c" * 64,
        )

    def generate_pair_scenarios(self, target, change, *, relationship):
        scenarios = [
            EvaluationScenario(
                scenario_id=f"scenario_{index}",
                category=category,
                user_prompt=f"Complete {category} task.",
                evaluation_goal="Observe the pair behavior.",
                expected_success_behavior=["complete the task"],
                evidence_to_collect=["trace", "output", "cost"],
                expected_behavior=PairScenarioExpectedBehavior(
                    skill_a_only="A handles its responsibility.",
                    skill_b_only="B handles its responsibility.",
                    combined="The pair handles the task.",
                ),
                input_contract=(
                    ScenarioInputContract(
                        profile_id="boundary-missing-input",
                        requirements=[ScenarioInputRequirement(
                            input_id="source-data",
                            fixture_id="source-data-absent",
                            availability="absent",
                            description="The boundary input is intentionally absent.",
                        )],
                    )
                    if category == "boundary" else ScenarioInputContract.no_input()
                ),
            )
            for index, category in enumerate(("complementary", "synergy", "conflict", "boundary"), 1)
        ]
        return [
            scenario.model_copy(update={
                "scenario_hash": scenario_hash_for(scenario.model_dump(mode="json")),
                "scenario_provenance": ScenarioProvenance(
                    hypothesis_source="eval_engineering.relationship_hypothesis",
                    relationship_hypothesis_hash=relationship.hypothesis_hash,
                    provider_metadata=relationship.provider_metadata,
                    scenario_hash=scenario_hash_for(scenario.model_dump(mode="json")),
                ),
            })
            for scenario in scenarios
        ]


def test_manifest_runner_executes_complete_pair_matrix(tmp_path: Path) -> None:
    target, oracle_source, _ = _target_and_oracle(tmp_path)
    plan = build_evolution_evaluation_plan(
        EvaluationTarget(
            target_id="pair-target",
            project_id="demo",
            component_type="skill_pair",
            name="a_and_b",
            description="Two capabilities",
            product_responsibility="Complete one task",
            user_job="Complete the task",
            component_members=["a", "b"],
        ),
        EvaluationChange(
            change_id="change-1",
            project_id="demo",
            change_type="interaction",
            evaluation_type="skill_pair_evaluation",
            evaluation_name="Pair matrix",
            summary="Run the interaction matrix",
            scenario_suite=ScenarioSuiteConfig(
                scenarios_per_category=1,
                max_scenarios=4,
                max_trials=12,
                default_repetitions=1,
                stability_sample_per_category=0,
                stability_repetitions=1,
                trial_timeout_seconds=30,
            ),
        ),
        scenario_generator=_MatrixGenerator(),
        evidence_requirements_generator=ScenarioEvidenceRequirementsGenerator(),
    )
    readiness = check_evaluation_plan_readiness(plan, FixtureCatalog(fixtures=[FixtureDescriptor(
        fixture_id="source-data-absent",
        kind="file",
        availability="absent",
        purpose="The boundary input is intentionally absent.",
    )]))
    oracle = SubprocessInteractionOracle(
        (sys.executable, str(oracle_source / "verify.py")),
        verifier_id="independent-test-oracle",
        working_directory=oracle_source,
    )
    runner = ManifestInteractionTrialRunner(
        target,
        fixture_catalog=FixtureCatalog(fixtures=[FixtureDescriptor(
            fixture_id="source-data-absent",
            kind="file",
            availability="absent",
            purpose="The boundary input is intentionally absent.",
        )]),
        fixture_root=None,
        oracle=oracle,
    )

    artifact = execute_evaluation_matrix(
        plan,
        evaluation_name="a_and_b",
        evaluation_id="evaluation-1",
        readiness=readiness,
        runner=runner,
        condition_kinds=PAIR_INTERACTION_CONDITIONS,
        run_root=tmp_path / "matrix",
    )

    assert artifact.metrics["condition_count"] == 12
    assert artifact.metrics["verified_condition_count"] == 12
    assert artifact.metrics["failure_rate"] == 0.0
    assert artifact.metrics["passed_condition_count"] == 12
