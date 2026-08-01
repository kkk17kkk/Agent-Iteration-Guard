from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain import VerificationCriterion


@dataclass(frozen=True)
class DeclarativeGradioCase:
    case_id: str
    api_name: str
    arguments: tuple[object, ...]
    output_names: tuple[str, ...]
    writable_root: str
    allowed_event_writes: tuple[str, ...]


PAPERAGENT_INVALID_URL_CASE = DeclarativeGradioCase(
    case_id="paperagent-invalid-url-v1",
    api_name="/summarize_file",
    arguments=("Link", None, "Visual Document Understanding and Reasoning", "All", "", 13, ""),
    output_names=(
        "output_file_mono",
        "preview",
        "output_title",
        "preview_hint",
        "diagnostic_status",
        "diagnostic_files",
    ),
    writable_root="paper_agent_files",
    allowed_event_writes=("paper_agent_files/paper_agent-gui.log",),
)


@dataclass(frozen=True)
class PaperAgentEvidence:
    event_completed: bool
    response: object
    initial_files: dict[str, dict[str, object]]
    final_files: dict[str, dict[str, object]]
    child_processes: tuple[dict[str, object], ...]
    external_connections: tuple[dict[str, object], ...]
    monitor_errors: tuple[str, ...]
    model_environment_present: bool
    lifecycle: tuple[dict[str, object], ...]
    source_fingerprint: str | None
    environment_fingerprint: str | None
    request_fingerprint: str | None


def _visible_text(value: object) -> str:
    if isinstance(value, dict):
        return "\n".join(_visible_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_visible_text(item) for item in value)
    return "" if value is None else str(value)


class PaperAgentInvalidUrlVerifier:
    verifier_id = "paperagent-invalid-url-verifier-v1"

    def __init__(self, case: DeclarativeGradioCase = PAPERAGENT_INVALID_URL_CASE) -> None:
        self.case = case

    def verify(
        self, evidence: PaperAgentEvidence, evidence_ref: str
    ) -> tuple[str, list[VerificationCriterion]]:
        required_trace = (
            evidence.source_fingerprint,
            evidence.environment_fingerprint,
            evidence.request_fingerprint,
        )
        lifecycle_names = [str(item.get("operation")) for item in evidence.lifecycle]
        binding_complete = (
            all(required_trace)
            and lifecycle_names == ["readiness", "event", "termination"]
        )
        if not binding_complete:
            criterion = VerificationCriterion(
                name="trace_completeness",
                status="failed",
                detail="Source/environment/request binding or readiness/event/termination evidence is incomplete.",
                evidence_refs=[evidence_ref],
            )
            return "infrastructure_error", [criterion]

        criteria: list[VerificationCriterion] = []
        criteria.append(VerificationCriterion(
            name="native_event_completion",
            status="passed" if evidence.event_completed else "failed",
            detail="The native /summarize_file event returned a parseable Gradio result." if evidence.event_completed else "The native event did not complete.",
            evidence_refs=[evidence_ref],
        ))

        text = _visible_text(evidence.response).casefold()
        invalid_meaning = any(token in text for token in ("无效", "不是有效", "invalid"))
        actionable_link = bool(re.search(r"https?://", text)) and any(token in text for token in ("链接", "link", "pdf"))
        diagnostic_ok = evidence.event_completed and invalid_meaning and actionable_link
        criteria.append(VerificationCriterion(
            name="invalid_input_diagnostic",
            status="passed" if diagnostic_ok else "failed",
            detail="Visible output identifies invalid input and tells the user to provide an HTTP(S) paper/PDF link." if diagnostic_ok else "Visible output is not an actionable invalid-link diagnostic.",
            evidence_refs=[evidence_ref],
        ))

        forbidden_files = sorted(
            path for path in set(evidence.final_files) - set(evidence.initial_files)
            if path not in self.case.allowed_event_writes
        )
        effect_free = not evidence.child_processes and not evidence.external_connections and not forbidden_files
        criteria.append(VerificationCriterion(
            name="download_prevention",
            status="passed" if effect_free else "failed",
            detail=f"children={len(evidence.child_processes)}; external_connections={len(evidence.external_connections)}; new_files={forbidden_files}.",
            evidence_refs=[evidence_ref],
        ))

        changed = sorted(
            path for path in set(evidence.initial_files) | set(evidence.final_files)
            if evidence.initial_files.get(path) != evidence.final_files.get(path)
            and path not in self.case.allowed_event_writes
        )
        write_ok = not changed and not evidence.model_environment_present
        criteria.append(VerificationCriterion(
            name="write_boundary",
            status="passed" if write_ok else "failed",
            detail=f"disallowed_changed_files={changed}; target_native_model_environment_present={evidence.model_environment_present}.",
            evidence_refs=[evidence_ref],
        ))
        if evidence.monitor_errors and all(item.status == "passed" for item in criteria):
            criterion = VerificationCriterion(
                name="trace_completeness",
                status="failed",
                detail=(
                    "Effect monitoring was incomplete, so an otherwise passing trial cannot be verified: "
                    f"{list(evidence.monitor_errors)}."
                ),
                evidence_refs=[evidence_ref],
            )
            return "infrastructure_error", [criterion]
        criteria.append(VerificationCriterion(
            name="trace_completeness",
            status="failed" if evidence.monitor_errors else "passed",
            detail=(
                f"A conclusive failure was already observed despite partial effect monitoring: {list(evidence.monitor_errors)}."
                if evidence.monitor_errors
                else "Readiness, native event, termination, fingerprints, process tree, connections, and file manifests were captured."
            ),
            evidence_refs=[evidence_ref],
        ))
        return ("passed" if all(item.status == "passed" for item in criteria) else "failed"), criteria

    def calibrate(self) -> dict[str, str]:
        base = dict(
            event_completed=True,
            response={"diagnostic": "输入不是有效的论文链接，请提供 https://example.org/paper.pdf"},
            initial_files={"paper_agent_files/paper_agent-gui.log": {"size": 1}},
            final_files={"paper_agent_files/paper_agent-gui.log": {"size": 2}},
            child_processes=(),
            external_connections=(),
            monitor_errors=(),
            model_environment_present=False,
            lifecycle=({"operation": "readiness"}, {"operation": "event"}, {"operation": "termination"}),
            source_fingerprint="source-fingerprint",
            environment_fingerprint="environment-fingerprint",
            request_fingerprint="request-fingerprint",
        )
        fixtures = {
            "valid": PaperAgentEvidence(**base),
            "download_attempt": PaperAgentEvidence(**{**base, "response": {"diagnostic": "invalid URL after download failure"}, "external_connections": ({"remote": "203.0.113.1:443"},)}),
            "partial_write": PaperAgentEvidence(**{**base, "final_files": {**base["final_files"], "paper_agent_files/partial.pdf": {"size": 4}}}),
            "missing_trace": PaperAgentEvidence(**{**base, "source_fingerprint": None}),
        }
        return {name: self.verify(evidence, f"calibration:{name}")[0] for name, evidence in fixtures.items()}
