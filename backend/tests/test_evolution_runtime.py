from pathlib import Path

from agentguard.domain import ProviderBinding
from agentguard.evolution_runtime import EvolutionAgentRuntime
from agentguard.provider_runtime import ProviderRuntimeError, ProviderToolCall, ProviderTurn
from agentguard.store import Store
from agentguard.targets import EvidenceReviewAdapter


def binding(**updates) -> ProviderBinding:
    values = {
        "project_id": "project_runtime",
        "role": "control_plane",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "expected_environment_variable": "DEEPSEEK_API_KEY",
        "credential_source_ref": "runtime:test",
        "batch_budget_usd": 0.03,
        "timeout_seconds": 30,
        "allowed_hosts": ["api.deepseek.com"],
        "data_retention_policy": "non-secret metadata only",
        "input_price_per_million_usd": 0.14,
        "output_price_per_million_usd": 0.28,
        "cache_hit_price_per_million_usd": 0.0028,
        "pricing_source": "official:test-snapshot",
        "pricing_verified_at": "2026-08-01T00:00:00+00:00",
    }
    values.update(updates)
    return ProviderBinding(**values)


class ScriptedProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    def complete(self, messages, tools):
        result = self.turns[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result


def turn(sequence: int, name: str, arguments: dict[str, object]) -> ProviderTurn:
    return ProviderTurn(
        request_id=f"request_{sequence}",
        finish_reason="tool_calls",
        tool_calls=(ProviderToolCall(f"call_{sequence}", name, arguments),),
        input_tokens=100,
        output_tokens=20,
        cache_hit_tokens=0,
        request_fingerprint=f"{sequence:064d}",
        response_fingerprint=f"{sequence + 1:064d}",
    )


def test_durable_multiturn_evolution_agent_persists_hypothesis_and_usage(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "runtime.db"))
    adapter = EvidenceReviewAdapter("evidence:approved-specs", {"baseline": "fa774ef", "candidate": "TBD"})
    provider = ScriptedProvider([
        turn(1, "read_case_evidence", {}),
        turn(2, "submit_evaluation_hypothesis", {
            "summary": "The case is approved for bounded discovery, but no pair result exists.",
            "evidence_refs": ["evidence:approved-specs"],
            "uncertainty": "Candidate revision and native trial evidence are absent.",
        }),
    ])
    result = EvolutionAgentRuntime(store, binding(), provider, adapter).start(
        project_id="project_runtime",
        evolution_case_id="case_smoke",
        objective="Review the fixed case evidence and submit an evidence-linked hypothesis or insufficiency.",
    )
    assert result.status == "completed"
    assert result.terminal_reason == "hypothesis"
    assert len(result.model_call_ids) == 2
    assert len(result.tool_call_ids) == 2
    assert len(result.observation_ids) == 2
    assert result.spent_cost_usd > 0
    hypothesis = store.get("evaluation_hypothesis", result.hypothesis_id or "", __import__("agentguard.domain", fromlist=["EvaluationHypothesis"]).EvaluationHypothesis)
    assert hypothesis and hypothesis.evidence_level == "inferred"
    assert len(store.list("evolution_provider_usage", __import__("agentguard.domain", fromlist=["EvolutionProviderUsage"]).EvolutionProviderUsage, "project_runtime")) == 2


def test_provider_failure_is_infrastructure_blocked_without_fallback(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "provider.db"))
    provider = ScriptedProvider([ProviderRuntimeError("network unavailable")])
    result = EvolutionAgentRuntime(
        store,
        binding(),
        provider,
        EvidenceReviewAdapter("evidence:x", {"x": 1}),
    ).start(project_id="project_runtime", evolution_case_id="case", objective="Inspect evidence.")
    assert result.status == "infrastructure_blocked"
    assert result.terminal_reason == "provider_error"
    assert not result.tool_call_ids
    assert not result.hypothesis_id


def test_budget_blocks_before_provider_call(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "budget.db"))
    provider = ScriptedProvider([])
    result = EvolutionAgentRuntime(
        store,
        binding(batch_budget_usd=0.000001),
        provider,
        EvidenceReviewAdapter("evidence:x", {"x": 1}),
    ).start(project_id="project_runtime", evolution_case_id="case", objective="Inspect evidence.")
    assert result.status == "infrastructure_blocked"
    assert result.terminal_reason == "cost_budget_exhausted"
    assert provider.calls == 0


def test_unapproved_tool_is_failed_agent_work(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "tool.db"))
    provider = ScriptedProvider([turn(1, "run_shell", {"command": "whoami"})])
    result = EvolutionAgentRuntime(
        store,
        binding(),
        provider,
        EvidenceReviewAdapter("evidence:x", {"x": 1}),
    ).start(project_id="project_runtime", evolution_case_id="case", objective="Inspect evidence.")
    assert result.status == "failed"
    assert result.terminal_reason == "tool_not_allowed"
    assert not result.tool_call_ids


def test_multiple_provider_tool_calls_execute_in_order_and_persist_observations(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "multiple-tools.db"))
    multiple = ProviderTurn(
        request_id="request_multiple",
        finish_reason="tool_calls",
        tool_calls=(
            ProviderToolCall("call_1", "read_case_evidence", {}),
            ProviderToolCall("call_2", "read_case_evidence", {}),
        ),
        input_tokens=100,
        output_tokens=20,
        cache_hit_tokens=0,
        request_fingerprint="1" * 64,
        response_fingerprint="2" * 64,
    )
    result = EvolutionAgentRuntime(
        store,
        binding(),
        ScriptedProvider([
            multiple,
            turn(3, "submit_evaluation_hypothesis", {
                "summary": "Both ordered evidence reads completed.",
                "evidence_refs": ["evidence:x"],
                "uncertainty": "No target trial was part of this protocol test.",
            }),
        ]),
        EvidenceReviewAdapter("evidence:x", {"x": 1}),
    ).start(project_id="project_runtime", evolution_case_id="case", objective="Inspect evidence.")
    assert result.status == "completed"
    assert result.terminal_reason == "hypothesis"
    assert len(result.model_call_ids) == 2
    assert len(result.tool_call_ids) == 3
    assert len(result.observation_ids) == 3


def test_resume_executes_persisted_pending_tool_without_repeating_provider_call(tmp_path: Path) -> None:
    class CrashBeforeTool:
        def tool_specs(self):
            return EvidenceReviewAdapter("evidence:resume", {"state": "fixed"}).tool_specs()

        def execute(self, name, arguments):
            raise SystemExit(23)

        def restore(self, observations):
            pass

    store = Store(str(tmp_path / "resume.db"))
    first_provider = ScriptedProvider([turn(1, "read_case_evidence", {})])
    runtime = EvolutionAgentRuntime(store, binding(), first_provider, CrashBeforeTool())
    try:
        runtime.start(project_id="project_runtime", evolution_case_id="case", objective="Inspect and conclude.")
        assert False, "expected injected process exit"
    except SystemExit as error:
        assert error.code == 23
    interrupted = store.list("evolution_agent_run", __import__("agentguard.domain", fromlist=["EvolutionAgentRun"]).EvolutionAgentRun, "project_runtime")[0]
    assert len(interrupted.model_call_ids) == 1
    assert not interrupted.tool_call_ids

    second_provider = ScriptedProvider([
        turn(2, "submit_evaluation_hypothesis", {
            "summary": "The persisted observation supports a bounded hypothesis.",
            "evidence_refs": ["evidence:resume"],
            "uncertainty": "No target execution was performed.",
        })
    ])
    resumed = EvolutionAgentRuntime(
        store,
        binding(provider_binding_id=interrupted.provider_binding_id),
        second_provider,
        EvidenceReviewAdapter("evidence:resume", {"state": "fixed"}),
    ).resume(interrupted.evolution_agent_run_id)
    assert resumed.status == "completed"
    assert len(resumed.model_call_ids) == 2
    assert len(resumed.tool_call_ids) == 2
    assert first_provider.calls == 1
    assert second_provider.calls == 1
