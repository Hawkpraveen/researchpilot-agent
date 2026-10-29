# ResearchPilot Agent

To connect the hosted app to Ollama on your computer through an authenticated
gateway, see [Local Ollama connection](LOCAL-OLLAMA.md). This optional gateway
runs locally and does not change the private agent deployed on Render.

Private Python service boundary for the bounded ResearchPilot research
workflow. This repository is separate from the public Node API and does not
write to the product database. The web client must never call this service;
the Node API owns job orchestration and makes internal service calls.

## Current capability

The service exposes health checks, `POST /api/v1/research/plan`, and
`POST /api/v1/research/run`. Execution searches the web through Tavily, sends
retrieved source content to the workspace's selected model, checks quoted
excerpts against that content, and returns a source-linked report. Ollama,
OpenAI, Anthropic, and custom OpenAI-compatible providers are supported.
Search requests, result slots, model output, and total time are bounded by depth.
Criteria without evidence remain explicitly unknown. This is first-pass research;
automated follow-up searches and independent factual verification are not implemented.

## Usage accounting

Request-local accounting captures each provider call before output validation.
Ollama evaluation counts, OpenAI-compatible usage, Anthropic input/output and
cache counters, and Tavily `usage.credits` are returned in the top-level `usage`
envelope on success and failure. Tavily requests set `include_usage: true`.
The Node API persists usage per execution, independently of whether a report
is published. Requests without returned usage remain unknown, not zero.

## Local development

Requires Python 3.11 or later.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
# Set TAVILY_API_KEY in .env before running research.
python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000 --reload
```

Verify the service at `http://127.0.0.1:8000/health`. Interactive API
documentation is available at `http://127.0.0.1:8000/docs`.

Set `TAVILY_API_KEY` in the ignored local `.env` file. Search uses Tavily even
when the selected model is local Ollama; queries leave this machine. Configure
the model endpoint, model name, and (for cloud models) API key in workspace
Settings. For Ollama, start Ollama and pull the configured model first.
The Node API resolves saved credentials for the job's tenant/workspace and
sends them only to this private service. They are not persisted in jobs or reports.
Search and cloud-model calls may incur provider charges. Missing keys, failed
providers, invalid model JSON, and unsupported citations fail the job instead
of saving a placeholder. Provider requests are not automatically retried.

Reports retain cited excerpts and retrieval timestamps, not full source pages.
Excerpt matching checks provenance, not whether the claim is true or follows
from the excerpt. Source reliability is unassessed. Review critical claims at
the linked sources. Cancellation prevents publication but does not stop a
provider request that is already running; the agent time budget still applies.

`HOST`, `PORT`, and `LOG_LEVEL` can also be set in `.env`. Keep the Agent bound
to a private service network; it accepts internal provider configuration and
must not be exposed publicly. After configuration, create a new research job;
historical planning-only reports are not automatically rerun.
