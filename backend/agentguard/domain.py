from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


RiskLevel = Literal["low", "medium", "high", "critical"]
OracleKind = Literal["state_assertion", "tool_trace", "test", "path_policy"]
HarnessStatus = Literal[
    "created", "planning", "planned", "running", "verifying", "deciding",
    "awaiting_evidence", "blocked", "recorded", "failed",
]
AgentRole = Literal["intake", "planner", "runner", "executor", "verifier", "gatekeeper"]
HandoffKind = Literal["evaluation_scope", "evaluation_plan", "evidence_request", "release_hold", "gate_block"]
EvidenceLevel = Literal["verified", "supported", "inferred", "unresolved"]
ChangeKind = Literal["permission_changed", "tool_capability_expanded", "skill_changed", "prompt_changed"]
FailureType = Literal["permission_violation"]
LLMAssistanceKind = Literal["failure_explanation", "requirement_mapping"]
WorkStatus = Literal["planned", "completed", "blocked"]
RunEventType = Literal[
    "RUN_CREATED", "PLAN_CREATED", "TRIALS_COMPLETED", "VERIFICATION_COMPLETED",
    "FINDING_CREATED", "RELEASE_DECIDED", "RUN_RECORDED", "LLM_ASSISTANCE_RECORDED",
    "CHECKPOINT_COMMITTED", "OPERATION_STARTED", "OPERATION_COMPLETED", "FAILURE_TICKET_CREATED",
    "TRIAL_STARTED", "TRIAL_COMPLETED", "METRICS_RECORDED", "REPLAY_RECORDED", "ABLATION_RECORDED",
    "BATCH_CREATED", "BATCH_ITEM_COMPLETED", "BATCH_CHECKPOINT_COMMITTED", "BATCH_RECORDED",
    "ACTION_PLANNED", "ACTION_COMPLETED", "OBSERVATION_RECORDED", "STAGE2_CHECKPOINT_COMMITTED",
]
MutationKind = Literal["prompt", "skill", "tool_schema", "permission", "workflow"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ident(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Product(BaseModel):
    product_id: str = Field(default_factory=lambda: ident("product"))
    name: str = Field(min_length=1)
    description: str = ""
    current_version_id: str | None = None
    created_at: str = Field(default_factory=now)


class Version(BaseModel):
    version_id: str = Field(default_factory=lambda: ident("version"))
    product_id: str
    label: str = Field(min_length=1)
    source_ref: str = "manual"
    created_at: str = Field(default_factory=now)


class FileAgentManifest(BaseModel):
    agent_name: str
    skill: str
    requested_write_paths: list[str]
    tool_capabilities: list[str]
    instructions: str = ""
    cleanup_temporary_files: bool = False


class ComponentSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: ident("snapshot"))
    product_id: str
    version_id: str
    component_type: str = "file_agent_manifest"
    name: str = "agent_manifest.json"
    fingerprint: str
    source_ref: str
    manifest: FileAgentManifest
    created_at: str = Field(default_factory=now)


class Change(BaseModel):
    change_id: str = Field(default_factory=lambda: ident("change"))
    kind: ChangeKind
    risk: RiskLevel
    before: list[str] | str
    after: list[str] | str


class ChangeSet(BaseModel):
    changeset_id: str = Field(default_factory=lambda: ident("changeset"))
    product_id: str
    baseline_version_id: str
    candidate_version_id: str
    baseline_snapshot: ComponentSnapshot
    candidate_snapshot: ComponentSnapshot
    changes: list[Change]
    created_at: str = Field(default_factory=now)


class Requirement(BaseModel):
    requirement_id: str = Field(default_factory=lambda: ident("req"))
    product_id: str
    title: str
    description: str = ""
    risk: RiskLevel = "medium"


class Capability(BaseModel):
    capability_id: str = Field(default_factory=lambda: ident("cap"))
    product_id: str
    name: str
    requirement_ids: list[str] = Field(default_factory=list)
    risk: RiskLevel = "medium"


class EvalCase(BaseModel):
    eval_case_id: str = Field(default_factory=lambda: ident("eval"))
    product_id: str
    name: str
    capability_ids: list[str] = Field(default_factory=list)
    oracle_kind: OracleKind = "state_assertion"


class EvalPlanItem(BaseModel):
    eval_case_id: str
    selected: bool
    reason: str
    risk: RiskLevel
    oracle_kind: OracleKind


class EvalPlan(BaseModel):
    eval_plan_id: str = Field(default_factory=lambda: ident("plan"))
    product_id: str
    changeset_id: str
    items: list[EvalPlanItem]
    created_at: str = Field(default_factory=now)

    @property
    def selected_case_ids(self) -> list[str]:
        return [item.eval_case_id for item in self.items if item.selected]


class WorkItem(BaseModel):
    work_item_id: str = Field(default_factory=lambda: ident("work"))
    harness_run_id: str
    eval_case_id: str
    owner: Literal["runner"] = "runner"
    objective: str
    input_artifact_ids: list[str]
    expected_output_type: Literal["execution_result"] = "execution_result"
    acceptance_criteria: str
    allowed_tools: list[str] = Field(default_factory=lambda: ["write_file"])
    status: WorkStatus = "planned"


class ToolCall(BaseModel):
    tool_name: Literal["read_file", "write_file", "delete_file"] = "write_file"
    path: str
    policy_decision: Literal["allowed", "denied", "unauthorized"]
    arguments_hash: str = ""
    side_effect_class: Literal["read", "write", "delete"] = "write"


class ExecutionResult(BaseModel):
    execution_id: str = Field(default_factory=lambda: ident("execution"))
    harness_run_id: str
    work_item_id: str
    status: Literal["completed", "runner_failed"] = "completed"
    tool_calls: list[ToolCall]
    environment_ref: str = "fake-file-agent-v1"
    operation_id: str | None = None
    output_fingerprint: str | None = None
    runner_trace_id: str | None = None
    external_cost_usd: float = Field(default=0.0, ge=0)
    created_at: str = Field(default_factory=now)


class VerificationResult(BaseModel):
    verification_id: str = Field(default_factory=lambda: ident("verification"))
    harness_run_id: str
    execution_id: str
    oracle_id: Literal["path_policy"] = "path_policy"
    expected: str
    observed: str
    passed: bool
    severity: RiskLevel = "low"
    failure_class: str | None = None
    failure_type: FailureType | None = None
    created_at: str = Field(default_factory=now)


class HarnessRun(BaseModel):
    harness_run_id: str = Field(default_factory=lambda: ident("harness"))
    product_id: str
    version_id: str
    baseline_version_id: str | None = None
    candidate_version_id: str | None = None
    changeset_id: str | None = None
    thread_id: str | None = None
    eval_case_ids: list[str] = Field(default_factory=list)
    status: HarnessStatus = "created"
    blocked_reason: str | None = None
    created_at: str = Field(default_factory=now)


class RunEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: ident("event"))
    harness_run_id: str
    sequence: int
    event_type: RunEventType
    artifact_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class Handoff(BaseModel):
    handoff_id: str = Field(default_factory=lambda: ident("handoff"))
    harness_run_id: str
    from_role: AgentRole
    to_role: AgentRole
    kind: HandoffKind
    summary: str
    eval_case_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: ident("evidence"))
    harness_run_id: str
    eval_case_id: str | None = None
    source: Literal["runner", "oracle", "verifier"]
    level: EvidenceLevel
    summary: str
    execution_id: str | None = None
    verification_id: str | None = None
    created_at: str = Field(default_factory=now)


class Finding(BaseModel):
    finding_id: str = Field(default_factory=lambda: ident("finding"))
    product_id: str
    harness_run_id: str
    title: str
    evidence_level: EvidenceLevel = "unresolved"
    evidence_ids: list[str] = Field(default_factory=list)
    severity: RiskLevel = "medium"


class ReleaseDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: ident("decision"))
    product_id: str
    version_id: str
    harness_run_id: str
    status: Literal["pending", "ready", "blocked"] = "pending"
    rationale: str
    finding_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class RunCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: ident("checkpoint"))
    harness_run_id: str
    next_step: Literal["plan", "execute", "verify", "gate", "record", "completed"]
    event_sequence: int
    created_at: str = Field(default_factory=now)


class Operation(BaseModel):
    operation_id: str
    harness_run_id: str
    work_item_id: str
    input_hash: str
    status: Literal["running", "completed", "interrupted"] = "running"
    execution_id: str | None = None
    tool_call_count: int = 0
    created_at: str = Field(default_factory=now)


class ToolPolicy(BaseModel):
    policy_id: str = Field(default_factory=lambda: ident("policy"))
    product_id: str
    harness_run_id: str
    allowed_read_paths: list[str]
    allowed_write_paths: list[str]
    allow_delete: bool = False
    sandbox_kind: Literal["temporary_directory"] = "temporary_directory"
    created_at: str = Field(default_factory=now)


Stage2ActionKind = Literal["read_file", "write_file", "delete_file", "finish"]
Stage2ActionStatus = Literal["planned", "running", "completed", "blocked", "failed"]
Stage2RunStatus = Literal["created", "running", "blocked", "finished", "failed", "budget_exhausted"]


class AgentAction(BaseModel):
    """The only command a Stage 2 model may send to the Harness."""

    action_id: str = Field(default_factory=lambda: ident("action"))
    agent_run_id: str
    step: int = Field(ge=1)
    kind: Stage2ActionKind
    path: str | None = None
    content: str | None = None
    expected_observation_fingerprint: str | None = None
    approval_required: bool = False
    approval_token: str | None = None
    status: Stage2ActionStatus = "planned"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    result: str | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now)


class AgentObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: ident("observation"))
    agent_run_id: str
    step: int = Field(ge=0)
    state_fingerprint: str
    files: dict[str, str] = Field(default_factory=dict)
    changed_paths: list[str] = Field(default_factory=list)
    last_action_id: str | None = None
    tool_result: str | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now)


class Stage2AgentRun(BaseModel):
    agent_run_id: str = Field(default_factory=lambda: ident("stage2"))
    product_id: str
    harness_run_id: str
    work_item_id: str
    stage1_batch_id: str
    task_kind: str
    task: dict[str, object] = Field(default_factory=dict)
    tool_manifest: dict[str, object] = Field(default_factory=dict)
    policy_id: str
    sandbox_path: str
    model_kind: Literal["deterministic", "fake", "json"] = "deterministic"
    status: Stage2RunStatus = "created"
    step_count: int = Field(default=0, ge=0)
    max_steps: int = Field(default=8, ge=1, le=32)
    action_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    checkpoint_id: str | None = None
    terminal_reason: str | None = None
    duplicate_side_effect_count: int = Field(default=0, ge=0)
    resumed_from_checkpoint: bool = False
    created_at: str = Field(default_factory=now)


class Stage2Checkpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: ident("stage2_checkpoint"))
    agent_run_id: str
    next_step: int = Field(default=1, ge=1)
    pending_action_id: str | None = None
    observation_id: str | None = None
    committed_action_ids: list[str] = Field(default_factory=list)
    state_fingerprint: str
    created_at: str = Field(default_factory=now)


class Stage2Operation(BaseModel):
    operation_id: str = Field(default_factory=lambda: ident("stage2_operation"))
    agent_run_id: str
    action_id: str
    side_effect_fingerprint: str = ""
    status: Literal["running", "completed"] = "running"
    side_effect_applied: bool = False
    duplicate: bool = False
    created_at: str = Field(default_factory=now)


class Stage2OracleResult(BaseModel):
    oracle_result_id: str = Field(default_factory=lambda: ident("stage2_oracle"))
    agent_run_id: str
    passed: bool
    expected: str
    observed: str
    action_order: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    stale_observation_rejected: bool = True
    created_at: str = Field(default_factory=now)


class Stage2GateCriterion(BaseModel):
    criterion: str
    status: Literal["verified", "partial", "failed", "missing"]
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    supporting_test: str
    failure_reason: str | None = None


class Stage2Gate(BaseModel):
    stage1_batch_id: str
    status: Literal["PASS", "PASS_WITH_LIMITATIONS", "BLOCKED"]
    criteria: list[Stage2GateCriterion] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class FailureTicket(BaseModel):
    ticket_id: str = Field(default_factory=lambda: ident("ticket"))
    product_id: str
    harness_run_id: str
    finding_id: str
    evidence_ids: list[str]
    title: str
    reproduction: str
    recommended_action: str
    created_at: str = Field(default_factory=now)


class TrialSpec(BaseModel):
    trial_id: str = Field(default_factory=lambda: ident("trial"))
    harness_run_id: str
    work_item_id: str
    ordinal: int = Field(ge=1)
    kind: Literal["evaluation", "replay", "ablation"] = "evaluation"
    cleanup_attempt: bool | None
    decision_source: Literal["fixture", "external_model"] = "fixture"
    candidate_fingerprint: str
    policy_fingerprint: str
    environment_fingerprint: str
    seed: int
    created_at: str = Field(default_factory=now)


class TrialResult(BaseModel):
    trial_result_id: str = Field(default_factory=lambda: ident("trial_result"))
    harness_run_id: str
    trial_id: str
    kind: Literal["evaluation", "replay", "ablation"] = "evaluation"
    execution_id: str
    verification_id: str
    evidence_id: str
    passed: bool
    latency_ms: float = Field(ge=0)
    cost_usd: float = Field(ge=0)
    trace_fingerprint: str
    created_at: str = Field(default_factory=now)


class TrialMetrics(BaseModel):
    metrics_id: str = Field(default_factory=lambda: ident("metrics"))
    harness_run_id: str
    trial_result_ids: list[str]
    trial_count: int = Field(ge=1)
    success_rate: float = Field(ge=0, le=1)
    variance: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    created_at: str = Field(default_factory=now)


class ReplaySpec(BaseModel):
    replay_spec_id: str = Field(default_factory=lambda: ident("replay"))
    source_trial_result_id: str
    harness_run_id: str
    candidate_fingerprint: str
    policy_fingerprint: str
    environment_fingerprint: str
    cleanup_attempt: bool
    seed: int
    source_trace_fingerprint: str
    created_at: str = Field(default_factory=now)


class ReplayResult(BaseModel):
    replay_result_id: str = Field(default_factory=lambda: ident("replay_result"))
    replay_spec_id: str
    execution_id: str
    verification_id: str
    trace_fingerprint: str
    reproduced: bool
    created_at: str = Field(default_factory=now)


class AblationReport(BaseModel):
    ablation_id: str = Field(default_factory=lambda: ident("ablation"))
    source_trial_result_id: str
    harness_run_id: str
    changed_field: Literal["cleanup_attempt"] = "cleanup_attempt"
    before_value: bool
    after_value: bool
    before_verification_id: str
    after_verification_id: str
    evidence_delta: str
    created_at: str = Field(default_factory=now)


class MutationPair(BaseModel):
    pair_id: str = Field(default_factory=lambda: ident("mutation"))
    product_id: str
    mutation_kind: MutationKind
    ordinal: int = Field(ge=1)
    baseline_version_id: str
    candidate_version_id: str
    expected_failure_type: FailureType | None = None
    expected_release: Literal["ready", "blocked"]
    valid: bool = True
    rejection_reason: str | None = None
    created_at: str = Field(default_factory=now)


class BatchRun(BaseModel):
    batch_id: str = Field(default_factory=lambda: ident("batch"))
    product_id: str
    pair_ids: list[str]
    trials_per_pair: int = Field(ge=3)
    max_workers: int = Field(ge=1, le=8)
    max_total_cost_usd: float = Field(ge=0)
    status: Literal["created", "running", "interrupted", "completed", "failed"] = "created"
    next_pair_index: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=now)


class BatchItem(BaseModel):
    batch_item_id: str = Field(default_factory=lambda: ident("batch_item"))
    batch_id: str
    pair_id: str
    ordinal: int = Field(ge=1)
    cache_key: str
    status: Literal["pending", "running", "completed", "cached", "failed"] = "pending"
    harness_run_id: str | None = None
    created_at: str = Field(default_factory=now)


class BatchCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: ident("batch_checkpoint"))
    batch_id: str
    next_pair_index: int = Field(ge=0)
    completed_item_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class TrialCacheEntry(BaseModel):
    cache_key: str
    harness_run_id: str
    candidate_fingerprint: str
    policy_fingerprint: str
    environment_fingerprint: str
    trials_per_pair: int = Field(ge=3)
    created_at: str = Field(default_factory=now)


class ProviderUsage(BaseModel):
    usage_id: str = Field(default_factory=lambda: ident("usage"))
    harness_run_id: str
    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    input_cache_write_tokens: int = Field(default=0, ge=0)
    input_cache_read_tokens: int = Field(default=0, ge=0)
    input_price_per_million_usd: float = Field(ge=0)
    output_price_per_million_usd: float = Field(ge=0)
    input_cache_write_price_per_million_usd: float = Field(ge=0)
    input_cache_read_price_per_million_usd: float = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    pricing_source: str = Field(min_length=1)
    budget_limit_usd: float = Field(ge=0)
    source: Literal["inspect_eval_log", "provider_response"]
    created_at: str = Field(default_factory=now)


class RunnerTrace(BaseModel):
    runner_trace_id: str = Field(default_factory=lambda: ident("runner_trace"))
    harness_run_id: str
    runner: Literal["inspect_ai"]
    provider: str
    model: str
    inspect_log_location: str = Field(min_length=1)
    output_sha256: str = Field(min_length=1)
    selected_cleanup_attempt: bool | None = None
    created_at: str = Field(default_factory=now)


class RunnerFailure(BaseModel):
    runner_failure_id: str = Field(default_factory=lambda: ident("runner_failure"))
    harness_run_id: str
    runner: Literal["inspect_ai"]
    category: Literal["provider", "budget", "contract"]
    reason: str = Field(min_length=1)
    created_at: str = Field(default_factory=now)


class ModelDecision(BaseModel):
    model_decision_id: str
    harness_run_id: str
    work_item_id: str
    candidate_fingerprint: str
    status: Literal["running", "completed"] = "running"
    cleanup_attempt: bool | None = None
    provider_usage_id: str | None = None
    runner_trace_id: str | None = None
    created_at: str = Field(default_factory=now)


class FailureExplanation(BaseModel):
    failure_type: FailureType
    explanation: str = Field(min_length=1)
    suspected_change_ids: list[str] = Field(default_factory=list)
    limitation: str = Field(min_length=1)


class RequirementMappingSuggestion(BaseModel):
    requirement_id: str
    candidate_capability: str = Field(min_length=1)
    impacted_change_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: Literal["low", "medium", "high"]


class LLMAssistance(BaseModel):
    assistance_id: str = Field(default_factory=lambda: ident("assist"))
    product_id: str
    kind: LLMAssistanceKind
    harness_run_id: str | None = None
    input_artifact_ids: list[str]
    evidence_level: Literal["inferred"] = "inferred"
    provider: str
    model: str
    provider_request_id: str
    prompt_version: str
    output: FailureExplanation | RequirementMappingSuggestion
    created_at: str = Field(default_factory=now)
