# ResearchPilot Connector v1

Windows users install Ollama and a chat model, download/extract the connector
from the website, and open the EXE once. In Settings they select **Local LLM**,
click **Connect this computer** and approve the browser's local-network access.
Then selecting a model saves it automatically. There are no tunnel URLs, API keys
or commands for users to manage. On later visits the paired computer reconnects
automatically. Optional Windows sign-in startup is opt-in in the connector window.

## Build and publish

On Windows, install PyInstaller into the agent development venv:

```powershell
.venv\Scripts\python.exe -m pip install pyinstaller
./connector/build.ps1
```

The build script puts the ZIP and SHA-256 file in the frontend repository's
`public/downloads/` directory. Commit these alongside the frontend changes to
publish the Download connector link through Cloudflare. The EXE bundles Python
and Tcl/Tk. It does not bundle Ollama, user credentials or a model. This first
release is unsigned; Windows SmartScreen can prompt users. macOS/Linux installers
and signing are not included. Python users can run `python connector/connector.py`.

Publish the agent source first. Then update the backend's agent submodule to
that published commit and push the backend changes. Render builds the API and
updated private agent together. Finally deploy the frontend and its download.
Deploy the API before making the new UI available. No new environment secrets
or paid services are needed. New MongoDB collections/indexes initialize at API
startup: connector pairings, devices and short-lived model tasks.

The executable defaults to the existing ResearchPilot Render API and Cloudflare
frontend hostname. If these URLs change, update `API` / `WEB` in `connector.py`
and rebuild. Development-only overrides: `--api http://127.0.0.1:5000/api/v1
--web http://localhost:8080`. These are explicit launch arguments, not values
accepted from a web page. Keep runtime endpoints on HTTPS outside localhost.

## Connection protocol

- The web app is authenticated using the existing session. A 256-bit, two-minute
  single-use pairing code is bound to tenant + workspace + user.
- Only the configured frontend Origin and loopback Host can request local
  pairing. The browser sends the code, never a user session or device token.
- The connector exchanges the code over HTTPS for a 30-day device token. The
  server stores its hash; the desktop stores the token in the user's profile.
- Device requests are outbound HTTPS long polls (15 seconds plus reconnect
  delay). There is no public local server, router port forwarding or tunnel.
- Discovery reports Ollama models with the `completion` capability. Embedding
  models are excluded. A model is selected and saved through the usual scoped
  provider settings, using an internal connector marker endpoint.
- Each research attempt gets a short-lived internal bearer capability bound to
  the job owner's scope and selected model. The private agent calls the API's
  loopback-only relay. The connector receives only its own claimed task, sends
  it to fixed `127.0.0.1:11434/api/chat`, and returns the native Ollama response.
- One inference per paired computer is allowed. Payloads are capped at 256 KiB.
  Timeouts and offline computers fail explicitly; there is no cloud fallback.
- Disconnect revokes the credential and rejects in-flight waiters. Existing
  research cancellation prevents report publication; as with the existing agent,
  it does not guarantee immediate interruption of a running Ollama generation.
- Queue payloads expire after 115 seconds and are deleted on completion or timeout.
  MongoDB TTL cleanup is asynchronous. No prompts/results are logged by the
  connector. Usage counters continue through the agent's existing Ollama ledger.

## Operational limits and verification

This version assumes one backend instance, matching the current job scheduler
and rate limiter. Running jobs fail across an API restart; queued connector
payloads expire, and the computer reconnects automatically. Keep the computer,
Ollama and connector on. Models can exceed the current research time budgets;
start with a small chat model and Quick research.

Chrome/Edge may ask for local-network permission for the loopback pairing call.
Users must allow it. If blocked, browser site settings can reset the permission.
Final hosted pairing should be checked in the target browser after deployment.
Tests cover scope isolation, one-time codes, expiry, revocation, queue ownership,
origin checks, credential handling and local model discovery. Signed installer
distribution, auto-update and a system-tray UI remain future enhancements.
