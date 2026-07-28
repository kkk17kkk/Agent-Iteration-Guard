from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field

def now() -> str: return datetime.now(timezone.utc).isoformat()
def ident(prefix: str) -> str: return f"{prefix}_{uuid4().hex[:12]}"
class Product(BaseModel):
    product_id: str = Field(default_factory=lambda: ident("product")); name: str = Field(min_length=1); description: str = ""; current_version_id: str | None = None; created_at: str = Field(default_factory=now)
class Version(BaseModel):
    version_id: str = Field(default_factory=lambda: ident("version")); product_id: str; label: str = Field(min_length=1); source_ref: str = "manual"; created_at: str = Field(default_factory=now)
class ComponentSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: ident("snapshot")); product_id: str; version_id: str; component_type: str; name: str; fingerprint: str = ""
class Requirement(BaseModel):
    requirement_id: str = Field(default_factory=lambda: ident("req")); product_id: str; title: str; description: str = ""; risk: Literal["low","medium","high","critical"] = "medium"
class Capability(BaseModel):
    capability_id: str = Field(default_factory=lambda: ident("cap")); product_id: str; name: str; requirement_ids: list[str] = Field(default_factory=list); risk: Literal["low","medium","high","critical"] = "medium"
class EvalCase(BaseModel):
    eval_case_id: str = Field(default_factory=lambda: ident("eval")); product_id: str; name: str; capability_ids: list[str] = Field(default_factory=list); oracle_kind: Literal["state_assertion","tool_trace","test"] = "state_assertion"
class EvalRun(BaseModel):
    eval_run_id: str = Field(default_factory=lambda: ident("run")); product_id: str; version_id: str; eval_case_ids: list[str] = Field(default_factory=list); status: Literal["created","running","completed","failed"] = "created"; created_at: str = Field(default_factory=now)
class Finding(BaseModel):
    finding_id: str = Field(default_factory=lambda: ident("finding")); product_id: str; eval_run_id: str; title: str; evidence_level: Literal["verified","supported","inferred","unresolved"] = "unresolved"
class ReleaseDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: ident("decision")); product_id: str; version_id: str; status: Literal["pending","ready","blocked"] = "pending"; rationale: str; created_at: str = Field(default_factory=now)
