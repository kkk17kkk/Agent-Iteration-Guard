import json
from pathlib import Path

from agentguard.integrations.lighttable_case import LIGHTTABLE_CONSTRAINT_CASE, LightTableConstraintVerifier
from agentguard.integrations.lighttable_profile import LIGHTTABLE_PROJECT_PROFILE
from agentguard.integrations.lighttable import verify_lighttable_trial
from agentguard.integrations.native_http import NativeHttpProcessRunner


def fixture_state():
    return {
        "user": [{"id": "default", "goal": "maintain", "dislikes": json.dumps(["鸡蛋"], ensure_ascii=False)}],
        "inventory_items": [{"id": "i1"}],
        "recommendation_history": [],
        "shopping_list_items": [],
        "feedback_events": [],
    }


def recipes():
    return [
        {"id": "r_001", "core_ingredients": ["番茄", "豆腐", "鸡蛋"]},
        {"id": "r_004", "core_ingredients": ["豆腐", "香菇"]},
    ]


def final_state():
    final = fixture_state()
    final["recommendation_history"] = [{"id": "rec_1"}]
    final["shopping_list_items"] = [{"id": "shop_1"}]
    return final


def test_verifier_accepts_allowed_recipe_and_declared_writes() -> None:
    status, criteria = verify_lighttable_trial(
        response_status=200,
        response={"plans": [{"dishes": [{"recipe_id": "r_004"}]}]},
        recipes=recipes(),
        initial=fixture_state(),
        final=final_state(),
        disliked_ingredients=["鸡蛋"],
        trace_complete=True,
        evidence_ref="fixture:valid",
    )
    assert status == "passed"
    assert all(item.status == "passed" for item in criteria)


def test_verifier_rejects_disliked_core_ingredient() -> None:
    status, criteria = verify_lighttable_trial(
        response_status=200,
        response={"plans": [{"dishes": [{"recipe_id": "r_001"}]}]},
        recipes=recipes(),
        initial=fixture_state(),
        final=final_state(),
        disliked_ingredients=["鸡蛋"],
        trace_complete=True,
        evidence_ref="fixture:wrong",
    )
    assert status == "failed"
    assert next(item for item in criteria if item.name == "constraint_adherence").status == "failed"


def test_verifier_rejects_prohibited_write() -> None:
    final = final_state()
    final["user"][0]["goal"] = "muscle_gain"
    status, criteria = verify_lighttable_trial(
        response_status=200,
        response={"plans": [{"dishes": [{"recipe_id": "r_004"}]}]},
        recipes=recipes(),
        initial=fixture_state(),
        final=final,
        disliked_ingredients=["鸡蛋"],
        trace_complete=True,
        evidence_ref="fixture:boundary",
    )
    assert status == "failed"
    assert next(item for item in criteria if item.name == "write_boundary").status == "failed"


def test_lighttable_is_composed_from_generic_runner_profile_case_and_verifier() -> None:
    runner = NativeHttpProcessRunner(Path("python"), LIGHTTABLE_PROJECT_PROFILE)
    assert runner.profile.profile_id == "lighttable-native-http-v1"
    assert LIGHTTABLE_CONSTRAINT_CASE.trial_operation.path == "/api/v1/recommend"
    assert LightTableConstraintVerifier.verifier_id == "lighttable-constraint-verifier-v1"
    generic_source = Path(__import__("agentguard.integrations.native_http", fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert "LIGHTTABLE" not in generic_source
    assert "鸡蛋" not in generic_source
