# Connect the hosted app to local Ollama for free

The bridge runs **on your computer**, separately from the agent running on Render:

`Render agent -> HTTPS tunnel -> authenticated bridge -> local Ollama`

No router port forwarding, public Ollama binding, paid domain or GPU rental is
required. Prompts and responses travel through Render and Cloudflare; this is
local model inference, not an entirely offline research workflow.

## Start

From this repository, with Ollama running and the existing `.venv` installed:

```powershell
# First time only: creates an ignored secret file, without printing the key.
.venv\Scripts\python.exe -c "import pathlib,secrets; p=pathlib.Path('.env.bridge'); p.exists() or p.write_text('RESEARCHPILOT_BRIDGE_TOKEN='+secrets.token_urlsafe(32)+'\n')"

# Terminal 1: leave running. This loads only the dedicated bridge secret file.
.venv\Scripts\python.exe -m uvicorn app.local_bridge:create_bridge --factory --env-file .env.bridge --host 127.0.0.1 --port 11435 --no-access-log

# Terminal 2: leave running. Tunnel the protected bridge, NEVER Ollama port 11434.
.local-tools\cloudflared.exe tunnel --url http://127.0.0.1:11435 --protocol http2
```

Install/download `cloudflared` from Cloudflare's official downloads if it is not
already in `.local-tools/cloudflared.exe`. This executable is ignored by Git.
Use the HTTPS `*.trycloudflare.com` hostname printed by cloudflared.

## Configure the deployed workspace

In ResearchPilot Settings:

| Field | Value |
| --- | --- |
| Mode | BYOK (the key authenticates your local bridge) |
| Provider | Custom / OpenAI-compatible |
| Endpoint | `https://YOUR-TUNNEL.trycloudflare.com/v1` |
| API key | Value of `RESEARCHPILOT_BRIDGE_TOKEN` from `.env.bridge` |
| Model | An installed chat model, including its exact tag, e.g. `llama3.2:3b` |

Copy the secret without printing it to your terminal:

```powershell
Set-Clipboard -Value ((Get-Content .env.bridge -Raw).Trim() -replace '^RESEARCHPILOT_BRIDGE_TOKEN=', '')
```

Paste it into the provider API-key field, test, select a listed model, and save.
Do not put this key in a `VITE_*` variable or commit it. No Render code change or
agent-submodule update is required: the existing custom provider sends the key
as an Authorization bearer header.

## Behavior and limits

- Only authenticated `GET /v1/models` and `POST /v1/chat/completions` are routed
  upstream. Model pull/delete and arbitrary Ollama APIs are not exposed.
- The bridge never forwards its authentication key to Ollama. Ollama itself
  ignores OpenAI-compatible API keys; the bridge supplies the missing check.
- Requests are capped at 256 KiB and 4096 output tokens. One generation runs at
  a time; concurrent generation returns 429. Generation has a 115-second timeout.
- Hosted research still has its existing request/depth budgets. Start with a
  small installed model and a Quick job; a successful model-list test does not
  establish generation speed or research quality. Do not select the embedding
  model as a chat model.
- Your computer, Ollama, bridge and tunnel must remain on. Stop either terminal
  with Ctrl+C to disconnect. Sleep/reboot breaks the connection.
- A Quick Tunnel is temporary; restarting it changes the URL. Update the saved
  endpoint and re-enter the bridge key when the URL changes. Quick Tunnels have
  no uptime guarantee. They do not support SSE; this bridge uses non-streaming
  generation, so it does not depend on SSE.
- To revoke access, stop the tunnel and replace the secret in `.env.bridge`, then
  restart the bridge and save the new key in workspace Settings.

References:
- https://docs.ollama.com/api/openai-compatibility
- https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/
- https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/
