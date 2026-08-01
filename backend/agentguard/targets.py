from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


class TargetInfrastructureError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetObservation:
    payload: dict[str, object]
    terminal: bool = False


@dataclass(frozen=True)
class TerminalArtifact:
    kind: str
    record_id: str
    terminal_reason: str


class TargetAdapter(Protocol):
    def tool_specs(self) -> list[dict[str, object]]: ...

    def execute(self, name: str, arguments: dict[str, object]) -> TargetObservation: ...

    def restore(self, observations: list[dict[str, object]]) -> None: ...


def payload_fingerprint(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EvidenceReviewAdapter:
    """Target-neutral live-runtime smoke adapter over fixed, non-secret evidence."""

    def __init__(self, evidence_ref: str, evidence: dict[str, object]) -> None:
        self.evidence_ref = evidence_ref
        self.evidence = evidence
        self.observed = False

    def tool_specs(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_case_evidence",
                    "description": "Read the approved non-secret case evidence before forming a hypothesis.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_evaluation_hypothesis",
                    "description": "Submit a non-Gate evidence-linked hypothesis after reading case evidence.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                            "uncertainty": {"type": "string"},
                        },
                        "required": ["summary", "evidence_refs", "uncertainty"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_insufficient_evidence",
                    "description": "Stop with an explicit insufficiency reason when evidence cannot support a hypothesis.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string"},
                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                            "uncertainty": {"type": "string"},
                        },
                        "required": ["summary", "evidence_refs", "uncertainty"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: dict[str, object]) -> TargetObservation:
        if name == "read_case_evidence":
            if arguments:
                raise ValueError("read_case_evidence accepts no arguments")
            self.observed = True
            return TargetObservation({"evidence_ref": self.evidence_ref, "evidence": self.evidence})
        if name not in {"submit_evaluation_hypothesis", "submit_insufficient_evidence"}:
            raise ValueError(f"Unknown target tool: {name}")
        summary = arguments.get("summary")
        evidence_refs = arguments.get("evidence_refs")
        uncertainty = arguments.get("uncertainty")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        if not isinstance(uncertainty, str) or not uncertainty.strip():
            raise ValueError("uncertainty must be a non-empty string")
        if not isinstance(evidence_refs, list) or not all(isinstance(item, str) for item in evidence_refs):
            raise ValueError("evidence_refs must be a list of strings")
        if name == "submit_evaluation_hypothesis" and (not self.observed or self.evidence_ref not in evidence_refs):
            raise ValueError("A hypothesis must reference evidence observed in this session")
        kind = "hypothesis" if name == "submit_evaluation_hypothesis" else "insufficient_evidence"
        return TargetObservation(
            {
                "hypothesis": {
                    "kind": kind,
                    "summary": summary.strip(),
                    "evidence_refs": evidence_refs,
                    "uncertainty": uncertainty.strip(),
                }
            },
            terminal=True,
        )

    def restore(self, observations: list[dict[str, object]]) -> None:
        self.observed = any(item.get("evidence_ref") == self.evidence_ref for item in observations)
