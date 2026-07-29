"""
build_clinical_ip_comparison_notebook.py
=========================================
Builds input/clinical_ip_comparison_final_version1.ipynb end-to-end: every
code cell is actually executed in-process (DuckDB SQL + pandas + matplotlib)
against the two RAW source files:
    - input/ip_final_version3.csv        (patents, target_harmonized)
    - input/clinical_final_version1.csv  (trials, target_harmonized)

Two parts:
  PART A -- reproduces (does not re-invent) the whitespace/crowded tier logic
            already coded in input/ip_landscape_final_version1.ipynb (Entry 5D
            quadrant classification) and input/clinical_landscape_final_version2.ipynb
            (Entry 11 scorecard, Entry 13 crowding top-20, Entry 31 Phase-1/2
            whitespace model), then cross-compares the two notebooks' own
            conclusions target-by-target on `target_harmonized`.
  PART B -- independent triangulation directly from the two raw CSVs (not
            derived from either prior notebook): modality-adjusted crowding,
            filing-to-first-trial timing lag, validated/patent-thin and
            patent-crowded/unproven flags, the CAR-T IP blind spot, and
            approximate sponsor/assignee overlap.

Mirrors the established pipeline convention: after the notebook is built,
every table is exported to output/clinical_ip_comparison_version1/data/*.csv,
every chart to output/clinical_ip_comparison_version1/figures/*.png, and a
copy of the notebook is placed alongside them.

Run:  python3 build_clinical_ip_comparison_notebook.py
"""
import ast
import base64
import contextlib
import io
import json
import os
import re
import shutil
import textwrap

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
IP_CSV = os.path.join(ROOT, "input", "ip_final_version3.csv")
CL_CSV = os.path.join(ROOT, "input", "clinical_final_version1.csv")
NOTEBOOK_PATH = os.path.join(ROOT, "input", "clinical_ip_comparison_final_version1.ipynb")
OUT_DIR = os.path.join(ROOT, "output", "clinical_ip_comparison_version1")
DATA_DIR = os.path.join(OUT_DIR, "data")
FIG_DIR = os.path.join(OUT_DIR, "figures")

pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)
pd.set_option("display.width", 160)

# ---------------------------------------------------------------------------
# Mini "notebook kernel" (same pattern as build_clinical_landscape_v2_notebook.py)
# ---------------------------------------------------------------------------
EXPORTED_TABLES = []
EXPORTED_FIGS = []
_TABLE_COUNTER = 0
_FIG_COUNTER = 0


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=125, bbox_inches="tight", facecolor="white")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def run_cell(code, ns, exec_count, entry_slug):
    global _TABLE_COUNTER, _FIG_COUNTER
    src = textwrap.dedent(code).strip("\n")
    tree = ast.parse(src, mode="exec")
    body = list(tree.body)
    last_expr = None
    if body and isinstance(body[-1], ast.Expr):
        last_expr = body.pop()

    display_queue = []

    def display(obj, name=None):
        display_queue.append((name, obj))

    ns["display"] = display

    stdout_buf = io.StringIO()
    outputs = []
    with contextlib.redirect_stdout(stdout_buf):
        if body:
            exec(compile(ast.Module(body=body, type_ignores=[]), "<cell>", "exec"), ns)
        last_val = None
        if last_expr is not None:
            last_val = eval(compile(ast.Expression(last_expr.value), "<cell>", "eval"), ns)

        stdout_text = stdout_buf.getvalue()
        if stdout_text:
            outputs.append({"output_type": "stream", "name": "stdout",
                             "text": stdout_text.splitlines(keepends=True)})

        for name, obj in display_queue:
            outputs.extend(_render_object(obj, exec_count, entry_slug, name))

        for num in plt.get_fignums():
            fig = plt.figure(num)
            b64 = _fig_to_b64(fig)
            _FIG_COUNTER += 1
            fname = f"{entry_slug}_fig{_FIG_COUNTER:02d}.png"
            EXPORTED_FIGS.append((fname, base64.b64decode(b64)))
            outputs.append({"output_type": "display_data",
                             "data": {"image/png": b64}, "metadata": {}})
        plt.close("all")

        if last_expr is not None:
            outputs.extend(_render_object(last_val, exec_count, entry_slug, None,
                                           as_execute_result=True))
    return outputs


def _render_object(obj, exec_count, entry_slug, name, as_execute_result=False):
    global _TABLE_COUNTER
    out_type = "execute_result" if as_execute_result else "display_data"
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        if isinstance(obj, pd.Series):
            obj = obj.to_frame()
        _TABLE_COUNTER += 1
        tname = name or f"{entry_slug}_table{_TABLE_COUNTER:02d}"
        EXPORTED_TABLES.append((tname, obj.copy()))
        data = {"text/html": obj.to_html(max_rows=60, na_rep="-"), "text/plain": repr(obj)}
        d = {"output_type": out_type, "data": data, "metadata": {"name": tname}}
        if as_execute_result:
            d["execution_count"] = exec_count
        return [d]
    elif obj is None:
        return []
    else:
        d = {"output_type": out_type, "data": {"text/plain": repr(obj)}, "metadata": {}}
        if as_execute_result:
            d["execution_count"] = exec_count
        return [d]


def md_cell(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code_cell(src, outputs, exec_count):
    return {"cell_type": "code", "metadata": {}, "execution_count": exec_count,
            "outputs": outputs,
            "source": textwrap.dedent(src).strip("\n").splitlines(keepends=True)}


NS = {"pd": pd, "np": np, "duckdb": duckdb, "plt": plt, "re": re,
      "IP_CSV": IP_CSV, "CL_CSV": CL_CSV}

ENTRIES = []


def entry(title, code, note=""):
    ENTRIES.append({"title": title, "note": note, "code": code})


# ===========================================================================
# ENTRY 1 -- Setup & data load (both raw sources)
# ===========================================================================
entry(
    "Entry 1: Setup & Dual Data Load (IP Patents + Clinical Trials, both on target_harmonized)",
    r"""
    # Executive framing ------------------------------------------------------
    # Goal: triangulate the ANTIBODY-THERAPEUTICS-IN-ONCOLOGY patent landscape
    # (input/ip_final_version3.csv) against the clinical-trial landscape
    # (input/clinical_final_version1.csv) to inform where WE should place our
    # own antibody-therapeutics program bet. Standardized join key on BOTH
    # sides: `target_harmonized` (per instruction -- no raw targets_hgnc/target_name).
    con = duckdb.connect()

    con.execute(f"CREATE OR REPLACE VIEW ip_raw AS SELECT * FROM read_csv_auto('{IP_CSV}')")
    ip = con.sql("SELECT * FROM ip_raw").df()

    con.execute(f"CREATE OR REPLACE VIEW cl_raw AS SELECT * FROM read_csv_auto('{CL_CSV}', ALL_VARCHAR=FALSE, SAMPLE_SIZE=-1)")
    cl_all = con.sql("SELECT * FROM cl_raw").df()

    print(f"IP patents  : {len(ip):,} rows, {ip.shape[1]} cols, {ip['target_harmonized'].nunique():,} distinct target_harmonized")
    print(f"Clinical    : {len(cl_all):,} rows total, {cl_all.shape[1]} cols")
    print(con.sql("SELECT in_scope, COUNT(*) AS n_rows FROM cl_raw GROUP BY 1 ORDER BY 2 DESC").df().to_string(index=False))
    """,
)

# ===========================================================================
# ENTRY 2 -- Clinical analytic base table (verbatim logic from
#            clinical_landscape_final_version2.ipynb Entry 2/3)
# ===========================================================================
entry(
    "Entry 2: Clinical Analytic Base Table -- Scope Filter, Phase Group, Outcome Bucket, Tumor-Type Classifier",
    r"""
    # Re-derives the SAME analytic base table as
    # clinical_landscape_final_version2.ipynb Entries 2-3 (verbatim logic) so
    # every downstream comparison in this notebook is apples-to-apples with
    # that prior analysis.
    df = cl_all[cl_all["in_scope"] == True].copy()  # noqa: E712 (nullable BooleanArray from DuckDB)

    def phase_group(raw_phase, ctgov_phase):
        s = str(ctgov_phase) if pd.notna(ctgov_phase) and str(ctgov_phase) != "None" else str(raw_phase)
        s = s.upper()
        has1 = ("PHASE1" in s) or ("EARLY_PHASE1" in s)
        has2 = "PHASE2" in s
        has3 = "PHASE3" in s
        has4 = "PHASE4" in s
        if has4:
            return "Phase 4"
        if has3:
            return "Phase 3"
        if has2:
            return "Phase 2"
        if has1:
            return "Phase 1"
        return "Unknown"

    df["phase_group"] = [phase_group(p, c) for p, c in zip(df["phase"], df["ctgov_phase"])]

    OUTCOME_BUCKET = {"positive": "Success", "approved": "Success", "negative": "Failure",
                      "terminated": "Failure", "ongoing": "Ongoing/Pending", "pending": "Ongoing/Pending",
                      "unclear": "Unclear"}
    df["outcome_bucket"] = df["outcome"].map(OUTCOME_BUCKET).fillna("Unclear")
    df["start_year"] = pd.to_datetime(df["start_date"], errors="coerce", format="mixed").dt.year
    df["target_h"] = df["target_harmonized"].replace({"": np.nan})

    # Solid-tumor-type classifier -- identical taxonomy to
    # clinical_landscape_final_version2.ipynb Entry 3, reused below (Entry 8/16
    # of THIS notebook) so both notebooks' tumor-type labels are directly comparable,
    # and reused a second time on the IP `indications` field (Entry 8) so the
    # patent side can be classified into the SAME canonical tumor-type vocabulary.
    TUMOR_CATEGORIES = [
        ("NSCLC", r"non[- ]?small[- ]?cell lung|nsclc"),
        ("SCLC", r"small[- ]?cell lung (carcinoma|cancer)|\bsclc\b"),
        ("Lung_Other", r"\blung\b|pulmonary"),
        ("Breast", r"\bbreast\b"),
        ("Ovarian", r"ovarian|\bovary\b"),
        ("Endometrial_Uterine", r"endometrial|\buterine\b"),
        ("Cervical", r"cervical|\bcervix\b"),
        ("Colorectal", r"colorectal|\bcolon\b|\brectal\b|\brectum\b"),
        ("Gastric_Esophageal", r"gastric|\bstomach\b|esophageal|oesophageal|gastroesophageal|\bgej\b"),
        ("Hepatocellular", r"hepatocellular|\bhcc\b"),
        ("Biliary_Cholangio", r"cholangiocarcinoma|\bbiliary\b"),
        ("Pancreatic", r"pancreatic|\bpancreas\b"),
        ("RCC", r"renal cell|\brcc\b|kidney cancer"),
        ("Bladder_Urothelial", r"\bbladder\b|urothelial"),
        ("Prostate", r"prostat"),
        ("Melanoma", r"melanoma"),
        ("HNSCC", r"head and neck|nasopharyngeal|oropharyngeal|laryngeal|hypopharyngeal|oral cavity"),
        ("Glioma_CNS", r"glioma|glioblastoma|astrocytoma|central nervous system|\bcns\b|brain (tumou?r|neoplasm)"),
        ("Mesothelioma", r"mesothelioma"),
        ("Sarcoma", r"sarcoma"),
        ("Thyroid", r"thyroid"),
        ("Skin_NonMelanoma", r"squamous cell carcinoma of the skin|cutaneous squamous|basal cell carcinoma|merkel cell"),
        ("Neuroendocrine", r"neuroendocrine|carcinoid"),
        ("GermCell_Testicular", r"testicular|germ cell"),
        ("Adrenal", r"adrenocortical|adrenal"),
        ("Salivary_Gland", r"salivary gland"),
        ("Solid_Tumor_Basket", r"solid tumou?r|advanced cancer|advanced malignant neoplasm|\bany cancer\b|metastatic cancer|metastatic solid"),
        ("Hematologic_Lymphoma", r"lymphoma"),
        ("Hematologic_Leukemia", r"leuk[ae]mia"),
        ("Hematologic_Myeloma", r"myeloma"),
        ("Hematologic_MDS_MPN", r"myelodysplastic|myeloproliferative"),
    ]
    COMPILED_TUMOR = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in TUMOR_CATEGORIES]

    def classify_tumor_tags(cond_text):
        if not cond_text or pd.isna(cond_text):
            return []
        return [name for name, rx in COMPILED_TUMOR if rx.search(str(cond_text))]

    df["condition_text"] = df["ctgov_conditions"].fillna(df["m_conditions"])
    df["tumor_tags"] = df["condition_text"].apply(classify_tumor_tags)
    con.register("trials", df)

    tumor_junction = df[["nct", "target_h", "modality_code", "phase_group", "outcome_bucket",
                         "lead_sponsor_golden"]].copy()
    tumor_junction["tumor_tags"] = df["tumor_tags"]
    tumor_junction = tumor_junction.explode("tumor_tags").rename(columns={"tumor_tags": "tumor_type"})
    tumor_junction = tumor_junction[tumor_junction["tumor_type"].notna()]
    con.register("tumor_junction", tumor_junction)

    print(f"Clinical in-scope analytic base: {len(df):,} rows ({df['nct'].nunique():,} unique NCTs)")
    print(f"Tumor-tag junction rows (trial x tumor tag): {len(tumor_junction):,}")
    """,
)

# ===========================================================================
# ENTRY 3 -- PART A: IP whitespace/crowded tiering (verbatim reproduction of
#            ip_landscape_final_version1.ipynb Entry 5D)
# ===========================================================================
entry(
    "Entry 3 [PART A]: IP Whitespace/Crowded Tiering -- Verbatim Reproduction of Prior Notebook's Entry 5D Quadrant Logic",
    r"""
    # Exact SQL/thresholds as coded in ip_landscape_final_version1.ipynb Entry 5D
    # (total_patents >= 30 = "high volume"; pct_recent >= 30% filed since 2023 =
    # "high momentum"), universe = targets with >= 3 patents. The 4 original
    # quadrants are collapsed to 3 tiers for direct comparison with the
    # clinical side: Crowded = Hot/Crowded + Mature/Legacy (high volume);
    # Whitespace (High Risk) = Emerging Whitespace (low volume, accelerating);
    # Medium = Quiet/Underexplored (low volume, low momentum).
    sql_ip_quadrant = f'''
        WITH base AS (
          SELECT target_harmonized AS t, filing_year
          FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL
        ),
        agg AS (
          SELECT t AS target_harmonized, COUNT(*) AS total_patents,
                 SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END) AS recent_3y,
                 ROUND(100.0*SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END)/COUNT(*),1) AS pct_recent
          FROM base GROUP BY 1 HAVING COUNT(*) >= 3
        )
        SELECT target_harmonized, total_patents, recent_3y, pct_recent,
          CASE
            WHEN total_patents >= 30 AND pct_recent >= 30 THEN 'Hot/Crowded (high volume, high momentum)'
            WHEN total_patents >= 30 AND pct_recent < 30 THEN 'Mature/Legacy (high volume, cooling)'
            WHEN total_patents < 30 AND pct_recent >= 30 THEN 'Emerging Whitespace (low volume, accelerating)'
            ELSE 'Quiet/Underexplored (low volume, low momentum)'
          END AS whitespace_quadrant
        FROM agg ORDER BY total_patents DESC;
    '''
    IP_QUADRANT = con.sql(sql_ip_quadrant).df()

    def ip_tier(q):
        if q in ("Hot/Crowded (high volume, high momentum)", "Mature/Legacy (high volume, cooling)"):
            return "Crowded"
        if q == "Emerging Whitespace (low volume, accelerating)":
            return "Whitespace (High Risk)"
        return "Medium"

    IP_QUADRANT["ip_tier"] = IP_QUADRANT["whitespace_quadrant"].apply(ip_tier)
    display(IP_QUADRANT, name="entry03_ip_tier_full")
    print(f"IP tiering universe (target_harmonized, >=3 patents): {len(IP_QUADRANT)}")
    display(IP_QUADRANT["ip_tier"].value_counts().rename_axis("ip_tier").reset_index(name="n_targets"),
            name="entry03_ip_tier_counts")
    """,
)

# ===========================================================================
# ENTRY 4 -- PART A: Clinical whitespace/crowded tiering (verbatim
#            reproduction of clinical_landscape_final_version2.ipynb
#            Entry 11 / 13 / 31)
# ===========================================================================
entry(
    "Entry 4 [PART A]: Clinical Whitespace/Crowded Tiering -- Verbatim Reproduction of Prior Notebook's Entry 11/13/31 Logic",
    r"""
    # Entry 11: TARGET_SCORECARD, targets with >=5 trials.
    TARGET_SCORECARD = con.sql('''
        SELECT target_h AS target_harmonized, COUNT(*) AS n_trials,
               COUNT(DISTINCT lead_sponsor_golden) AS n_sponsors,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure
        FROM trials WHERE target_h IS NOT NULL GROUP BY 1 HAVING COUNT(*) >= 5 ORDER BY n_trials DESC
    ''').df()

    # Entry 13: crowding matrix -- "Crowded" = top-20 targets by trial volume.
    CROWDED_TOP20 = TARGET_SCORECARD.sort_values("n_trials", ascending=False).head(20)["target_harmonized"].tolist()

    # Entry 31: Phase-1/2-ONLY whitespace model -- zero recorded failures,
    # entire footprint Phase 1/2/Unknown (no Phase 3/4 ever seen), active
    # since 2019. No minimum trial-count threshold (as originally coded).
    target_stats = con.sql('''
        SELECT target_h AS target_harmonized, COUNT(*) AS n_trials,
               COUNT(DISTINCT lead_sponsor_golden) AS n_sponsors,
               MAX(start_year) AS latest_start_year, MIN(start_year) AS earliest_start_year,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure,
               SUM(CASE WHEN phase_group IN ('Phase 1','Phase 2') THEN 1 ELSE 0 END) AS n_ph12,
               SUM(CASE WHEN phase_group IN ('Phase 3','Phase 4') THEN 1 ELSE 0 END) AS n_late_stage
        FROM trials WHERE target_h IS NOT NULL GROUP BY 1
    ''').df()
    WHITESPACE_OVERALL = target_stats[(target_stats["n_late_stage"] == 0) & (target_stats["n_ph12"] > 0) &
                                       (target_stats["n_failure"] == 0) & (target_stats["latest_start_year"] >= 2019)]
    WHITESPACE_TARGETS = WHITESPACE_OVERALL["target_harmonized"].tolist()

    def cl_tier(t):
        if t in CROWDED_TOP20:
            return "Crowded"
        if t in WHITESPACE_TARGETS:
            return "Whitespace (High Risk)"
        return "Medium"

    cl_universe = sorted(set(TARGET_SCORECARD["target_harmonized"]) | set(WHITESPACE_TARGETS))
    CL_TIER = pd.DataFrame({"target_harmonized": cl_universe})
    CL_TIER["cl_tier"] = CL_TIER["target_harmonized"].apply(cl_tier)
    CL_TIER = CL_TIER.merge(TARGET_SCORECARD, on="target_harmonized", how="left")
    CL_TIER = CL_TIER.merge(target_stats[["target_harmonized", "latest_start_year", "n_ph12", "n_late_stage"]],
                            on="target_harmonized", how="left")

    display(CL_TIER, name="entry04_clinical_tier_full")
    print(f"Clinical tiering universe: {len(CL_TIER)} (TARGET_SCORECARD >=5 trials: {len(TARGET_SCORECARD)}; "
          f"WHITESPACE_OVERALL, no min-trial threshold: {len(WHITESPACE_TARGETS)})")
    display(CL_TIER["cl_tier"].value_counts().rename_axis("cl_tier").reset_index(name="n_targets"),
            name="entry04_clinical_tier_counts")
    """,
)

# ===========================================================================
# ENTRY 5 -- PART A: Cross-notebook comparison
# ===========================================================================
entry(
    "Entry 5 [PART A]: Cross-Notebook Tier Comparison -- Merge on target_harmonized",
    r"""
    ip_small = IP_QUADRANT.rename(columns={"ip_tier": "ip_tier"})[["target_harmonized", "total_patents", "pct_recent", "ip_tier"]]
    cl_small = CL_TIER.rename(columns={"cl_tier": "cl_tier"})[["target_harmonized", "n_trials", "n_success", "n_failure", "cl_tier"]]
    MERGED_TIERS = ip_small.merge(cl_small, on="target_harmonized", how="outer", indicator=True)

    def match_status(row):
        if row["_merge"] == "left_only":
            return "IP-only (no clinical tier)"
        if row["_merge"] == "right_only":
            return "Clinical-only (no IP tier)"
        if row["ip_tier"] == row["cl_tier"]:
            return f"MATCH ({row['ip_tier']})"
        return f"MISMATCH (IP={row['ip_tier']} / Clinical={row['cl_tier']})"

    MERGED_TIERS["match_status"] = MERGED_TIERS.apply(match_status, axis=1)
    display(MERGED_TIERS, name="entry05_merged_tiers_full")

    both = MERGED_TIERS[MERGED_TIERS["_merge"] == "both"]
    print(f"Targets present in BOTH IP and Clinical tier universes: {len(both)}")
    summary = both["match_status"].apply(lambda s: "MATCH" if s.startswith("MATCH") else "MISMATCH").value_counts()
    display(summary.rename_axis("status").reset_index(name="n_targets"), name="entry05_match_summary")

    fig, ax = plt.subplots(figsize=(6, 5))
    summary.plot(kind="bar", ax=ax, color=["#16a34a", "#dc2626"])
    ax.set_title(f"Cross-Notebook Tier Agreement, {len(both)} Targets in Both Universes")
    ax.set_ylabel("# targets"); ax.tick_params(axis="x", rotation=0)
    for i, v in enumerate(summary.values):
        ax.text(i, v, str(v), ha="center", va="bottom")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 6 -- PART A: full crowded/whitespace/medium-in-both lists
# ===========================================================================
entry(
    "Entry 6 [PART A]: Crowded-in-Both / Whitespace-in-Both / Medium-in-Both -- Full Target Lists",
    r"""
    both = MERGED_TIERS[MERGED_TIERS["_merge"] == "both"]

    crowded_both = both[(both["ip_tier"] == "Crowded") & (both["cl_tier"] == "Crowded")].sort_values("total_patents", ascending=False)
    ws_both = both[(both["ip_tier"] == "Whitespace (High Risk)") & (both["cl_tier"] == "Whitespace (High Risk)")]
    medium_both = both[(both["ip_tier"] == "Medium") & (both["cl_tier"] == "Medium")]

    print(f"CROWDED in both ({len(crowded_both)}): most-validated, most-contested targets -- de-risked biology, but a crowded IP field.")
    display(crowded_both[["target_harmonized", "total_patents", "n_trials", "n_success", "n_failure"]], name="entry06_crowded_in_both")

    print(f"\nWHITESPACE (High Risk) in both ({len(ws_both)}): patent-thin AND clinically-thin -- highest upside, highest uncertainty.")
    display(ws_both[["target_harmonized", "total_patents", "pct_recent", "n_trials"]], name="entry06_whitespace_in_both")

    print(f"\nMEDIUM in both ({len(medium_both)}): moderate activity on both sides -- neither clearly open nor clearly closed.")
    display(medium_both[["target_harmonized", "total_patents", "n_trials", "n_success", "n_failure"]], name="entry06_medium_in_both")

    mismatches = both[both["ip_tier"] != both["cl_tier"]].sort_values(["ip_tier", "cl_tier"])
    print(f"\nALL MISMATCHES ({len(mismatches)}): different tier assigned by each prior notebook.")
    display(mismatches[["target_harmonized", "ip_tier", "cl_tier", "total_patents", "pct_recent", "n_trials", "n_success", "n_failure"]],
            name="entry06_all_mismatches")
    """,
)

# ===========================================================================
# ENTRY 7 -- PART A: SPOTLIGHT -- "IP Crowded, Clinical hasn't caught up"
# ===========================================================================
entry(
    "Entry 7 [PART A -- SPOTLIGHT]: 'IP Crowded, Clinical Hasn't Caught Up' -- Heavily Patented Targets With Little/No Clinical Validation Yet",
    r"""
    # Every target the IP notebook calls Crowded (>=30 patents) where the
    # clinical notebook does NOT also call it Crowded -- i.e. patent filers
    # are racing ahead of clinical proof. Includes targets absent entirely
    # from the clinical >=5-trial/whitespace universe (IP-only).
    ip_crowded_all = MERGED_TIERS[MERGED_TIERS["ip_tier"] == "Crowded"].copy()
    ip_crowded_not_caught = ip_crowded_all[ip_crowded_all["cl_tier"] != "Crowded"].copy()
    ip_crowded_not_caught["clinical_status"] = np.where(
        ip_crowded_not_caught["_merge"] == "left_only", "No clinical trial data at all (target absent from clinical universe)",
        ip_crowded_not_caught["cl_tier"].apply(lambda t: f"Clinical tier = {t}" if pd.notna(t) else "n/a"))
    ip_crowded_not_caught = ip_crowded_not_caught.sort_values("total_patents", ascending=False)

    cols = ["target_harmonized", "total_patents", "pct_recent", "n_trials", "n_success", "n_failure", "clinical_status"]
    display(ip_crowded_not_caught[cols], name="entry07_ip_crowded_clinical_not_caught")
    print(f"{len(ip_crowded_not_caught)} of {len(ip_crowded_all)} IP-Crowded targets have NOT been matched by clinical "
          f"activity (no trials at all, or trials exist but below the clinical Crowded bar).")

    fig, ax = plt.subplots(figsize=(9, 8))
    top25 = ip_crowded_not_caught.head(25).iloc[::-1]
    colors = ["#94a3b8" if s.startswith("No clinical") else "#f59e0b" for s in top25["clinical_status"]]
    ax.barh(top25["target_harmonized"], top25["total_patents"], color=colors)
    ax.set_xlabel("Total IP Patents"); ax.set_title("Top-25 'IP Crowded, Clinical Not Caught Up' Targets\n(grey = zero clinical trials; amber = some trials, below Crowded bar)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 8 -- PART A: SPOTLIGHT -- clinical Phase 1/2 whitespace x IP tier
# ===========================================================================
entry(
    "Entry 8 [PART A -- SPOTLIGHT]: Clinical Phase 1/2 Whitespace Targets Cross-Referenced Against IP Tier",
    r"""
    # The clinical notebook's Phase-1/2-only whitespace list (zero failures,
    # active since 2019) is the most interesting set for program design --
    # cross-reference every one of those targets against its IP tier to see
    # which are TRULY wide open (also IP-whitespace/medium) vs which are a
    # clinical green light sitting on top of a crowded patent minefield.
    ws_clinical = MERGED_TIERS[MERGED_TIERS["cl_tier"] == "Whitespace (High Risk)"].copy()
    ws_clinical["ip_status"] = np.where(ws_clinical["_merge"] == "right_only",
                                        "No IP patent data at all (target absent from IP universe)",
                                        ws_clinical["ip_tier"])
    ws_clinical = ws_clinical.merge(
        CL_TIER[["target_harmonized", "latest_start_year", "n_ph12"]], on="target_harmonized", how="left", suffixes=("", "_dup")
    )
    ws_clinical = ws_clinical.sort_values("n_trials", ascending=False)

    cols = ["target_harmonized", "n_trials", "n_success", "latest_start_year", "n_ph12", "total_patents", "pct_recent", "ip_status"]
    display(ws_clinical[cols], name="entry08_clinical_whitespace_x_ip_tier")

    breakdown = ws_clinical["ip_status"].value_counts()
    print(f"{len(ws_clinical)} clinical Phase-1/2 whitespace targets, split by IP-side reality:")
    display(breakdown.rename_axis("ip_status").reset_index(name="n_targets"), name="entry08_ip_status_breakdown")

    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    breakdown.plot(kind="pie", ax=axes[0], autopct="%1.0f%%", textprops={"fontsize": 8})
    axes[0].set_ylabel(""); axes[0].set_title("Clinical Phase-1/2 Whitespace Targets,\nby IP-Side Reality")

    truly_open = ws_clinical[ws_clinical["ip_status"].isin(["No IP patent data at all (target absent from IP universe)",
                                                             "Whitespace (High Risk)", "Medium"])].nlargest(20, "n_trials")
    axes[1].barh(truly_open["target_harmonized"], truly_open["n_trials"], color="#16a34a")
    axes[1].set_xlabel("# Clinical Trials"); axes[1].set_title("Top-20 'Truly Open' Candidates\n(clinical Ph1/2 whitespace AND IP not crowded)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 9 -- PART A: solid-tumor-indication dissection
# ===========================================================================
entry(
    "Entry 9 [PART A]: Solid-Tumor-Indication Dissection of Both Notebooks' Tiers",
    r"""
    # IP side: classify the free-text `indications` field into the SAME
    # canonical tumor-type taxonomy used by the clinical notebook (Entry 2 of
    # THIS notebook), so both sides are directly comparable indication-by-indication.
    HEME_KEYWORDS = ["leukemia", "lymphoma", "myeloma", "hematologic", "hematological", "blood cancer",
                     "AML", "ALL", "CLL", "CML", "MDS", "multiple myeloma", "non-hodgkin", "hodgkin"]

    ip_ind = con.sql(f'''
        SELECT target_harmonized AS target_h, TRIM(UNNEST(STR_SPLIT(TRIM(indications), '|'))) AS raw_indication
        FROM read_csv_auto('{IP_CSV}')
        WHERE target_harmonized IS NOT NULL AND indications IS NOT NULL
          AND TRIM(indications) NOT IN ('', 'oncology TA query hit')
    ''').df()
    ip_ind["tumor_tags"] = ip_ind["raw_indication"].apply(classify_tumor_tags)
    ip_junction = ip_ind.explode("tumor_tags").rename(columns={"tumor_tags": "tumor_type"})
    ip_junction = ip_junction[ip_junction["tumor_type"].notna() & ~ip_junction["tumor_type"].str.startswith("Hematologic")
                              & (ip_junction["tumor_type"] != "Solid_Tumor_Basket")]

    ip_by_target_tumor = ip_junction.groupby(["target_h", "tumor_type"]).size().reset_index(name="n_patents")
    cl_by_target_tumor = tumor_junction[~tumor_junction["tumor_type"].str.startswith("Hematologic") &
                                        (tumor_junction["tumor_type"] != "Solid_Tumor_Basket")]
    cl_by_target_tumor = cl_by_target_tumor.groupby(["target_h", "tumor_type"]).size().reset_index(name="n_trials")

    # Focus on the three headline sets from Entries 6-8: Crowded-in-both,
    # Whitespace-in-both, and IP-Crowded-Clinical-Not-Caught.
    focus_targets = sorted(set(crowded_both["target_harmonized"]) | set(ws_both["target_harmonized"]) |
                            set(ip_crowded_not_caught["target_harmonized"].head(25)))

    ip_focus = ip_by_target_tumor[ip_by_target_tumor["target_h"].isin(focus_targets)]
    cl_focus = cl_by_target_tumor[cl_by_target_tumor["target_h"].isin(focus_targets)]
    indication_dissect = ip_focus.merge(cl_focus, on=["target_h", "tumor_type"], how="outer").fillna(0)
    indication_dissect = indication_dissect.rename(columns={"target_h": "target_harmonized"})
    indication_dissect["n_patents"] = indication_dissect["n_patents"].astype(int)
    indication_dissect["n_trials"] = indication_dissect["n_trials"].astype(int)
    indication_dissect = indication_dissect.merge(MERGED_TIERS[["target_harmonized", "ip_tier", "cl_tier"]].drop_duplicates("target_harmonized"),
                                                   on="target_harmonized", how="left")
    indication_dissect = indication_dissect.sort_values(["target_harmonized", "n_patents"], ascending=[True, False])
    display(indication_dissect, name="entry09_solid_tumor_indication_dissection")
    print(f"{indication_dissect['target_harmonized'].nunique()} spotlighted targets x "
          f"{indication_dissect['tumor_type'].nunique()} solid-tumor indications, {len(indication_dissect)} non-empty combos.")

    top_indications = indication_dissect.groupby("tumor_type")[["n_patents", "n_trials"]].sum().sort_values("n_patents", ascending=False).head(12)
    fig, ax = plt.subplots(figsize=(10, 6))
    top_indications.plot(kind="barh", ax=ax, color=["#7c3aed", "#0891b2"])
    ax.set_title("Spotlighted Targets: Patent vs Trial Volume by Solid-Tumor Indication"); ax.set_xlabel("Count")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 10 -- PART B: independent raw-data quadrant (sanity check)
# ===========================================================================
entry(
    "Entry 10 [PART B]: Independent Raw-Data Triangulation -- Direct Patent-Count vs Trial-Count Quadrant",
    r"""
    # Computed FRESH from the two raw CSVs (not derived from either prior
    # notebook) as a sanity check against Part A: simple patent-volume vs
    # trial-volume crossing, no phase/momentum logic at all.
    ip_counts = con.sql(f'''
        SELECT target_harmonized, COUNT(*) AS n_patents
        FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL GROUP BY 1
    ''').df()
    cl_counts = df.groupby("target_h").size().reset_index(name="n_trials").rename(columns={"target_h": "target_harmonized"})

    RAW_TRIANGULATION = ip_counts.merge(cl_counts, on="target_harmonized", how="outer").fillna(0)
    RAW_TRIANGULATION["n_patents"] = RAW_TRIANGULATION["n_patents"].astype(int)
    RAW_TRIANGULATION["n_trials"] = RAW_TRIANGULATION["n_trials"].astype(int)

    ip_med = RAW_TRIANGULATION.loc[RAW_TRIANGULATION["n_patents"] > 0, "n_patents"].median()
    cl_med = RAW_TRIANGULATION.loc[RAW_TRIANGULATION["n_trials"] > 0, "n_trials"].median()

    def raw_quadrant(row):
        has_ip, has_cl = row["n_patents"] > 0, row["n_trials"] > 0
        if not has_ip and not has_cl:
            return "Neither (not in either dataset)"
        if has_ip and not has_cl:
            return "IP-only (patented, zero trials)"
        if has_cl and not has_ip:
            return "Clinical-only (trialed, zero patents)"
        if row["n_patents"] >= ip_med and row["n_trials"] >= cl_med:
            return "Crowded Both (above-median volume, both sides)"
        if row["n_patents"] < ip_med and row["n_trials"] < cl_med:
            return "Whitespace Both (below-median volume, both sides)"
        return "Split (crowded one side, thin the other)"

    RAW_TRIANGULATION["raw_quadrant"] = RAW_TRIANGULATION.apply(raw_quadrant, axis=1)
    display(RAW_TRIANGULATION.sort_values("n_patents", ascending=False), name="entry10_raw_triangulation_full")
    display(RAW_TRIANGULATION["raw_quadrant"].value_counts().rename_axis("raw_quadrant").reset_index(name="n_targets"),
            name="entry10_raw_quadrant_counts")

    fig, ax = plt.subplots(figsize=(9, 7))
    both_only = RAW_TRIANGULATION[(RAW_TRIANGULATION["n_patents"] > 0) & (RAW_TRIANGULATION["n_trials"] > 0)]
    ax.scatter(both_only["n_patents"], both_only["n_trials"], alpha=0.5, s=30, c="#2563eb")
    ax.axvline(ip_med, color="gray", linestyle="--", linewidth=0.8)
    ax.axhline(cl_med, color="gray", linestyle="--", linewidth=0.8)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("# Patents (log scale)"); ax.set_ylabel("# Trials (log scale)")
    ax.set_title(f"Raw Patent-Count vs Trial-Count, {len(both_only)} Targets Present in Both Datasets")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 11 -- PART B: modality-adjusted crowding
# ===========================================================================
entry(
    "Entry 11 [PART B]: Modality-Adjusted Crowding -- Same Target, Different Modality Buckets",
    r"""
    # A target can be crowded in one modality format and wide open in
    # another (e.g. HER2 as a naked mAb vs HER2 as an ADC vs HER2 bispecific).
    ip_mod = con.sql(f'''
        SELECT target_harmonized, modality_code, COUNT(*) AS n_patents
        FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL GROUP BY 1,2
    ''').df()
    cl_mod = df.groupby(["target_h", "modality_code"]).size().reset_index(name="n_trials").rename(columns={"target_h": "target_harmonized"})

    MODALITY_TRIANGULATION = ip_mod.merge(cl_mod, on=["target_harmonized", "modality_code"], how="outer").fillna(0)
    MODALITY_TRIANGULATION["n_patents"] = MODALITY_TRIANGULATION["n_patents"].astype(int)
    MODALITY_TRIANGULATION["n_trials"] = MODALITY_TRIANGULATION["n_trials"].astype(int)
    MODALITY_TRIANGULATION = MODALITY_TRIANGULATION[
        (MODALITY_TRIANGULATION["n_patents"] + MODALITY_TRIANGULATION["n_trials"]) >= 5
    ].sort_values(["target_harmonized", "n_patents"], ascending=[True, False])
    display(MODALITY_TRIANGULATION, name="entry11_modality_adjusted_triangulation")

    # Targets with meaningfully different crowding pictures ACROSS modalities
    # for the SAME target (e.g. crowded as mAb, thin as ADC).
    pivot_p = MODALITY_TRIANGULATION.pivot_table(index="target_harmonized", columns="modality_code", values="n_patents", fill_value=0)
    multi_modality_ip = pivot_p[(pivot_p > 0).sum(axis=1) >= 2]
    print(f"{len(multi_modality_ip)} targets have IP activity in >=2 distinct modality buckets -- "
          f"a target 'crowded' at the antigen level may still have an open modality-specific niche.")

    top_multi = multi_modality_ip.loc[multi_modality_ip.sum(axis=1).sort_values(ascending=False).head(15).index]
    fig, ax = plt.subplots(figsize=(10, 7))
    top_multi.plot(kind="barh", stacked=True, ax=ax, colormap="tab10")
    ax.set_title("Top-15 Multi-Modality Targets: IP Patents by Modality Bucket"); ax.set_xlabel("# Patents")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 12 -- PART B: filing-to-first-trial timing lag
# ===========================================================================
entry(
    "Entry 12 [PART B]: Timing Triangulation -- Patent Filing Year vs Clinical Trial Start Year, per Target",
    r"""
    # Does IP consistently lead clinical activity, or are some targets being
    # trialed with no (or very late) patent filing -- a freedom-to-operate flag?
    ip_years = con.sql(f'''
        SELECT target_harmonized, MIN(filing_year) AS first_filing_year, MAX(filing_year) AS latest_filing_year, COUNT(*) AS n_patents
        FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL GROUP BY 1
    ''').df()
    cl_years = df.groupby("target_h").agg(first_trial_year=("start_year", "min"),
                                           latest_trial_year=("start_year", "max"),
                                           n_trials=("nct", "count")).reset_index().rename(columns={"target_h": "target_harmonized"})

    TIMING_LAG = ip_years.merge(cl_years, on="target_harmonized", how="inner")
    TIMING_LAG = TIMING_LAG[TIMING_LAG["n_patents"] >= 3]
    TIMING_LAG["lag_years_filing_to_trial"] = TIMING_LAG["first_trial_year"] - TIMING_LAG["first_filing_year"]

    def lead_flag(lag):
        if pd.isna(lag):
            return "Unknown"
        if lag > 1:
            return "IP LEADS (patent filed well before first trial)"
        if lag < -1:
            return "CLINICAL LEADS (trial started well before first patent) -- possible FTO gap"
        return "Roughly concurrent"

    TIMING_LAG["lead_flag"] = TIMING_LAG["lag_years_filing_to_trial"].apply(lead_flag)
    display(TIMING_LAG.sort_values("lag_years_filing_to_trial"), name="entry12_timing_lag_full")
    display(TIMING_LAG["lead_flag"].value_counts().rename_axis("lead_flag").reset_index(name="n_targets"), name="entry12_lead_flag_counts")

    clinical_leads = TIMING_LAG[TIMING_LAG["lead_flag"].str.startswith("CLINICAL LEADS")].sort_values("lag_years_filing_to_trial")
    print(f"\n{len(clinical_leads)} targets show clinical trials starting BEFORE any patent was filed (>=3 patents, possible FTO gap):")
    display(clinical_leads[["target_harmonized", "first_trial_year", "first_filing_year", "lag_years_filing_to_trial", "n_trials", "n_patents"]],
            name="entry12_clinical_leads_targets")

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.hist(TIMING_LAG["lag_years_filing_to_trial"].dropna(), bins=30, color="#0e7490")
    ax.axvline(0, color="black", linestyle="--")
    ax.set_xlabel("Years (first trial start - first patent filing)"); ax.set_ylabel("# targets")
    ax.set_title("Filing-to-First-Trial Lag Distribution (positive = IP led; negative = clinical led)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 13 -- PART B: validated / patent-thin (business case to file now)
# ===========================================================================
entry(
    "Entry 13 [PART B]: Clinically-Validated, Patent-Thin Targets -- Highest Business Case to File Now",
    r"""
    cl_validation = df.groupby("target_h").agg(
        n_trials=("nct", "count"),
        n_success=("outcome_bucket", lambda s: (s == "Success").sum()),
    ).reset_index().rename(columns={"target_h": "target_harmonized"})
    cl_validation = cl_validation[cl_validation["n_success"] > 0]

    ip_volume = con.sql(f'''
        SELECT target_harmonized, COUNT(*) AS n_patents, COUNT(DISTINCT current_assignee) AS n_assignees
        FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL GROUP BY 1
    ''').df()

    VALIDATED_PATENT_THIN = cl_validation.merge(ip_volume, on="target_harmonized", how="left")
    VALIDATED_PATENT_THIN["n_patents"] = VALIDATED_PATENT_THIN["n_patents"].fillna(0).astype(int)
    VALIDATED_PATENT_THIN = VALIDATED_PATENT_THIN[VALIDATED_PATENT_THIN["n_patents"] <= 10].sort_values(
        ["n_success", "n_trials"], ascending=False)
    display(VALIDATED_PATENT_THIN, name="entry13_validated_patent_thin")
    print(f"{len(VALIDATED_PATENT_THIN)} targets have >=1 recorded clinical success AND <=10 IP patents -- "
          f"clinical proof-of-concept already exists, but the IP field remains largely open. Strongest 'file now' business case.")

    fig, ax = plt.subplots(figsize=(9, 8))
    top20 = VALIDATED_PATENT_THIN.head(20).iloc[::-1]
    ax.barh(top20["target_harmonized"], top20["n_patents"], color="#16a34a")
    for i, (n_s, n_p) in enumerate(zip(top20["n_success"], top20["n_patents"])):
        ax.text(n_p, i, f"  {int(n_s)} clinical success(es)", va="center", fontsize=8)
    ax.set_xlabel("# IP Patents (low = open)"); ax.set_title("Top-20 Clinically-Validated, Patent-Thin Targets")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 14 -- PART B: patent-crowded / clinically unproven (FTO risk)
# ===========================================================================
entry(
    "Entry 14 [PART B]: Patent-Crowded, Clinically-Unproven Targets -- Highest Litigation / FTO Risk If We Enter",
    r"""
    ip_hhi = con.sql(f'''
        WITH base AS (
          SELECT target_harmonized AS t, current_assignee, COUNT(*) AS n
          FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL GROUP BY 1,2
        ),
        tot AS (SELECT t, SUM(n) AS total FROM base GROUP BY 1 HAVING SUM(n) >= 10)
        SELECT b.t AS target_harmonized, MAX(total) AS n_patents
        FROM base b JOIN tot ON b.t = tot.t GROUP BY 1
    ''').df()

    cl_unproven = df.groupby("target_h").agg(
        n_trials=("nct", "count"),
        n_success=("outcome_bucket", lambda s: (s == "Success").sum()),
        n_failure=("outcome_bucket", lambda s: (s == "Failure").sum()),
    ).reset_index().rename(columns={"target_h": "target_harmonized"})

    PATENT_CROWDED_UNPROVEN = ip_hhi.merge(cl_unproven, on="target_harmonized", how="left")
    PATENT_CROWDED_UNPROVEN[["n_trials", "n_success", "n_failure"]] = PATENT_CROWDED_UNPROVEN[["n_trials", "n_success", "n_failure"]].fillna(0)
    PATENT_CROWDED_UNPROVEN = PATENT_CROWDED_UNPROVEN[PATENT_CROWDED_UNPROVEN["n_success"] == 0].sort_values("n_patents", ascending=False)
    display(PATENT_CROWDED_UNPROVEN, name="entry14_patent_crowded_unproven")
    print(f"{len(PATENT_CROWDED_UNPROVEN)} targets have >=10 IP patents but ZERO recorded clinical success "
          f"(no trials, ongoing-only, or failed-only) -- highest patent-litigation exposure for the least proven biology.")

    fig, ax = plt.subplots(figsize=(9, 8))
    top20 = PATENT_CROWDED_UNPROVEN.head(20).iloc[::-1]
    colors = ["#dc2626" if f > 0 else "#94a3b8" for f in top20["n_failure"]]
    ax.barh(top20["target_harmonized"], top20["n_patents"], color=colors)
    ax.set_xlabel("# IP Patents"); ax.set_title("Top-20 Patent-Crowded, Clinically-Unproven Targets\n(red = has recorded failure(s); grey = no readout yet)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 15 -- PART B: CAR-T IP blind spot
# ===========================================================================
entry(
    "Entry 15 [PART B]: The CAR-T IP Blind Spot -- Clinically Active CAR-T Targets With No Antibody-Modality Patent Tag",
    r"""
    # ip_final_version3.csv has NO CAR-T modality_code at all (antibody
    # patent filings rarely file as "CAR-T" per se) -- but a CAR-T binder
    # domain is usually antibody-derived, so the SAME target may still show
    # up in IP data under MAB/BISPECIFIC/ADC. This checks whether that's true.
    cart_targets = df[df["modality_code"] == "CAR-T"].groupby("target_h").size().reset_index(name="n_cart_trials").rename(
        columns={"target_h": "target_harmonized"}).sort_values("n_cart_trials", ascending=False)

    ip_any = con.sql(f'''
        SELECT target_harmonized, COUNT(*) AS n_patents, STRING_AGG(DISTINCT modality_code, ', ') AS ip_modalities
        FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL GROUP BY 1
    ''').df()

    CART_BLIND_SPOT = cart_targets.merge(ip_any, on="target_harmonized", how="left")
    CART_BLIND_SPOT["n_patents"] = CART_BLIND_SPOT["n_patents"].fillna(0).astype(int)
    CART_BLIND_SPOT["ip_coverage"] = np.where(CART_BLIND_SPOT["n_patents"] == 0,
                                               "NO antibody-modality IP found for this target at all",
                                               "Antibody-modality IP exists under: " + CART_BLIND_SPOT["ip_modalities"].fillna(""))
    display(CART_BLIND_SPOT.head(30), name="entry15_cart_blind_spot")

    n_no_ip = (CART_BLIND_SPOT["n_patents"] == 0).sum()
    print(f"{len(CART_BLIND_SPOT)} distinct CAR-T targets in the clinical data; {n_no_ip} of them have NO matching "
          f"antibody-modality IP record at all in ip_final_version3.csv -- a genuine freedom-to-operate blind spot "
          f"(this dataset simply does not cover CAR-T construct patents), NOT necessarily a real absence of IP risk.")

    fig, ax = plt.subplots(figsize=(9, 8))
    top20 = CART_BLIND_SPOT.head(20).iloc[::-1]
    colors = ["#94a3b8" if p == 0 else "#0891b2" for p in top20["n_patents"]]
    ax.barh(top20["target_harmonized"], top20["n_cart_trials"], color=colors)
    ax.set_xlabel("# CAR-T Clinical Trials"); ax.set_title("Top-20 CAR-T Targets by Trial Volume\n(grey = zero matching antibody-modality IP record found)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 16 -- PART B: sponsor / assignee overlap (approximate name match)
# ===========================================================================
entry(
    "Entry 16 [PART B]: Sponsor <-> Assignee Overlap -- Same Company Patenting AND Trialing the Same Target",
    r"""
    # Approximate, name-normalized matching (company legal-suffix noise means
    # exact string equality would under-count) -- strip common corporate
    # suffixes/punctuation and uppercase before comparing. Caveat clearly
    # flagged: this is an approximate signal, not an exact entity match.
    SUFFIX_RE = re.compile(r"\b(INC|LLC|LTD|LIMITED|CORP|CORPORATION|CO|GMBH|AG|SA|SAS|PLC|NV|BV|KG|SPA|"
                           r"PHARMACEUTICALS?|PHARMA|THERAPEUTICS|BIOSCIENCES|BIOTECH|BIOPHARMA|HOLDINGS|GROUP)\.?\b")

    def norm_name(s):
        if pd.isna(s):
            return None
        s = str(s).upper()
        s = re.sub(r"[.,]", " ", s)
        s = SUFFIX_RE.sub("", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s if s else None

    ip_assignee = con.sql(f'''
        SELECT target_harmonized, current_assignee, COUNT(*) AS n_patents
        FROM read_csv_auto('{IP_CSV}') WHERE target_harmonized IS NOT NULL AND current_assignee IS NOT NULL
        GROUP BY 1,2
    ''').df()
    ip_assignee["assignee_norm"] = ip_assignee["current_assignee"].apply(norm_name)

    cl_sponsor = df.groupby(["target_h", "lead_sponsor_golden"]).size().reset_index(name="n_trials").rename(columns={"target_h": "target_harmonized"})
    cl_sponsor["sponsor_norm"] = cl_sponsor["lead_sponsor_golden"].apply(norm_name)

    SPONSOR_ASSIGNEE_OVERLAP = ip_assignee.merge(cl_sponsor, left_on=["target_harmonized", "assignee_norm"],
                                                  right_on=["target_harmonized", "sponsor_norm"], how="inner")
    SPONSOR_ASSIGNEE_OVERLAP = SPONSOR_ASSIGNEE_OVERLAP[["target_harmonized", "current_assignee", "n_patents",
                                                          "lead_sponsor_golden", "n_trials"]].sort_values(
        ["n_patents", "n_trials"], ascending=False)
    display(SPONSOR_ASSIGNEE_OVERLAP, name="entry16_sponsor_assignee_overlap")
    print(f"{len(SPONSOR_ASSIGNEE_OVERLAP)} (target, company) pairs where the SAME company (approximate "
          f"name-normalized match) both holds patents AND runs trials on that target -- the strongest possible "
          f"competitive signal: they believe in the biology enough to both file and trial it themselves.")

    top_companies = SPONSOR_ASSIGNEE_OVERLAP.groupby("current_assignee").agg(
        n_targets=("target_harmonized", "nunique"), total_patents=("n_patents", "sum"), total_trials=("n_trials", "sum")
    ).sort_values("n_targets", ascending=False).head(15)
    display(top_companies, name="entry16_top_companies_both_sides")

    fig, ax = plt.subplots(figsize=(9, 7))
    top_companies.sort_values("n_targets")["n_targets"].plot(kind="barh", ax=ax, color="#7c3aed")
    ax.set_xlabel("# Distinct Targets (patented AND trialed by same company)")
    ax.set_title("Top-15 Companies Most Active on BOTH the IP and Clinical Side")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 17 -- PART B: solid-tumor-indication triangulation, raw data
# ===========================================================================
entry(
    "Entry 17 [PART B]: Solid-Tumor-Indication Whitespace/Crowding, Computed Directly From Raw Data",
    r"""
    # Independent of Part A's notebook-tier reproduction: rank every solid
    # tumor indication by total patent volume vs total trial volume across
    # ALL qualifying targets (>=3 patents or >=5 trials), to see which
    # indications are broadly open vs broadly saturated on EITHER axis.
    ip_ind_totals = ip_junction.groupby("tumor_type").agg(n_patents=("target_h", "count"), n_targets_patented=("target_h", "nunique")).reset_index()
    cl_ind_totals = cl_by_target_tumor.groupby("tumor_type").agg(n_trials=("n_trials", "sum"), n_targets_trialed=("target_h", "nunique")).reset_index()

    INDICATION_RAW_TRIANGULATION = ip_ind_totals.merge(cl_ind_totals, on="tumor_type", how="outer").fillna(0)
    for c in ["n_patents", "n_targets_patented", "n_trials", "n_targets_trialed"]:
        INDICATION_RAW_TRIANGULATION[c] = INDICATION_RAW_TRIANGULATION[c].astype(int)
    INDICATION_RAW_TRIANGULATION = INDICATION_RAW_TRIANGULATION.sort_values("n_trials", ascending=False)
    display(INDICATION_RAW_TRIANGULATION, name="entry17_indication_raw_triangulation")

    p_med = INDICATION_RAW_TRIANGULATION["n_patents"].median()
    t_med = INDICATION_RAW_TRIANGULATION["n_trials"].median()

    def ind_quadrant(row):
        if row["n_patents"] >= p_med and row["n_trials"] >= t_med:
            return "Broadly Crowded (high patent AND high trial volume)"
        if row["n_patents"] < p_med and row["n_trials"] < t_med:
            return "Broadly Open (low patent AND low trial volume)"
        return "Split (open on one axis, saturated on the other)"

    INDICATION_RAW_TRIANGULATION["indication_quadrant"] = INDICATION_RAW_TRIANGULATION.apply(ind_quadrant, axis=1)
    display(INDICATION_RAW_TRIANGULATION[["tumor_type", "indication_quadrant"]], name="entry17_indication_quadrant_labels")

    fig, ax = plt.subplots(figsize=(9, 8))
    colors_map = {"Broadly Crowded (high patent AND high trial volume)": "#dc2626",
                  "Broadly Open (low patent AND low trial volume)": "#16a34a",
                  "Split (open on one axis, saturated on the other)": "#f59e0b"}
    for label, sub in INDICATION_RAW_TRIANGULATION.groupby("indication_quadrant"):
        ax.scatter(sub["n_patents"], sub["n_trials"], label=label, color=colors_map[label], s=70)
    for _, row in INDICATION_RAW_TRIANGULATION.iterrows():
        ax.annotate(row["tumor_type"], (row["n_patents"], row["n_trials"]), fontsize=7, xytext=(4, 4), textcoords="offset points")
    ax.set_xlabel("# IP Patents (solid tumor indication)"); ax.set_ylabel("# Clinical Trials (solid tumor indication)")
    ax.set_title("Solid-Tumor Indications: Patent Volume vs Trial Volume"); ax.legend(fontsize=7)
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 18 -- Executive summary
# ===========================================================================
entry(
    "Entry 18: Executive Summary & Key Findings",
    r"""
    n_both = len(MERGED_TIERS[MERGED_TIERS["_merge"] == "both"])
    n_match = (MERGED_TIERS[MERGED_TIERS["_merge"] == "both"]["ip_tier"] == MERGED_TIERS[MERGED_TIERS["_merge"] == "both"]["cl_tier"]).sum()
    n_crowded_both = len(crowded_both)
    n_ws_both = len(ws_both)
    n_ip_crowded_not_caught = len(ip_crowded_not_caught)
    n_cl_ws = len(ws_clinical)
    n_clinical_leads = len(clinical_leads)
    n_validated_thin = len(VALIDATED_PATENT_THIN)
    n_crowded_unproven = len(PATENT_CROWDED_UNPROVEN)
    n_cart_no_ip = int((CART_BLIND_SPOT["n_patents"] == 0).sum())
    n_overlap_companies = SPONSOR_ASSIGNEE_OVERLAP["current_assignee"].nunique()

    print("EXECUTIVE SUMMARY -- Clinical x IP Triangulation, Antibody Therapeutics in Oncology")
    print("=" * 88)
    print(f"Universe: {IP_QUADRANT.shape[0]} IP-tiered targets (>=3 patents) x {CL_TIER.shape[0]} clinical-tiered "
          f"targets; {n_both} targets qualify for a tier on BOTH sides ({n_match} agree, {n_both - n_match} disagree).")
    print()
    print("PART A -- Prior-Notebook Cross-Comparison:")
    print(f"  - {n_crowded_both} targets are CROWDED in both notebooks (de-risked biology, but a contested IP field): "
          f"{', '.join(crowded_both['target_harmonized'].head(9).tolist())}")
    print(f"  - {n_ws_both} targets are WHITESPACE in both (highest upside, highest uncertainty).")
    print(f"  - {n_ip_crowded_not_caught} targets are IP-Crowded but clinical activity has NOT caught up -- "
          f"patent filers are racing ahead of clinical proof.")
    print(f"  - Of {n_cl_ws} clinical Phase-1/2 whitespace targets, "
          f"{(ws_clinical['ip_status']=='No IP patent data at all (target absent from IP universe)').sum() + (ws_clinical['ip_status'].isin(['Whitespace (High Risk)','Medium'])).sum()} "
          f"are ALSO not IP-crowded -- truly open candidates; the rest sit on a crowded patent position despite "
          f"looking clinically clean.")
    print()
    print("PART B -- Raw-Data Triangulation:")
    print(f"  - {n_clinical_leads} targets show clinical trials starting BEFORE any patent filing (>=3 patents) -- "
          f"a potential freedom-to-operate gap worth legal review before committing.")
    print(f"  - {n_validated_thin} targets have >=1 clinical success AND <=10 patents -- the strongest 'file now' case.")
    print(f"  - {n_crowded_unproven} targets have >=10 patents and ZERO recorded clinical success -- highest "
          f"patent-litigation exposure for the least proven biology.")
    print(f"  - {n_cart_no_ip} of {len(CART_BLIND_SPOT)} clinically active CAR-T targets have no matching "
          f"antibody-modality IP record at all -- a dataset coverage gap, not a real absence of risk.")
    print(f"  - {n_overlap_companies} companies are found patenting AND trialing the same target under an "
          f"approximate name match -- the strongest observed competitive-conviction signal.")
    print()
    print("STRATEGIC IMPLICATION: the single most actionable output of this triangulation is the "
          "'clinically validated, patent-thin' list (Entry 13) crossed with the 'clinical Phase-1/2 whitespace, "
          "IP not crowded' list (Entry 8) -- these are the targets where both datasets independently agree "
          "there is room to move, which is the balanced (patent-freedom + clinical-validation) framing requested.")
    """,
)

# ===========================================================================
# ENTRY 19 -- PART C: TUMOR-TYPE DEEP DIVE -- validated/patent-thin, by indication
# ===========================================================================
entry(
    "Entry 19 [PART C -- TUMOR TYPE DEEP DIVE]: Validated & Patent-Thin Targets, by Solid-Tumor Indication",
    r"""
    # Entries 10-16 (Part B) were all computed at the TARGET level, pooling
    # every indication together. This Part C re-cuts the two highest-value
    # business calls -- "file now" (this entry) and "FTO risk" (next entry)
    # -- at the (target, solid-tumor-indication) level, reusing the same
    # canonical tumor-type taxonomy and the ip_by_target_tumor /
    # tumor_junction structures built in Entry 9 / Entry 2. A target can look
    # clinically validated overall (Entry 13) while still being unproven in
    # ONE specific indication that carries a heavy patent position, and vice
    # versa -- that nuance only shows up at this granularity.
    cl_val_by_tumor = tumor_junction.groupby(["target_h", "tumor_type"]).agg(
        n_trials=("nct", "count"),
        n_success=("outcome_bucket", lambda s: (s == "Success").sum()),
    ).reset_index()
    cl_val_by_tumor = cl_val_by_tumor[
        (cl_val_by_tumor["n_success"] > 0) & ~cl_val_by_tumor["tumor_type"].str.startswith("Hematologic")
        & (cl_val_by_tumor["tumor_type"] != "Solid_Tumor_Basket")
    ]

    VALIDATED_PATENT_THIN_BY_TUMOR = cl_val_by_tumor.merge(ip_by_target_tumor, on=["target_h", "tumor_type"], how="left")
    VALIDATED_PATENT_THIN_BY_TUMOR["n_patents"] = VALIDATED_PATENT_THIN_BY_TUMOR["n_patents"].fillna(0).astype(int)
    VALIDATED_PATENT_THIN_BY_TUMOR = VALIDATED_PATENT_THIN_BY_TUMOR.rename(columns={"target_h": "target_harmonized"})
    VALIDATED_PATENT_THIN_BY_TUMOR = VALIDATED_PATENT_THIN_BY_TUMOR[VALIDATED_PATENT_THIN_BY_TUMOR["n_patents"] <= 5].sort_values(
        ["n_success", "n_trials"], ascending=False)
    display(VALIDATED_PATENT_THIN_BY_TUMOR, name="entry19_validated_patent_thin_by_tumor")
    print(f"{len(VALIDATED_PATENT_THIN_BY_TUMOR)} (target, solid-tumor-indication) pairs have >=1 recorded clinical "
          f"success in that SPECIFIC indication AND <=5 patents tagged to that same indication -- the sharpest "
          f"'file now, this indication' business case (target-level thinness in Entry 13 can mask indication-level "
          f"crowding, and vice versa).")

    fig, ax = plt.subplots(figsize=(10, 8))
    top20 = VALIDATED_PATENT_THIN_BY_TUMOR.head(20).iloc[::-1]
    labels = top20["target_harmonized"] + " | " + top20["tumor_type"]
    ax.barh(labels, top20["n_patents"], color="#16a34a")
    for i, (n_s, n_p) in enumerate(zip(top20["n_success"], top20["n_patents"])):
        ax.text(n_p, i, f"  {int(n_s)} success(es)", va="center", fontsize=7)
    ax.set_xlabel("# Patents Tagged to This Indication (low = open)")
    ax.set_title("Top-20 Clinically-Validated, Patent-Thin (Target x Indication) Pairs")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 20 -- PART C: patent-crowded / clinically unproven, by indication
# ===========================================================================
entry(
    "Entry 20 [PART C -- TUMOR TYPE DEEP DIVE]: Patent-Crowded & Clinically-Unproven Targets, by Solid-Tumor Indication (FTO Risk by Indication)",
    r"""
    ip_crowded_by_tumor = ip_by_target_tumor[ip_by_target_tumor["n_patents"] >= 5].copy()

    cl_unproven_by_tumor = tumor_junction.groupby(["target_h", "tumor_type"]).agg(
        n_trials=("nct", "count"),
        n_success=("outcome_bucket", lambda s: (s == "Success").sum()),
        n_failure=("outcome_bucket", lambda s: (s == "Failure").sum()),
    ).reset_index()

    PATENT_CROWDED_UNPROVEN_BY_TUMOR = ip_crowded_by_tumor.merge(cl_unproven_by_tumor, on=["target_h", "tumor_type"], how="left")
    PATENT_CROWDED_UNPROVEN_BY_TUMOR[["n_trials", "n_success", "n_failure"]] = PATENT_CROWDED_UNPROVEN_BY_TUMOR[
        ["n_trials", "n_success", "n_failure"]].fillna(0)
    PATENT_CROWDED_UNPROVEN_BY_TUMOR = PATENT_CROWDED_UNPROVEN_BY_TUMOR.rename(columns={"target_h": "target_harmonized"})
    PATENT_CROWDED_UNPROVEN_BY_TUMOR = PATENT_CROWDED_UNPROVEN_BY_TUMOR[
        PATENT_CROWDED_UNPROVEN_BY_TUMOR["n_success"] == 0].sort_values("n_patents", ascending=False)
    display(PATENT_CROWDED_UNPROVEN_BY_TUMOR, name="entry20_patent_crowded_unproven_by_tumor")
    print(f"{len(PATENT_CROWDED_UNPROVEN_BY_TUMOR)} (target, solid-tumor-indication) pairs have >=5 patents tagged "
          f"to that indication but ZERO recorded clinical success in that SAME indication -- the sharpest "
          f"FTO/litigation-exposure view, since a target can look clinically proven overall (Entry 14) while still "
          f"being unproven in a SPECIFIC, heavily patented indication.")

    fig, ax = plt.subplots(figsize=(10, 8))
    top20 = PATENT_CROWDED_UNPROVEN_BY_TUMOR.head(20).iloc[::-1]
    labels = top20["target_harmonized"] + " | " + top20["tumor_type"]
    colors_ = ["#dc2626" if f > 0 else "#94a3b8" for f in top20["n_failure"]]
    ax.barh(labels, top20["n_patents"], color=colors_)
    ax.set_xlabel("# Patents Tagged to This Indication")
    ax.set_title("Top-20 Patent-Crowded, Clinically-Unproven (Target x Indication) Pairs\n"
                  "(red = has recorded failure(s) in this indication; grey = no readout yet)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 21 -- PART C: filing-to-trial timing lag, by indication
# ===========================================================================
entry(
    "Entry 21 [PART C -- TUMOR TYPE DEEP DIVE]: Filing-to-First-Trial Timing Lag, by Solid-Tumor Indication",
    r"""
    # Entry 12 computed the lead/lag flag per TARGET only. Re-derived here
    # per (target, indication) -- a target can be "IP leads" overall while
    # clinical trials in ONE specific indication actually started first.
    ip_ind_years = con.sql(f'''
        SELECT target_harmonized AS target_h, filing_year,
               TRIM(UNNEST(STR_SPLIT(TRIM(indications), '|'))) AS raw_indication
        FROM read_csv_auto('{IP_CSV}')
        WHERE target_harmonized IS NOT NULL AND indications IS NOT NULL
          AND TRIM(indications) NOT IN ('', 'oncology TA query hit')
    ''').df()
    ip_ind_years["tumor_tags"] = ip_ind_years["raw_indication"].apply(classify_tumor_tags)
    ip_ty = ip_ind_years.explode("tumor_tags").rename(columns={"tumor_tags": "tumor_type"})
    ip_ty = ip_ty[ip_ty["tumor_type"].notna() & ~ip_ty["tumor_type"].str.startswith("Hematologic")
                  & (ip_ty["tumor_type"] != "Solid_Tumor_Basket")]
    ip_ty_years = ip_ty.groupby(["target_h", "tumor_type"]).agg(
        first_filing_year=("filing_year", "min"), n_patents=("filing_year", "count")).reset_index()
    ip_ty_years = ip_ty_years[ip_ty_years["n_patents"] >= 3]

    df_ty = df[["nct", "target_h", "start_year", "tumor_tags"]].explode("tumor_tags").rename(columns={"tumor_tags": "tumor_type"})
    df_ty = df_ty[df_ty["tumor_type"].notna()]
    cl_ty_years = df_ty.groupby(["target_h", "tumor_type"]).agg(
        first_trial_year=("start_year", "min"), n_trials=("nct", "count")).reset_index()

    TIMING_LAG_BY_TUMOR = ip_ty_years.merge(cl_ty_years, on=["target_h", "tumor_type"], how="inner")
    TIMING_LAG_BY_TUMOR["lag_years_filing_to_trial"] = TIMING_LAG_BY_TUMOR["first_trial_year"] - TIMING_LAG_BY_TUMOR["first_filing_year"]
    TIMING_LAG_BY_TUMOR["lead_flag"] = TIMING_LAG_BY_TUMOR["lag_years_filing_to_trial"].apply(lead_flag)
    TIMING_LAG_BY_TUMOR = TIMING_LAG_BY_TUMOR.rename(columns={"target_h": "target_harmonized"})
    display(TIMING_LAG_BY_TUMOR.sort_values("lag_years_filing_to_trial"), name="entry21_timing_lag_by_tumor_full")
    display(TIMING_LAG_BY_TUMOR["lead_flag"].value_counts().rename_axis("lead_flag").reset_index(name="n_pairs"),
            name="entry21_lead_flag_counts_by_tumor")

    clinical_leads_by_tumor = TIMING_LAG_BY_TUMOR[TIMING_LAG_BY_TUMOR["lead_flag"].str.startswith("CLINICAL LEADS")].sort_values(
        "lag_years_filing_to_trial")
    print(f"{len(clinical_leads_by_tumor)} (target, indication) pairs show clinical trials in that SPECIFIC indication "
          f"starting BEFORE any patent was filed (>=3 patents, possible indication-specific FTO gap):")
    display(clinical_leads_by_tumor[["target_harmonized", "tumor_type", "first_trial_year", "first_filing_year",
                                      "lag_years_filing_to_trial", "n_trials", "n_patents"]],
            name="entry21_clinical_leads_by_tumor")

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.hist(TIMING_LAG_BY_TUMOR["lag_years_filing_to_trial"].dropna(), bins=30, color="#0e7490")
    ax.axvline(0, color="black", linestyle="--")
    ax.set_xlabel("Years (first trial start - first patent filing), per (target, indication)")
    ax.set_ylabel("# (target, indication) pairs")
    ax.set_title("Filing-to-First-Trial Lag Distribution, by Indication\n(positive = IP led; negative = clinical led)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 22 -- PART C: CAR-T clinical activity vs antibody IP coverage, by indication
# ===========================================================================
entry(
    "Entry 22 [PART C -- TUMOR TYPE DEEP DIVE]: CAR-T Clinical Activity vs Antibody-Modality IP Coverage, by Solid-Tumor Indication",
    r"""
    # Entry 15 checked the CAR-T IP blind spot at the target level only, and
    # necessarily restricted the IP side to solid-tumor indications (CAR-T's
    # real-world indication mix is heavily hematologic, which Entries 9/19/20
    # deliberately exclude). Here the IP side is left UNFILTERED by tumor
    # category (reusing ip_ind from Entry 9 before its solid-tumor-only
    # filter) so hematologic CAR-T indications -- where most CAR-T activity
    # actually sits -- are not silently dropped.
    cart_df = df[df["modality_code"] == "CAR-T"][["nct", "target_h", "tumor_tags"]]
    cart_ty = cart_df.explode("tumor_tags").rename(columns={"tumor_tags": "tumor_type"})
    cart_ty = cart_ty[cart_ty["tumor_type"].notna()]
    cart_by_tumor = cart_ty.groupby(["target_h", "tumor_type"]).size().reset_index(name="n_cart_trials")

    ip_ty_all = ip_ind.explode("tumor_tags").rename(columns={"tumor_tags": "tumor_type"})
    ip_ty_all = ip_ty_all[ip_ty_all["tumor_type"].notna()]
    ip_ty_all_counts = ip_ty_all.groupby(["target_h", "tumor_type"]).size().reset_index(name="n_patents")

    CART_BY_TUMOR = cart_by_tumor.merge(ip_ty_all_counts, on=["target_h", "tumor_type"], how="left")
    CART_BY_TUMOR["n_patents"] = CART_BY_TUMOR["n_patents"].fillna(0).astype(int)
    CART_BY_TUMOR = CART_BY_TUMOR.rename(columns={"target_h": "target_harmonized"})
    CART_BY_TUMOR["ip_coverage"] = np.where(CART_BY_TUMOR["n_patents"] == 0,
                                             "No antibody-modality IP for this target+indication",
                                             "Antibody-modality IP exists for this target+indication")
    CART_BY_TUMOR = CART_BY_TUMOR.sort_values("n_cart_trials", ascending=False)
    display(CART_BY_TUMOR.head(30), name="entry22_cart_by_tumor")

    n_no_ip_pairs = (CART_BY_TUMOR["n_patents"] == 0).sum()
    print(f"{len(CART_BY_TUMOR)} (target, indication) pairs with CAR-T clinical activity; {n_no_ip_pairs} of them "
          f"have NO matching antibody-modality IP record for that SAME target+indication combo -- most concentrated "
          f"in hematologic indications, which is where CAR-T predominantly operates and where this antibody-patent "
          f"dataset has its thinnest natural coverage.")

    fig, ax = plt.subplots(figsize=(10, 8))
    top20 = CART_BY_TUMOR.head(20).iloc[::-1]
    labels = top20["target_harmonized"] + " | " + top20["tumor_type"]
    colors_ = ["#94a3b8" if p == 0 else "#0891b2" for p in top20["n_patents"]]
    ax.barh(labels, top20["n_cart_trials"], color=colors_)
    ax.set_xlabel("# CAR-T Clinical Trials")
    ax.set_title("Top-20 CAR-T (Target x Indication) Pairs by Trial Volume\n"
                  "(grey = zero matching antibody-modality IP record found)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 23 -- Tumor-type analysis executive summary
# ===========================================================================
entry(
    "Entry 23: Tumor-Type Analysis -- Executive Summary",
    r"""
    n_ft_thin = len(VALIDATED_PATENT_THIN_BY_TUMOR)
    n_fto_risk = len(PATENT_CROWDED_UNPROVEN_BY_TUMOR)
    n_cl_leads_ty = len(clinical_leads_by_tumor)
    n_cart_pairs = len(CART_BY_TUMOR)
    n_cart_no_ip_pairs = n_no_ip_pairs

    print("TUMOR-TYPE ANALYSIS -- EXECUTIVE SUMMARY (Entries 19-22, Part C)")
    print("=" * 88)
    print("Entries 10-16 (Part B) were computed at the TARGET level only, pooling every indication "
          "together. This Part C re-cuts the highest-value business questions at the (target, "
          "solid-tumor-indication) level using the shared canonical tumor-type taxonomy from Entry 2/9:")
    print()
    print(f"  - {n_ft_thin} (target, indication) pairs are clinically validated AND patent-thin in that SAME "
          f"indication -- the sharpest 'file now, this indication' business case (Entry 19).")
    print(f"  - {n_fto_risk} (target, indication) pairs are patent-crowded (>=5 patents) with ZERO clinical "
          f"success in that SAME indication -- the sharpest indication-level FTO/litigation exposure (Entry 20).")
    print(f"  - {n_cl_leads_ty} (target, indication) pairs show clinical trials starting BEFORE any patent filing "
          f"in that indication -- an indication-specific freedom-to-operate flag finer than Entry 12's target-level view (Entry 21).")
    print(f"  - Of {n_cart_pairs} CAR-T (target, indication) pairs, {n_cart_no_ip_pairs} have no matching "
          f"antibody-modality IP coverage at all -- concentrated in hematologic indications, the dataset's "
          f"structural blind spot (Entry 22).")
    print()
    print("TAKEAWAY: target-level tiers (Part A/B) can mask or overstate an indication-specific opportunity or "
          "risk. Before finalizing a program bet, cross-check the target-level verdict against its indication-level "
          "cut here -- a target that looks 'validated' or 'crowded' overall may be wide open (or still totally "
          "unproven) in the ONE indication actually being considered.")
    """,
)


# ===========================================================================
# DRIVER
# ===========================================================================
def main():
    cells = []
    intro = f"""# Clinical x IP Triangulation -- Antibody Therapeutics in Oncology

**Prepared for:** Executive / Portfolio Strategy Review (new antibody-therapeutics program scoping)
**Analyst view:** Head of Data Analytics, cross-functional patent + clinical competitive intelligence
**Sources:**
- `input/ip_final_version3.csv` ({IP_CSV}) -- 8,901 antibody-oncology patent records
- `input/clinical_final_version1.csv` ({CL_CSV}) -- 19,357 antibody-oncology trial records
**Join key on BOTH sides:** the curated **`target_harmonized`** column only.

**Structure:**
- **Part A (Entries 3-9):** reproduces -- does not re-invent -- the whitespace/crowded tier logic
  already coded in `ip_landscape_final_version1.ipynb` (Entry 5D quadrants) and
  `clinical_landscape_final_version2.ipynb` (Entries 11/13/31), then cross-compares the two
  notebooks' own conclusions target-by-target, with dedicated spotlights on (i) targets the IP
  notebook calls Crowded that clinical activity hasn't caught up to, and (ii) the clinical
  notebook's Phase-1/2-only whitespace targets cross-referenced against IP crowding, and a
  solid-tumor-indication dissection of both.
- **Part B (Entries 10-17):** independent triangulation computed directly from the two raw CSVs
  (modality-adjusted crowding, filing-to-first-trial timing lag, validated/patent-thin and
  patent-crowded/unproven flags, the CAR-T IP blind spot, approximate sponsor/assignee overlap,
  and a raw-data solid-tumor-indication cut).
- **Entry 18:** consolidated executive summary.

Every table and chart below is generated live via DuckDB SQL + pandas; nothing is hand-typed.
"""
    cells.append(md_cell(intro))

    exec_count = 0
    for i, e in enumerate(ENTRIES, start=1):
        slug = f"entry{i:02d}"
        md = f"## {e['title']}\n\n{e['note']}" if e["note"] else f"## {e['title']}"
        cells.append(md_cell(md))
        exec_count += 1
        outputs = run_cell(e["code"], NS, exec_count, slug)
        cells.append(code_cell(e["code"], outputs, exec_count))
        print(f"[{i:02d}/{len(ENTRIES)}] executed: {e['title']}")

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.13"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }

    os.makedirs(os.path.dirname(NOTEBOOK_PATH), exist_ok=True)
    with open(NOTEBOOK_PATH, "w") as f:
        json.dump(notebook, f, indent=1)
    print(f"\nNotebook written: {NOTEBOOK_PATH}")

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FIG_DIR, exist_ok=True)
    n_exported_tables = 0
    for name, tdf in EXPORTED_TABLES:
        safe = re.sub(r"[^A-Za-z0-9_]+", "_", name)
        tdf.to_csv(os.path.join(DATA_DIR, f"{safe}.csv"), index=True)
        n_exported_tables += 1
    for name, png_bytes in EXPORTED_FIGS:
        with open(os.path.join(FIG_DIR, name), "wb") as f:
            f.write(png_bytes)
    shutil.copyfile(NOTEBOOK_PATH, os.path.join(OUT_DIR, "clinical_ip_comparison_final_version1.ipynb"))

    print(f"Exported {n_exported_tables} tables to {DATA_DIR}")
    print(f"Exported {len(EXPORTED_FIGS)} figures to {FIG_DIR}")
    print(f"Notebook copy: {os.path.join(OUT_DIR, 'clinical_ip_comparison_final_version1.ipynb')}")


if __name__ == "__main__":
    main()
