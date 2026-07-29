"""
build_clinical_landscape_notebook.py
=====================================
Builds input/clinical_landscape_final_version1.ipynb end-to-end: every code
cell is actually executed in-process (DuckDB SQL + pandas + matplotlib), and
the *real* computed outputs (tables as HTML/text, charts as PNG) are embedded
into the notebook JSON exactly as Jupyter would store them. No numbers are
hand-typed; everything is computed from input/clinical_final_version1.csv.

Also mirrors the existing IP-landscape pipeline convention: after the notebook
is built, every table with >10 rows is exported to
output/clinical_landscape_final_version/data/*.csv, every chart to
output/clinical_landscape_final_version/figures/*.png, and a copy of the
notebook is placed alongside them.

Run:  python3 build_clinical_landscape_notebook.py
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
INPUT_CSV = os.path.join(ROOT, "input", "clinical_final_version1.csv")
NOTEBOOK_PATH = os.path.join(ROOT, "input", "clinical_landscape_final_version1.ipynb")
OUT_DIR = os.path.join(ROOT, "output", "clinical_landscape_final_version")
DATA_DIR = os.path.join(OUT_DIR, "data")
FIG_DIR = os.path.join(OUT_DIR, "figures")

pd.set_option("display.max_columns", 40)
pd.set_option("display.width", 160)

# ---------------------------------------------------------------------------
# Mini "notebook kernel": executes a code string in a shared namespace and
# captures stdout, `display(...)` calls, the final bare expression, and any
# matplotlib figures -- into nbformat-style cell outputs, just like a real
# Jupyter kernel would.
# ---------------------------------------------------------------------------
EXPORTED_TABLES = []   # (name, DataFrame) for every table shown via display()/tab()
EXPORTED_FIGS = []     # (name, png_bytes) for every chart rendered
_TABLE_COUNTER = 0
_FIG_COUNTER = 0


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=125, bbox_inches="tight", facecolor="white")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def run_cell(code, ns, exec_count, entry_slug):
    """Execute `code` in namespace `ns`; return list of nbformat output dicts."""
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

        # Any dataframes / figures pushed via display()
        for name, obj in display_queue:
            outputs.extend(_render_object(obj, exec_count, entry_slug, name))

        # matplotlib figures created but not explicitly display()-ed
        for num in plt.get_fignums():
            fig = plt.figure(num)
            b64 = _fig_to_b64(fig)
            _FIG_COUNTER += 1
            fname = f"{entry_slug}_fig{_FIG_COUNTER:02d}.png"
            EXPORTED_FIGS.append((fname, base64.b64decode(b64)))
            outputs.append({"output_type": "display_data",
                             "data": {"image/png": b64}, "metadata": {}})
        plt.close("all")

        # Trailing bare expression (e.g. `df.head(10)`)
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
        d = {"output_type": out_type, "data": data, "metadata": {}}
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


# ---------------------------------------------------------------------------
# Build the notebook: a list of (markdown, code, slug) entries executed in
# one shared namespace `NS`, in order -- exactly like a person running cells
# top to bottom in Jupyter.
# ---------------------------------------------------------------------------
NS = {"pd": pd, "np": np, "duckdb": duckdb, "plt": plt, "re": re, "INPUT_CSV": INPUT_CSV}

ENTRIES = []  # populated by entry() calls below


def entry(title, code, note=""):
    ENTRIES.append({"title": title, "note": note, "code": code})


# ===========================================================================
# ENTRY 1 -- Setup & data load
# ===========================================================================
entry(
    "Entry 1: Setup, Scope & Data Load",
    r"""
    # Executive framing -----------------------------------------------------
    # We are evaluating antibody-based oncology assets (mAb / ADC / bispecific /
    # BiTE / CAR-T / radioligand / PROTAC) currently or historically in the
    # clinic, to decide WHERE our own antibody-therapeutics program should
    # place its target + modality bet. Source: input/clinical_final_version1.csv
    con = duckdb.connect()
    con.execute(f"CREATE OR REPLACE VIEW raw_trials AS SELECT * FROM read_csv_auto('{INPUT_CSV}', ALL_VARCHAR=FALSE, SAMPLE_SIZE=-1)")
    raw = con.sql("SELECT * FROM raw_trials").df()
    print(f"Raw rows: {len(raw):,}   Columns: {raw.shape[1]}   Unique NCTs: {raw['nct'].nunique():,}")
    print()
    print(con.sql('''
        SELECT in_scope, COUNT(*) AS n_rows
        FROM raw_trials GROUP BY 1 ORDER BY 2 DESC
    ''').df().to_string(index=False))
    """,
)

# ===========================================================================
# ENTRY 2 -- Cleaning: scope filter, phase canonicalization, outcome bucket
# ===========================================================================
entry(
    "Entry 2: Analytic Base Table -- Scope Filter, Canonical Phase & Outcome Bucket",
    r"""
    # Keep in-scope antibody-modality oncology trials only. Build a canonical
    # phase (favors CT.gov phase, falls back to curated `phase` text) and a
    # 4-way outcome bucket (Success / Failure / Ongoing-Pending / Unclear) off
    # the curated `outcome` column -- this is our label for "succeeded /
    # failed / still-in-trial".
    df = raw[raw["in_scope"] == True].copy()  # noqa: E712 (nullable BooleanArray from DuckDB)

    def canon_phase(raw_phase, ctgov_phase):
        s = str(ctgov_phase) if pd.notna(ctgov_phase) and str(ctgov_phase) != "None" else str(raw_phase)
        s = s.upper()
        has1 = ("PHASE1" in s) or ("EARLY_PHASE1" in s)
        has2 = "PHASE2" in s
        has3 = "PHASE3" in s
        has4 = "PHASE4" in s
        if has4:
            return "Phase 4"
        if has3 and has2:
            return "Phase 2/3"
        if has3:
            return "Phase 3"
        if has2 and has1:
            return "Phase 1/2"
        if has2:
            return "Phase 2"
        if has1:
            return "Phase 1"
        return "Unknown"

    df["phase_canon"] = [canon_phase(p, c) for p, c in zip(df["phase"], df["ctgov_phase"])]

    OUTCOME_BUCKET = {
        "positive": "Success", "approved": "Success",
        "negative": "Failure", "terminated": "Failure",
        "ongoing": "Ongoing/Pending", "pending": "Ongoing/Pending",
        "unclear": "Unclear",
    }
    df["outcome_bucket"] = df["outcome"].map(OUTCOME_BUCKET).fillna("Unclear")

    df["start_year"] = pd.to_datetime(df["start_date"], errors="coerce", format="mixed").dt.year
    df["target_h"] = df["target_harmonized"].replace({"": np.nan})
    df["n_targets_h"] = pd.to_numeric(df["n_targets_harmonized"], errors="coerce")

    con.register("trials", df)
    print(f"In-scope analytic base: {len(df):,} rows ({df['nct'].nunique():,} unique NCTs)")
    print()
    print("Phase canonicalization coverage:")
    print(df["phase_canon"].value_counts(dropna=False).to_string())
    print()
    print("Outcome bucket coverage:")
    print(df["outcome_bucket"].value_counts(dropna=False).to_string())
    """,
)


# ===========================================================================
# ENTRY 3 -- Tumor-type harmonization engine (keyword classifier)
# ===========================================================================
entry(
    "Entry 3: Tumor-Type Harmonization Engine (Solid Tumor Classifier)",
    r"""
    # There is no curated tumor-type column, so we build one from
    # ctgov_conditions (fallback m_conditions) using an ordered keyword/regex
    # classifier -- most-specific categories checked first (e.g. NSCLC before
    # generic "lung"). A trial can carry MULTIPLE tags (basket trials); we
    # keep the full tag list for tumor-specific cuts and a single
    # `tumor_primary` (first match) for row-level summaries.
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
    COMPILED = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in TUMOR_CATEGORIES]

    def classify_tumor_tags(cond_text):
        if not cond_text or pd.isna(cond_text):
            return []
        return [name for name, rx in COMPILED if rx.search(str(cond_text))]

    df["condition_text"] = df["ctgov_conditions"].fillna(df["m_conditions"])
    df["tumor_tags"] = df["condition_text"].apply(classify_tumor_tags)
    df["tumor_primary"] = df["tumor_tags"].apply(lambda t: t[0] if t else None)
    df["tumor_primary"] = df["tumor_primary"].where(
        df["tumor_primary"].notna(),
        np.where(df["condition_text"].notna(), "Other_Unclassified", "No_Condition_Data"),
    )

    def tumor_group(primary):
        if primary.startswith("Hematologic"):
            return "Hematologic"
        if primary == "Solid_Tumor_Basket":
            return "Solid (Basket/Unspecified)"
        if primary in ("Other_Unclassified", "No_Condition_Data"):
            return "Unclassified"
        return "Solid (Named Type)"

    df["tumor_group"] = df["tumor_primary"].apply(tumor_group)
    con.register("trials", df)

    print("Tumor-group coverage (row-level, primary tag):")
    print(df["tumor_group"].value_counts(dropna=False).to_string())
    print()
    print(f"Classifier hit-rate (rows w/ condition text that matched >=1 tag): "
          f"{(df['tumor_tags'].str.len() > 0).sum():,} / {df['condition_text'].notna().sum():,}")

    top_primary = df["tumor_primary"].value_counts().head(25).to_frame("n_trials")
    display(top_primary, name="entry03_top_tumor_primary")

    # Trial x tumor-tag junction (long form) -- basket trials counted once per tag
    tumor_junction = df[["nct", "target_h", "modality_code", "phase_canon", "outcome_bucket", "tumor_tags"]].explode("tumor_tags")
    tumor_junction = tumor_junction.rename(columns={"tumor_tags": "tumor_type"})
    tumor_junction = tumor_junction[tumor_junction["tumor_type"].notna()]
    con.register("tumor_junction", tumor_junction)
    print(f"\nTumor-tag junction rows (trial x tumor tag): {len(tumor_junction):,}")
    """,
)

# ===========================================================================
# ENTRY 4 -- Modality landscape overview + trend chart
# ===========================================================================
entry(
    "Entry 4: Modality Landscape Overview & Filing/Start Trend",
    r"""
    q = con.sql('''
        SELECT modality_code, COUNT(*) AS n_trials,
               COUNT(DISTINCT lead_sponsor_golden) AS n_sponsors,
               COUNT(DISTINCT target_h) AS n_distinct_targets
        FROM trials GROUP BY 1 ORDER BY n_trials DESC
    ''').df()
    display(q, name="entry04_modality_overview")

    trend = con.sql('''
        SELECT start_year, modality_code, COUNT(*) AS n_trials
        FROM trials
        WHERE start_year BETWEEN 2000 AND 2025
          AND modality_code IN ('MAB','ADC','BISPECIFIC','CAR-T','BiTE','RADIOLIGAND')
        GROUP BY 1,2
    ''').df()
    pivot = trend.pivot_table(index="start_year", columns="modality_code", values="n_trials", fill_value=0)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
    q.set_index("modality_code")["n_trials"].sort_values().plot(kind="barh", ax=axes[0], color="#2563eb")
    axes[0].set_title("Trial count by modality (all phases, all-time)")
    axes[0].set_xlabel("# trials")

    pivot.plot(ax=axes[1], linewidth=2)
    axes[1].set_title("Trial starts per year, by modality")
    axes[1].set_xlabel("Start year"); axes[1].set_ylabel("# trials started")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 5 -- Target landscape overview (top 30)
# ===========================================================================
entry(
    "Entry 5: Target Landscape Overview (Top 30 Targets by Clinical Activity)",
    r"""
    q = con.sql('''
        SELECT target_h AS target_harmonized, COUNT(*) AS n_trials,
               COUNT(DISTINCT lead_sponsor_golden) AS n_sponsors,
               COUNT(DISTINCT modality_code) AS n_modalities,
               COUNT(DISTINCT nct) AS n_ncts
        FROM trials
        WHERE target_h IS NOT NULL
        GROUP BY 1 ORDER BY n_trials DESC LIMIT 30
    ''').df()
    display(q, name="entry05_top30_targets")
    print(f"Distinct target_harmonized values (incl. multi-target combos): {df['target_h'].nunique():,}")
    """,
)

# ===========================================================================
# ENTRY 6 -- Modality x Phase funnel + chart
# ===========================================================================
entry(
    "Entry 6: Modality x Phase Funnel (Development-Stage Mix)",
    r"""
    q = con.sql('''
        SELECT modality_code, phase_canon, COUNT(*) AS n
        FROM trials WHERE modality_code IN ('MAB','ADC','BISPECIFIC','CAR-T','BiTE','RADIOLIGAND')
        GROUP BY 1,2
    ''').df()
    pivot = q.pivot_table(index="modality_code", columns="phase_canon", values="n", fill_value=0)
    order = ["Phase 1", "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Phase 4", "Unknown"]
    pivot = pivot[[c for c in order if c in pivot.columns]]
    display(pivot, name="entry06_modality_phase_counts")

    pivot_pct = pivot.div(pivot.sum(axis=1), axis=0) * 100
    fig, ax = plt.subplots(figsize=(9, 4.5))
    pivot_pct.plot(kind="barh", stacked=True, ax=ax, colormap="viridis")
    ax.set_xlabel("% of trials"); ax.set_title("Phase mix by modality (development-stage funnel)")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 7 -- Target x Phase heatmap (top 25)
# ===========================================================================
entry(
    "Entry 7: Target x Phase Matrix (Top 25 Targets)",
    r"""
    top25 = con.sql("SELECT target_h FROM trials WHERE target_h IS NOT NULL GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 25").df()["target_h"].tolist()
    mat = df[df["target_h"].isin(top25)].pivot_table(index="target_h", columns="phase_canon", values="nct", aggfunc="count", fill_value=0)
    mat = mat.reindex(top25)
    order = ["Phase 1", "Phase 1/2", "Phase 2", "Phase 2/3", "Phase 3", "Phase 4", "Unknown"]
    mat = mat[[c for c in order if c in mat.columns]]
    display(mat, name="entry07_top25_target_phase_matrix")

    fig, ax = plt.subplots(figsize=(8, 9))
    im = ax.imshow(mat.values, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(mat.columns))); ax.set_xticklabels(mat.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(mat.index))); ax.set_yticklabels(mat.index)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v > 0:
                ax.text(j, i, int(v), ha="center", va="center", fontsize=7)
    ax.set_title("Top-25 targets x phase -- trial counts")
    plt.colorbar(im, ax=ax, label="# trials")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 8 -- Modality-level outcome rates
# ===========================================================================
entry(
    "Entry 8: Modality-Level Clinical Outcome Rates",
    r"""
    q = con.sql('''
        SELECT modality_code, outcome_bucket, COUNT(*) AS n
        FROM trials WHERE modality_code IN ('MAB','ADC','BISPECIFIC','CAR-T','BiTE','RADIOLIGAND')
        GROUP BY 1,2
    ''').df()
    pivot = q.pivot_table(index="modality_code", columns="outcome_bucket", values="n", fill_value=0)
    pivot_pct = (pivot.div(pivot.sum(axis=1), axis=0) * 100).round(1)
    display(pivot, name="entry08_modality_outcome_counts")
    display(pivot_pct, name="entry08_modality_outcome_pct")

    cols = [c for c in ["Success", "Failure", "Ongoing/Pending", "Unclear"] if c in pivot_pct.columns]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    pivot_pct[cols].plot(kind="barh", stacked=True, ax=ax, color=["#16a34a", "#dc2626", "#2563eb", "#9ca3af"])
    ax.set_xlabel("% of trials"); ax.set_title("Clinical outcome mix by modality")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 9 -- Target-level outcome scorecard (core "succeeded/failed/in trial by phase")
# ===========================================================================
entry(
    "Entry 9: Target Validation Scorecard -- Succeeded / Failed / Still-in-Trial, by Phase",
    r"""
    q = con.sql('''
        SELECT target_h AS target_harmonized,
               COUNT(*) AS n_trials,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure,
               SUM(CASE WHEN outcome_bucket='Ongoing/Pending' THEN 1 ELSE 0 END) AS n_ongoing,
               SUM(CASE WHEN outcome_bucket='Unclear' THEN 1 ELSE 0 END) AS n_unclear,
               SUM(CASE WHEN phase_canon='Phase 1' THEN 1 ELSE 0 END) AS ph1,
               SUM(CASE WHEN phase_canon='Phase 1/2' THEN 1 ELSE 0 END) AS ph1_2,
               SUM(CASE WHEN phase_canon='Phase 2' THEN 1 ELSE 0 END) AS ph2,
               SUM(CASE WHEN phase_canon='Phase 2/3' THEN 1 ELSE 0 END) AS ph2_3,
               SUM(CASE WHEN phase_canon='Phase 3' THEN 1 ELSE 0 END) AS ph3,
               SUM(CASE WHEN phase_canon='Phase 4' THEN 1 ELSE 0 END) AS ph4,
               SUM(CASE WHEN outcome='approved' THEN 1 ELSE 0 END) AS n_approved,
               MAX(start_year) AS latest_start_year
        FROM trials
        WHERE target_h IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= 5
        ORDER BY n_trials DESC
    ''').df()
    q["success_rate_%"] = (q["n_success"] / q["n_trials"] * 100).round(1)
    q["failure_rate_%"] = (q["n_failure"] / q["n_trials"] * 100).round(1)

    def target_status(row):
        if row["n_success"] > 0 and row["n_failure"] > 0:
            return "Mixed (succeeded & failed)"
        if row["n_success"] > 0:
            return "Validated (success recorded)"
        if row["n_failure"] > 0:
            return "Failed only (no success yet)"
        return "Still-in-trial (no readout yet)"

    q["target_status"] = q.apply(target_status, axis=1)
    TARGET_SCORECARD = q
    display(q, name="entry09_target_outcome_scorecard")
    print(f"\n{len(q)} targets have >=5 trials. Status breakdown:")
    display(q["target_status"].value_counts().to_frame("n_targets"), name="entry09_target_status_summary")
    """,
)

# ===========================================================================
# ENTRY 10 -- Chart for entry 9
# ===========================================================================
entry(
    "Entry 10: Chart -- Top-25 Targets, Outcome Composition",
    r"""
    plot_df = TARGET_SCORECARD.sort_values("n_trials", ascending=False).head(25).set_index("target_harmonized")
    plot_df = plot_df[["n_success", "n_failure", "n_ongoing", "n_unclear"]].iloc[::-1]
    fig, ax = plt.subplots(figsize=(9, 8))
    plot_df.plot(kind="barh", stacked=True, ax=ax, color=["#16a34a", "#dc2626", "#2563eb", "#9ca3af"])
    ax.set_xlabel("# trials"); ax.set_title("Top-25 most-active targets: trial outcome composition")
    ax.legend(loc="lower right")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 11 -- Modality x Target crowding matrix (top 20)
# ===========================================================================
entry(
    "Entry 11: Target x Modality Crowding Matrix (Top 20 Targets)",
    r"""
    top20 = TARGET_SCORECARD.sort_values("n_trials", ascending=False).head(20)["target_harmonized"].tolist()
    mat = df[df["target_h"].isin(top20)].pivot_table(index="target_h", columns="modality_code", values="nct", aggfunc="count", fill_value=0)
    mat = mat.reindex(top20)
    display(mat, name="entry11_target_modality_matrix")

    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(mat.values, cmap="Blues", aspect="auto")
    ax.set_xticks(range(len(mat.columns))); ax.set_xticklabels(mat.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(mat.index))); ax.set_yticklabels(mat.index)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat.values[i, j]
            if v > 0:
                ax.text(j, i, int(v), ha="center", va="center", fontsize=7)
    ax.set_title("Top-20 targets x modality -- trial counts (crowding matrix)")
    plt.colorbar(im, ax=ax, label="# trials")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 12 -- Tumor-type x modality landscape (solid tumors)
# ===========================================================================
entry(
    "Entry 12: Solid-Tumor-Type x Modality Landscape",
    r"""
    q = con.sql('''
        SELECT tumor_type, modality_code, COUNT(*) AS n_trials
        FROM tumor_junction
        WHERE tumor_type NOT LIKE 'Hematologic%' AND tumor_type <> 'Solid_Tumor_Basket'
        GROUP BY 1,2
    ''').df()
    pivot = q.pivot_table(index="tumor_type", columns="modality_code", values="n_trials", fill_value=0)
    pivot["Total"] = pivot.sum(axis=1)
    pivot = pivot.sort_values("Total", ascending=False)
    display(pivot, name="entry12_tumor_modality_matrix")

    top15 = pivot.head(15).drop(columns="Total")
    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(top15.values, cmap="Purples", aspect="auto")
    ax.set_xticks(range(len(top15.columns))); ax.set_xticklabels(top15.columns, rotation=45, ha="right")
    ax.set_yticks(range(len(top15.index))); ax.set_yticklabels(top15.index)
    for i in range(top15.shape[0]):
        for j in range(top15.shape[1]):
            v = top15.values[i, j]
            if v > 0:
                ax.text(j, i, int(v), ha="center", va="center", fontsize=7)
    ax.set_title("Top-15 solid tumor types x modality -- trial counts")
    plt.colorbar(im, ax=ax, label="# trials")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 13 -- Tumor-type x target landscape (top 5 targets per top tumor types)
# ===========================================================================
entry(
    "Entry 13: Top Targets per Solid Tumor Type",
    r"""
    q = con.sql('''
        WITH agg AS (
          SELECT tumor_type, target_h AS target_harmonized, COUNT(*) AS n_trials
          FROM tumor_junction
          WHERE tumor_type NOT LIKE 'Hematologic%' AND tumor_type <> 'Solid_Tumor_Basket' AND target_h IS NOT NULL
          GROUP BY 1,2
        ), ranked AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY tumor_type ORDER BY n_trials DESC) AS rnk,
                 SUM(n_trials) OVER (PARTITION BY tumor_type) AS tumor_total
          FROM agg
        )
        SELECT tumor_type, target_harmonized, n_trials, tumor_total FROM ranked
        WHERE rnk <= 5 AND tumor_total >= 20
        ORDER BY tumor_total DESC, n_trials DESC
    ''').df()
    display(q, name="entry13_top_targets_per_tumor_type")
    """,
)

# ===========================================================================
# ENTRY 14 -- Chart for entry 13 (small multiples)
# ===========================================================================
entry(
    "Entry 14: Chart -- Top Targets per Solid Tumor Type (Small Multiples)",
    r"""
    q = con.sql('''
        WITH agg AS (
          SELECT tumor_type, target_h AS target_harmonized, COUNT(*) AS n_trials
          FROM tumor_junction
          WHERE tumor_type NOT LIKE 'Hematologic%' AND tumor_type <> 'Solid_Tumor_Basket' AND target_h IS NOT NULL
          GROUP BY 1,2
        ), ranked AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY tumor_type ORDER BY n_trials DESC) AS rnk,
                 SUM(n_trials) OVER (PARTITION BY tumor_type) AS tumor_total
          FROM agg
        )
        SELECT tumor_type, target_harmonized, n_trials, tumor_total FROM ranked
        WHERE rnk <= 5 AND tumor_total >= 20
        ORDER BY tumor_total DESC, n_trials DESC
    ''').df()
    top_tumors = q.drop_duplicates("tumor_type").sort_values("tumor_total", ascending=False)["tumor_type"].head(8).tolist()
    fig, axes = plt.subplots(2, 4, figsize=(16, 7))
    for ax, tt in zip(axes.flat, top_tumors):
        sub = q[q["tumor_type"] == tt].sort_values("n_trials")
        ax.barh(sub["target_harmonized"], sub["n_trials"], color="#0891b2")
        ax.set_title(tt, fontsize=10)
        ax.tick_params(labelsize=8)
    plt.suptitle("Top-5 targets in each of the 8 most-studied solid tumor types")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 15 -- Per-tumor-type target outcome scorecard
# ===========================================================================
entry(
    "Entry 15: Per-Tumor-Type Target Outcome Scorecard (Solid Tumors)",
    r"""
    q = con.sql('''
        SELECT tumor_type, target_h AS target_harmonized,
               COUNT(*) AS n_trials,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure,
               SUM(CASE WHEN outcome_bucket='Ongoing/Pending' THEN 1 ELSE 0 END) AS n_ongoing
        FROM tumor_junction
        WHERE tumor_type NOT LIKE 'Hematologic%' AND tumor_type <> 'Solid_Tumor_Basket' AND target_h IS NOT NULL
        GROUP BY 1,2
        HAVING COUNT(*) >= 3
        ORDER BY n_trials DESC
    ''').df()
    TUMOR_TARGET_SCORECARD = q
    display(q.head(50), name="entry15_tumor_target_outcome")
    print(f"{len(q)} target x solid-tumor-type combinations with >=3 trials (full table exported to CSV).")
    """,
)

# ===========================================================================
# ENTRY 16 -- Combo strategy divergence: IO-combo vs monotherapy/all-comers
# ===========================================================================
entry(
    "Entry 16: Regimen-Dependent Divergence -- Same Target, IO-Combo vs Monotherapy",
    r"""
    # "Same target succeeded with IO but failed as an all-comers monotherapy"
    def regimen_bucket(is_combo, partner_class):
        ic = str(is_combo)
        if ic == "True":
            pc = str(partner_class) if pd.notna(partner_class) else ""
            return "IO-combo" if "IO" in pc else "Non-IO-combo (chemo/TKI/other)"
        if ic == "False":
            return "Monotherapy"
        return "Unknown regimen"

    df["regimen_bucket"] = [regimen_bucket(a, b) for a, b in zip(df["is_combo"], df["combo_partner_class"])]
    con.register("trials", df)

    q = con.sql('''
        SELECT target_h AS target_harmonized, regimen_bucket,
               COUNT(*) AS n_trials,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure
        FROM trials
        WHERE target_h IS NOT NULL AND regimen_bucket IN ('IO-combo','Monotherapy')
        GROUP BY 1,2
    ''').df()
    piv_t = q.pivot_table(index="target_harmonized", columns="regimen_bucket", values="n_trials", fill_value=0)
    piv_s = q.pivot_table(index="target_harmonized", columns="regimen_bucket", values="n_success", fill_value=0)
    piv_f = q.pivot_table(index="target_harmonized", columns="regimen_bucket", values="n_failure", fill_value=0)

    divergence = pd.DataFrame({
        "n_trials_IO_combo": piv_t.get("IO-combo", 0), "n_success_IO_combo": piv_s.get("IO-combo", 0), "n_failure_IO_combo": piv_f.get("IO-combo", 0),
        "n_trials_mono": piv_t.get("Monotherapy", 0), "n_success_mono": piv_s.get("Monotherapy", 0), "n_failure_mono": piv_f.get("Monotherapy", 0),
    }).fillna(0)
    divergence = divergence[(divergence["n_trials_IO_combo"] >= 2) & (divergence["n_trials_mono"] >= 2)]
    divergence["divergence_flag"] = np.where(
        (divergence["n_success_IO_combo"] > 0) & (divergence["n_failure_mono"] > 0) & (divergence["n_success_mono"] == 0),
        "IO-combo succeeds, monotherapy fails",
        np.where(
            (divergence["n_success_mono"] > 0) & (divergence["n_failure_IO_combo"] > 0) & (divergence["n_success_IO_combo"] == 0),
            "Monotherapy succeeds, IO-combo fails", "No clear divergence"))
    REGIMEN_DIVERGENCE = divergence.sort_values("n_trials_IO_combo", ascending=False)
    display(REGIMEN_DIVERGENCE, name="entry16_regimen_divergence")

    flagged = REGIMEN_DIVERGENCE[REGIMEN_DIVERGENCE["divergence_flag"] != "No clear divergence"]
    print(f"\n{len(flagged)} targets show a regimen-dependent outcome divergence (IO-combo vs monotherapy), "
          f"out of {len(REGIMEN_DIVERGENCE)} targets with >=2 trials in both arms.")
    display(flagged, name="entry16_regimen_divergence_flagged")
    """,
)

# ===========================================================================
# ENTRY 17 -- Chart for entry 16
# ===========================================================================
entry(
    "Entry 17: Chart -- IO-Combo vs Monotherapy Success-Rate Divergence",
    r"""
    plot_df = REGIMEN_DIVERGENCE.copy()
    plot_df["success_rate_IO"] = plot_df["n_success_IO_combo"] / plot_df["n_trials_IO_combo"] * 100
    plot_df["success_rate_mono"] = plot_df["n_success_mono"] / plot_df["n_trials_mono"] * 100

    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    ax.scatter(plot_df["success_rate_mono"], plot_df["success_rate_IO"],
               s=plot_df["n_trials_IO_combo"] * 10 + 20, alpha=0.55, c="#7c3aed", edgecolor="white")
    for name, row in plot_df.iterrows():
        ax.annotate(name, (row["success_rate_mono"], row["success_rate_IO"]), fontsize=7)
    top_lim = max(plot_df["success_rate_mono"].max(), plot_df["success_rate_IO"].max(), 10) + 5
    ax.plot([0, top_lim], [0, top_lim], "--", color="gray", linewidth=1)
    ax.set_xlim(0, top_lim); ax.set_ylim(0, top_lim)
    ax.set_xlabel("Monotherapy / all-comers success rate (%)")
    ax.set_ylabel("IO-combo success rate (%)")
    ax.set_title("Regimen-dependent divergence by target\n(above diagonal = IO-combo helps; below = mono better)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 18 -- Modality divergence: naked mAb vs ADC, same target
# ===========================================================================
entry(
    "Entry 18: Modality-Dependent Divergence -- Same Target, Naked mAb vs ADC",
    r"""
    # "Failed as mono[clonal antibody] but succeeded as an ADC" (or vice versa)
    q = con.sql('''
        SELECT target_h AS target_harmonized, modality_code,
               COUNT(*) AS n_trials,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure
        FROM trials
        WHERE target_h IS NOT NULL AND modality_code IN ('MAB','ADC')
        GROUP BY 1,2
    ''').df()
    piv_t = q.pivot_table(index="target_harmonized", columns="modality_code", values="n_trials", fill_value=0)
    piv_s = q.pivot_table(index="target_harmonized", columns="modality_code", values="n_success", fill_value=0)
    piv_f = q.pivot_table(index="target_harmonized", columns="modality_code", values="n_failure", fill_value=0)

    mod_div = pd.DataFrame({
        "n_trials_MAB": piv_t.get("MAB", 0), "n_success_MAB": piv_s.get("MAB", 0), "n_failure_MAB": piv_f.get("MAB", 0),
        "n_trials_ADC": piv_t.get("ADC", 0), "n_success_ADC": piv_s.get("ADC", 0), "n_failure_ADC": piv_f.get("ADC", 0),
    }).fillna(0)
    mod_div = mod_div[(mod_div["n_trials_MAB"] >= 2) & (mod_div["n_trials_ADC"] >= 2)]
    mod_div["divergence_flag"] = np.where(
        (mod_div["n_success_ADC"] > 0) & (mod_div["n_failure_MAB"] > 0) & (mod_div["n_success_MAB"] == 0),
        "ADC succeeds, naked mAb fails",
        np.where((mod_div["n_success_MAB"] > 0) & (mod_div["n_failure_ADC"] > 0) & (mod_div["n_success_ADC"] == 0),
                 "Naked mAb succeeds, ADC fails", "No clear divergence"))
    MODALITY_DIVERGENCE = mod_div.sort_values("n_trials_ADC", ascending=False)
    display(MODALITY_DIVERGENCE, name="entry18_modality_divergence")

    flagged2 = MODALITY_DIVERGENCE[MODALITY_DIVERGENCE["divergence_flag"] != "No clear divergence"]
    print(f"\n{len(flagged2)} targets show a modality-dependent outcome divergence (naked mAb vs ADC), "
          f"out of {len(MODALITY_DIVERGENCE)} targets with >=2 trials in both modalities.")
    display(flagged2, name="entry18_modality_divergence_flagged")
    """,
)

# ===========================================================================
# ENTRY 19 -- Chart for entry 18
# ===========================================================================
entry(
    "Entry 19: Chart -- Naked mAb vs ADC Success-Rate Divergence",
    r"""
    plot_df = MODALITY_DIVERGENCE.copy()
    plot_df["success_rate_MAB"] = plot_df["n_success_MAB"] / plot_df["n_trials_MAB"] * 100
    plot_df["success_rate_ADC"] = plot_df["n_success_ADC"] / plot_df["n_trials_ADC"] * 100

    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    ax.scatter(plot_df["success_rate_MAB"], plot_df["success_rate_ADC"],
               s=plot_df["n_trials_ADC"] * 10 + 20, alpha=0.55, c="#ea580c", edgecolor="white")
    for name, row in plot_df.iterrows():
        ax.annotate(name, (row["success_rate_MAB"], row["success_rate_ADC"]), fontsize=7)
    top_lim = max(plot_df["success_rate_MAB"].max(), plot_df["success_rate_ADC"].max(), 10) + 5
    ax.plot([0, top_lim], [0, top_lim], "--", color="gray", linewidth=1)
    ax.set_xlim(0, top_lim); ax.set_ylim(0, top_lim)
    ax.set_xlabel("Naked mAb success rate (%)")
    ax.set_ylabel("ADC success rate (%)")
    ax.set_title("Modality-dependent divergence by target\n(above diagonal = ADC helps; below = naked mAb better)")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 20 -- Line-of-therapy divergence (1L vs 2L)
# ===========================================================================
entry(
    "Entry 20: Line-of-Therapy Divergence -- Same Target, 1L vs 2L+",
    r"""
    q = con.sql('''
        SELECT target_h AS target_harmonized, m_line_of_therapy,
               COUNT(*) AS n_trials,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure
        FROM trials
        WHERE target_h IS NOT NULL AND m_line_of_therapy IN ('1L','2L')
        GROUP BY 1,2
    ''').df()
    piv_t = q.pivot_table(index="target_harmonized", columns="m_line_of_therapy", values="n_trials", fill_value=0)
    piv_s = q.pivot_table(index="target_harmonized", columns="m_line_of_therapy", values="n_success", fill_value=0)
    piv_f = q.pivot_table(index="target_harmonized", columns="m_line_of_therapy", values="n_failure", fill_value=0)

    lot_div = pd.DataFrame({
        "n_trials_1L": piv_t.get("1L", 0), "n_success_1L": piv_s.get("1L", 0), "n_failure_1L": piv_f.get("1L", 0),
        "n_trials_2L": piv_t.get("2L", 0), "n_success_2L": piv_s.get("2L", 0), "n_failure_2L": piv_f.get("2L", 0),
    }).fillna(0)
    lot_div = lot_div[(lot_div["n_trials_1L"] >= 1) & (lot_div["n_trials_2L"] >= 1)]
    lot_div = lot_div.sort_values(["n_trials_1L", "n_trials_2L"], ascending=False)
    display(lot_div, name="entry20_line_of_therapy_divergence")
    print(f"{len(lot_div)} targets have annotated activity in both 1L and 2L+ settings "
          f"(m_line_of_therapy is sparsely populated: {df['m_line_of_therapy'].notna().sum():,}/{len(df):,} rows).")
    """,
)

# ===========================================================================
# ENTRY 21 -- Sponsor concentration & competitive intensity per target
# ===========================================================================
entry(
    "Entry 21: Sponsor Concentration & Competitive Intensity per Target",
    r"""
    q = con.sql('''
        SELECT target_h AS target_harmonized, lead_sponsor_golden, COUNT(*) AS n
        FROM trials WHERE target_h IS NOT NULL AND lead_sponsor_golden IS NOT NULL
        GROUP BY 1,2
    ''').df()
    rows = []
    for target, g in q.groupby("target_harmonized"):
        shares = g["n"] / g["n"].sum()
        rows.append({
            "target_harmonized": target,
            "n_trials": int(g["n"].sum()),
            "n_sponsors": int(g["lead_sponsor_golden"].nunique()),
            "top_sponsor": g.loc[g["n"].idxmax(), "lead_sponsor_golden"],
            "top_sponsor_share_%": round(shares.max() * 100, 1),
            "hhi": round((shares ** 2).sum() * 10000, 0),
        })
    conc = pd.DataFrame(rows)
    conc = conc[conc["n_trials"] >= 5].sort_values("n_trials", ascending=False)
    SPONSOR_CONCENTRATION = conc
    display(conc.head(40), name="entry21_sponsor_concentration")
    print("HHI > 2500 = highly concentrated (single-sponsor-dominated); HHI < 1500 = fragmented/competitive field.")
    """,
)

# ===========================================================================
# ENTRY 22 -- Clinical whitespace scoring model, overall
# ===========================================================================
entry(
    "Entry 22: Clinical Whitespace Scoring Model -- Overall (Phase 1/2, No Failures, Rising Activity)",
    r"""
    def zscore(s):
        sd = s.std(ddof=0)
        return (s - s.mean()) / sd if sd > 0 else s * 0

    target_stats = con.sql('''
        SELECT target_h AS target_harmonized,
               COUNT(*) AS n_trials,
               COUNT(DISTINCT lead_sponsor_golden) AS n_sponsors,
               COUNT(DISTINCT modality_code) AS n_modalities,
               MAX(start_year) AS latest_start_year,
               MIN(start_year) AS earliest_start_year,
               SUM(CASE WHEN outcome_bucket='Success' THEN 1 ELSE 0 END) AS n_success,
               SUM(CASE WHEN outcome_bucket='Failure' THEN 1 ELSE 0 END) AS n_failure,
               SUM(CASE WHEN phase_canon IN ('Phase 3','Phase 2/3','Phase 4') THEN 1 ELSE 0 END) AS n_late_stage
        FROM trials WHERE target_h IS NOT NULL GROUP BY 1
    ''').df()

    whitespace = target_stats[
        (target_stats["n_late_stage"] == 0) &
        (target_stats["n_failure"] == 0) &
        (target_stats["latest_start_year"] >= 2019)
    ].copy()

    whitespace["score_recency"] = zscore(whitespace["latest_start_year"])
    whitespace["score_activity"] = zscore(np.log1p(whitespace["n_trials"]))
    whitespace["score_low_crowding"] = -zscore(whitespace["n_sponsors"])
    whitespace["score_validation"] = zscore(whitespace["n_success"])
    whitespace["whitespace_score"] = (
        whitespace["score_recency"] + whitespace["score_activity"] +
        whitespace["score_low_crowding"] + whitespace["score_validation"]
    ) / 4
    WHITESPACE_OVERALL = whitespace.sort_values("whitespace_score", ascending=False)

    cols = ["target_harmonized", "n_trials", "n_sponsors", "n_modalities",
            "earliest_start_year", "latest_start_year", "n_success", "whitespace_score"]
    display(WHITESPACE_OVERALL[cols].head(30), name="entry22_whitespace_overall")
    print(f"{len(WHITESPACE_OVERALL)} candidate whitespace targets overall "
          f"(Phase 1/2 only, zero recorded failures, active since 2019).")
    """,
)

# ===========================================================================
# ENTRY 23 -- Whitespace by modality
# ===========================================================================
entry(
    "Entry 23: Whitespace Targets by Modality",
    r"""
    top_ws = WHITESPACE_OVERALL.head(40)
    mod_map = df[df["target_h"].isin(top_ws["target_harmonized"])].groupby(["target_h", "modality_code"]).size().reset_index(name="n_trials")
    merged = mod_map.merge(top_ws[["target_harmonized", "whitespace_score"]], left_on="target_h", right_on="target_harmonized")
    by_modality = (merged.sort_values(["modality_code", "whitespace_score"], ascending=[True, False])
                          .groupby("modality_code").head(5)[["modality_code", "target_h", "n_trials", "whitespace_score"]]
                          .rename(columns={"target_h": "target_harmonized"}))
    display(by_modality, name="entry23_whitespace_by_modality")

    fig, ax = plt.subplots(figsize=(9, 6))
    piv = by_modality.pivot_table(index="target_harmonized", columns="modality_code", values="whitespace_score", fill_value=0)
    piv = piv.loc[by_modality.sort_values("whitespace_score", ascending=False)["target_harmonized"].unique()]
    piv.plot(kind="barh", ax=ax, color=plt.cm.tab10.colors[:piv.shape[1]])
    ax.set_xlabel("Whitespace score"); ax.set_title("Top whitespace targets, grouped by modality of the underlying trials")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 24 -- Whitespace by solid tumor type
# ===========================================================================
entry(
    "Entry 24: Whitespace Targets by Solid Tumor Type",
    r"""
    ws_targets = WHITESPACE_OVERALL["target_harmonized"].tolist()
    ws_junction = tumor_junction[
        tumor_junction["target_h"].isin(ws_targets) &
        ~tumor_junction["tumor_type"].str.startswith("Hematologic") &
        (tumor_junction["tumor_type"] != "Solid_Tumor_Basket")
    ]
    q = ws_junction.groupby(["tumor_type", "target_h"]).size().reset_index(name="n_trials")
    q = q.merge(WHITESPACE_OVERALL[["target_harmonized", "whitespace_score"]], left_on="target_h", right_on="target_harmonized")
    q = q.sort_values(["tumor_type", "whitespace_score"], ascending=[True, False]).groupby("tumor_type").head(5)
    q = q[["tumor_type", "target_h", "n_trials", "whitespace_score"]].rename(columns={"target_h": "target_harmonized"})
    display(q, name="entry24_whitespace_by_tumor_type")

    top_tumors = (q.groupby("tumor_type")["whitespace_score"].max().sort_values(ascending=False).head(8).index.tolist())
    fig, axes = plt.subplots(2, 4, figsize=(16, 7))
    for ax, tt in zip(axes.flat, top_tumors):
        sub = q[q["tumor_type"] == tt].sort_values("whitespace_score")
        ax.barh(sub["target_harmonized"], sub["whitespace_score"], color="#059669")
        ax.set_title(tt, fontsize=10)
        ax.tick_params(labelsize=8)
    plt.suptitle("Highest-scoring whitespace targets, by solid tumor type")
    plt.tight_layout()
    """,
)

# ===========================================================================
# ENTRY 25 -- Portfolio recommendation shortlist (synthesis)
# ===========================================================================
entry(
    "Entry 25: Portfolio Recommendation Shortlist -- Candidate Target/Modality/Tumor-Type Bets",
    r"""
    ws_junction = tumor_junction[
        tumor_junction["target_h"].isin(WHITESPACE_OVERALL["target_harmonized"]) &
        ~tumor_junction["tumor_type"].str.startswith("Hematologic") &
        (tumor_junction["tumor_type"] != "Solid_Tumor_Basket")
    ]
    tt_counts = ws_junction.groupby(["target_h", "tumor_type"]).size().reset_index(name="n_trials_in_tumor")
    best_tumor = tt_counts.sort_values("n_trials_in_tumor", ascending=False).drop_duplicates("target_h")

    mod_counts = df[df["target_h"].isin(WHITESPACE_OVERALL["target_harmonized"])].groupby(["target_h", "modality_code"]).size().reset_index(name="n_trials_in_modality")
    best_modality = mod_counts.sort_values("n_trials_in_modality", ascending=False).drop_duplicates("target_h")

    shortlist = WHITESPACE_OVERALL.head(15).copy()
    shortlist = shortlist.merge(best_tumor.rename(columns={"target_h": "target_harmonized"}), on="target_harmonized", how="left")
    shortlist = shortlist.merge(best_modality.rename(columns={"target_h": "target_harmonized"}), on="target_harmonized", how="left")

    def rationale(row):
        bits = [f"{row['n_trials']} trials since {int(row['earliest_start_year'])}", f"{row['n_sponsors']} sponsor(s) (low crowding)"]
        if row["n_success"] > 0:
            bits.append(f"{int(row['n_success'])} early positive readout(s)")
        else:
            bits.append("no failures recorded yet")
        if pd.notna(row.get("tumor_type")):
            bits.append(f"concentrated in {row['tumor_type']}")
        if pd.notna(row.get("modality_code")):
            bits.append(f"lead modality tested: {row['modality_code']}")
        return "; ".join(bits)

    shortlist["rationale"] = shortlist.apply(rationale, axis=1)
    cols = ["target_harmonized", "n_trials", "n_sponsors", "tumor_type", "modality_code", "whitespace_score", "rationale"]
    display(shortlist[cols], name="entry25_portfolio_shortlist")
    """,
)

# ===========================================================================
# ENTRY 26 -- Executive summary (computed dynamically from real variables)
# ===========================================================================
entry(
    "Entry 26: Executive Summary & Key Findings",
    r"""
    top_modality = con.sql("SELECT modality_code, COUNT(*) n FROM trials GROUP BY 1 ORDER BY 2 DESC LIMIT 1").df().iloc[0]
    top_target_row = TARGET_SCORECARD.sort_values("n_trials", ascending=False).iloc[0]
    n_validated = (TARGET_SCORECARD["target_status"] == "Validated (success recorded)").sum()
    n_mixed = (TARGET_SCORECARD["target_status"] == "Mixed (succeeded & failed)").sum()
    n_failed_only = (TARGET_SCORECARD["target_status"] == "Failed only (no success yet)").sum()
    n_still_trial = (TARGET_SCORECARD["target_status"] == "Still-in-trial (no readout yet)").sum()
    n_regimen_flagged = (REGIMEN_DIVERGENCE["divergence_flag"] != "No clear divergence").sum()
    n_modality_flagged = (MODALITY_DIVERGENCE["divergence_flag"] != "No clear divergence").sum()
    n_whitespace = len(WHITESPACE_OVERALL)
    top_ws_target = WHITESPACE_OVERALL.iloc[0]["target_harmonized"]

    print("EXECUTIVE SUMMARY -- Antibody Oncology Clinical Landscape")
    print("=" * 70)
    print(f"Universe analyzed: {len(df):,} in-scope antibody-modality oncology trial records "
          f"({df['nct'].nunique():,} unique NCT numbers).")
    print(f"Dominant modality: {top_modality['modality_code']} ({int(top_modality['n']):,} trials).")
    print(f"Most clinically active target: {top_target_row['target_harmonized']} "
          f"({int(top_target_row['n_trials'])} trials, {top_target_row['success_rate_%']:.1f}% success rate).")
    print()
    print(f"Among {len(TARGET_SCORECARD)} targets with >=5 trials:")
    print(f"  - {n_validated} are VALIDATED (>=1 recorded clinical success/approval)")
    print(f"  - {n_mixed} are MIXED (succeeded in some settings, failed in others)")
    print(f"  - {n_failed_only} have FAILED ONLY so far (no success recorded)")
    print(f"  - {n_still_trial} are STILL IN TRIAL with no readout yet")
    print()
    print(f"{n_regimen_flagged} targets show a clear IO-combo vs monotherapy outcome divergence.")
    print(f"{n_modality_flagged} targets show a clear naked-mAb vs ADC outcome divergence.")
    print()
    print(f"{n_whitespace} targets qualify as clinical WHITESPACE (Phase 1/2 only, zero failures, active since 2019).")
    print(f"Top-ranked whitespace target: {top_ws_target}.")
    """,
)

# ===========================================================================
# DRIVER: execute every entry, assemble notebook JSON, write outputs
# ===========================================================================
def main():
    cells = []
    intro = f"""# Clinical Trial Landscape -- Antibody Therapeutics in Oncology
**Prepared for:** Executive / Portfolio Strategy Review
**Analyst view:** Head of Data Analytics (clinical-stage competitive intelligence)
**Source data:** `input/clinical_final_version1.csv` ({INPUT_CSV})
**Scope:** Antibody-modality oncology clinical trials (mAb, ADC, bispecific, BiTE, CAR-T, radioligand, PROTAC),
filtered to `in_scope == True`.
**Target taxonomy:** the curated **`target_harmonized`** column only (no raw `targets_hgnc`/`target_name`).

This notebook builds the full analytic base end-to-end (DuckDB SQL + pandas), then walks through 26
entries: landscape overview -> phase funnels -> target validation scorecards -> tumor-type cuts ->
regimen/modality "subtle difference" divergence analysis -> clinical whitespace scoring -> a portfolio
shortlist -> executive summary. Every table and chart below is generated live from the CSV; nothing is
hand-typed.
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

    # ---- Output folder: data/*.csv, figures/*.png, notebook copy ----------
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FIG_DIR, exist_ok=True)
    n_exported_tables = 0
    for name, tdf in EXPORTED_TABLES:
        if len(tdf) > 10:
            safe = re.sub(r"[^A-Za-z0-9_]+", "_", name)
            tdf.to_csv(os.path.join(DATA_DIR, f"{safe}.csv"), index=True)
            n_exported_tables += 1
    for name, png_bytes in EXPORTED_FIGS:
        with open(os.path.join(FIG_DIR, name), "wb") as f:
            f.write(png_bytes)
    shutil.copyfile(NOTEBOOK_PATH, os.path.join(OUT_DIR, "clinical_landscape_final_version1.ipynb"))

    print(f"Exported {n_exported_tables} tables (>10 rows) to {DATA_DIR}")
    print(f"Exported {len(EXPORTED_FIGS)} figures to {FIG_DIR}")
    print(f"Notebook copy: {os.path.join(OUT_DIR, 'clinical_landscape_final_version1.ipynb')}")


if __name__ == "__main__":
    main()
