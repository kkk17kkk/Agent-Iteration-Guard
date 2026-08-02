import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .domain import (
    EnvironmentCheck,
    HistoricalReplayEvidence,
    MemoryDependency,
    MemoryEntry,
    NativeHarnessContract,
    ProductContractRevision,
    ProviderBinding,
    RuntimeEnvironmentContract,
    RuntimeEnvironmentPreflight,
    TaskVerifierContract,
)
from .evolution import EvolutionIntakeError
from .service import AssistantInputError, ProductNotFoundError, Service


app = FastAPI(title="Agent Iteration Guard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


def service() -> Service:
    return Service(os.getenv("AGENTGUARD_DB", "data/agentguard.db"))


class CreateProduct(BaseModel):
    name: str
    description: str = ""


class ImportVersion(BaseModel):
    source: str
    label: str


class EvolutionIntakeRequest(BaseModel):
    source: str
    baseline_ref: str
    candidate_ref: str
    repository_url: str | None = None
    declared_entrypoint: str | None = None


class MemoryEntryRequest(BaseModel):
    kind: str
    content: str
    evidence_level: str
    evidence_refs: list[str] = []
    applicable_revision_ids: list[str] = []
    status: str = "candidate"
    recorded_by: str = "human"


class MemoryDependencyRequest(BaseModel):
    memory_id: str
    dependent_kind: str
    dependent_id: str
    component_paths: list[str] = []
    component_fingerprints: list[str] = []


class ProductContractRevisionRequest(BaseModel):
    applicable_revision_ids: list[str] = []
    goals: list[str] = []
    non_goals: list[str] = []
    requirements: list[str] = []
    risks: list[str] = []
    evidence_refs: list[str] = []
    status: str = "candidate"


class ProviderBindingRequest(BaseModel):
    role: str
    provider: Literal["deepseek", "openai", "vllm"]
    base_url: str | None = None
    model: str
    expected_environment_variable: str
    credential_source_ref: str
    batch_budget_usd: float
    timeout_seconds: int
    allowed_hosts: list[str] = []
    data_retention_policy: str
    max_model_calls: int = 8
    max_tool_calls: int = 12
    max_wall_time_seconds: int = 360
    max_output_tokens: int = 512
    temperature: float = 0.0
    input_price_per_million_usd: float | None = None
    output_price_per_million_usd: float | None = None
    cache_hit_price_per_million_usd: float | None = None
    pricing_source: str | None = None
    pricing_verified_at: str | None = None


class NativeHarnessContractRequest(BaseModel):
    baseline_entrypoint: str
    candidate_entrypoint: str
    adapter_ref: str
    trace_schema_ref: str
    behavior_mode: str = "reconstruction"
    status: str = "incomplete"


class RuntimeEnvironmentContractRequest(BaseModel):
    docker_ref: str | None = None
    dependency_lock_ref: str | None = None
    model_config_ref: str | None = None
    tools_manifest_ref: str | None = None
    reset_command_ref: str | None = None
    initial_state_ref: str | None = None
    status: str = "incomplete"


class TaskVerifierContractRequest(BaseModel):
    task_spec_ref: str
    verifier_ref: str
    pass_iff: str
    initial_state_ref: str
    trace_evidence_ref: str
    status: str = "incomplete"


class RuntimePreflightRequest(BaseModel):
    environment_contract_id: str
    checks: list[EnvironmentCheck]


class HistoricalReplayEvidenceRequest(BaseModel):
    revision_id: str
    trace_sha256: str
    tool_result_sha256: str
    execution_log_sha256: str
    initial_state_sha256: str
    verifier_evidence_ref: str


def _unprocessable(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "product": "agent-iteration-guard"}


@app.get("/api/v1/products")
def products():
    return service().products()


@app.post("/api/v1/products")
def create(body: CreateProduct):
    product, version = service().create(body.name, body.description)
    return {"product": product, "version": version}


@app.post("/api/v1/products/{product_id}/versions")
def import_version(product_id: str, body: ImportVersion):
    try:
        return {"version": service().import_version(product_id, Path(body.source), body.label)}
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error


@app.post("/api/v1/products/{project_id}/evolution/intake")
def intake_agent_evolution(project_id: str, body: EvolutionIntakeRequest):
    try:
        return service().intake_agent_evolution(project_id, Path(body.source), body.baseline_ref, body.candidate_ref, repository_url=body.repository_url, declared_entrypoint=body.declared_entrypoint).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except EvolutionIntakeError as error:
        raise _unprocessable(error) from error


@app.get("/api/v1/products/{project_id}/evolution/reports/{report_id}")
def evolution_report(project_id: str, report_id: str):
    try:
        return service().evolution_report(project_id, report_id)
    except (ProductNotFoundError, AssistantInputError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/contracts")
def record_product_contract_revision(project_id: str, body: ProductContractRevisionRequest):
    try:
        return service().record_product_contract_revision(project_id, ProductContractRevision(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/provider-bindings")
def record_provider_binding(project_id: str, body: ProviderBindingRequest):
    try:
        return service().record_provider_binding(project_id, ProviderBinding(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/memory")
def record_memory_entry(project_id: str, body: MemoryEntryRequest):
    try:
        return service().record_memory_entry(project_id, MemoryEntry(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/memory/dependencies")
def record_memory_dependency(project_id: str, body: MemoryDependencyRequest):
    try:
        return service().record_memory_dependency(project_id, MemoryDependency(project_id=project_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{changeset_id}/propagate-stale")
def propagate_evolution_stale(project_id: str, changeset_id: str):
    try:
        return service().propagate_evolution_stale(project_id, changeset_id)
    except (ProductNotFoundError, EvolutionIntakeError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/native-harness-contract")
def record_native_harness_contract(project_id: str, case_id: str, body: NativeHarnessContractRequest):
    try:
        return service().record_native_harness_contract(project_id, NativeHarnessContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/environment-contract")
def record_runtime_environment_contract(project_id: str, case_id: str, body: RuntimeEnvironmentContractRequest):
    try:
        return service().record_runtime_environment_contract(project_id, RuntimeEnvironmentContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/task-verifier-contract")
def record_task_verifier_contract(project_id: str, case_id: str, body: TaskVerifierContractRequest):
    try:
        return service().record_task_verifier_contract(project_id, TaskVerifierContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/environment-preflight")
def record_runtime_preflight(project_id: str, case_id: str, body: RuntimePreflightRequest):
    try:
        return service().record_runtime_preflight(project_id, RuntimeEnvironmentPreflight(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/replay-evidence")
def record_historical_replay_evidence(project_id: str, case_id: str, body: HistoricalReplayEvidenceRequest):
    try:
        return service().record_historical_replay_evidence(project_id, HistoricalReplayEvidence(project_id=project_id, evolution_case_id=case_id, **body.model_dump()))
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise _unprocessable(error) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/admission")
def assess_evolution_admission(project_id: str, case_id: str):
    try:
        return service().assess_evolution_admission(project_id, case_id).as_dict()
    except (ProductNotFoundError, EvolutionIntakeError) as error:
        raise _unprocessable(error) from error
