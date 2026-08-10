"""Independent Oracle for a declared interaction and trace contract.

The target process is untrusted.  This verifier receives the persisted request
and observation over stdin, evaluates only declarations present in that
request, and never receives the target provider credential.
"""

from __future__ import annotations

import json
import sys
from typing import Any


VERIFIER_ID = "declared-interaction-contract-oracle-v1"


def _assertion(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def _trace_contract(request: dict[str, Any]) -> dict[str, Any]:
    input_contract = request.get("input_contract") or {}
    condition = request.get("condition_kind")
    return (input_contract.get("condition_traces") or {}).get(condition) or input_contract.get("trace") or {}


def verify(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload["request"]
    observation = payload["observation"]
    output = observation.get("output")
    events = observation.get("trace") or []
    event_types = [event.get("event_type") for event in events]
    contract = _trace_contract(request)
    required = set(contract.get("required_event_types") or [])
    forbidden = set(contract.get("forbidden_event_types") or [])
    provider_usage = contract.get("provider_usage", "optional")
    provider_calls = event_types.count("provider_call_completed")

    assertions = [
        _assertion(
            "target_completed",
            observation.get("observations", {}).get("target_completed") is True,
            "The target must explicitly record completion.",
        ),
        _assertion(
            "structured_output",
            isinstance(output, dict) and bool(output),
            "The target must return a non-empty structured output object.",
        ),
        _assertion(
            "required_trace_events",
            required.issubset(set(event_types)),
            f"Required={sorted(required)}; observed={sorted(set(event_types))}.",
        ),
        _assertion(
            "forbidden_trace_events",
            forbidden.isdisjoint(set(event_types)),
            f"Forbidden={sorted(forbidden)}; observed={sorted(set(event_types))}.",
        ),
    ]
    if provider_usage == "required":
        assertions.append(_assertion("provider_usage", provider_calls > 0, "A provider call is required by the scenario."))
    elif provider_usage == "forbidden":
        assertions.append(_assertion("provider_usage", provider_calls == 0, "Provider calls are forbidden by the scenario."))
    else:
        assertions.append(
            _assertion(
                "provider_usage_observed",
                True,
                f"Provider usage is optional; observed {provider_calls} completed call(s).",
            )
        )

    passed = all(item["status"] == "passed" for item in assertions)
    scenario_id = str(request["scenario_id"])
    condition = str(request["condition_kind"])
    return {
        "schema_version": "aig.independent-oracle-result.v1",
        "verifier_id": VERIFIER_ID,
        "oracle_type": "rule_based",
        "oracle_version": "1.0",
        "validation_input": {
            "scenario_id": scenario_id,
            "category": request["category"],
            "condition_kind": condition,
            "required_event_types": sorted(required),
            "forbidden_event_types": sorted(forbidden),
            "provider_usage": provider_usage,
            "provider_call_count": provider_calls,
        },
        "status": "verified",
        "outcome": "passed" if passed else "failed",
        "assertions": assertions,
        "verification_scopes": ["structural", "behavioral"],
        "scope_limitations": [
            "This Oracle does not verify domain correctness or external factual correctness."
        ],
        "failure_types_evaluated": [],
        "evidence_refs": [f"oracle:{VERIFIER_ID}:{scenario_id}:{condition}"],
        "summary": f"Declared interaction contract: {sum(item['status'] == 'passed' for item in assertions)}/{len(assertions)} assertions passed.",
    }


if __name__ == "__main__":
    print(json.dumps(verify(json.load(sys.stdin)), ensure_ascii=False))
