import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .llm import LLMProviderError
from .service import AssistantInputError, ProductNotFoundError, Service
from .evolution import EvolutionIntakeError
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
from .stage2 import HttpJsonActionModel


app = FastAPI(title="Agent Iteration Guard", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def service() -> Service:
    return Service(os.getenv("AGENTGUARD_DB", "data/agentguard.db"))


class CreateProduct(BaseModel):
    name: str
    description: str = ""


class ImportVersion(BaseModel):
    source: str
    label: str


class StartRun(BaseModel):
    product_id: str
    baseline_version_id: str
    candidate_version_id: str


class TicketRun(StartRun):
    case_id: str


class MultiTrialRun(StartRun):
    cleanup_attempts: list[bool] = [False, False, True]


class ExternalMultiTrialRun(StartRun):
    trials: int = 3
    max_total_cost_usd: float = 0.05


class ControlledReplanRequest(BaseModel):
    additional_trial_budget: int = 1
    allow_runner_switch: bool = False


class MutationBatchRequest(BaseModel):
    max_workers: int = 2
    trials_per_pair: int = 3
    max_total_cost_usd: float = 0.0
    product_id: str | None = None


class RequirementMappingRequest(BaseModel):
    requirement_id: str
    changeset_id: str


class Stage2RunRequest(BaseModel):
    stage1_batch_id: str
    product_id: str | None = None
    baseline_version_id: str | None = None
    candidate_version_id: str | None = None
    task_kind: str = "update_title"
    fixture_variant: str = "default"
    model_kind: str = "deterministic"
    max_steps: int = 8


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
    provider: str
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "product": "agent-iteration-guard"}


@app.get("/api/v1/products")
def products():
    return service().products()


@app.post("/api/v1/products/{project_id}/evolution/intake")
def intake_agent_evolution(project_id: str, body: EvolutionIntakeRequest):
    try:
        return service().intake_agent_evolution(
            project_id,
            Path(body.source),
            body.baseline_ref,
            body.candidate_ref,
            repository_url=body.repository_url,
            declared_entrypoint=body.declared_entrypoint,
        ).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="project not found") from error
    except EvolutionIntakeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/v1/products/{project_id}/evolution/reports/{report_id}")
def evolution_report(project_id: str, report_id: str):
    try:
        return service().evolution_report(project_id, report_id)
    except (ProductNotFoundError, AssistantInputError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/contracts")
def record_product_contract_revision(project_id: str, body: ProductContractRevisionRequest):
    try:
        contract = ProductContractRevision(project_id=project_id, **body.model_dump())
        return service().record_product_contract_revision(project_id, contract)
    except (ProductNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/provider-bindings")
def record_provider_binding(project_id: str, body: ProviderBindingRequest):
    try:
        binding = ProviderBinding(project_id=project_id, **body.model_dump())
        return service().record_provider_binding(project_id, binding)
    except (ProductNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/memory")
def record_memory_entry(project_id: str, body: MemoryEntryRequest):
    try:
        memory = MemoryEntry(project_id=project_id, **body.model_dump())
        return service().record_memory_entry(project_id, memory)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/memory/dependencies")
def record_memory_dependency(project_id: str, body: MemoryDependencyRequest):
    try:
        dependency = MemoryDependency(project_id=project_id, **body.model_dump())
        return service().record_memory_dependency(project_id, dependency)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{changeset_id}/propagate-stale")
def propagate_evolution_stale(project_id: str, changeset_id: str):
    try:
        return service().propagate_evolution_stale(project_id, changeset_id)
    except (ProductNotFoundError, EvolutionIntakeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/native-harness-contract")
def record_native_harness_contract(project_id: str, case_id: str, body: NativeHarnessContractRequest):
    try:
        contract = NativeHarnessContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump())
        return service().record_native_harness_contract(project_id, contract)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/environment-contract")
def record_runtime_environment_contract(project_id: str, case_id: str, body: RuntimeEnvironmentContractRequest):
    try:
        contract = RuntimeEnvironmentContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump())
        return service().record_runtime_environment_contract(project_id, contract)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/task-verifier-contract")
def record_task_verifier_contract(project_id: str, case_id: str, body: TaskVerifierContractRequest):
    try:
        contract = TaskVerifierContract(project_id=project_id, evolution_case_id=case_id, **body.model_dump())
        return service().record_task_verifier_contract(project_id, contract)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/environment-preflight")
def record_runtime_preflight(project_id: str, case_id: str, body: RuntimePreflightRequest):
    try:
        preflight = RuntimeEnvironmentPreflight(
            project_id=project_id, evolution_case_id=case_id, **body.model_dump()
        )
        return service().record_runtime_preflight(project_id, preflight)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/replay-evidence")
def record_historical_replay_evidence(project_id: str, case_id: str, body: HistoricalReplayEvidenceRequest):
    try:
        evidence = HistoricalReplayEvidence(project_id=project_id, evolution_case_id=case_id, **body.model_dump())
        return service().record_historical_replay_evidence(project_id, evidence)
    except (ProductNotFoundError, EvolutionIntakeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products/{project_id}/evolution/{case_id}/admission")
def assess_evolution_admission(project_id: str, case_id: str):
    try:
        return service().assess_evolution_admission(project_id, case_id).as_dict()
    except (ProductNotFoundError, EvolutionIntakeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/products")
def create(body: CreateProduct):
    product, version = service().create(body.name, body.description)
    return {"product": product, "version": version}


@app.post("/api/v1/fixtures/minimal")
def fixture():
    return {"product": service().fixture()}


@app.post("/api/v1/fixtures/file-agent")
def file_agent_fixture():
    return {"fixture": service().file_agent_fixture().as_dict()}


@app.post("/api/v1/fixtures/file-management-agent")
def file_management_fixture():
    return {"fixture": service().file_management_fixture().as_dict()}


@app.post("/api/v1/fixtures/ticket-agent")
def ticket_agent_fixture():
    return {"fixture": service().ticket_agent_fixture().as_dict()}


@app.post("/api/v1/products/{product_id}/versions")
def import_version(product_id: str, body: ImportVersion):
    try:
        return {"version": service().import_version(product_id, Path(body.source), body.label)}
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error


@app.post("/api/v1/runs")
def start_run(body: StartRun):
    try:
        return service().run_file_agent(
            body.product_id,
            body.baseline_version_id,
            body.candidate_version_id,
        ).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error


@app.post("/api/v1/runs/file-management")
def start_file_management_run(body: StartRun):
    try:
        return service().start_file_management_run(
            body.product_id,
            body.baseline_version_id,
            body.candidate_version_id,
        ).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error


@app.post("/api/v1/runs/{harness_run_id}/resume")
def resume_file_management_run(harness_run_id: str):
    try:
        return service().resume_file_management_run(harness_run_id).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/ticket")
def start_ticket_agent_run(body: TicketRun):
    try:
        return service().start_ticket_agent_run(
            body.product_id, body.baseline_version_id, body.candidate_version_id, body.case_id
        ).as_dict()
    except (ProductNotFoundError, AssistantInputError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/ticket/{harness_run_id}/resume")
def resume_ticket_agent_run(harness_run_id: str):
    try:
        return service().resume_ticket_agent_run(harness_run_id).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/stage2/runs")
def start_stage2_run(body: Stage2RunRequest):
    try:
        action_model = None
        if body.model_kind == "http_json":
            endpoint = os.getenv("AGENTGUARD_STAGE2_MODEL_URL")
            if not endpoint:
                raise HTTPException(status_code=422, detail="AGENTGUARD_STAGE2_MODEL_URL is required for http_json")
            action_model = HttpJsonActionModel(endpoint)
        return service().start_stage2_file_agent(
            body.stage1_batch_id,
            product_id=body.product_id,
            baseline_version_id=body.baseline_version_id,
            candidate_version_id=body.candidate_version_id,
            task_kind=body.task_kind,
            fixture_variant=body.fixture_variant,
            model_kind=body.model_kind,
            action_model=action_model,
            max_steps=body.max_steps,
        ).model_dump()
    except (ProductNotFoundError, AssistantInputError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/stage2/runs/{agent_run_id}/resume")
def resume_stage2_run(agent_run_id: str):
    try:
        return service().resume_stage2_file_agent(agent_run_id).model_dump()
    except (AssistantInputError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/v1/stage2/runs/{agent_run_id}/report")
def report_stage2_run(agent_run_id: str):
    try:
        return service().report_stage2_file_agent(agent_run_id)
    except (AssistantInputError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/v1/stage2/gates/{stage1_batch_id}")
def gate_stage2_run(stage1_batch_id: str):
    try:
        return service().gate_stage2_file_agent(stage1_batch_id).model_dump()
    except (AssistantInputError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/trials")
def evaluate_file_management_trials(body: MultiTrialRun):
    try:
        return service().evaluate_file_management_trials(
            body.product_id,
            body.baseline_version_id,
            body.candidate_version_id,
            body.cleanup_attempts,
        ).as_dict()
    except (ProductNotFoundError, AssistantInputError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/trials/external")
def evaluate_file_management_external_trials(body: ExternalMultiTrialRun):
    try:
        return service().evaluate_file_management_external_trials(
            body.product_id,
            body.baseline_version_id,
            body.candidate_version_id,
            trial_count=body.trials,
            max_total_cost_usd=body.max_total_cost_usd,
        ).as_dict()
    except (ProductNotFoundError, AssistantInputError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/{harness_run_id}/replays/{source_trial_result_id}")
def replay_file_management_trial(harness_run_id: str, source_trial_result_id: str):
    try:
        return service().replay_file_management_trial(harness_run_id, source_trial_result_id).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/{harness_run_id}/ablations/cleanup/{source_trial_result_id}")
def ablate_file_management_cleanup(harness_run_id: str, source_trial_result_id: str):
    try:
        return service().ablate_file_management_cleanup(harness_run_id, source_trial_result_id).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/{harness_run_id}/replan")
def controlled_replan_file_management(harness_run_id: str, body: ControlledReplanRequest):
    try:
        return service().controlled_replan_file_management(
            harness_run_id,
            additional_trial_budget=body.additional_trial_budget,
            allow_runner_switch=body.allow_runner_switch,
        ).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/benchmarks/file-management")
def create_file_management_mutation_batch(body: MutationBatchRequest):
    try:
        return service().create_file_management_mutation_batch(
            max_workers=body.max_workers,
            trials_per_pair=body.trials_per_pair,
            max_total_cost_usd=body.max_total_cost_usd,
            product_id=body.product_id,
        ).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/benchmarks/{batch_id}/run")
def run_file_management_mutation_batch(batch_id: str):
    try:
        return service().run_file_management_mutation_batch(batch_id).as_dict()
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/v1/runs/{harness_run_id}/assistant-explanation")
def explain_failure(harness_run_id: str):
    try:
        return {"assistance": service().explain_failure(harness_run_id)}
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LLMProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/v1/products/{product_id}/assistant-mappings")
def suggest_requirement_mapping(product_id: str, body: RequirementMappingRequest):
    try:
        return {
            "assistance": service().suggest_requirement_mapping(
                product_id, body.requirement_id, body.changeset_id
            )
        }
    except AssistantInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LLMProviderError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/v1/products/{product_id}/reports")
def prepare_report(product_id: str):
    try:
        return service().prepare_harness_run(product_id).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error
