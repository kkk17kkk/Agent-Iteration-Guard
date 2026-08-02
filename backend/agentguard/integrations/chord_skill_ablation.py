"""Skill-ablation case plugin for CHORD's native App Profile Skill."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..domain import ProviderBinding, SkillAblationEvidence, SkillAblationVerification, SkillContract, SkillTraceEvent, SutProviderUsage, VerificationCriterion
from ..integrations.native_command import CommandOperation
from ..skill_ablation import SkillAblationVerifier
from ..target_onboarding import source_working_tree_fingerprint
from ..target_runtime import TargetRuntimeAdapter, TargetTraceEvidence
from ..targets import TargetInfrastructureError


@dataclass(frozen=True)
class ChordSkillAblationConfig:
    manifest_path: Path
    cache_root: Path
    run_root: Path
    skill_contract: SkillContract


@dataclass(frozen=True)
class ChordSkillAblationResult:
    evidence: SkillAblationEvidence
    verification: SkillAblationVerification
    target_criteria: tuple[VerificationCriterion, ...]
    evidence_path: Path


class ChordSkillAblationCaseRunner:
    """Run CHORD's declared command entrypoint and independently score its output."""

    def __init__(self, config: ChordSkillAblationConfig) -> None:
        self.config = config

    def run(
        self,
        *,
        trial_ref: str,
        intervention: str,
        binding: ProviderBinding,
        credential_reader: Callable[[str], str | None],
    ) -> ChordSkillAblationResult:
        if intervention not in {"enabled", "removed", "invalid_replacement", "related_precheck"}:
            raise ValueError("Unsupported approved CHORD intervention.")
        trial_dir = self.config.run_root / _safe_component(trial_ref)
        trial_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trial_dir / "target-trace.jsonl"
        adapter = TargetRuntimeAdapter(self.config.manifest_path, self.config.cache_root)
        before = source_working_tree_fingerprint(adapter.source)
        runtime_error: str | None = None
        try:
            command = adapter.run_command(
                CommandOperation("run_profile"), state_path=trial_dir / "state", binding=binding,
                credential_reader=credential_reader, trace_path=trace_path,
                trial_environment={"AGENTGUARD_STAGE7_INTERVENTION": intervention},
            )
        except (OSError, TimeoutError, TargetInfrastructureError) as error:
            runtime_error = type(error).__name__
            command = None
        after = source_working_tree_fingerprint(adapter.source)
        trace = adapter.read_trace(trace_path) if trace_path.is_file() else TargetTraceEvidence((), {"status": "trace_not_created"})
        payload = {"trial_ref": trial_ref, "intervention": intervention,
                   "stdout": command.stdout if command else "", "exit_code": command.exit_code if command else None,
                   "duration_seconds": command.duration_seconds if command else None,
                   "target_trace": list(trace.events), "target_trace_verification": trace.verification,
                   "source_before": before, "source_after": after}
        evidence_path = trial_dir / "trial-evidence.json"
        evidence_ref = _write_evidence(evidence_path, payload)
        target_criteria = tuple(_verify_target_output(command.stdout if command else {}, trace, before == after, evidence_ref))
        evidence = _build_evidence(
            self.config.skill_contract, trial_ref, intervention, command.stdout if command else {}, trace,
            before == after, evidence_ref, target_criteria, runtime_error,
        )
        verification = SkillAblationVerifier().verify(self.config.skill_contract, evidence)
        _write_json(trial_dir / "skill-contract.json", self.config.skill_contract.model_dump())
        _write_json(trial_dir / "skill-evidence.json", evidence.model_dump())
        _write_json(trial_dir / "skill-verification.json", verification.model_dump())
        return ChordSkillAblationResult(evidence, verification, target_criteria, evidence_path)


def _build_evidence(
    contract: SkillContract,
    trial_ref: str,
    intervention: str,
    output: object,
    trace: TargetTraceEvidence,
    source_unchanged: bool,
    evidence_ref: str,
    target_criteria: tuple[VerificationCriterion, ...],
    runtime_error: str | None = None,
) -> SkillAblationEvidence:
    events = [SkillTraceEvent(sequence=index, event_type=str(item.get("event_type") or "unknown"), evidence_ref=f"{evidence_ref}#trace:{index}", payload=_safe_trace(item)) for index, item in enumerate(trace.events)]
    provider_events = [item for item in trace.events if item.get("event_type") == "native_provider_request_completed" and item.get("request_id")]
    deliverable = _structured_result(output)
    model_trace = deliverable.get("model_trace") if isinstance(deliverable.get("model_trace"), dict) else {}
    trigger = next((event for event in events if event.event_type == "native_provider_request_started"), None)
    return SkillAblationEvidence(
        project_id=contract.project_id, evolution_case_id=contract.evolution_case_id, skill_contract_id=contract.skill_contract_id,
        trial_ref=trial_ref,
        intervention={
            "enabled": "enabled", "removed": "disabled", "invalid_replacement": "replacement",
            "related_precheck": "replacement",
        }[intervention],
        sut_provider_request_ids=[str(item["request_id"]) for item in provider_events],
        sut_provider_usage=[SutProviderUsage(request_id=str(item["request_id"]), input_tokens=int(item.get("input_tokens") or 0), output_tokens=int(item.get("output_tokens") or 0), cache_hit_tokens=int(item.get("cache_hit_tokens") or 0)) for item in provider_events],
        trigger_event=trigger, trace_events=events, trace_complete=trace.verification.get("status") == "passed",
        deliverable=deliverable, deliverable_evidence_ref=evidence_ref if deliverable else None,
        target_criteria=list(target_criteria),
        runtime_error=runtime_error,
        boundary_outcome="none" if source_unchanged else "unexpected", boundary_evidence_refs=[evidence_ref],
        fallback_used=not bool(model_trace.get("used_llm")) or bool(model_trace.get("fallback_reason")),
    )


def _verify_target_output(output: object, trace: TargetTraceEvidence, source_unchanged: bool, evidence_ref: str) -> list[VerificationCriterion]:
    result = _structured_result(output)
    model_trace = result.get("model_trace") if isinstance(result.get("model_trace"), dict) else {}
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    report = str(result.get("report_markdown") or "")
    expected = {"installed": metrics.get("installed_app_count"), "lending": metrics.get("lending_app_count")}
    observed = {"installed": _reported_count(report, r"共安装\s*(\d+)\s*款"), "lending": _reported_count(report, r"借贷\s*App\s*数量[^\n]*?(\d+)\s*个")}
    contradictions = {key: value for key, value in observed.items() if value is not None and expected.get(key) is not None and value != expected[key]}
    shape_ok = bool(result) and isinstance(evidence.get("raw_counts"), dict) and bool(report)
    llm_ok = bool(model_trace.get("used_llm")) and not model_trace.get("fallback_reason")
    return [
        VerificationCriterion(name="target_profile_shape", status="passed" if shape_ok else "failed", detail="Target returned a structured profile with source evidence." if shape_ok else "Target profile output is incomplete.", evidence_refs=[evidence_ref]),
        VerificationCriterion(name="target_llm_branch", status="passed" if llm_ok else "failed", detail="Target reports native LLM use without fallback." if llm_ok else "Target output reports fallback or no LLM use.", evidence_refs=[evidence_ref]),
        VerificationCriterion(name="profile_evidence_consistency", status="passed" if not contradictions else "failed", detail="Narrative numeric claims agree with target-computed metrics." if not contradictions else f"Narrative contradictions={contradictions}; target_metrics={expected}.", evidence_refs=[evidence_ref]),
        VerificationCriterion(name="source_write_boundary", status="passed" if source_unchanged else "failed", detail="No declared source state changed." if source_unchanged else "Target source state changed during a read-only profile run.", evidence_refs=[evidence_ref]),
    ]


def _structured_result(output: object) -> dict[str, object]:
    if not isinstance(output, dict): return {}
    results = output.get("result", {}).get("results", []) if isinstance(output.get("result"), dict) else []
    if not isinstance(results, list) or not results or not isinstance(results[0], dict): return {}
    data = results[0].get("result", {}).get("data", {}) if isinstance(results[0].get("result"), dict) else {}
    return data.get("structured_result", {}) if isinstance(data, dict) and isinstance(data.get("structured_result"), dict) else {}


def _reported_count(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _safe_trace(item: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in item.items() if key in {"event_type", "target_event_type", "request_id", "model", "input_tokens", "output_tokens", "cache_hit_tokens", "status", "module", "node", "reason"}}


def _write_evidence(path: Path, payload: dict[str, object]) -> str:
    _write_json(path, payload)
    return f"file:{path.resolve()};sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
