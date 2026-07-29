"""Function-calling schema, persona, and dispatch loop -- ported from the validated Phase-1 notebook
(input/app_data_analytics.ipynb), adapted to return a structured result dict (for the web API route)
instead of just printing to a notebook cell."""
import json

from openai import OpenAI

from .tools import KNOWN_VOCAB, TOOL_REGISTRY, ToolError, _IP_SORT_COLS

client = OpenAI()

TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "top_n_sponsors",
        "description": "Top-N sponsors (assignee companies) ranked by patent count, optionally filtered "
                        "by modality and/or an indication keyword. Use for 'top N sponsors/companies for X'.",
        "parameters": {"type": "object", "properties": {
            "modality": {"type": ["string", "null"], "enum": KNOWN_VOCAB["ip_modality_code"],
                         "description": "Filter to one IP modality code, or omit for all modalities."},
            "indication_contains": {"type": ["string", "null"],
                                     "description": "Free-text substring to match against the indications field."},
            "n": {"type": "integer", "description": "How many top sponsors to return.", "default": 10},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "sort_ip_patents",
        "description": "Sort/filter IP patent rows by a column (e.g. filing year, assignee, target). Use "
                        "for 'sort/list the X patents by Y'. For 'sponsor's country of origin' / 'source "
                        "country' questions use sort_by='source_country' (derived from the PCT filing "
                        "office, i.e. where the applicant filed from) -- NOT 'authority', which is almost "
                        "entirely 'WO' (international PCT publication) and carries no country signal.",
        "parameters": {"type": "object", "properties": {
            "modality": {"type": ["string", "null"], "enum": KNOWN_VOCAB["ip_modality_code"]},
            "sort_by": {"type": "string", "enum": _IP_SORT_COLS, "default": "filing_year"},
            "ascending": {"type": "boolean", "default": True},
            "indication_contains": {"type": ["string", "null"],
                                     "description": "Free-text substring to match against the indications field."},
            "limit": {"type": "integer", "default": 100},
        }, "required": ["sort_by"]},
    }},
    {"type": "function", "function": {
        "name": "group_breakdown",
        "description": "Count-by-group breakdown of the IP or clinical dataset (e.g. patents by "
                        "source_country, trials by outcome/phase_group). Use for 'how many X by Y' / "
                        "distribution/count questions, including 'patent COUNT by source country'.",
        "parameters": {"type": "object", "properties": {
            "dataset": {"type": "string", "enum": ["ip", "clinical"]},
            "group_by": {"type": "string",
                         "description": "Column to group by. For dataset='ip', typical choices: "
                                        "modality_code, source_country (sponsor's country of origin, via "
                                        "PCT filing office), authority, target_harmonized, primary_assignee, "
                                        "filing_year. For dataset='clinical', typical choices: modality_code, "
                                        "outcome, phase_group, target_harmonized, lead_sponsor.",
            },
            "filters": {"type": ["object", "null"],
                        "description": "Optional exact-match column:value filters, e.g. {\"modality_code\": \"ADC\"}."},
            "top_n": {"type": ["integer", "null"]},
        }, "required": ["dataset", "group_by"]},
    }},
    {"type": "function", "function": {
        "name": "compare_target_ip_vs_clinical",
        "description": "Cross-dataset snapshot for ONE harmonized target: IP crowding (patent count, top "
                        "sponsors, modalities) vs clinical activity/outcomes (trial count, furthest phase, "
                        "approved/positive/negative/terminated counts). Use for 'compare IP vs clinical "
                        "progress for target X'.",
        "parameters": {"type": "object", "properties": {
            "target_harmonized": {"type": "string", "description": "Harmonized target symbol, e.g. HER2, EGFR, CLDN18.2."},
        }, "required": ["target_harmonized"]},
    }},
    {"type": "function", "function": {
        "name": "run_whitespace_2x2",
        "description": "2x2 whitespace/triangulation matrix (epitope crowding X-axis vs clinical performance "
                        "Y-axis) placing targets into TRUE WHITE SPACE / BATTLEGROUND / R&D TRAP / RED FLAGS "
                        "quadrants. Use for '2x2', 'whitespace', 'triangulation' questions, optionally scoped "
                        "to an indication/tumor type and/or a single target.",
        "parameters": {"type": "object", "properties": {
            "indication_or_tumor_type": {"type": ["string", "null"],
                                         "description": "e.g. 'pancreatic cancer'. Omit for the workflow's default indication scope."},
            "target_harmonized": {"type": ["string", "null"],
                                  "description": "Restrict the result to one target's placement, or omit for all nodes."},
            "by_modality": {"type": "boolean", "default": False,
                            "description": "True = place target x modality combinations separately."},
            "sample_n": {"type": "integer", "default": 0, "description": "0 = no subsampling of the node grid."},
        }, "required": []},
    }},
]

SYSTEM_PROMPT = """You are the Data Analytics Agent for an oncology antibody-therapeutics IP and clinical \
intelligence tool, used by board members. You have 10-15 years of hands-on experience in oncology \
biologics R&D, patent/IP strategy, and clinical development -- you understand modalities (mAb, ADC, \
bispecific, BiTE, CAR-T, radioligand, PROTAC), trial phases, and patent landscaping conventions.

Rules you must always follow:
1. You never write or execute freeform code/SQL against the raw data. You may ONLY answer by calling one \
of the provided tool functions. If no tool fits the question, say so plainly instead of guessing.
2. You never see or fabricate raw data rows -- you only receive small, already-aggregated tool results. \
Narrate ONLY from those results; never invent numbers, sponsor names, or targets not present in the result.
3. Always begin your final answer with a short "Interpreted as:" line stating the filters/parameters you \
extracted from the user's question (modality, indication, target, sort column, etc.), so a wrong \
interpretation is visible rather than silently wrong.
4. If a tool call fails (unknown value, no matching data), explain the limitation plainly to the user \
(e.g. list the valid known values if given) rather than retrying blindly or apologizing excessively.
5. Preserve important data-quality caveats surfaced by the tools verbatim (e.g. the patent 'authority' \
field being almost entirely WO/PCT filings with no real per-country breakdown, or 'sponsor' being a \
heuristic first-segment of a multi-assignee field) -- do not smooth these over.
6. Be concise and board-appropriate: lead with the headline finding, then supporting detail.
7. For run_whitespace_2x2 results specifically: the UI already renders a full quadrant scatter plot and \
per-quadrant target lists, so do NOT repeat a per-target/per-quadrant breakdown in your narrative. Keep it \
to 2-3 sentences: the total node count, how many targets fell in each quadrant, and one headline callout \
(e.g. the strongest TRUE WHITE SPACE candidate) -- nothing more."""


def _dispatch_tool_call(name, args_json):
    """Executes one LLM-requested tool call against the real Python functions. ToolError becomes a
    normal (non-crashing) error message fed back to the model. Returns (json_string, structured_result)."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        payload = {"error": f"Unknown tool '{name}'."}
        return json.dumps(payload), payload
    try:
        args = json.loads(args_json or "{}")
        args = {k: v for k, v in args.items() if v is not None}
        result_df, note = fn(**args)
        payload = {
            "note": note,
            "columns": list(result_df.columns),
            "rows": json.loads(result_df.head(50).to_json(orient="records")),
            "row_count_returned": len(result_df),
        }
        return json.dumps(payload), payload
    except ToolError as e:
        payload = {"error": str(e)}
        return json.dumps(payload), payload
    except TypeError as e:
        payload = {"error": f"Invalid arguments for '{name}': {e}"}
        return json.dumps(payload), payload


def ask_agent(user_query, model="gpt-4o-mini", max_rounds=4):
    """Runs the full NLQ -> tool-call -> narration loop for one user question.

    Returns a dict: {
        "narrative": str,                # the final board-facing answer text
        "tool_calls": [{"name": str, "arguments": dict}],   # what the agent chose to call
        "table": {"columns": [...], "rows": [...]} | None,  # last successful tool result, for UI table/chart
        "error": str | None,             # set if the agent never produced tool results (rare)
    }
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_query}]
    tool_call_log = []
    last_table = None
    for round_idx in range(max_rounds):
        # Force a tool call on the first round -- otherwise a small model can narrate its *intent*
        # instead of actually invoking a function. Subsequent rounds go back to "auto".
        tool_choice = "required" if round_idx == 0 else "auto"
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=TOOLS_SCHEMA, tool_choice=tool_choice)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return {"narrative": msg.content, "tool_calls": tool_call_log, "table": last_table, "error": None}
        messages.append(msg)
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            tool_call_log.append({"name": tc.function.name, "arguments": args})
            result_json, payload = _dispatch_tool_call(tc.function.name, tc.function.arguments)
            if "error" not in payload:
                last_table = {"columns": payload["columns"], "rows": payload["rows"], "note": payload["note"]}
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_json})
    return {
        "narrative": "The agent did not converge to a final answer within the round limit.",
        "tool_calls": tool_call_log, "table": last_table, "error": "max_rounds_exceeded",
    }
