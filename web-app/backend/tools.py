"""The ONLY functions the LLM agent is ever allowed to call. Ported verbatim (logic-identical) from the
validated Phase-1 notebook (input/app_data_analytics.ipynb) -- every filter is validated against
KNOWN_VOCAB so the agent can't silently invent a nonexistent value, and every function returns a small,
already-aggregated pandas DataFrame + a plain-language note, never a raw row dump."""
import os
import re
import tempfile

import pandas as pd

from . import multitarget_locked_whitespace_workflow as MTW
from .data_loader import CLINICAL_CSV, IP_WHITESPACE_CSV, load_data
ip_landscape_df, ip_whitespace_df, clinical_df, KNOWN_VOCAB = load_data()

_IP_SORT_COLS = ["filing_year", "application_date", "publication_date", "authority", "source_country",
                  "primary_assignee", "target_harmonized", "indications"]
_PHASE_ORDER = {"PHASE4": 4, "PHASE3": 3, "PHASE2": 2, "PHASE1": 1, "EARLY_PHASE1": 0.5, "UNKNOWN": -1}


class ToolError(Exception):
    """User-facing tool error (unknown column/value, no matching data, etc). The message is safe to
    show directly to the user -- tool functions never let a raw stack trace / file path escape."""
    pass


def _validate_choice(value, vocab_key, label):
    if value is None:
        return None
    vocab = KNOWN_VOCAB[vocab_key]
    match = next((v for v in vocab if v.upper() == str(value).upper()), None)
    if match is None:
        raise ToolError(f"Unknown {label} '{value}'. Known values: {', '.join(vocab)}")
    return match


def top_n_sponsors(modality=None, indication_contains=None, n=10):
    """Top-N sponsors (assignee) by patent count."""
    modality = _validate_choice(modality, "ip_modality_code", "modality_code")
    df = ip_landscape_df
    if modality:
        df = df[df["modality_code"] == modality]
    if indication_contains:
        df = df[df["indications"].str.contains(re.escape(indication_contains), case=False, na=False)]
    if df.empty:
        raise ToolError("No IP records matched those filters.")
    counts = (df["primary_assignee"].dropna().value_counts().head(max(1, int(n)))
              .rename_axis("sponsor").reset_index(name="patent_count"))
    note = (f"Top {len(counts)} sponsors by patent count"
            + (f", modality={modality}" if modality else "")
            + (f", indication contains '{indication_contains}'" if indication_contains else "")
            + f" (n={len(df)} matching patents). 'sponsor' = primary assignee (first pipe-segment of "
              f"current_assignee) -- a heuristic; a few rows list an individual inventor, not a company.")
    return counts, note


def sort_ip_patents(modality=None, sort_by="filing_year", ascending=True, indication_contains=None, limit=100):
    """Sort/filter IP patent rows by a column, optionally filtered by modality/indication."""
    modality = _validate_choice(modality, "ip_modality_code", "modality_code")
    if sort_by not in _IP_SORT_COLS:
        raise ToolError(f"Cannot sort by '{sort_by}'. Choose one of: {', '.join(_IP_SORT_COLS)}")
    df = ip_landscape_df
    if modality:
        df = df[df["modality_code"] == modality]
    if indication_contains:
        df = df[df["indications"].str.contains(re.escape(indication_contains), case=False, na=False)]
    if df.empty:
        raise ToolError("No IP records matched those filters.")
    cols = ["publication_number", "title", "primary_assignee", "filing_year", "authority", "source_country",
            "modality_code", "target_harmonized", "indications"]
    out = df[cols].sort_values(sort_by, ascending=bool(ascending), na_position="last").head(int(limit))
    note = (f"{len(df)} {modality or 'all-modality'} patents sorted by {sort_by} "
            f"({'asc' if ascending else 'desc'}), showing top {len(out)}.")
    if sort_by == "authority" and df["authority"].nunique() <= 1:
        note += (" NOTE: this dataset's 'authority' field is almost entirely 'WO' (PCT international "
                 "filing) -- use 'source_country' instead for a real per-country breakdown of where "
                 "sponsors filed from.")
    if sort_by == "source_country" and df["source_country"].isna().all():
        note += " NOTE: no source-country data could be derived for these rows."
    return out, note


def group_breakdown(dataset="ip", group_by="modality_code", filters=None, top_n=None):
    """Generic count-by-group breakdown over the ip or clinical basic-tier table."""
    df = {"ip": ip_landscape_df, "clinical": clinical_df}.get(dataset)
    if df is None:
        raise ToolError("dataset must be 'ip' or 'clinical'.")
    if group_by not in df.columns:
        raise ToolError(f"Unknown column '{group_by}' for dataset '{dataset}'.")
    for col, val in (filters or {}).items():
        if col not in df.columns:
            raise ToolError(f"Unknown filter column '{col}' for dataset '{dataset}'.")
        df = df[df[col] == val]
    if df.empty:
        raise ToolError("No records matched those filters.")
    counts = df[group_by].value_counts(dropna=False)
    if top_n:
        counts = counts.head(int(top_n))
    out = counts.rename_axis(group_by).reset_index(name="count")
    note = f"Count of {dataset} records grouped by {group_by} (n={len(df)} matching records)."
    return out, note


def compare_target_ip_vs_clinical(target_harmonized):
    """IP crowding snapshot vs clinical trial activity/outcomes for ONE harmonized target."""
    key = str(target_harmonized).strip().upper()
    ip_rows = ip_landscape_df[ip_landscape_df["target_harmonized"].str.upper() == key]
    clin_rows = clinical_df[clinical_df["target_harmonized"].str.upper() == key]
    if ip_rows.empty and clin_rows.empty:
        raise ToolError(f"No IP or clinical records found for target_harmonized='{target_harmonized}'.")
    outcome_counts = clin_rows["outcome"].value_counts().to_dict()
    furthest_phase = (max(clin_rows["phase_group"], key=lambda p: _PHASE_ORDER.get(p, -1))
                      if not clin_rows.empty else None)
    row = {
        "target_harmonized": target_harmonized,
        "patent_count": len(ip_rows),
        "ip_modalities": "|".join(sorted(ip_rows["modality_code"].dropna().unique())) or None,
        "top_ip_sponsors": ", ".join(ip_rows["primary_assignee"].value_counts().head(3).index.tolist()) or None,
        "earliest_filing_year": (int(ip_rows["filing_year"].min())
                                  if ip_rows["filing_year"].notna().any() else None),
        "trial_count": len(clin_rows),
        "clinical_modalities": "|".join(sorted(clin_rows["modality_code"].dropna().unique())) or None,
        "furthest_phase_reached": furthest_phase,
        "approved_trials": int(outcome_counts.get("approved", 0)),
        "positive_trials": int(outcome_counts.get("positive", 0)),
        "negative_trials": int(outcome_counts.get("negative", 0)),
        "terminated_trials": int(outcome_counts.get("terminated", 0)),
        "ongoing_or_pending_trials": int(outcome_counts.get("ongoing", 0) + outcome_counts.get("pending", 0)),
    }
    note = (f"IP vs clinical snapshot for target_harmonized='{target_harmonized}' "
            f"({len(ip_rows)} patents, {len(clin_rows)} trials).")
    return pd.DataFrame([row]), note


def run_whitespace_2x2(indication_or_tumor_type=None, target_harmonized=None, by_modality=False, sample_n=0):
    """2x2/triangulation placement via multitarget_locked_whitespace_workflow.MTW.run(), scoped to an
    indication/tumor type if given and/or to a single target."""
    terms = ([t.strip().lower() for t in re.split(r"[,/]", indication_or_tumor_type) if t.strip()]
             if indication_or_tumor_type else
             [t.strip().lower() for t in MTW.DEF_INDICATION.split(",")])
    clin_csv_path, tmp_clin_name = CLINICAL_CSV, None
    if indication_or_tumor_type:
        mask = pd.Series(False, index=clinical_df.index)
        for col in ("m_conditions", "ctgov_conditions"):
            if col in clinical_df.columns:
                mask = mask | clinical_df[col].fillna("").str.lower().apply(
                    lambda s: any(t in s for t in terms))
        scoped = clinical_df[mask]
        if scoped.empty:
            raise ToolError(f"No clinical trials found matching indication/tumor-type terms {terms}.")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
        scoped.to_csv(tmp.name, index=False)
        clin_csv_path, tmp_clin_name = tmp.name, tmp.name
    try:
        with tempfile.TemporaryDirectory() as tmp_out:
            rows = MTW.run(
                ip_csv=str(IP_WHITESPACE_CSV), clin_csv=str(clin_csv_path),
                as_of=MTW.DEF_AS_OF, indication_terms=terms, outdir=tmp_out,
                key_col=MTW.DEF_KEY_COL, mod_col=MTW.DEF_MOD_COL,
                by_modality=bool(by_modality), sample_n=int(sample_n or 0), seed=7,
            )
    finally:
        if tmp_clin_name:
            os.unlink(tmp_clin_name)
    df = pd.DataFrame(rows)
    # CD3 alone is a generic T-cell-engager anchor arm, not a real biological target of interest --
    # exclude it as a standalone node (combos like "B7-H3|CD3" are kept, since those name a real target pair).
    df = df[df["target_harmonized"].str.upper() != "CD3"]
    if target_harmonized:
        key = str(target_harmonized).strip().upper()
        df = df[df["target_harmonized"].str.upper() == key]
        if df.empty:
            raise ToolError(f"No 2x2/whitespace placement found for target_harmonized='{target_harmonized}'.")
    note = (f"2x2 whitespace/triangulation placement ({'target x modality' if by_modality else 'target-level'}), "
            f"indication/tumor-type terms={terms}, as-of {MTW.DEF_AS_OF}. Quadrants: TRUE WHITE SPACE "
            f"(validated biology, open IP), BATTLEGROUND (validated, contested IP), R&D TRAP (unproven, "
            f"open IP), RED FLAGS (unproven, contested). {len(df)} node(s) returned.")
    return df.sort_values(["quadrant", "clinical_performance_Y"], ascending=[True, False]), note


TOOL_REGISTRY = {
    "top_n_sponsors": top_n_sponsors,
    "sort_ip_patents": sort_ip_patents,
    "group_breakdown": group_breakdown,
    "compare_target_ip_vs_clinical": compare_target_ip_vs_clinical,
    "run_whitespace_2x2": run_whitespace_2x2,
}
