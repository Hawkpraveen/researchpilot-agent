ResearchPilot Connector for Windows

1. Install Ollama from https://ollama.com and download a chat model.
2. Double-click ResearchPilot Connector.exe. It installs into your user profile.
3. Open ResearchPilot > Settings > Local LLM and choose Connect this computer.
4. Allow the browser's local-network permission, then choose a model. Ready!

There are no tunnel URLs or API keys to copy. Your computer must stay on, with
Ollama and the connector running. You can minimize the connector window, and
optionally check "Start when I sign in to Windows" for automatic reconnection.
Closing its window disconnects the computer. Revoke access in ResearchPilot
Settings to unpair it. Do not select an embedding-only model for research.

Model prompts and responses pass through ResearchPilot's server. Inference runs
on your computer. Your model requests are bound to your signed-in workspace.
This unsigned first release may trigger Windows SmartScreen. Only run a build
you obtained from the ResearchPilot application or a trusted project release.

To uninstall: uncheck startup in the connector, close it, and delete the
ResearchPilot folder from %LOCALAPPDATA%.
