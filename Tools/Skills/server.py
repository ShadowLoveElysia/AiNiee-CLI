"""
AiNiee Skills HTTP Server

A lightweight REST server that exposes the Skills framework over HTTP.
No MCP, no FastAPI, no uvicorn — just the Python standard library.

Endpoints:
    GET  /skills              — List all skills with metadata
    GET  /skills/<name>       — Describe a specific skill
    POST /skills/<name>       — Execute a skill with arguments
    GET  /health              — Health check

Usage:
    python Tools/Skills/server.py [--port PORT] [--host HOST]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict
from urllib.parse import urlparse


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Tools.Skills.skills import build_registry


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


class SkillsHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Skills server."""

    # Shared across all instances
    registry = build_registry()

    def log_message(self, format: str, *args: Any) -> None:
        """Log to stderr so stdout stays clean for potential JSONL consumers."""
        sys.stderr.write(f"[Skills] {args[0]} {args[1]} {args[2]}\n")

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str, code: str = "") -> None:
        self._send_json({"error": message, "error_code": code}, status)

    def _read_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._send_json({
                "status": "ok",
                "service": "ainiee-skills",
                "skills_count": self.registry.count,
            })
            return

        if path == "/skills":
            self._send_json({
                "skills": self.registry.list_skills(),
                "count": self.registry.count,
            })
            return

        if path.startswith("/skills/"):
            name = path[len("/skills/"):]
            try:
                meta = self.registry.get_skill_meta(name)
                self._send_json(meta)
            except Exception as e:
                self._send_error(404, str(e), "UNKNOWN_SKILL")
            return

        self._send_error(404, f"Not found: {path}", "NOT_FOUND")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/skills/"):
            name = path[len("/skills/"):]
            body = self._read_body()
            args = body.get("args", body)
            try:
                result = self.registry.execute(name, args)
                self._send_json(result.to_dict())
            except Exception as e:
                self._send_error(500, str(e), "EXECUTION_ERROR")
            return

        self._send_error(404, f"Not found: {path}", "NOT_FOUND")


def run_server(
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    """Start the Skills HTTP server."""
    server = HTTPServer((host, port), SkillsHTTPHandler)
    sys.stderr.write(
        f"[Skills] Server starting on http://{host}:{port}\n"
        f"[Skills] Endpoints:\n"
        f"[Skills]   GET  /health       — Health check\n"
        f"[Skills]   GET  /skills       — List all skills\n"
        f"[Skills]   GET  /skills/<name> — Describe a skill\n"
        f"[Skills]   POST /skills/<name> — Execute a skill\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[Skills] Shutting down...\n")
        server.server_close()


def run_server_detached(
    host: str = "127.0.0.1",
    port: int = 8766,
) -> HTTPServer:
    """Start server in a way that can be stopped programmatically."""
    server = HTTPServer((host, port), SkillsHTTPHandler)
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sys.stderr.write(f"[Skills] Server started (detached) on http://{host}:{port}\n")
    return server


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AiNiee Skills HTTP Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host address.")
    parser.add_argument("--port", type=int, default=8766, help="Port number.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        run_server(host=args.host, port=args.port)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
