# Backend package (Data Analytics Agent)

Ported, logic-identical copy of the validated Phase-1 notebook
(`input/app_data_analytics.ipynb`): the tool functions, `KNOWN_VOCAB`, the OpenAI
function-calling schema, the "Data Analytics Agent" persona, and the dispatch loop.

- `data_loader.py` -- loads the Phase-0 trimmed CSVs under `data/` and derives
  `primary_assignee` / `phase_group` / `KNOWN_VOCAB`.
- `tools.py` -- the 5 tool functions the LLM is ever allowed to call
  (`top_n_sponsors`, `sort_ip_patents`, `group_breakdown`,
  `compare_target_ip_vs_clinical`, `run_whitespace_2x2`), plus `ToolError`.
- `multitarget_locked_whitespace_workflow.py`, `locked_whitespace_workflow_csv_v3_param.py`,
  `epitope.py` -- the 2x2/whitespace/triangulation engine (all three required together,
  see `/memories/repo/ip_landscape_report_pipeline.md` and `/memories/session/plan.md`
  for why `epitope.py` is an import-time-only dependency with no runtime LLM call in
  this app's query paths).
- `agent.py` -- `TOOLS_SCHEMA`, `SYSTEM_PROMPT`, and `ask_agent(query)` -> structured
  `{narrative, tool_calls, table, error}` dict.
- `cli.py` -- **dev-only** stdin/stdout JSON bridge, used for standalone testing
  without the Next.js server (see below). Not used in production.

## Production (Vercel)

`api/query.py` (project-root `api/` directory) is a native Vercel Python Serverless
Function that imports `backend.agent.ask_agent` directly -- no subprocess involved.
Vercel auto-detects any `*.py` file under `api/` and deploys it as its own function,
using `requirements.txt` (alongside the project root) to install `pandas`/`openai` at
build time. The frontend's `fetch("/api/query")` call hits this function directly.

Set `OPENAI_API_KEY` as a Vercel project environment variable (Project Settings ->
Environment Variables) before deploying -- `api/query.py` does not load any `.env`
file itself.

## Running standalone (for testing without the Next.js server)

```bash
cd web-app
source ../.venv-1/bin/activate
echo '{"query": "top 10 sponsors for bispecific antibody"}' | python3 -m backend.cli
```

`OPENAI_API_KEY` must be present in the environment (loaded from the project-root
`.env` when testing via the notebook, or from `web-app/.env.local` when testing via
the Next.js dev server -- `cli.py` itself does not call `load_dotenv()`).
