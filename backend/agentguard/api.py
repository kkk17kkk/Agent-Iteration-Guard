import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .service import ProductNotFoundError, Service


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


@app.post("/api/v1/products/{product_id}/reports")
def prepare_report(product_id: str):
    try:
        return service().prepare_harness_run(product_id).as_dict()
    except ProductNotFoundError as error:
        raise HTTPException(status_code=404, detail="product not found") from error
