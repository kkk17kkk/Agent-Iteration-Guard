"""LLM-backed scenario generation for the generic Evolution Planner."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .domain import ProviderBinding
from .evaluation_planning import (
    EvaluationChange,
    EvaluationEvidenceRequirement,
    EvaluationScenario,
    ScenarioProvenance,
    EvaluationTarget,
    PairScenarioExpectedBehavior,
    scenario_hash_for,
)
from .interaction_evaluation import (
    InteractionRelationship,
    InteractionRelationshipProfile,
    InteractionHypothesisSource,
    PlanningCallMetadata,
    scenario_categories_for_relationship,
    validate_scenario_categories,
)
from .evolution_types import EvaluationDimension
from .provider_runtime import ProviderRuntimeError, ProviderTurn
from .scenario_contracts import ScenarioInputContract


class ScenarioGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: list[EvaluationScenario] = Field(min_length=3, max_length=5)


class PairGeneratedScenario(BaseModel):
    """Pair-specific LLM output matching the user-facing generation contract."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=100)
    category: Literal["complementary", "synergy", "conflict", "single_skill_dominant", "boundary"]
    user_prompt: str = Field(min_length=1, max_length=600)
    evaluation_goal: str = Field(min_length=1, max_length=300)
    expected_behavior: PairScenarioExpectedBehavior
    evidence_to_collect: list[str] = Field(min_length=1, max_length=8)
    input_contract: ScenarioInputContract = Field(default_factory=ScenarioInputContract.no_input)


class PairScenarioGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenarios: list[PairGeneratedScenario] = Field(min_length=3, max_length=5)


class EvaluationScenarioGenerator(Protocol):
    def generate(self, target: EvaluationTarget, change: EvaluationChange) -> list[EvaluationScenario]: ...


class InteractionScenarioGenerator(Protocol):
    def analyze_pair_relationship(
        self, target: EvaluationTarget, change: EvaluationChange
    ) -> InteractionRelationshipProfile: ...

    def generate_pair_scenarios(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        relationship: InteractionRelationshipProfile,
    ) -> list[EvaluationScenario]: ...


class EvaluationEvidenceRequirementsGenerator(Protocol):
    def generate(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        scenarios: list[EvaluationScenario],
    ) -> list[EvaluationEvidenceRequirement]: ...


class ScenarioEvidenceRequirementsGenerator:
    """Bind scenario-declared collection targets to all four evidence dimensions."""

    def generate(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        scenarios: list[EvaluationScenario],
        *,
        dimensions: list[EvaluationDimension] | None = None,
    ) -> list[EvaluationEvidenceRequirement]:
        del target, change
        required_dimensions = dimensions or ["trigger", "execution", "delivery", "boundary"]
        return [
            EvaluationEvidenceRequirement(
                requirement_id=f"evidence_requirement_{scenario.scenario_id}",
                scenario_id=scenario.scenario_id,
                dimensions=required_dimensions,
                evidence_to_collect=scenario.evidence_to_collect,
            )
            for scenario in scenarios
        ]


class LLMEvaluationScenarioGenerator:
    """Use one bounded control-plane call to design realistic user scenarios."""

    tool_name = "submit_evaluation_scenarios"
    max_format_retries = 2

    def __init__(self, provider, binding: ProviderBinding) -> None:
        self.provider = provider
        self.binding = binding

    def generate(self, target: EvaluationTarget, change: EvaluationChange) -> list[EvaluationScenario]:
        if self.binding.role != "control_plane":
            raise ValueError("Evaluation Scenario Generator requires a control_plane ProviderBinding.")
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "target": target.model_dump(mode="json"),
                        "change": change.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        # Tool-argument parsing and scenario-shape validation use the same
        # bounded, idempotent control-plane retry. Other provider failures
        # remain fail-fast and visible; no local scenarios are fabricated.
        for attempt in range(self.max_format_retries + 1):
            try:
                turn: ProviderTurn = self.provider.complete(messages, [self._tool_spec()])
                break
            except ProviderRuntimeError as error:
                retryable = "invalid Chat Completions payload" in str(error)
                if not retryable or attempt == self.max_format_retries:
                    raise
                messages = _retry_messages(messages, str(error))
                continue
        if len(turn.tool_calls) != 1 or turn.tool_calls[0].name != self.tool_name:
            raise ProviderRuntimeError("Evaluation Scenario Generator did not submit one scenario set.")
        # The provider response is available outside the provider-retry loop.
        # Validate it below, and retry semantic violations with explicit
        # corrective feedback rather than silently reducing coverage.
        for attempt in range(self.max_format_retries + 1):
            try:
                generated = ScenarioGenerationResult.model_validate(turn.tool_calls[0].arguments)
                categories = {scenario.category for scenario in generated.scenarios}
                required = {"normal", "constraint_conflict", "boundary"}
                if not required.issubset(categories):
                    raise ValueError(
                        "the scenario set must cover normal, constraint_conflict, and boundary categories"
                    )
                _validate_trace_event_types(target, generated.scenarios)
                _validate_fixture_declarations(target, generated.scenarios)
            except ValueError as error:
                message = f"Evaluation Scenario Generator returned invalid scenarios: {error}"
                if attempt == self.max_format_retries:
                    raise ProviderRuntimeError(message) from error
                messages = _retry_messages(messages, message)
                try:
                    turn = self.provider.complete(messages, [self._tool_spec()])
                except ProviderRuntimeError:
                    raise
                if len(turn.tool_calls) != 1 or turn.tool_calls[0].name != self.tool_name:
                    raise ProviderRuntimeError("Evaluation Scenario Generator did not submit one scenario set.")
                continue
            metadata = _planning_call_metadata(self.binding, turn)
            return [
                _freeze_scenario(
                    scenario.model_copy(update={"scenario_id": f"scenario_{index + 1}"}),
                    metadata=metadata,
                    hypothesis_hash=None,
                )
                for index, scenario in enumerate(generated.scenarios)
            ]
        raise AssertionError("unreachable")

    def analyze_pair_relationship(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
    ) -> InteractionRelationshipProfile:
        """Ask the Eval Engineering LLM to classify the interaction shape."""

        if self.binding.role != "control_plane":
            raise ValueError("Pair relationship analysis requires a control_plane ProviderBinding.")
        messages = [
            {"role": "system", "content": self._pair_relationship_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {"target": target.model_dump(mode="json"), "change": change.model_dump(mode="json")},
                    ensure_ascii=False,
                ),
            },
        ]
        for attempt in range(self.max_format_retries + 1):
            try:
                turn = self.provider.complete(messages, [self._pair_relationship_tool_spec()])
            except ProviderRuntimeError as error:
                if "invalid Chat Completions payload" not in str(error) or attempt == self.max_format_retries:
                    raise
                messages = _pair_relationship_retry_messages(messages, str(error))
                continue
            try:
                if len(turn.tool_calls) != 1 or turn.tool_calls[0].name != "submit_pair_relationship":
                    raise ValueError("Pair relationship analysis did not submit one relationship profile.")
                profile = InteractionRelationshipProfile.model_validate(turn.tool_calls[0].arguments)
                metadata = _planning_call_metadata(self.binding, turn)
                source = InteractionHypothesisSource(
                    inputs=["description", "responsibility", "dependency", "boundary"]
                )
                unsigned = profile.model_copy(
                    update={
                        "hypothesis_source": source,
                        "provider_metadata": metadata,
                        "hypothesis_hash": None,
                    }
                )
                hypothesis_hash = _hash_payload(
                    unsigned.model_dump(mode="json", exclude={"hypothesis_hash", "provider_metadata"})
                )
                return unsigned.model_copy(update={"hypothesis_hash": hypothesis_hash})
            except ValueError as error:
                if attempt == self.max_format_retries:
                    raise ProviderRuntimeError(f"Pair relationship analysis returned invalid output: {error}") from error
                messages = _pair_relationship_retry_messages(messages, str(error))
        raise AssertionError("unreachable")

    def generate_pair_scenarios(
        self,
        target: EvaluationTarget,
        change: EvaluationChange,
        *,
        relationship: InteractionRelationshipProfile,
    ) -> list[EvaluationScenario]:
        """Generate the Pair-specific scenario matrix from the chosen policy."""

        if self.binding.role != "control_plane":
            raise ValueError("Pair Scenario Generator requires a control_plane ProviderBinding.")
        required_categories = list(scenario_categories_for_relationship(relationship.relationship))
        messages = [
            {"role": "system", "content": self._pair_scenario_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "skill_a": target.component_members[0] if len(target.component_members) > 0 else "",
                        "skill_b": target.component_members[1] if len(target.component_members) > 1 else "",
                        "target": target.model_dump(mode="json"),
                        "change": change.model_dump(mode="json"),
                        "relationship": relationship.model_dump(mode="json"),
                        "required_categories": required_categories,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        for attempt in range(self.max_format_retries + 1):
            try:
                turn = self.provider.complete(messages, [self._pair_scenario_tool_spec()])
            except ProviderRuntimeError as error:
                if "invalid Chat Completions payload" not in str(error) or attempt == self.max_format_retries:
                    raise
                messages = _pair_retry_messages(messages, str(error))
                continue
            try:
                if len(turn.tool_calls) != 1 or turn.tool_calls[0].name != "submit_pair_evaluation_scenarios":
                    raise ValueError("Pair Scenario Generator did not submit one scenario set.")
                generated = PairScenarioGenerationResult.model_validate(turn.tool_calls[0].arguments)
                validate_scenario_categories(
                    relationship.relationship,
                    [scenario.category for scenario in generated.scenarios],
                )
                _validate_pair_condition_trace_contract(generated.scenarios)
                _validate_trace_event_types(target, generated.scenarios)
                _validate_fixture_declarations(target, generated.scenarios)
                _validate_pair_fixture_states(generated.scenarios)
                metadata = _planning_call_metadata(self.binding, turn)
                return [
                    _freeze_scenario(EvaluationScenario(
                        scenario_id=f"scenario_{index + 1}",
                        category=scenario.category,
                        user_prompt=scenario.user_prompt,
                        evaluation_goal=scenario.evaluation_goal,
                        expected_success_behavior=[scenario.expected_behavior.combined],
                        evidence_to_collect=scenario.evidence_to_collect,
                        expected_behavior=scenario.expected_behavior,
                        input_contract=scenario.input_contract,
                    ), metadata=metadata, hypothesis_hash=relationship.hypothesis_hash)
                    for index, scenario in enumerate(generated.scenarios)
                ]
            except (ValueError, TypeError) as error:
                if attempt == self.max_format_retries:
                    raise ProviderRuntimeError(f"Pair Scenario Generator returned invalid scenarios: {error}") from error
                messages = _pair_retry_messages(
                    messages,
                    f"{error}; return exactly these category counts: {required_categories}",
                )
        raise AssertionError("unreachable")

    @staticmethod
    def _system_prompt() -> str:
        return """You are an expert AI Agent evaluation planner.

Your task is to design realistic evaluation scenarios for testing whether an Agent capability provides meaningful product value.
You are NOT evaluating a run and must not predict its result. You are designing real user inputs that allow another system to evaluate:
1. whether the capability is correctly activated,
2. whether execution follows the intended behavior,
3. whether the final user-facing outcome improves,
4. whether the capability handles constraints and boundaries safely.

Given the component definition and Agent change definition, generate 3-5 diverse user task scenarios. Use the component's responsibility, expected behavior, user benefit, declared boundary, related constraints, and expected deliverable as the source of truth.

The target may include evidence-linked evaluation_knowledge from earlier completed evaluations. Use it only as prior coverage guidance for risks, dimensions, and scenario templates; it is not ground truth, must not predict a result, and must not replace the declared core coverage.

The target payload includes the project's declared Fixture Catalog and Trace event catalog. Use only its semantic fixture_id values in input_contract, follow any non-sensitive semantic_hints attached to those fixtures, and use only declared trace event types in ScenarioTraceContract; never invent a fixture ID, path, subject value, or event name. If no exact event type is needed, leave the event arrays empty and express the condition through provider_usage. If the catalog cannot support a requested boundary, declare the missing contract and let Readiness block it rather than substituting another input.

The scenarios must cover these purposes:

1. normal — the primary intended user task. Make it a realistic request with enough context for the Agent to perform the intended job and provide clear product value.
2. constraint_conflict — realistic requirements that conflict and require reasoning, such as a user goal conflicting with available resources, preferences conflicting with safety or component rules, or multiple component-specific constraints competing with each other.
3. boundary — a task near or outside the declared capability boundary. The Agent should need to decide whether to act, reduce scope, ask for clarification, or refuse unsupported or risky behavior.
4. robustness (optional) — the same underlying task expressed with different wording, incomplete information, or implicit intent.
5. interaction (optional) — the target capability used with a related capability, tool, memory, or instruction where cooperation or conflict is a real product question.

Each scenario must look like a real user message, not an artificial benchmark question or technical test instruction. Do not expose internal component names, change types, arm names, experiment IDs, verifier terminology, or implementation details in user_prompt.
For each scenario provide the evaluation purpose, observable successful behavior, and concrete evidence to collect across Trigger, Execution, Delivery, and Boundary. The evidence list should describe observations, not conclusions.
Also provide an input_contract. It must reference only semantic fixture IDs declared by the project and include scenario-specific trace expectations. Use an empty contract only when no external input state is required; boundary scenarios must declare the input state they test, including whether required data is present or absent. If a missing-input scenario should not call a model or tool, set provider_usage to forbidden and require the clarification/refusal event instead of inheriting a global provider requirement.
Do not make all scenarios minor paraphrases of one task. Maximize coverage of the component's actual product value while keeping every request plausible for a real user.
Never write the result of a run. Another evaluation executor will run the generated user prompts and collect immutable evidence.
Return JSON through the required tool only."""

    @staticmethod
    def _pair_relationship_system_prompt() -> str:
        return """You are the Eval Engineering planner for an Agent capability interaction evaluation.

Classify the relationship between the two declared capabilities using only their responsibilities,
expected behavior, product context, and boundaries. Do not judge a run and do not invent performance.

Use exactly one relationship:
- complementary: each capability contributes a distinct useful part of the same user job;
- competitive: the capabilities optimize competing goals or may over-trigger one another;
- validator_checker: one capability validates, checks, constrains, or reviews the other's output;
- uncertain: the declarations do not justify a stronger relationship.

Return a concise rationale and observable relationship signals. The result only selects a bounded scenario
matrix; it is not a product conclusion."""

    @classmethod
    def _pair_scenario_system_prompt(cls) -> str:
        return """You are an expert AI Agent evaluation planner.

Design realistic user task scenarios for evaluating whether two Agent Skills provide complementary value when combined.
Compare Skill A only, Skill B only, and Skill A + Skill B together. Do not evaluate individual skill quality and do not
predict a run result. Design tasks that reveal additional product value, information exchange, coordination, conflict,
unnecessary activation, latency/cost impact, and boundary behavior.

If the target includes evidence-linked evaluation_knowledge, use it only to prioritize coverage. It is prior experience, not a
ground-truth verdict for this pair and not evidence that the newly generated scenarios will produce the same result.

For every scenario return:
1. scenario_id;
2. category: complementary, synergy, conflict, single_skill_dominant, or boundary;
3. user_prompt: the exact message a real user would send, without internal component names or evaluator terminology;
4. evaluation_goal;
5. expected_behavior as an object with skill_a_only, skill_b_only, and combined product-language expectations;
6. evidence_to_collect covering capability activation, execution trace, intermediate outputs, final deliverable,
   latency/cost, and boundary behavior as applicable.
7. input_contract describing required or absent semantic fixture IDs from the target's declared Fixture Catalog and allowed trace behavior using only the target's declared Trace event catalog. The shared trace contract applies to every arm; use condition_traces keyed by a_only, b_only, and combined when required events or provider usage differ by matrix arm. Do not put a skill-specific completion event in the shared contract if that arm does not execute the skill. Interpret provider_usage per matrix arm, not as a copy of the target-wide Provider declaration: a deterministic checker or tool arm may be forbidden, while an arm that may or may not call a provider should be optional. Use required only when that arm's declared behavior necessarily calls the provider. Keep the contract compact: leave event arrays empty when provider_usage is sufficient, and use condition_traces only when a condition-specific event or provider rule is essential. Do not invent fixture IDs, file paths, subject values, event names, or silently use a substitute input.

Category intent:
- complementary: both capabilities are naturally needed for one useful user outcome;
- synergy: one capability's output should improve the other's decision or create a meaningful feedback loop;
- conflict: objectives or constraints compete and the Agent must resolve them without override or looping;
- single_skill_dominant: only one capability is genuinely needed and the other should not add needless work;
- boundary: information is incomplete, ambiguous, unsafe, or outside one capability's boundary.

For boundary scenarios, input_contract is mandatory. If the scenario tests missing data, declare an absent fixture requirement; do not describe missing data while providing a normal data fixture. Keep trace.required_event_types and trace.forbidden_event_types empty for ordinary Pair scenarios; the target trace is still collected. If a boundary or deterministic checker arm must not call a provider, use a compact condition_traces entry only for that arm to set provider_usage=forbidden. Do not set every arm to required merely because the registered target has a provider mapping; that mapping describes what the target can inject, not what every isolated capability must call.

Use the required category list supplied by Eval Engineering exactly, including duplicate categories when requested.
Do not turn simple sequential execution into a synergy claim. Keep each user_prompt under 45 words and other descriptions concise, avoid double-quote characters inside strings, and return RFC 8259 JSON through the required tool only."""

    @staticmethod
    def _pair_relationship_tool_spec() -> dict[str, object]:
        schema = InteractionRelationshipProfile.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": "submit_pair_relationship",
                "description": "Submit only the relationship classification for two Agent capabilities.",
                "parameters": schema,
            },
        }

    @staticmethod
    def _pair_scenario_tool_spec() -> dict[str, object]:
        schema = PairScenarioGenerationResult.model_json_schema()
        pair_schema = schema.get("$defs", {}).get("PairGeneratedScenario")
        if isinstance(pair_schema, dict):
            required = pair_schema.setdefault("required", [])
            if "input_contract" not in required:
                required.append("input_contract")
        schema["description"] = "Pair-specific realistic scenarios with per-arm expected behavior."
        return {
            "type": "function",
            "function": {
                "name": "submit_pair_evaluation_scenarios",
                "description": "Submit only the required Skill Pair scenario set.",
                "parameters": schema,
            },
        }

    @classmethod
    def _tool_spec(cls) -> dict[str, object]:
        schema = ScenarioGenerationResult.model_json_schema()
        schema["description"] = "Three to five realistic, diverse user task scenarios for one Agent change evaluation."
        return {
            "type": "function",
            "function": {
                "name": cls.tool_name,
                "description": "Submit only the evaluation scenario set.",
                "parameters": schema,
            },
        }


def _retry_messages(messages: list[dict[str, str]], error: str) -> list[dict[str, str]]:
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Correct the previous scenario submission and call the required tool again. "
                "Return 3-5 diverse scenarios, including normal, constraint_conflict, and boundary. "
                f"Validation feedback: {error}"
            ),
        },
    ]


def _planning_call_metadata(binding: ProviderBinding, turn: ProviderTurn) -> PlanningCallMetadata:
    return PlanningCallMetadata(
        provider=binding.provider,
        model=binding.model,
        request_id=turn.request_id,
        request_fingerprint=turn.request_fingerprint,
        response_fingerprint=turn.response_fingerprint,
    )


def _hash_payload(payload: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _freeze_scenario(
    scenario: EvaluationScenario,
    *,
    metadata: PlanningCallMetadata,
    hypothesis_hash: str | None,
) -> EvaluationScenario:
    scenario_hash = scenario_hash_for(scenario.model_dump(mode="json"))
    provenance = ScenarioProvenance(
        hypothesis_source="eval_engineering.relationship_hypothesis" if hypothesis_hash else "eval_engineering.scenario_generation",
        relationship_hypothesis_hash=hypothesis_hash,
        provider_metadata=metadata,
        scenario_hash=scenario_hash,
    )
    return scenario.model_copy(update={"scenario_hash": scenario_hash, "scenario_provenance": provenance})


def _pair_retry_messages(messages: list[dict[str, str]], error: str) -> list[dict[str, str]]:
    trace_instruction = (
        " For Pair trace contracts, shared required_event_types and forbidden_event_types may contain only "
        "events emitted by every arm, such as trial_started/trial_completed. If any arm-specific event is needed, "
        "move it into condition_traces with all three keys a_only, b_only, and combined; the shared event arrays "
        "must then be empty. The shortest valid choice is empty event arrays and provider_usage only."
        if "condition_traces" in error or "trace event" in error
        else ""
    )
    fixture_instruction = (
        " Match every input_contract requirement to the catalog availability exactly. Never mark a present fixture "
        "as absent; use the declared empty/missing fixture for a boundary state and keep declared profile/context "
        "fixtures present. Non-boundary scenarios must not require absent fixtures."
        if "fixture" in error
        else ""
    )
    provider_instruction = (
        " Set provider_usage per matrix arm: do not inherit required from the target-wide provider mapping; "
        "a deterministic checker/tool arm should be forbidden and an uncertain arm should be optional."
    )
    return [
        *messages,
        {
            "role": "user",
            "content": (
        "Correct the previous interaction-evaluation submission and call the required tool again. "
                "Return the requested relationship profile or the exact Pair scenario category matrix; do not add "
                "single-skill evaluation categories that were not requested. All function arguments must be valid "
                "RFC 8259 JSON: quote every string and enum value, use double quotes, and do not use trailing commas. "
                "Do not place unescaped double-quote characters inside string values; use apostrophes instead. "
                "Keep descriptions concise so the complete argument object is closed. "
                + trace_instruction
                + fixture_instruction
                + provider_instruction
                + " "
                f"Validation feedback: {error}"
            ),
        },
    ]


def _pair_relationship_retry_messages(messages: list[dict[str, str]], error: str) -> list[dict[str, str]]:
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Correct the previous relationship submission and call submit_pair_relationship again. "
                "Return exactly one relationship classification with rationale and signals. "
                f"Validation feedback: {error}"
            ),
        },
    ]


def _validate_trace_event_types(target: EvaluationTarget, scenarios: list[object]) -> None:
    """Reject planner-created trace names that the registered target cannot emit."""

    declared = set(target.trace_event_types)
    if not declared:
        return
    for scenario in scenarios:
        contract = getattr(scenario, "input_contract", None)
        if contract is None:
            continue
        requested = set(contract.trace.required_event_types) | set(contract.trace.forbidden_event_types)
        for trace in contract.condition_traces.values():
            requested.update(trace.required_event_types)
            requested.update(trace.forbidden_event_types)
        invalid = sorted(requested - declared)
        if invalid:
            raise ValueError(
                f"Scenario {getattr(scenario, 'scenario_id', '<unknown>')} uses undeclared trace event types: {invalid}."
            )


def _validate_fixture_declarations(target: EvaluationTarget, scenarios: list[object]) -> None:
    """Reject generated fixture states that contradict Project Intelligence."""

    catalog = target.fixture_catalog
    for scenario in scenarios:
        contract = getattr(scenario, "input_contract", None)
        if contract is None:
            continue
        for requirement in contract.requirements:
            fixture = catalog.get(requirement.fixture_id)
            if fixture is None:
                raise ValueError(
                    f"Scenario {getattr(scenario, 'scenario_id', '<unknown>')} references undeclared fixture "
                    f"{requirement.fixture_id!r}."
                )
            if fixture.availability != requirement.availability:
                raise ValueError(
                    f"Scenario {getattr(scenario, 'scenario_id', '<unknown>')} requires fixture "
                    f"{requirement.fixture_id!r} as {requirement.availability}, but the catalog declares "
                    f"{fixture.availability}."
                )


def _validate_pair_fixture_states(scenarios: list[object]) -> None:
    """Keep missing-input states in the explicit Pair boundary category."""

    for scenario in scenarios:
        if getattr(scenario, "category", None) == "boundary":
            continue
        contract = getattr(scenario, "input_contract", None)
        if contract is None:
            continue
        absent = [item.fixture_id for item in contract.requirements if item.availability == "absent"]
        if absent:
            raise ValueError(
                f"Pair scenario {getattr(scenario, 'scenario_id', '<unknown>')} uses absent fixtures {absent}; "
                "reserve missing-input states for the boundary category."
            )


def _validate_pair_condition_trace_contract(scenarios: list[object]) -> None:
    """Keep Pair arm evidence requirements from leaking across A/B/combined."""

    expected_conditions = {"a_only", "b_only", "combined"}
    for scenario in scenarios:
        contract = getattr(scenario, "input_contract", None)
        if contract is None:
            continue
        shared_events = set(contract.trace.required_event_types) | set(contract.trace.forbidden_event_types)
        if not shared_events:
            continue
        global_events = {event for event in shared_events if event.startswith(("trial_", "provider_"))}
        arm_events = shared_events.difference(global_events)
        if not arm_events:
            continue
        if set(contract.condition_traces) != expected_conditions:
            raise ValueError(
                f"Pair scenario {getattr(scenario, 'scenario_id', '<unknown>')} has arm-specific trace events "
                "but condition_traces must declare a_only, b_only, and combined."
            )
        raise ValueError(
            f"Pair scenario {getattr(scenario, 'scenario_id', '<unknown>')} must leave shared trace event arrays empty "
            "when using condition_traces; move required and forbidden events into each arm."
        )


__all__ = [
    "EvaluationEvidenceRequirementsGenerator",
    "EvaluationScenarioGenerator",
    "InteractionScenarioGenerator",
    "LLMEvaluationScenarioGenerator",
    "PairGeneratedScenario",
    "PairScenarioGenerationResult",
    "ScenarioEvidenceRequirementsGenerator",
    "ScenarioGenerationResult",
]
