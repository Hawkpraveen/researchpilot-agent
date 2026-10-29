"""ResearchPilot desktop connector: outbound HTTPS polling, loopback-only pairing.

Standard-library runtime; package with PyInstaller for users without Python.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

VERSION = "1.0.0"
API = "https://researchpilot-be.onrender.com/api/v1"
WEB = "https://researchpilot-fe.praveen-pk200109.workers.dev"
PORT = 11436
MAX_BYTES = 256 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_json(url, payload=None, token=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("The response is too large.")
        return json.loads(data) if data else {}


class Connector:
    def __init__(self, api=API, web=WEB, state_path=None):
        self.api = api.rstrip("/")
        self.web = web.rstrip("/")
        self.state_path = state_path or Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".config"))) / "ResearchPilot" / "connector.json"
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.token = ""
        self.models = []
        self.message = "Open ResearchPilot Settings to connect this computer."
        self.working = False
        self.model_cache = {}
        try:
            saved = json.loads(self.state_path.read_text())
            if saved.get("api") == self.api:
                self.token = saved.get("token", "")
        except (OSError, ValueError):
            pass

    def pair(self, code):
        result = request_json(self.api + "/connectors/pair", {"code": code, "name": platform.node()[:80] or "My computer"})
        with self.lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"api": self.api, "token": result["token"]}))
            temporary.chmod(0o600)
            temporary.replace(self.state_path)
            self.token = result["token"]
            self.message = "Connected. Choose an installed model in ResearchPilot."

    def generate(self, task, token):
        try:
            payload = task["payload"]
            # Only native non-streaming chat with a locally installed model.
            if payload.get("model") not in self.models or payload.get("stream") is not False:
                raise ValueError("The selected model is not installed.")
            result = request_json("http://127.0.0.1:11434/api/chat", payload, timeout=110)
            request_json(self.api + "/connectors/runtime/results", {"taskId": task["id"], "result": result}, token)
            self.message = "Ready. Your local model is connected."
        except Exception:
            self.message = "The local model could not finish. Check Ollama or select a smaller model."
            try:
                request_json(self.api + "/connectors/runtime/results", {"taskId": task["id"], "error": "Local model unavailable or timed out."}, token)
            except Exception:
                pass
        finally:
            self.working = False

    def run(self):
        retry = 2
        while not self.stop.is_set():
            try:
                tags = request_json("http://127.0.0.1:11434/api/tags", timeout=5)
                installed = [model["name"] for model in tags.get("models", []) if isinstance(model.get("name"), str)][:200]
                for name in installed:
                    if name not in self.model_cache:
                        details = request_json("http://127.0.0.1:11434/api/show", {"model": name}, timeout=5)
                        self.model_cache[name] = "completion" in details.get("capabilities", [])
                self.models = [name for name in installed if self.model_cache.get(name)]
                with self.lock:
                    token = self.token
                if not token:
                    self.message = "Ollama found. Open ResearchPilot Settings to connect."
                else:
                    response = request_json(self.api + "/connectors/runtime/poll", {"models": self.models}, token, timeout=25)
                    self.message = "Running your model…" if self.working else "Connected. Choose a model in ResearchPilot."
                    task = response.get("task")
                    if task and not self.working:
                        self.working = True
                        threading.Thread(target=self.generate, args=(task, token), daemon=True).start()
                retry = 2
            except HTTPError as error:
                if error.code == 401:
                    with self.lock:
                        self.token = ""
                    self.message = "Connection expired. Open ResearchPilot Settings to reconnect."
                else:
                    self.message = "ResearchPilot is reconnecting…"
                retry = min(20, retry * 2)
            except (OSError, URLError, ValueError):
                self.message = "Waiting for Ollama and an internet connection…"
                retry = min(20, retry * 2)
            self.stop.wait(retry)


def control_server(connector, port=PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log pairing codes, tokens, prompts or responses.

        def allowed(self):
            return self.headers.get("Origin") == connector.web and self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def reply(self, status, payload=None):
            self.send_response(status)
            if self.allowed():
                self.send_header("Access-Control-Allow-Origin", connector.web)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if payload is not None:
                self.wfile.write(json.dumps(payload).encode())

        def do_OPTIONS(self):
            self.reply(204 if self.allowed() else 403)

        def do_GET(self):
            if not self.allowed():
                return self.reply(403)
            if self.path != "/status":
                return self.reply(404)
            self.reply(200, {"running": True, "paired": bool(connector.token), "version": VERSION})

        def do_POST(self):
            if not self.allowed():
                return self.reply(403)
            if self.path != "/pair":
                return self.reply(404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 512:
                    return self.reply(413)
                body = json.loads(self.rfile.read(length))
                code = body.get("code")
                if not isinstance(code, str) or not 30 <= len(code) <= 100 or set(body) != {"code"}:
                    return self.reply(400)
                connector.pair(code)
                self.reply(200, {"paired": True})
            except Exception:
                self.reply(502, {"error": "Could not pair. Try connecting again in Settings."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def install_windows():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    target = Path(os.environ["LOCALAPPDATA"]) / "ResearchPilot" / "ResearchPilot Connector.exe"
    if Path(sys.executable).resolve() != target.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sys.executable, target)
        subprocess.Popen([str(target)], creationflags=subprocess.CREATE_NO_WINDOW)
        raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--api", default=API)
    parser.add_argument("--web", default=WEB)
    parser.add_argument("--self-test", metavar="OUTPUT_FILE")
    args = parser.parse_args()
    if args.self_test:
        import tkinter
        Path(args.self_test).write_text(json.dumps({"version": VERSION, "tk": tkinter.Tcl().eval("info patchlevel"), "ok": True}))
        return
    for url in (args.api, args.web):
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}):
            parser.error("Use HTTPS, or an explicit localhost development URL.")
    install_windows()
    connector = Connector(args.api, args.web)
    try:
        server = control_server(connector)
    except OSError:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(0, "ResearchPilot Connector is already running. Open Settings in ResearchPilot.", "ResearchPilot Connector", 0)
        return
    threading.Thread(target=connector.run, daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    import tkinter as tk
    import webbrowser
    root = tk.Tk()
    root.title("ResearchPilot Connector")
    root.geometry("460x230")
    root.resizable(False, False)
    root.configure(bg="#101829")
    tk.Label(root, text="ResearchPilot Connector", font=("Segoe UI", 17, "bold"), fg="#f3f5ff", bg="#101829").pack(pady=(22, 12))
    status = tk.StringVar(value=connector.message)
    tk.Label(root, textvariable=status, wraplength=410, fg="#b7c2d8", bg="#101829", font=("Segoe UI", 10)).pack(pady=8)
    tk.Button(root, text="Open ResearchPilot", command=lambda: webbrowser.open(connector.web + "/workspace/settings"), padx=20, pady=7).pack(pady=10)
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import winreg
        startup = tk.BooleanVar()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                startup.set(bool(winreg.QueryValueEx(key, "ResearchPilotConnector")[0]))
        except OSError:
            pass
        def change_startup():
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                if startup.get():
                    winreg.SetValueEx(key, "ResearchPilotConnector", 0, winreg.REG_SZ, f'"{sys.executable}" --background')
                else:
                    try:
                        winreg.DeleteValue(key, "ResearchPilotConnector")
                    except FileNotFoundError:
                        pass
        tk.Checkbutton(root, text="Start when I sign in to Windows", variable=startup, command=change_startup, bg="#101829", fg="#b7c2d8", selectcolor="#26314a").pack()
    def refresh():
        status.set(connector.message)
        root.after(1000, refresh)
    def close():
        connector.stop.set()
        server.shutdown()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)
    if args.background:
        root.iconify()
    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()
