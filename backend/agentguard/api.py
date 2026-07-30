import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .llm import LLMProviderError
from .service import AssistantInputError, ProductNotFoundError, Service
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


class MultiTrialRun(StartRun):
    cleanup_attempts: list[bool] = [False, False, True]


class ExternalMultiTrialRun(StartRun):
    trials: int = 3
    max_total_cost_usd: float = 0.05


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
    model_kind: str = "deterministic"
    max_steps: int = 8


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


@app.post("/api/v1/fixtures/minimal")
def fixture():
    return {"product": service().fixture()}


@app.post("/api/v1/fixtures/file-agent")
def file_agent_fixture():
    return {"fixture": service().file_agent_fixture().as_dict()}


@app.post("/api/v1/fixtures/file-management-agent")
def file_management_fixture():
    return {"fixture": service().file_management_fixture().as_dict()}


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


@app.post("/api/v1/stage2/runs")
def start_stage2_run(body: Stage2RunRequest):
    try:
        action_model = None
        if body.model_kind == "http_json":
            endpoint = os.getenv("AGENTGUARD_STAGE2_MODEL_URL")
            if not endpoint:
                raise HTTPException(status_code=422, detail="AGENTGUARD_STAGE2_MODEL_URL is required for http_json")
            action_model = HttpJsonActionModel(endpoint)
        if body.model_kind == "real_llm" and not os.getenv("DEEPSEEK_API_KEY"):
            raise HTTPException(status_code=422, detail="DEEPSEEK_API_KEY is required for real_llm")
        return service().start_stage2_file_agent(
            body.stage1_batch_id,
            product_id=body.product_id,
            baseline_version_id=body.baseline_version_id,
            candidate_version_id=body.candidate_version_id,
            task_kind=body.task_kind,
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
