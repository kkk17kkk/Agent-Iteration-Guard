# Agent Iteration Guard

Agent Iteration Guard is a local-first harness for tracking Tool / Skill agent versions, preserving evaluation evidence, and producing release-readiness decisions.

The initial draft establishes one coherent product boundary:

- a framework-independent domain core;
- SQLite-backed product, version, requirement, capability and evaluation records;
- CLI and HTTP API over the same application service;
- a small LangGraph adapter for deterministic report preparation;
- a focused React workbench for the Phase 1 flow.

## Quick start

```bash
cd backend
python -m pip install -e .
agentguard init
agentguard fixture load minimal
agentguard product list
```

Run the API:

```bash
cd backend
uvicorn agentguard.api:app --reload --port 8000
```

Run the frontend:

```bash
cd frontend
npm install
npm run dev
```

## Phase 1 commands

```text
agentguard init
agentguard product add --name "My Agent"
agentguard product list
agentguard product get <product_id>
agentguard fixture load minimal
agentguard report prepare --product-id <product_id>
```

Use `--format json` for machine-readable output. Known errors include a stage, reason and actionable next step.

## Current boundary

Phase 1 does not scan repositories, compare versions, execute real agent tasks, or auto-approve releases. A prepared report intentionally remains `pending` until later phases add executable evaluation evidence.
