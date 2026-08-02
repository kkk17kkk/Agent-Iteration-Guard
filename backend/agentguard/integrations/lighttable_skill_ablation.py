"""Skill-ablation case plugin for LightTable's native recipe-planning capability.

The generic runtime adapter owns process launch and credential injection.  This
module only supplies the approved target case and its deterministic SQLite /
trace oracle; it never decides outcomes from model prose.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..domain import (
    ProviderBinding,
    SkillAblationEvidence,
    SkillAblationVerification,
    SkillContract,
    SkillTraceEvent,
    SutProviderUsage,
    VerificationCriterion,
)
from ..skill_ablation import SkillAblationVerifier
from ..target_runtime import TargetRuntimeAdapter, TargetTraceEvidence
from ..targets import TargetInfrastructureError
from .lighttable_case import LIGHTTABLE_CONSTRAINT_CASE


@dataclass(frozen=True)
class LightTableSkillAblationConfig:
    manifest_path: Path
    cache_root: Path
    canonical_seed: Path
    run_root: Path
    project_id: str
    evolution_case_id: str
    skill_contract: SkillContract


@dataclass(frozen=True)
class LightTableSkillAblationResult:
    evidence: SkillAblationEvidence
    verification: SkillAblationVerification
    target_criteria: tuple[VerificationCriterion, ...]
    evidence_path: Path


class LightTableSkillAblationCaseRunner:
    """Execute one approved LightTable arm against a fresh local SQLite seed."""

    def __init__(self, config: LightTableSkillAblationConfig) -> None:
        self.config = config
        if config.skill_contract.project_id != config.project_id:
            raise ValueError("LightTable SkillContract project scope does not match the case configuration.")
        if config.skill_contract.evolution_case_id != config.evolution_case_id:
            raise ValueError("LightTable SkillContract case scope does not match the case configuration.")

    def run(
        self,
        *,
        trial_ref: str,
        intervention: str,
        binding: ProviderBinding | None,
        credential_reader: Callable[[str], str | None],
    ) -> LightTableSkillAblationResult:
        if intervention not in {"enabled", "removed", "invalid_replacement", "related_precheck"}:
            raise ValueError("Unsupported approved LightTable intervention.")
        if not self.config.canonical_seed.is_file():
            raise TargetInfrastructureError("The declared LightTable canonical SQLite seed is unavailable.")

        trial_dir = self.config.run_root / _safe_component(trial_ref)
        trial_dir.mkdir(parents=True, exist_ok=True)
        database = trial_dir / "lighttable.db"
        trace_path = trial_dir / "target-trace.jsonl"
        log_path = trial_dir / "target.log"
        shutil.copy2(self.config.canonical_seed, database)
        initial = _snapshot_sqlite(database)

        adapter = TargetRuntimeAdapter(self.config.manifest_path, self.config.cache_root)
        service = adapter.start_service(
            state_path=database,
            log_path=log_path,
            binding=binding,
            credential_reader=credential_reader,
            trace_path=trace_path,
            trial_environment={"AGENTGUARD_STAGE7_INTERVENTION": intervention},
        )
        readiness_status: int | None = None
        readiness_response: object = None
        response_status: int | None = None
        response: object = None
        runtime_error: str | None = None
        try:
            readiness_status, readiness_response = adapter.execute_http(
                service, _readiness_operation(adapter.manifest.runtime.readiness_path or "")
            )
            response_status, response = adapter.execute_http(service, LIGHTTABLE_CONSTRAINT_CASE.trial_operation)
        except (OSError, TimeoutError, TargetInfrastructureError) as error:
            runtime_error = type(error).__name__
        finally:
            service.close()

        final = _snapshot_sqlite(database)
        trace = adapter.read_trace(trace_path) if trace_path.is_file() else TargetTraceEvidence((), {"status": "trace_not_created"})
        evidence_path = trial_dir / "trial-evidence.json"
        payload = {
            "trial_ref": trial_ref,
            "intervention": intervention,
            "readiness": {"status": readiness_status, "response": readiness_response},
            "response_status": response_status,
            "response": response,
            "initial": initial,
            "final": final,
            "target_trace": list(trace.events),
            "target_trace_verification": trace.verification,
        }
        evidence_ref = _write_evidence(evidence_path, payload)
        target_criteria = tuple(_verify_target_outcome(
            response_status=response_status,
            response=response,
            trace=trace,
            initial=initial,
            final=final,
            evidence_ref=evidence_ref,
        ))
        evidence = _build_evidence(
            contract=self.config.skill_contract,
            trial_ref=trial_ref,
            intervention=intervention,
            response=response,
            trace=trace,
            initial=initial,
            final=final,
            evidence_ref=evidence_ref,
            target_criteria=target_criteria,
            runtime_error=runtime_error,
        )
        verification = SkillAblationVerifier().verify(self.config.skill_contract, evidence)
        _write_json(trial_dir / "skill-contract.json", self.config.skill_contract.model_dump())
        _write_json(trial_dir / "skill-evidence.json", evidence.model_dump())
        _write_json(trial_dir / "skill-verification.json", verification.model_dump())
        return LightTableSkillAblationResult(evidence, verification, target_criteria, evidence_path)


def _build_evidence(
    *,
    contract: SkillContract,
    trial_ref: str,
    intervention: str,
    response: object,
    trace: TargetTraceEvidence,
    initial: dict[str, list[dict[str, object]]],
    final: dict[str, list[dict[str, object]]],
    evidence_ref: str,
    target_criteria: tuple[VerificationCriterion, ...],
    runtime_error: str | None,
) -> SkillAblationEvidence:
    events = [
        SkillTraceEvent(sequence=index, event_type=str(item.get("event_type") or "unknown"),
                        evidence_ref=f"{evidence_ref}#trace:{index}", payload=_safe_trace_payload(item))
        for index, item in enumerate(trace.events)
    ]
    provider_events = [
        item for item in trace.events
        if item.get("event_type") == "native_provider_request_completed" and item.get("request_id")
    ]
    request_ids = [str(item["request_id"]) for item in provider_events]
    usage = [
        SutProviderUsage(
            request_id=str(item["request_id"]),
            input_tokens=int(item.get("input_tokens") or 0),
            output_tokens=int(item.get("output_tokens") or 0),
            cache_hit_tokens=int(item.get("cache_hit_tokens") or 0),
        )
        for item in provider_events
    ]
    trigger = next((event for event in events if event.event_type in contract.required_trace_event_types), None)
    fallback = any(str(item.get("event_type", "")).endswith("fallback") for item in trace.events)
    boundary = _boundary_outcome(initial, final)
    return SkillAblationEvidence(
        project_id=contract.project_id,
        evolution_case_id=contract.evolution_case_id,
        skill_contract_id=contract.skill_contract_id,
        trial_ref=trial_ref,
        intervention={
            "enabled": "enabled",
            "removed": "disabled",
            "invalid_replacement": "replacement",
            "related_precheck": "replacement",
        }[intervention],
        sut_provider_request_ids=request_ids,
        sut_provider_usage=usage,
        trigger_event=trigger,
        trace_events=events,
        trace_complete=trace.verification.get("status") == "passed",
        deliverable=response if isinstance(response, dict) else {},
        deliverable_evidence_ref=evidence_ref if isinstance(response, dict) else None,
        target_criteria=list(target_criteria),
        runtime_error=runtime_error,
        boundary_outcome=boundary,
        boundary_evidence_refs=[evidence_ref],
        fallback_used=fallback,
    )


def _verify_target_outcome(
    *,
    response_status: int | None,
    response: object,
    trace: TargetTraceEvidence,
    initial: dict[str, list[dict[str, object]]],
    final: dict[str, list[dict[str, object]]],
    evidence_ref: str,
) -> list[VerificationCriterion]:
    plans = response.get("plans") if isinstance(response, dict) else None
    response_ok = response_status == 200 and isinstance(plans, list) and bool(plans)
    generated = [item for item in trace.events if item.get("event_type") == "recipe_planning_generated"]
    ingredients = {
        str(value).strip().casefold()
        for item in generated for value in (item.get("core_ingredients") or [])
        if str(value).strip()
    }
    constraint_ok = response_ok and bool(generated) and "鸡蛋" not in ingredients
    boundary = _boundary_outcome(initial, final)
    return [
        VerificationCriterion(
            name="target_response_shape", status="passed" if response_ok else "failed",
            detail="Target returned a non-empty structured meal-plan response." if response_ok else "Target response was not an accepted non-empty meal-plan response.",
            evidence_refs=[evidence_ref],
        ),
        VerificationCriterion(
            name="generated_constraint_adherence", status="passed" if constraint_ok else "failed",
            detail="Target-generated recipe ingredients exclude the approved disliked ingredient." if constraint_ok else "Generated-plan trace is absent or includes the disliked ingredient.",
            evidence_refs=[evidence_ref],
        ),
        VerificationCriterion(
            name="sqlite_write_boundary", status="passed" if boundary == "none" else "failed",
            detail="Only the declared recommendation-history/shopping-list state changed." if boundary == "none" else "SQLite changes exceeded the declared state boundary.",
            evidence_refs=[evidence_ref],
        ),
    ]


def _boundary_outcome(initial: dict[str, list[dict[str, object]]], final: dict[str, list[dict[str, object]]]) -> str:
    changed = {table for table in set(initial) | set(final) if initial.get(table, []) != final.get(table, [])}
    allowed = {"recommendation_history", "shopping_list_items"}
    history_delta = len(final.get("recommendation_history", [])) - len(initial.get("recommendation_history", []))
    return "none" if changed <= allowed and history_delta == 1 else "unexpected"


def _snapshot_sqlite(path: Path) -> dict[str, list[dict[str, object]]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        tables = [str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {
            table: [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }
    finally:
        connection.close()


def _readiness_operation(path: str):
    from .native_http import HttpOperation
    return HttpOperation(name="readiness", method="GET", path=path)


def _safe_trace_payload(event: dict[str, object]) -> dict[str, object]:
    allowed = {"event_type", "model", "request_id", "plan_count", "reason", "core_ingredients", "input_tokens", "output_tokens", "cache_hit_tokens"}
    return {key: value for key, value in event.items() if key in allowed}


def _write_evidence(path: Path, payload: dict[str, object]) -> str:
    _write_json(path, payload)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"file:{path.resolve()};sha256:{digest}"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_component(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
