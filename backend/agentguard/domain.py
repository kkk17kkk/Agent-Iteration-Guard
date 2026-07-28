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
ChangeKind = Literal["permission_changed", "tool_capability_expanded", "skill_changed"]
WorkStatus = Literal["planned", "completed", "blocked"]
RunEventType = Literal[
    "RUN_CREATED", "PLAN_CREATED", "TRIALS_COMPLETED", "VERIFICATION_COMPLETED",
    "FINDING_CREATED", "RELEASE_DECIDED", "RUN_RECORDED",
]


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
    tool_name: Literal["write_file"] = "write_file"
    path: str
    policy_decision: Literal["allowed", "unauthorized"]


class ExecutionResult(BaseModel):
    execution_id: str = Field(default_factory=lambda: ident("execution"))
    harness_run_id: str
    work_item_id: str
    status: Literal["completed"] = "completed"
    tool_calls: list[ToolCall]
    environment_ref: str = "fake-file-agent-v1"
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
    created_at: str = Field(default_factory=now)


class HarnessRun(BaseModel):
    harness_run_id: str = Field(default_factory=lambda: ident("harness"))
    product_id: str
    version_id: str
    baseline_version_id: str | None = None
    candidate_version_id: str | None = None
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
