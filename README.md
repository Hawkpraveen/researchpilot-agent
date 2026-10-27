# ResearchPilot Agent

Private Python service boundary for the bounded ResearchPilot research
workflow. This repository is separate from the public Node API and does not
write to the product database. The web client must never call this service;
the Node API owns job orchestration and makes internal service calls.

## Current capability

The service exposes health checks and a strict, bounded planning endpoint at
`POST /api/v1/research/plan`. Planning returns criteria-based questions and a
depth-specific budget. It does not run searches, call a language model, verify
sources, or generate findings. Those stages remain unimplemented; the
workflow and evidence policy are defined in
`ResearchPilot-Architecture/docs/05_AGENT_WORKFLOW.md`.

## Local development

Requires Python 3.11 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000 --reload
```

Verify the service at `http://127.0.0.1:8000/health`. Interactive API
documentation is available at `http://127.0.0.1:8000/docs`.

The service makes no external provider calls, requires no model or search key,
and does not load or write product data. `HOST`, `PORT`, and `LOG_LEVEL` can
be set in the ignored local `.env` file. Keep the Agent bound to a private
service network; it is not a public or browser-facing API.
