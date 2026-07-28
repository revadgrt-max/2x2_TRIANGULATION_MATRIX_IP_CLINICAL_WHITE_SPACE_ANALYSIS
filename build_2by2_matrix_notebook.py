"""
build_2by2_matrix_notebook.py
==============================================================================
Builds input/2by2matrix_final_version1.ipynb end-to-end. Every code cell is
actually executed in-process and its REAL computed output (tables as HTML,
printed text, PNG charts) is embedded into the notebook JSON exactly as
Jupyter would store it -- nothing is hand-typed.

Engine: reuses input/multitarget_locked_whitespace_workflow.py UNCHANGED
(imported as a module) for the deterministic 2x2 placement -- X = FTO/epitope
crowding (3-layer weighted, from IP claims), Y = clinical validation
(phase-weighted net outcome signal). Join key: target_harmonized (verbatim,
per explicit instruction -- NOT target x modality).

Sources: input/ip_final_version4.csv (patents) + input/clinical_final_version1.csv
(trials).

PHASING (see CHECKPOINT markdown cell at the end of this file's ENTRIES list):
  Phase 1 (this run)              -> Pancreatic tumor pilot only, then PAUSE.
  Phase 2 (after user's go-ahead) -> every solid tumor type (ranked by trial
                                      volume) +/- line-of-therapy cuts where
                                      volume supports it, then the whole
                                      dataset as one run.
Re-run this script any time to rebuild the notebook + re-sync output/.

Run:  source .venv-1/bin/activate && python3 build_2by2_matrix_notebook.py
"""
import ast
import base64
import contextlib
import io
import json
import os
import re
import shutil
import sys
import textwrap

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(ROOT, "input")
IP_CSV = os.path.join(INPUT_DIR, "ip_final_version4.csv")
CLIN_CSV = os.path.join(INPUT_DIR, "clinical_final_version1.csv")
NOTEBOOK_PATH = os.path.join(INPUT_DIR, "2by2matrix_final_version1.ipynb")
OUT_DIR = os.path.join(ROOT, "output", "2by2 matrix_ip_clinical")
DATA_DIR = os.path.join(OUT_DIR, "data")
FIG_DIR = os.path.join(OUT_DIR, "figures")
RUNS_DIR = os.path.join(OUT_DIR, "workflow_runs")

sys.path.insert(0, INPUT_DIR)
import multitarget_locked_whitespace_workflow as MTW  # noqa: E402  (reused verbatim)

pd.set_option("display.max_columns", 40)
pd.set_option("display.width", 160)

# ---------------------------------------------------------------------------
# Mini "notebook kernel" (same pattern as build_clinical_landscape_notebook.py
# / build_clinical_landscape_v2_notebook.py): executes a code string in a
# shared namespace and captures stdout, display(...) tables, embed_png(...)
# images, matplotlib figures, and the trailing bare expression -- into real
# nbformat-style cell outputs.
# ---------------------------------------------------------------------------
EXPORTED_TABLES = []   # (name, DataFrame) for every table shown via display()
EXPORTED_FIGS = []     # (name, png_bytes) for every chart / embedded image
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
    image_queue = []

    def display(obj, name=None):
        display_queue.append((name, obj))

    def embed_png(path, name=None):
        with open(path, "rb") as f:
            image_queue.append((name or os.path.basename(path), f.read()))

    ns["display"] = display
    ns["embed_png"] = embed_png

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

        for name, png_bytes in image_queue:
            _FIG_COUNTER += 1
            fname = f"{entry_slug}_fig{_FIG_COUNTER:02d}_{re.sub(r'[^A-Za-z0-9_]+', '_', name)}"
            if not fname.lower().endswith(".png"):
                fname += ".png"
            EXPORTED_FIGS.append((fname, png_bytes))
            outputs.append({"output_type": "display_data",
                             "data": {"image/png": base64.b64encode(png_bytes).decode("ascii")},
                             "metadata": {}})

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
        data = {"text/html": obj.to_html(max_rows=100, na_rep="-"), "text/plain": repr(obj)}
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


# ---------------------------------------------------------------------------
# Build the notebook: a list of (title, code, note) entries executed in one
# shared namespace `NS`, in order -- exactly like running cells top to bottom.
# ---------------------------------------------------------------------------
NS = {
    "pd": pd, "np": np, "duckdb": duckdb, "plt": plt, "re": re, "os": os,
    "IP_CSV": IP_CSV, "CLIN_CSV": CLIN_CSV, "OUT_DIR": OUT_DIR, "RUNS_DIR": RUNS_DIR,
    "MTW": MTW,
}

ENTRIES = []


def entry(title, code, note=""):
    ENTRIES.append({"title": title, "note": note, "code": code})


# ===========================================================================
# ENTRY 1 -- Setup & data load
# ===========================================================================
entry(
    "Entry 1: Setup, Scope & Data Load",
    r"""
    # Executive framing ------------------------------------------------------
    # Question: where should we place our antibody-therapeutics oncology bet?
    # X axis = freedom-to-operate / epitope-IP crowding (from patents).
    # Y axis = clinical validation signal (from trials).
    # Join key: target_harmonized (curated taxonomy), present in BOTH files.
    con = duckdb.connect()
    con.execute(f"CREATE OR REPLACE VIEW raw_ip AS SELECT * FROM read_csv_auto('{IP_CSV}', ALL_VARCHAR=FALSE, SAMPLE_SIZE=-1)")
    con.execute(f"CREATE OR REPLACE VIEW raw_clin AS SELECT * FROM read_csv_auto('{CLIN_CSV}', ALL_VARCHAR=FALSE, SAMPLE_SIZE=-1)")
    ip_raw = con.sql("SELECT * FROM raw_ip").df()
    clin_raw = con.sql("SELECT * FROM raw_clin").df()
    print(f"IP master   : {len(ip_raw):,} rows x {ip_raw.shape[1]} cols   ({ip_raw['target_harmonized'].nunique():,} distinct target_harmonized)")
    print(f"Clinical    : {len(clin_raw):,} rows x {clin_raw.shape[1]} cols   ({clin_raw['target_harmonized'].nunique():,} distinct target_harmonized)")
    print()
    print("IP  is_antibody distribution:", dict(ip_raw["is_antibody"].value_counts(dropna=False)))
    print("Clinical in_scope distribution:", dict(clin_raw["in_scope"].value_counts(dropna=False)))
    """,
)

# ===========================================================================
# ENTRY 2 -- Scoping filters
# ===========================================================================
entry(
    "Entry 2: Scoping Filters -- Antibody-Only IP & In-Scope Clinical Trials",
    r"""
    # Keep only antibody-format patents on the IP side, and only in_scope
    # (curated antibody-modality oncology) trials on the clinical side --
    # matches the scoping already applied throughout the other notebooks in
    # this project. Both `is_antibody` and `in_scope` are DuckDB-inferred
    # real nullable booleans, so compare with `== True`, not a string.
    ip_scoped = ip_raw[ip_raw["is_antibody"] == True].copy()  # noqa: E712
    clin_scoped = clin_raw[clin_raw["in_scope"] == True].copy()  # noqa: E712

    # Data-quality fix: `application_date` / `filing_year` in this source file are stored
    # as float-like text ("20100330.0", "2010.0") -- the workflow engine's `_ip_apd()`
    # requires pure digit strings (isdigit()) to parse them, so a trailing ".0" silently
    # makes EVERY patent's application date unparseable -> 0 grounded patents ever counted.
    # Strip the spurious ".0" suffix once here, upstream of the (unmodified) engine.
    for _col in ["application_date", "filing_year"]:
        ip_scoped[_col] = (ip_scoped[_col].astype(str)
                           .str.replace(r"\.0$", "", regex=True)
                           .replace({"nan": ""}))

    os.makedirs(os.path.join(RUNS_DIR, "_shared"), exist_ok=True)
    IP_SCOPED_CSV = os.path.join(RUNS_DIR, "_shared", "ip_final_version4_antibody_only.csv")
    ip_scoped.to_csv(IP_SCOPED_CSV, index=False)

    print(f"IP:       {len(ip_raw):,} -> {len(ip_scoped):,} rows kept (is_antibody == 'yes')")
    print(f"Clinical: {len(clin_raw):,} -> {len(clin_scoped):,} rows kept (in_scope == True)")
    print(f"Scoped IP file written: {IP_SCOPED_CSV}")
    """,
)

# ===========================================================================
# ENTRY 3 -- Tumor-type classifier (reused from build_clinical_landscape_notebook.py)
# ===========================================================================
entry(
    "Entry 3: Tumor-Type Classifier (reused from the clinical-landscape notebooks)",
    r"""
    # No curated tumor-type column exists in the clinical file, so we reuse
    # the same ordered keyword/regex classifier already validated in
    # build_clinical_landscape_notebook.py -- most-specific categories first
    # (e.g. NSCLC before generic "lung"). A trial can carry multiple tags
    # (basket trials); tumor_primary = first (most specific) match.
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

    clin_scoped["condition_text"] = clin_scoped["ctgov_conditions"].fillna(clin_scoped["m_conditions"])
    clin_scoped["tumor_tags"] = clin_scoped["condition_text"].apply(classify_tumor_tags)
    clin_scoped["tumor_primary"] = clin_scoped["tumor_tags"].apply(lambda t: t[0] if t else None)
    clin_scoped["tumor_primary"] = clin_scoped["tumor_primary"].where(
        clin_scoped["tumor_primary"].notna(),
        np.where(clin_scoped["condition_text"].notna(), "Other_Unclassified", "No_Condition_Data"),
    )

    tumor_volume = clin_scoped["tumor_primary"].value_counts().head(25).to_frame("n_trials")
    print("Top tumor types by in-scope trial volume (preview for Phase 2 sequencing):")
    display(tumor_volume, name="entry03_tumor_type_volume_preview")
    """,
)

# ===========================================================================
# ENTRY 4 -- Methodology
# ===========================================================================
entry(
    "Entry 4: Methodology -- Deterministic 2x2 Whitespace Engine",
    r"""
    print("Engine: input/multitarget_locked_whitespace_workflow.py (imported verbatim, un-modified).")
    print()
    print("X axis -- epitope_crowding_X in [0,1] (0 = open, 1 = crowded):")
    print("  3 weighted layers from patent claims -- L1 cross-modality epitope lock (hard floor),")
    print("  L2 modality-specific claim density, L3 indication-specific use/combo claim density.")
    print(f"  Layer weights: L1={MTW.W.FTO_W_L1}  L2={MTW.W.FTO_W_L2}  L3={MTW.W.FTO_W_L3}   (L1 hard floor: {MTW.W.FTO_L1_IS_HARD_FLOOR})")
    print()
    print("Y axis -- clinical_performance_Y in [0,1] (0 = failed, 1 = validated):")
    print("  Phase-weighted net outcome across decisive trials (approved/positive/negative/terminated),")
    print(f"  squashed with tanh; positive Phase 3+ readout floors Y at 0.75. Phase weights: {MTW.W.PHASE_WEIGHT}")
    print()
    print(f"Quadrant split point (both axes): {MTW.W.PERF_SPLIT}")
    print("  Y hi & X open    -> TRUE WHITE SPACE   (validated biology, room to build IP position)")
    print("  Y hi & X crowded -> BATTLEGROUND       (validated biology, contested IP)")
    print("  Y lo & X open    -> R&D TRAP           (unproven biology, IP looks open -- proceed with caution)")
    print("  Y lo & X crowded -> RED FLAGS          (unproven biology AND contested IP -- avoid)")
    print()
    print(f"Join key: '{MTW.DEF_KEY_COL}' (verbatim, case/space-normalized) -- per instruction, NOT split by modality.")
    print("Node granularity for this analysis: one node per target_harmonized (modality collapsed, by_modality=False).")
    """,
)

# ===========================================================================
# ENTRY 5 -- PILOT: Pancreatic tumor run
# ===========================================================================
entry(
    "Entry 5 [PILOT]: Pancreatic Tumor -- 2x2 Placement",
    r"""
    PILOT_DIR = os.path.join(RUNS_DIR, "pancreatic")
    os.makedirs(PILOT_DIR, exist_ok=True)

    clin_pancreatic = clin_scoped[clin_scoped["tumor_primary"] == "Pancreatic"].copy()
    CLIN_PANC_CSV = os.path.join(PILOT_DIR, "clinical_input_filtered_pancreatic.csv")
    clin_pancreatic.drop(columns=["tumor_tags"]).to_csv(CLIN_PANC_CSV, index=False)
    print(f"Pancreatic in-scope clinical rows: {len(clin_pancreatic):,} "
          f"({clin_pancreatic['target_harmonized'].nunique():,} distinct targets)")

    IP_SCOPED_CSV = os.path.join(RUNS_DIR, "_shared", "ip_final_version4_antibody_only.csv")
    pilot_rows = MTW.run(
        ip_csv=IP_SCOPED_CSV, clin_csv=CLIN_PANC_CSV, as_of=MTW.DEF_AS_OF,
        indication_terms=["pancreatic", "pancreas"], outdir=PILOT_DIR,
        key_col="target_harmonized", mod_col="modality_code",
        by_modality=False, sample_n=0, seed=7,
    )
    pancreatic_master = pd.DataFrame(pilot_rows)
    print()
    print(f"Total target_harmonized nodes considered: {len(pancreatic_master)}")
    print(f"Placed on both axes (real 2x2 members): {int((pancreatic_master['x_resolved'] & pancreatic_master['y_resolved']).sum())}")

    quadrant_counts = (pancreatic_master.groupby("quadrant").size()
                       .reindex(list(MTW.W.ALL_QUADRANTS) + ["UNRESOLVED"], fill_value=0)
                       .to_frame("n_targets"))
    display(quadrant_counts, name="pancreatic_quadrant_counts")
    """,
)

# ===========================================================================
# ENTRY 6 -- Pancreatic quadrant detail tables
# ===========================================================================
entry(
    "Entry 6 [PILOT]: Pancreatic 2x2 -- Quadrant Detail Tables",
    r"""
    placed = pancreatic_master[pancreatic_master["x_resolved"] & pancreatic_master["y_resolved"]].copy()
    cols = ["target_harmonized", "clinical_performance_Y", "clinical_label", "n_trials",
            "epitope_crowding_X", "patents_grounded", "blocking_assignee", "key_patent"]

    for q in MTW.W.ALL_QUADRANTS:
        sub = placed[placed["quadrant"] == q].sort_values("clinical_performance_Y", ascending=False)[cols]
        print(f"--- {q} ({len(sub)} targets) ---")
        display(sub, name=f"pancreatic_quadrant_{re.sub(r'[^A-Za-z0-9]+', '_', q).strip('_').lower()}")
    """,
)

# ===========================================================================
# ENTRY 7 -- Pancreatic watch-lists (partial-evidence targets)
# ===========================================================================
entry(
    "Entry 7 [PILOT]: Pancreatic Watch-Lists -- Partial-Evidence Targets",
    r"""
    # Targets resolved on only ONE axis are surfaced as watch-lists (per
    # discussion), not dropped -- these are often the earliest-mover signal.
    ip_only = pancreatic_master[pancreatic_master["x_resolved"] & ~pancreatic_master["y_resolved"]].copy()
    ip_only = ip_only.sort_values(["epitope_crowding_X", "patents_grounded"], ascending=[True, False])
    clin_only = pancreatic_master[pancreatic_master["y_resolved"] & ~pancreatic_master["x_resolved"]].copy()
    clin_only = clin_only.sort_values("clinical_performance_Y", ascending=False)
    neither = pancreatic_master[~pancreatic_master["x_resolved"] & ~pancreatic_master["y_resolved"]]

    print(f"Patent-only, clinically untested in pancreatic ({len(ip_only)} targets) -- top 25 by openness:")
    display(ip_only[["target_harmonized", "epitope_crowding_X", "patents_grounded", "blocking_assignee", "key_patent"]].head(25),
            name="pancreatic_watchlist_ip_only_patent_untested")

    print(f"\\nClinically signaled, IP picture unclear in pancreatic ({len(clin_only)} targets) -- top 25 by performance:")
    display(clin_only[["target_harmonized", "clinical_performance_Y", "clinical_label", "n_trials"]].head(25),
            name="pancreatic_watchlist_clinical_only_ip_unclear")

    print(f"\\nNeither axis resolved (no grounded pancreatic-relevant patents AND no decisive pancreatic trial readout): {len(neither)} targets")
    """,
)

# ===========================================================================
# ENTRY 8 -- Pancreatic 2x2 scatter plot
# ===========================================================================
entry(
    "Entry 8 [PILOT]: Pancreatic 2x2 Scatter Plot",
    r"""
    PILOT_DIR = os.path.join(RUNS_DIR, "pancreatic")
    embed_png(os.path.join(PILOT_DIR, "multitarget_2x2.png"), name="pancreatic_2x2_scatter")
    print(f"Chart generated by multitarget_locked_whitespace_workflow.py, source: {PILOT_DIR}/multitarget_2x2.png")
    """,
)

# ===========================================================================
# ENTRY 9 -- Executive synthesis + checkpoint
# ===========================================================================
entry(
    "Entry 9 [PILOT]: Executive Synthesis -- Pancreatic Pilot",
    r"""
    placed = pancreatic_master[pancreatic_master["x_resolved"] & pancreatic_master["y_resolved"]]
    tws = placed[placed["quadrant"] == "TRUE WHITE SPACE"].sort_values("clinical_performance_Y", ascending=False)
    battleground = placed[placed["quadrant"] == "BATTLEGROUND"].sort_values("clinical_performance_Y", ascending=False)
    redflags = placed[placed["quadrant"] == "RED FLAGS"]
    rdtrap = placed[placed["quadrant"] == "R&D TRAP"]
    ip_only_n = int((pancreatic_master["x_resolved"] & ~pancreatic_master["y_resolved"]).sum())
    clin_only_n = int((pancreatic_master["y_resolved"] & ~pancreatic_master["x_resolved"]).sum())

    print("PANCREATIC PILOT -- EXECUTIVE SUMMARY")
    print("=" * 60)
    print(f"{len(pancreatic_master)} target_harmonized nodes touch pancreatic cancer across the two datasets;")
    print(f"{len(placed)} have BOTH a grounded IP position and a decisive clinical readout and are placed on the 2x2.")
    print()
    print(f"TRUE WHITE SPACE ({len(tws)}): validated biology, room to build an IP position.")
    if len(tws):
        print("  Top candidates: " + ", ".join(tws["target_harmonized"].head(5).tolist()))
    print(f"BATTLEGROUND ({len(battleground)}): validated biology, but IP already contested.")
    if len(battleground):
        print("  Top candidates: " + ", ".join(battleground["target_harmonized"].head(5).tolist()))
    print(f"R&D TRAP ({len(rdtrap)}): open IP but biology unproven in pancreatic -- proceed with caution.")
    print(f"RED FLAGS ({len(redflags)}): unproven biology AND contested IP -- avoid.")
    print()
    print(f"Watch-lists: {ip_only_n} patent-only (no pancreatic clinical readout yet), "
          f"{clin_only_n} clinically-signaled with an unclear pancreatic IP position.")
    """,
    note=(
        "This pilot validated the pipeline end-to-end on one tumor type (join key, scoping filters, "
        "quadrant definitions, watch-lists all confirmed). Per go-ahead, Entry 10 onward now "
        "generalizes this into a reusable runner, demonstrates it on **Prostate**, then sweeps every "
        "remaining named solid tumor type (ranked by trial volume, with 1L/2L cuts where volume "
        "supports it) and finally the whole dataset as one run."
    ),
)

# ===========================================================================
# ENTRY 10 -- Reusable per-tumor-type / per-line runner (generalizes the pilot)
# ===========================================================================
entry(
    "Entry 10: Reusable Per-Tumor-Type 2x2 Runner",
    r"""
    # Generalizes Entry 5-9 into functions so every subsequent tumor-type (and
    # line-of-therapy) cut runs through the IDENTICAL logic as the approved
    # pancreatic pilot -- same scoping, same engine call, same watch-list
    # treatment of partial-evidence targets.

    # Approximate free-text indication terms per named solid tumor tag, used
    # for the IP engine's L3 "use in THIS indication" layer (substring match
    # against the IP file's `indications` free-text column).
    TUMOR_INDICATION_TERMS = {
        "NSCLC": ["non-small", "non small", "nsclc", "lung"],
        "SCLC": ["small cell lung", "sclc", "lung"],
        "Lung_Other": ["lung", "pulmonary"],
        "Breast": ["breast"],
        "Ovarian": ["ovarian", "ovary"],
        "Endometrial_Uterine": ["endometrial", "uterine"],
        "Cervical": ["cervical", "cervix"],
        "Colorectal": ["colorectal", "colon", "rectal", "rectum"],
        "Gastric_Esophageal": ["gastric", "stomach", "esophageal", "gastroesophageal"],
        "Hepatocellular": ["hepatocellular", "hcc"],
        "Biliary_Cholangio": ["cholangiocarcinoma", "biliary"],
        "Pancreatic": ["pancreatic", "pancreas"],
        "RCC": ["renal cell", "rcc", "kidney"],
        "Bladder_Urothelial": ["bladder", "urothelial"],
        "Prostate": ["prostate", "prostatic"],
        "Melanoma": ["melanoma"],
        "HNSCC": ["head and neck", "nasopharyngeal", "oropharyngeal", "laryngeal", "oral cavity"],
        "Glioma_CNS": ["glioma", "glioblastoma", "astrocytoma", "brain", "cns"],
        "Sarcoma": ["sarcoma"],
    }
    # Excluded per the same convention used in the prior clinical-landscape
    # notebooks: Hematologic_* (different biology/program), Solid_Tumor_Basket
    # (not a single tumor type), Other_Unclassified / No_Condition_Data (no
    # usable tumor signal).
    NAMED_SOLID_TUMORS = [t for t in TUMOR_INDICATION_TERMS]
    MIN_LINE_ROWS = 30  # only attempt a 1L/2L cut if that line has >= this many rows

    IP_SCOPED_CSV = os.path.join(RUNS_DIR, "_shared", "ip_final_version4_antibody_only.csv")

    def analyze_tumor_cut(run_name, tags, indication_terms, clin_source, line_filter=None):
        # Filter clin_source to tumor tag(s) [+ optional line_of_therapy], run the
        # MTW engine, return (master_df, n_input_rows). tags=None -> no tumor
        # filter at all (whole-dataset run).
        if tags is None:
            sub = clin_source.copy()
        else:
            tags = tags if isinstance(tags, (list, tuple)) else [tags]
            sub = clin_source[clin_source["tumor_primary"].isin(tags)].copy()
        if line_filter:
            sub = sub[sub["m_line_of_therapy"] == line_filter]
        run_dir = os.path.join(RUNS_DIR, run_name)
        os.makedirs(run_dir, exist_ok=True)
        clin_csv_path = os.path.join(run_dir, f"clinical_input_filtered_{run_name}.csv")
        sub.drop(columns=["tumor_tags"], errors="ignore").to_csv(clin_csv_path, index=False)
        rows = MTW.run(
            ip_csv=IP_SCOPED_CSV, clin_csv=clin_csv_path, as_of=MTW.DEF_AS_OF,
            indication_terms=indication_terms, outdir=run_dir,
            key_col="target_harmonized", mod_col="modality_code",
            by_modality=False, sample_n=0, seed=7,
        )
        return pd.DataFrame(rows), len(sub)

    def summarize_cut(master_df, prefix, label, run_dir, show_tables=True, show_chart=True):
        # Quadrant counts + detail tables + watch-lists + chart + one-line
        # synthesis, all under a `prefix`-namespaced set of exported table/figure names.
        placed = master_df[master_df["x_resolved"] & master_df["y_resolved"]]
        quadrant_counts = (master_df.groupby("quadrant").size()
                           .reindex(list(MTW.W.ALL_QUADRANTS) + ["UNRESOLVED"], fill_value=0)
                           .to_frame("n_targets"))
        display(quadrant_counts, name=f"{prefix}_quadrant_counts")

        cols = ["target_harmonized", "clinical_performance_Y", "clinical_label", "n_trials",
                "epitope_crowding_X", "patents_grounded", "blocking_assignee", "key_patent"]
        if show_tables:
            for q in MTW.W.ALL_QUADRANTS:
                sub = placed[placed["quadrant"] == q].sort_values("clinical_performance_Y", ascending=False)[cols]
                if len(sub):
                    display(sub, name=f"{prefix}_quadrant_{re.sub(r'[^A-Za-z0-9]+', '_', q).strip('_').lower()}")

            ip_only = master_df[master_df["x_resolved"] & ~master_df["y_resolved"]].copy()
            ip_only = ip_only.sort_values(["epitope_crowding_X", "patents_grounded"], ascending=[True, False])
            clin_only = master_df[master_df["y_resolved"] & ~master_df["x_resolved"]].copy()
            clin_only = clin_only.sort_values("clinical_performance_Y", ascending=False)
            if len(ip_only):
                display(ip_only[["target_harmonized", "epitope_crowding_X", "patents_grounded", "blocking_assignee", "key_patent"]].head(25),
                        name=f"{prefix}_watchlist_ip_only")
            if len(clin_only):
                display(clin_only[["target_harmonized", "clinical_performance_Y", "clinical_label", "n_trials"]].head(25),
                        name=f"{prefix}_watchlist_clinical_only")

        if show_chart:
            png_path = os.path.join(run_dir, "multitarget_2x2.png")
            if os.path.exists(png_path):
                embed_png(png_path, name=f"{prefix}_2x2_scatter")

        tws = placed[placed["quadrant"] == "TRUE WHITE SPACE"].sort_values("clinical_performance_Y", ascending=False)
        battleground = placed[placed["quadrant"] == "BATTLEGROUND"]
        rdtrap = placed[placed["quadrant"] == "R&D TRAP"]
        redflags = placed[placed["quadrant"] == "RED FLAGS"]
        ip_only_n = int((master_df["x_resolved"] & ~master_df["y_resolved"]).sum())
        clin_only_n = int((master_df["y_resolved"] & ~master_df["x_resolved"]).sum())
        print(f"[{label}] {len(master_df)} nodes, {len(placed)} placed on 2x2 -- "
              f"TRUE WHITE SPACE={len(tws)} BATTLEGROUND={len(battleground)} "
              f"R&D TRAP={len(rdtrap)} RED FLAGS={len(redflags)} | "
              f"watch-lists: IP-only={ip_only_n} clinical-only={clin_only_n}")
        if len(tws):
            print(f"  Top TRUE WHITE SPACE: " + ", ".join(tws["target_harmonized"].head(5).tolist()))
        return {"label": label, "n_nodes": len(master_df), "n_placed": len(placed),
                "n_tws": len(tws), "n_battleground": len(battleground), "n_rdtrap": len(rdtrap),
                "n_redflags": len(redflags), "ip_only": ip_only_n, "clin_only": clin_only_n,
                "top_tws": tws["target_harmonized"].head(5).tolist(),
                "tws_targets": tws["target_harmonized"].tolist(),
                "redflag_targets": redflags["target_harmonized"].tolist(),
                "rdtrap_targets": rdtrap["target_harmonized"].tolist()}

    print(f"Runner ready. {len(NAMED_SOLID_TUMORS)} named solid tumor types queued "
          f"(excludes Hematologic_*, Solid_Tumor_Basket, Other_Unclassified, No_Condition_Data).")
    print(f"Line-of-therapy cuts only attempted where a line has >= {MIN_LINE_ROWS} rows.")
    """,
)

# ===========================================================================
# ENTRY 11 -- Prostate tumor demonstration run
# ===========================================================================
entry(
    "Entry 11 [DEMO]: Prostate Tumor -- 2x2 Placement",
    r"""
    prostate_master, n_prostate_rows = analyze_tumor_cut(
        "prostate", "Prostate", TUMOR_INDICATION_TERMS["Prostate"], clin_scoped)
    print(f"Prostate in-scope clinical rows: {n_prostate_rows:,} "
          f"({clin_scoped[clin_scoped['tumor_primary'] == 'Prostate']['target_harmonized'].nunique():,} distinct targets)")
    line_counts = clin_scoped[clin_scoped["tumor_primary"] == "Prostate"]["m_line_of_therapy"].value_counts()
    print(f"Line-of-therapy volume in Prostate: {dict(line_counts)}")

    prostate_summary = summarize_cut(prostate_master, "prostate", "Prostate",
                                      os.path.join(RUNS_DIR, "prostate"))

    for line in ("1L", "2L"):
        if line_counts.get(line, 0) >= MIN_LINE_ROWS:
            sub_master, n_sub = analyze_tumor_cut(
                f"prostate_{line.lower()}", "Prostate", TUMOR_INDICATION_TERMS["Prostate"],
                clin_scoped, line_filter=line)
            print(f"\\n-- Prostate {line} sub-cut ({n_sub} rows) --")
            summarize_cut(sub_master, f"prostate_{line.lower()}", f"Prostate {line}",
                          os.path.join(RUNS_DIR, f"prostate_{line.lower()}"))
        else:
            print(f"\\nProstate {line}: only {line_counts.get(line, 0)} rows (< {MIN_LINE_ROWS}) -- skipping sub-cut.")
    """,
)

# ===========================================================================
# ENTRY 12 -- Systematic sweep: every remaining named solid tumor type
# ===========================================================================
entry(
    "Entry 12: Systematic Sweep -- All Remaining Named Solid Tumor Types",
    r"""
    already_run = {"Pancreatic", "Prostate"}
    tumor_order = (clin_scoped[clin_scoped["tumor_primary"].isin(NAMED_SOLID_TUMORS)]
                   ["tumor_primary"].value_counts().index.tolist())
    remaining_tumors = [t for t in tumor_order if t not in already_run]
    print(f"Running {len(remaining_tumors)} remaining named solid tumor types, ranked by trial volume:")
    print(remaining_tumors)

    sweep_summaries = []
    for t in remaining_tumors:
        run_name = re.sub(r"[^A-Za-z0-9]+", "_", t).lower()
        master_df, n_rows = analyze_tumor_cut(run_name, t, TUMOR_INDICATION_TERMS[t], clin_scoped)
        summ = summarize_cut(master_df, run_name, t, os.path.join(RUNS_DIR, run_name))
        summ["tumor_type"] = t
        summ["n_input_rows"] = n_rows
        sweep_summaries.append(summ)

        line_counts = clin_scoped[clin_scoped["tumor_primary"] == t]["m_line_of_therapy"].value_counts()
        for line in ("1L", "2L"):
            if line_counts.get(line, 0) >= MIN_LINE_ROWS:
                sub_name = f"{run_name}_{line.lower()}"
                sub_master, n_sub = analyze_tumor_cut(sub_name, t, TUMOR_INDICATION_TERMS[t], clin_scoped, line_filter=line)
                sub_summ = summarize_cut(sub_master, sub_name, f"{t} {line}", os.path.join(RUNS_DIR, sub_name),
                                          show_tables=False, show_chart=False)
                sub_summ["tumor_type"] = f"{t} {line}"
                sub_summ["n_input_rows"] = n_sub
                sweep_summaries.append(sub_summ)

    print(f"\\nSweep complete: {len(sweep_summaries)} cuts run (tumor types + qualifying 1L/2L sub-cuts).")
    """,
    note=(
        "1L/2L sub-cut tables/charts are kept out of the notebook body to control length (counts are "
        "still printed and exported to CSV) -- the main per-tumor cuts show full quadrant + watch-list "
        "detail and chart, exactly like the Pancreatic/Prostate runs above."
    ),
)

# ===========================================================================
# ENTRY 13 -- Cross-tumor consolidated summary
# ===========================================================================
entry(
    "Entry 13: Cross-Tumor Consolidated Summary",
    r"""
    from collections import Counter

    pancreatic_tws = pancreatic_master[(pancreatic_master["x_resolved"] & pancreatic_master["y_resolved"]) &
                                        (pancreatic_master["quadrant"] == "TRUE WHITE SPACE")]["target_harmonized"].tolist()

    all_cuts = {"Pancreatic": pancreatic_tws, "Prostate": prostate_summary["tws_targets"]}
    for s in sweep_summaries:
        all_cuts[s["tumor_type"]] = s["tws_targets"]

    sweep_rows = [{"tumor_type": "Pancreatic",
                   "n_input_rows": len(clin_scoped[clin_scoped["tumor_primary"] == "Pancreatic"]),
                   "n_nodes": len(pancreatic_master),
                   "n_placed": int((pancreatic_master["x_resolved"] & pancreatic_master["y_resolved"]).sum()),
                   "n_tws": len(pancreatic_tws), "top_tws": ", ".join(pancreatic_tws[:5])}]
    sweep_rows.append({"tumor_type": "Prostate", "n_input_rows": n_prostate_rows,
                        "n_nodes": prostate_summary["n_nodes"], "n_placed": prostate_summary["n_placed"],
                        "n_tws": prostate_summary["n_tws"], "top_tws": ", ".join(prostate_summary["top_tws"])})
    for s in sweep_summaries:
        sweep_rows.append({"tumor_type": s["tumor_type"], "n_input_rows": s["n_input_rows"],
                           "n_nodes": s["n_nodes"], "n_placed": s["n_placed"], "n_tws": s["n_tws"],
                           "top_tws": ", ".join(s["top_tws"])})

    cross_tumor_summary = pd.DataFrame(sweep_rows).sort_values("n_input_rows", ascending=False)
    display(cross_tumor_summary, name="cross_tumor_sweep_summary")

    tws_counter = Counter(tgt for lst in all_cuts.values() for tgt in lst)
    recurring_tws = pd.DataFrame(
        [{"target_harmonized": t, "n_tumor_cuts_as_TWS": n,
          "tumor_types": ", ".join(k for k, v in all_cuts.items() if t in v)}
         for t, n in tws_counter.items() if n >= 2]
    ).sort_values("n_tumor_cuts_as_TWS", ascending=False)
    print(f"Targets flagged TRUE WHITE SPACE in >=2 tumor-type cuts (strongest cross-indication bets): {len(recurring_tws)}")
    if len(recurring_tws):
        display(recurring_tws, name="recurring_true_whitespace_targets")
    """,
)

# ===========================================================================
# ENTRY 14 -- Whole dataset run
# ===========================================================================
entry(
    "Entry 14: Whole-Dataset Run -- All Tumor Types Combined",
    r"""
    # No tumor filter (tags=None -> every in-scope antibody clinical row, every
    # antibody patent). indication_terms=[] deliberately: the IP engine's L3
    # layer ("use in THIS indication") only makes sense for a single named
    # indication -- at whole-dataset granularity there is no single indication,
    # so L3 is intentionally left at 0 for every target and X reduces to
    # L1 (cross-modality epitope lock) + L2 (modality claim density) only.
    whole_master, n_whole_rows = analyze_tumor_cut("whole_dataset", None, [], clin_scoped)
    print(f"Whole-dataset in-scope clinical rows: {n_whole_rows:,} "
          f"({clin_scoped['target_harmonized'].nunique():,} distinct targets)")
    whole_summary = summarize_cut(whole_master, "whole_dataset", "Whole Dataset (All Tumor Types)",
                                   os.path.join(RUNS_DIR, "whole_dataset"))
    """,
)

# ===========================================================================
# ENTRY 15 -- Grand executive synthesis / portfolio shortlist
# ===========================================================================
entry(
    "Entry 15: Grand Executive Synthesis -- Portfolio Shortlist",
    r"""
    print("PORTFOLIO SHORTLIST -- ACROSS ALL CUTS (Pancreatic, Prostate, sweep, whole dataset)")
    print("=" * 78)
    print(f"Whole-dataset view: {whole_summary['n_nodes']} nodes, {whole_summary['n_placed']} placed -- "
          f"TRUE WHITE SPACE={whole_summary['n_tws']} BATTLEGROUND={whole_summary['n_battleground']} "
          f"R&D TRAP={whole_summary['n_rdtrap']} RED FLAGS={whole_summary['n_redflags']}")
    if whole_summary["top_tws"]:
        print("  Whole-dataset top TRUE WHITE SPACE: " + ", ".join(whole_summary["top_tws"]))
    print()
    if len(recurring_tws):
        print("Recommend prioritizing targets validated as TRUE WHITE SPACE across MULTIPLE tumor types")
        print("(broader addressable population, biology de-risked more than once, IP still open):")
        for _, r in recurring_tws.head(10).iterrows():
            print(f"  - {r['target_harmonized']}: {r['n_tumor_cuts_as_TWS']} cuts ({r['tumor_types']})")
    else:
        print("No target recurred as TRUE WHITE SPACE across multiple tumor-type cuts in this run --")
        print("cross-indication whitespace bets are indication-specific here, not target-level.")
    print()
    print(f"Total cuts run this session: Pancreatic, Prostate, {len(sweep_summaries)} sweep cuts, "
          f"Whole Dataset = {2 + len(sweep_summaries) + 1} cuts.")
    print("All quadrant tables, watch-lists and charts for every cut are exported under")
    print(f"  {OUT_DIR}")
    """,
    note=(
        "This closes the analysis workflow requested: Pancreatic pilot -> Prostate demo -> full "
        "tumor-type sweep (+ line-of-therapy where supported) -> whole dataset -> cross-cut synthesis. "
        "Next: PDF / DOCX / PPTX deliverables + notebook DOCX mirror, built by a follow-on report script "
        "against this executed notebook."
    ),
)

# ===========================================================================
# ENTRY 16 -- Consolidated per-tumor-type 2x2 classification ledger
# ===========================================================================
entry(
    "Entry 16: Per-Tumor-Type 2x2 Classification -- Consolidated Ledger",
    r"""
    # One single, easy-to-read reference: for every named solid tumor type run in
    # this notebook (Entries 5-9 Pancreatic, 11 Prostate, 12 the sweep), list every
    # target that landed on the 2x2 (both axes resolved) together with its quadrant
    # classification, grouped under the tumor-type name. Reads each cut's own
    # master_multitarget_whitespace.csv straight off disk (already written by
    # MTW.run() earlier in this notebook) -- no recomputation, exact same numbers.
    ledger_rows = []
    for t in NAMED_SOLID_TUMORS:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", t).lower()
        master_csv = os.path.join(RUNS_DIR, slug, "master_multitarget_whitespace.csv")
        if not os.path.exists(master_csv):
            continue
        m = pd.read_csv(master_csv)
        placed = m[(m["x_resolved"] == True) & (m["y_resolved"] == True)].copy()  # noqa: E712
        placed.insert(0, "tumor_type", t)
        ledger_rows.append(placed[["tumor_type", "target_harmonized", "quadrant",
                                    "clinical_performance_Y", "clinical_label", "n_trials",
                                    "epitope_crowding_X", "patents_grounded", "blocking_assignee"]])

    tumor_2x2_ledger = pd.concat(ledger_rows, ignore_index=True)
    tumor_2x2_ledger = tumor_2x2_ledger.sort_values(
        ["tumor_type", "quadrant", "clinical_performance_Y"], ascending=[True, True, False])
    display(tumor_2x2_ledger, name="tumor_2x2_classification_ledger")

    print(f"Consolidated ledger: {len(tumor_2x2_ledger)} placed target-x-tumor rows across "
          f"{tumor_2x2_ledger['tumor_type'].nunique()} named solid tumor types.")
    print("Full detail (all columns, all rows): data/tumor_2x2_classification_ledger.csv")
    print()

    for t in NAMED_SOLID_TUMORS:
        sub = tumor_2x2_ledger[tumor_2x2_ledger["tumor_type"] == t]
        if not len(sub):
            continue
        print(f"=== {t} ({len(sub)} placed targets) ===")
        counts = sub["quadrant"].value_counts()
        for q in MTW.W.ALL_QUADRANTS:
            n_q = int(counts.get(q, 0))
            if n_q:
                names = ", ".join(sub[sub["quadrant"] == q]["target_harmonized"].tolist())
                print(f"  {q} ({n_q}): {names}")
            else:
                print(f"  {q} (0):")
        print()
    """,
    note=(
        "Consolidated view of every per-tumor-type 2x2 cut already run in Entries 5-9/11/12, one table + "
        "one printed block per tumor-type name with its classified targets listed underneath. Appended here "
        "(rather than inserted earlier) to avoid renumbering already-referenced entries."
    ),
)


# ===========================================================================
# DRIVER: execute every entry, assemble notebook JSON, write outputs
# ===========================================================================
def main():
    cells = []
    intro = f"""# 2x2 Matrix Analysis -- Antibody Therapeutics IP x Clinical (Oncology)
**Prepared for:** Executive / Program Strategy Review
**Analyst view:** Head of Data Analytics -- target/modality selection for a new antibody-therapeutics program
**Sources:** `input/ip_final_version4.csv` (patents) x `input/clinical_final_version1.csv` (trials)
**Engine:** `input/multitarget_locked_whitespace_workflow.py` (reused verbatim, deterministic, no LLM)
**Join key:** `target_harmonized` only (per instruction)

**Plan:** Entry 1-4 build the shared data + methodology. Entry 5-9 pilot the pipeline on **Pancreatic**.
Entry 10-11 generalize it into a reusable runner and demonstrate it on **Prostate**. Entry 12-13 sweep
every remaining named solid tumor type (ranked by trial volume, with 1L/2L cuts where volume
supports it). Entry 14 runs the **whole dataset** as one cut. Entry 15 is the cross-cut executive
synthesis / portfolio shortlist.
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
    shutil.copyfile(NOTEBOOK_PATH, os.path.join(OUT_DIR, "2by2matrix_final_version1.ipynb"))

    print(f"Exported {n_exported_tables} tables to {DATA_DIR}")
    print(f"Exported {len(EXPORTED_FIGS)} figures to {FIG_DIR}")
    print(f"Notebook copy: {os.path.join(OUT_DIR, '2by2matrix_final_version1.ipynb')}")


if __name__ == "__main__":
    main()
