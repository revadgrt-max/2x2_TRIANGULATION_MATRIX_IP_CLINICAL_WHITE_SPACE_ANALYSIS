"""Native Vercel Python Serverless Function serving POST /api/query.

Replaces the dev-only subprocess bridge (backend/cli.py + the old Next.js route.ts) now that this
app is hosted on Vercel: Vercel auto-deploys any *.py file under a project-root `api/` directory as
its own serverless function (Python runtime), using the classic BaseHTTPRequestHandler contract.
`requirements.txt` alongside the project root tells Vercel's build step which packages to install
(pandas, openai) before the function is packaged.

OPENAI_API_KEY must be set as a Vercel project environment variable (Project Settings -> Environment
Variables) -- this module does not load any .env file itself.
"""
import json
from http.server import BaseHTTPRequestHandler

from backend.agent import ask_agent


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
            query = str(payload.get("query", "")).strip()
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid request body."}, 400)
            return
        if not query:
            self._send_json({"error": "Query must not be empty."}, 400)
            return
        try:
            result = ask_agent(query)
            self._send_json(result, 200)
        except Exception as e:  # noqa: BLE001 -- top-level guard so the client always gets valid JSON
            print(f"Agent invocation failed: {e}")
            self._send_json(
                {"narrative": None, "tool_calls": [], "table": None,
                 "error": "The agent failed to respond. Please try again."},
                502,
            )

    def _send_json(self, payload: dict, status: int) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
