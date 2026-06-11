import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import requests
from ollama.api import system_prompt_update
from python_dotenv import load_dotenv

DATABASE_PATH = "~/watson/data/watson.db"
LOGS_PATH = "~/watson/logs/"

load_dotenv()


class SystemPromptUpdateHandler(BaseHTTPRequestHandler):

    # ── Response helpers ───────────────────────────────────────

    def _send(self, status: int, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def _parse(self):
        """Return (path_parts, query_string_dict)."""
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        qs = parse_qs(parsed.query)
        return parts, qs

    def _log_error(self, message: str):
        with open(os.path.expanduser(LOGS_PATH), "a") as log_file:
            log_file.write(f"[{datetime.now()}] {message}\n")

    # ── GET ────────────────────────────────────────────────────

    def do_GET(self):
        parts, _ = self._parse()
        try:
            if parts == ["update_system_prompt"]:
                system_prompt_update()
                self._send(200, {"status": "System prompt updated successfully"})
            else:
                self._send(404, {"error": "Not found"})

        except Exception as e:
            self._log_error(f"Error updating system prompt: {e}")
            self._send(500, {"error": str(e)})

    # ── CORS preflight ─────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()


def run() -> str:
    """Return a summary of the System Prompt Update service."""
    try:
        return "[System Prompt Update] Service available."
    except Exception as exc:
        return f"[System Prompt Update] Unavailable: {exc}"


if __name__ == "__main__":
    server = HTTPServer(("", PORT), SystemPromptUpdateHandler)
    print(f"[System Prompt Update] Listening on port {PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[System Prompt Update] Shutting down.")
        server.server_close()
