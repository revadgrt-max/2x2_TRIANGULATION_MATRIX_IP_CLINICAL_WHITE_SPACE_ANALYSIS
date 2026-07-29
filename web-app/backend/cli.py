"""Dev-only CLI entrypoint: reads {"query": "..."} as a single JSON line from stdin, runs the agent,
and prints the structured result dict as one JSON line to stdout. Invoked by the Next.js API route
(local dev) via a Python subprocess -- this is a stopgap for local iteration BEFORE Phase 4 (Vercel
hosting), which will need a native Vercel Python serverless function instead (see backend/README.md).

OPENAI_API_KEY must already be present in this process's environment (passed through by the parent
Node process from web-app/.env.local) -- this script does NOT load .env files itself.
"""
import json
import sys

from .agent import ask_agent


def main():
    raw = sys.stdin.readline()
    try:
        payload = json.loads(raw)
        query = payload["query"]
    except (json.JSONDecodeError, KeyError) as e:
        print(json.dumps({"narrative": None, "tool_calls": [], "table": None,
                           "error": f"Invalid request payload: {e}"}))
        return
    try:
        result = ask_agent(query)
    except Exception as e:  # noqa: BLE001 -- top-level guard so the Node side always gets valid JSON
        result = {"narrative": None, "tool_calls": [], "table": None, "error": f"Agent failed: {e}"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
