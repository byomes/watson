#!/usr/bin/env python3
"""
Theological Argument Evaluation Job — stdlib only, no external dependencies.
Run: PYTHONPATH=/home/billyomes/watson python3 jobs/theology/evaluate.py
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import sqlite3
from datetime import datetime

from ollama import Model
from python_dotenv import load_dotenv

PORT = 5100

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

load_dotenv()
API_KEY = os.getenv("THEOLOGY_API_KEY")
MODEL_PATH = os.getenv("THEOLOGY_MODEL_PATH")

db_path = os.path.expanduser("~/watson/data/watson.db")
log_path = os.path.expanduser("~/watson/logs/theology.log")


def log_error(message):
    with open(log_path, "a") as f:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        f.write(f"{timestamp} - ERROR: {message}\n")


class Handler(BaseHTTPRequestHandler):

    def _send(self, status: int, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in _CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        parts, _ = self._parse()
        try:
            data = self._read_json()
            argument_text = data.get("argument", "").strip()
            if not argument_text:
                self._send(400, {"error": "Argument text is required"})
                return

            model = Model(API_KEY, MODEL_PATH)
            response = model.evaluate_theological_argument(argument_text)

            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute(
                "INSERT INTO theology_evaluations (argument, result, timestamp) VALUES (?, ?, ?)",
                (argument_text, json.dumps(response), datetime.now()),
            )
            conn.commit()
            conn.close()

            self._send(201, {"response": response})

        except json.JSONDecodeError:
            self._send(400, {"error": "Invalid JSON"})
        except Exception as e:
            log_error(f"Failed to evaluate argument: {e}")
            self._send(500, {"error": str(e)})

    def _parse(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        qs = parse_qs(parsed.query)
        return parts, qs


def run() -> str:
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute(
            "CREATE TABLE IF NOT EXISTS theology_evaluations (id INTEGER PRIMARY KEY AUTOINCREMENT, argument TEXT, result TEXT, timestamp DATETIME)"
        )
        conn.commit()
        conn.close()

        server = HTTPServer(("", PORT), Handler)
        print(f"[theology-api] Listening on port {PORT}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("[theology-api] Shutting down.")
            server.server_close()

        return f"Theological Evaluation API: Running on port {PORT}."
    except Exception as exc:
        return f"Theological Evaluation API unavailable: {exc}"


if __name__ == "__main__":
    print(run())
