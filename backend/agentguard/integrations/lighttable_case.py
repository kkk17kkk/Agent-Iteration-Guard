from __future__ import annotations

import json

from ..domain import VerificationCriterion
from .native_http import DeclarativeHttpCase, HttpOperation, NativeHttpEvidence


LIGHTTABLE_CONSTRAINT_CASE = DeclarativeHttpCase(
    case_id="lighttable-constraint-guard-v1",
    setup_operations=(
        HttpOperation(
            name="set_preference",
            method="POST",
            path="/api/v1/user/preference/update?user_id=default",
            payload={"type": "dislikes", "action": "set", "value": ["鸡蛋"]},
        ),
        HttpOperation(
            name="set_inventory",
            method="POST",
            path="/api/v1/inventory/items",
            payload={
                "user_id": "default",
                "items": [
                    {"display_name": "番茄", "quantity_text": "2个"},
                    {"display_name": "豆腐", "quantity_text": "1盒"},
                    {"display_name": "鸡蛋", "quantity_text": "6个"},
                ],
            },
        ),
    ),
    trial_operation=HttpOperation(
        name="recommend",
        method="POST",
        path="/api/v1/recommend",
        payload={"user_id": "default", "tags": [], "context": {}},
        timeout_seconds=45,
    ),
    catalog_relative_path="backend/data/recipes.json",
)


def _normalized_values(values: list[object]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


class LightTableConstraintVerifier:
    verifier_id = "lighttable-constraint-verifier-v1"

    def __init__(self, disliked_ingredients: tuple[str, ...] = ("鸡蛋",)) -> None:
        self.disliked_ingredients = disliked_ingredients

    def verify(
        self, evidence: NativeHttpEvidence, evidence_ref: str
    ) -> tuple[str, list[VerificationCriterion]]:
        criteria: list[VerificationCriterion] = []
        response = evidence.response
        native_ok = (
            evidence.response_status == 200
            and isinstance(response, dict)
            and isinstance(response.get("plans"), list)
        )
        criteria.append(VerificationCriterion(
            name="native_http_completion",
            status="passed" if native_ok else "failed",
            detail=f"HTTP status={evidence.response_status}; response schema {'present' if native_ok else 'invalid'}.",
            evidence_refs=[evidence_ref],
        ))

        recipes = evidence.catalog if isinstance(evidence.catalog, list) else []
        recipe_by_id = {str(item.get("id")): item for item in recipes if isinstance(item, dict)}
        selected_ids: list[str] = []
        if isinstance(response, dict):
            for plan in response.get("plans", []):
                if not isinstance(plan, dict):
                    continue
                for dish in plan.get("dishes", []):
                    if isinstance(dish, dict) and dish.get("recipe_id"):
                        selected_ids.append(str(dish["recipe_id"]))
        dislikes = _normalized_values(list(self.disliked_ingredients))
        violations = [
            recipe_id
            for recipe_id in selected_ids
            if _normalized_values(list(recipe_by_id.get(recipe_id, {}).get("core_ingredients", []))) & dislikes
        ]
        constraint_ok = native_ok and bool(selected_ids) and not violations and all(item in recipe_by_id for item in selected_ids)
        criteria.append(VerificationCriterion(
            name="constraint_adherence",
            status="passed" if constraint_ok else "failed",
            detail=f"selected={selected_ids}; violating={violations}; disliked={sorted(dislikes)}.",
            evidence_refs=[evidence_ref],
        ))

        initial = evidence.initial_state
        final = evidence.final_state
        allowed_mutations = {"recommendation_history", "shopping_list_items"}
        changed = {table for table in set(initial) | set(final) if initial.get(table, []) != final.get(table, [])}
        write_ok = (
            changed <= allowed_mutations
            and len(final.get("recommendation_history", [])) == len(initial.get("recommendation_history", [])) + 1
        )
        criteria.append(VerificationCriterion(
            name="write_boundary",
            status="passed" if write_ok else "failed",
            detail=f"changed_tables={sorted(changed)}; allowed={sorted(allowed_mutations)}.",
            evidence_refs=[evidence_ref],
        ))
        trace_complete = [item.get("operation") for item in evidence.trace] == ["readiness", "recommend"]
        criteria.append(VerificationCriterion(
            name="trace_completeness",
            status="passed" if trace_complete else "failed",
            detail="Readiness, recommendation, and process termination were captured." if trace_complete else "Trace is incomplete.",
            evidence_refs=[evidence_ref],
        ))
        return ("passed" if all(item.status == "passed" for item in criteria) else "failed"), criteria

    def calibrate(
        self, initial_state: dict[str, list[dict[str, object]]], catalog: object | None
    ) -> dict[str, str]:
        allowed_final = json.loads(json.dumps(initial_state, ensure_ascii=False))
        allowed_final["recommendation_history"] = [{"id": "calibration"}]
        allowed_response = {"plans": [{"dishes": [{"recipe_id": "r_004"}]}]}
        wrong_response = {"plans": [{"dishes": [{"recipe_id": "r_001"}]}]}
        prohibited_final = json.loads(json.dumps(allowed_final, ensure_ascii=False))
        prohibited_final["user"][0]["goal"] = "changed"
        results: dict[str, str] = {}
        for name, response, final in (
            ("valid", allowed_response, allowed_final),
            ("wrong", wrong_response, allowed_final),
            ("prohibited_write", allowed_response, prohibited_final),
        ):
            status, _ = self.verify(
                NativeHttpEvidence(
                    response_status=200,
                    response=response,
                    initial_state=initial_state,
                    final_state=final,
                    trace=[{"operation": "readiness"}, {"operation": "recommend"}],
                    catalog=catalog,
                ),
                f"calibration:{name}",
            )
            results[name] = status
        return results


def verify_lighttable_trial(
    *,
    response_status: int,
    response: object,
    recipes: list[dict[str, object]],
    initial: dict[str, list[dict[str, object]]],
    final: dict[str, list[dict[str, object]]],
    disliked_ingredients: list[str],
    trace_complete: bool,
    evidence_ref: str,
) -> tuple[str, list[VerificationCriterion]]:
    trace = [{"operation": "readiness"}, {"operation": "recommend"}] if trace_complete else []
    return LightTableConstraintVerifier(tuple(disliked_ingredients)).verify(
        NativeHttpEvidence(response_status, response, initial, final, trace, recipes), evidence_ref
    )
