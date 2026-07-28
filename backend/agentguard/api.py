import os
from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .service import Service
app=FastAPI(title="Agent Iteration Guard",version="0.1.0");app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173"],allow_methods=["*"],allow_headers=["*"])
def svc():return Service(os.getenv("AGENTGUARD_DB","data/agentguard.db"))
class Create(BaseModel):name:str;description:str=""
@app.get("/health")
def health():return {"status":"ok","product":"agent-iteration-guard"}
@app.get("/api/v1/products")
def products():return svc().products()
@app.post("/api/v1/products")
def create(body:Create):p,v=svc().create(body.name,body.description);return {"product":p,"version":v}
@app.post("/api/v1/fixtures/minimal")
def fixture():return {"product":svc().fixture()}
@app.post("/api/v1/products/{product_id}/reports")
def report(product_id:str):
 try:r,d=svc().report(product_id);return {"eval_run":r,"findings":[],"release_decision":d}
 except KeyError:raise HTTPException(404,"product not found")
