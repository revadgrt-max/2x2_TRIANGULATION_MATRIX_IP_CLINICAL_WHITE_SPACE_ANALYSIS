"""
Build the IP Antibody Therapeutics Landscape Analysis report package.

Recomputes every analytic from input/ip_lanscape.ipynb via DuckDB, exports:
  - output/IP Landscape/data/*.csv          (one CSV per table with > 10 rows)
  - output/IP Landscape/data/IP_Landscape_All_Tables.xlsx (all tables, one sheet each)
  - output/IP Landscape/figures/*.png       (all chart panels, high-res)
  - output/IP Landscape/IP_Antibody_Therapeutics_Landscape_Analysis.docx
  - output/IP Landscape/IP_Antibody_Therapeutics_Landscape_Analysis.pdf
  - output/IP Landscape/IP_Antibody_Therapeutics_Landscape_Analysis.pptx
  - output/IP Landscape/ip_lanscape.ipynb    (copy of the source notebook)
"""

import shutil
from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import pandas as pd

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
BASE = Path("/Users/revathisekar/Documents/vibe analytics trial 2")
csv_path = BASE / "input" / "FINAL_master_plus_mab_addendum.csv"
NOTEBOOK_SRC = BASE / "input" / "ip_lanscape.ipynb"

OUT = BASE / "output" / "IP Landscape"
DATA = OUT / "data"
FIG = OUT / "figures"
for d in (OUT, DATA, FIG):
    d.mkdir(parents=True, exist_ok=True)

con = duckdb.connect()

TABLE_REGISTRY = []  # list of dicts: key, title, df, csv_path (or None)


def register(key, title, df, force_csv=False):
    """Export df to CSV if it has more than 10 rows (or force_csv), track for report + Excel workbook."""
    path = None
    if len(df) > 10 or force_csv:
        path = DATA / f"{key}.csv"
        df.to_csv(path, index=False)
    TABLE_REGISTRY.append({"key": key, "title": title, "df": df, "csv_path": path})
    return df


print("Recomputing all analytics via DuckDB ...")

# ----------------------------------------------------------------------------
# Entry 1-3: base overview
# ----------------------------------------------------------------------------
sql_first_5 = f"SELECT * FROM read_csv_auto('{csv_path.as_posix()}') LIMIT 5;"
first_5_df = con.sql(sql_first_5).df()

sql_total_rows = f"SELECT COUNT(*) AS total_rows FROM read_csv_auto('{csv_path.as_posix()}');"
total_rows_df = con.sql(sql_total_rows).df()

sql_modality_counts = f"""
SELECT modality_only, COUNT(*) AS row_count
FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY modality_only ORDER BY row_count DESC;
"""
modality_counts_df = register("table_modality_counts", "Rows by Modality", con.sql(sql_modality_counts).df())

sql_overview = f"""
SELECT COUNT(*) AS total_rows, MIN(filing_year) AS min_year, MAX(filing_year) AS max_year
FROM read_csv_auto('{csv_path.as_posix()}');
"""
overview_df = con.sql(sql_overview).df()

sql_is_antibody = f"""
SELECT is_antibody, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY is_antibody ORDER BY n DESC;
"""
is_antibody_df = register("table_is_antibody_flag", "is_antibody Flag Distribution", con.sql(sql_is_antibody).df())

sql_authority = f"""
SELECT authority, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY authority ORDER BY n DESC LIMIT 15;
"""
authority_df = register("table_authority", "Top Authorities / Jurisdictions", con.sql(sql_authority).df())

sql_modality = f"""
SELECT modality_only, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY modality_only ORDER BY n DESC LIMIT 20;
"""
modality_df = register("table_modality_distribution", "Modality Distribution", con.sql(sql_modality).df())

sql_top_targets = f"""
SELECT COALESCE(target_corrected, target) AS target_name, COUNT(*) AS n
FROM read_csv_auto('{csv_path.as_posix()}') GROUP BY target_name ORDER BY n DESC LIMIT 25;
"""
top_targets_df = register("table01_top25_targets", "Top 25 Targets by Patent Count", con.sql(sql_top_targets).df())

sql_top_indications = f"""
SELECT TRIM(indications) AS indication, COUNT(*) AS n
FROM read_csv_auto('{csv_path.as_posix()}')
WHERE indications IS NOT NULL AND TRIM(indications) <> ''
GROUP BY indication ORDER BY n DESC LIMIT 25;
"""
top_indications_df = register("table02_top25_indication_strings", "Top 25 Raw Indication Strings", con.sql(sql_top_indications).df())

sql_top_assignees = f"""
SELECT current_assignee, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
WHERE current_assignee IS NOT NULL GROUP BY current_assignee ORDER BY n DESC LIMIT 25;
"""
top_assignees_df = register("table03_top25_assignees", "Top 25 Assignees / Companies", con.sql(sql_top_assignees).df())

sql_filing_trend = f"""
SELECT filing_year, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
WHERE filing_year IS NOT NULL GROUP BY filing_year ORDER BY filing_year;
"""
filing_trend_df = register("table04_filing_trend_by_year", "Filing Trend by Year", con.sql(sql_filing_trend).df())

sql_category = f"""
SELECT category, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY category ORDER BY n DESC LIMIT 15;
"""
category_df = register("table_category_distribution", "Category Distribution", con.sql(sql_category).df())

sql_claim_focus = f"""
SELECT claim_focus, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY claim_focus ORDER BY n DESC LIMIT 15;
"""
claim_focus_df = register("table05_claim_focus_top15", "Claim Focus Distribution (Top 15)", con.sql(sql_claim_focus).df())

sql_crowding_relevance = f"""
SELECT crowding_relevance, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY crowding_relevance ORDER BY n DESC LIMIT 15;
"""
crowding_relevance_df = register("table06_crowding_relevance_top15", "Crowding Relevance Distribution (Top 15)", con.sql(sql_crowding_relevance).df())

sql_resolution_confidence = f"""
SELECT resolution_confidence, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY resolution_confidence ORDER BY n DESC;
"""
resolution_confidence_df = register("table_resolution_confidence", "Resolution Confidence Distribution", con.sql(sql_resolution_confidence).df())

sql_record_source = f"""
SELECT record_source, COUNT(*) AS n FROM read_csv_auto('{csv_path.as_posix()}')
GROUP BY record_source ORDER BY n DESC;
"""
record_source_df = register("table_record_source", "Record Source Distribution", con.sql(sql_record_source).df())

sql_avg_claims = f"""
SELECT AVG(independent_claim_count) AS avg_independent_claims, AVG(total_claim_count) AS avg_total_claims
FROM read_csv_auto('{csv_path.as_posix()}');
"""
avg_claims_df = con.sql(sql_avg_claims).df()

sql_claim_disclosure = f"""
SELECT
  SUM(CASE WHEN claim_mentions_target THEN 1 ELSE 0 END) AS claim_mentions_target_n,
  SUM(CASE WHEN claim_mentions_sequence_or_cdr THEN 1 ELSE 0 END) AS claim_mentions_seq_cdr_n,
  SUM(CASE WHEN claim_mentions_epitope_domain_or_competition THEN 1 ELSE 0 END) AS claim_mentions_epitope_n,
  COUNT(*) AS total_rows
FROM read_csv_auto('{csv_path.as_posix()}');
"""
claim_disclosure_df = con.sql(sql_claim_disclosure).df()

# ----------------------------------------------------------------------------
# Entry: indication whitespace / target-modality matrix / target scoring
# ----------------------------------------------------------------------------
sql_indication_whitespace = f"""
WITH split AS (
  SELECT UNNEST(STR_SPLIT(TRIM(indications), '|')) AS tumor_type
  FROM read_csv_auto('{csv_path.as_posix()}')
  WHERE indications IS NOT NULL AND TRIM(indications) <> '' AND TRIM(indications) <> 'oncology TA query hit'
)
SELECT TRIM(tumor_type) AS tumor_type, COUNT(*) AS n FROM split GROUP BY 1 ORDER BY n DESC;
"""
indication_whitespace_df = register("table07_parsed_indication_whitespace", "Parsed Indication Whitespace (Individual Tumor Types)", con.sql(sql_indication_whitespace).df())

sql_target_modality_matrix = f"""
WITH top_targets AS (
  SELECT COALESCE(target_corrected, target) AS t, COUNT(*) AS n
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
  GROUP BY 1 ORDER BY n DESC LIMIT 15
)
SELECT COALESCE(d.target_corrected, d.target) AS target_name, d.modality_only, COUNT(*) AS n
FROM read_csv_auto('{csv_path.as_posix()}') d
JOIN top_targets tt ON COALESCE(d.target_corrected, d.target) = tt.t
GROUP BY 1,2 ORDER BY target_name, n DESC;
"""
target_modality_matrix_df = register("table08_target_modality_crowding_matrix_top15", "Target x Modality Crowding Matrix (Top 15 Targets)", con.sql(sql_target_modality_matrix).df())

sql_target_whitespace_score = f"""
WITH base AS (
  SELECT COALESCE(target_corrected, target) AS t, current_assignee, filing_year
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
)
SELECT t AS target_name, COUNT(*) AS total_patents, COUNT(DISTINCT current_assignee) AS unique_assignees,
       SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END) AS filed_last_3y
FROM base GROUP BY 1 HAVING COUNT(*) >= 10 ORDER BY total_patents DESC LIMIT 30;
"""
target_whitespace_score_df = register("table09_target_whitespace_scoring_top30", "Target Whitespace Scoring (Top 30)", con.sql(sql_target_whitespace_score).df())

# ----------------------------------------------------------------------------
# Target Whitespace Multi-Angle: A, B, C, D
# ----------------------------------------------------------------------------
sql_target_whitespace_summary = f"""
WITH base AS (
  SELECT COALESCE(target_corrected, target) AS t, current_assignee, filing_year, modality_only,
         CASE WHEN indications IS NOT NULL AND TRIM(indications) NOT IN ('', 'oncology TA query hit') THEN 1 ELSE 0 END AS has_specific_indication
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
)
SELECT t AS target_name, COUNT(*) AS total_patents, COUNT(DISTINCT current_assignee) AS unique_assignees,
       COUNT(DISTINCT modality_only) AS distinct_modalities,
       SUM(has_specific_indication) AS patents_with_specific_indication,
       COUNT(*) - SUM(has_specific_indication) AS patents_generic_oncology_only,
       ROUND(100.0 * SUM(has_specific_indication) / COUNT(*), 1) AS pct_indication_resolved,
       SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END) AS filed_last_3y,
       ROUND(100.0 * SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_recent
FROM base GROUP BY 1 HAVING COUNT(*) >= 8 ORDER BY total_patents DESC;
"""
target_whitespace_summary_df = register("table10A_target_whitespace_summary_all", "A. Target-Level Whitespace Summary", con.sql(sql_target_whitespace_summary).df())

sql_target_modality_share = f"""
WITH base AS (
  SELECT COALESCE(target_corrected, target) AS t, modality_only
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
),
tot AS (SELECT t, COUNT(*) AS total FROM base GROUP BY 1)
SELECT b.t AS target_name, b.modality_only, COUNT(*) AS n, ROUND(100.0*COUNT(*)/tot.total,1) AS pct_of_target
FROM base b JOIN tot ON b.t = tot.t WHERE tot.total >= 15 GROUP BY 1,2,tot.total ORDER BY target_name, n DESC;
"""
target_modality_share_df = register("table10B_target_modality_share_all", "B. Target x Modality Share", con.sql(sql_target_modality_share).df())

sql_target_indication_matrix = f"""
WITH expanded AS (
  SELECT COALESCE(target_corrected, target) AS t, TRIM(UNNEST(STR_SPLIT(TRIM(indications), '|'))) AS tumor
  FROM read_csv_auto('{csv_path.as_posix()}')
  WHERE indications IS NOT NULL AND TRIM(indications) NOT IN ('', 'oncology TA query hit')
    AND COALESCE(target_corrected, target) IS NOT NULL
),
top_t AS (
  SELECT COALESCE(target_corrected, target) AS t, COUNT(*) n
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
  GROUP BY 1 ORDER BY n DESC LIMIT 12
),
top_i AS (SELECT tumor, COUNT(*) n FROM expanded GROUP BY 1 ORDER BY n DESC LIMIT 12)
SELECT e.t AS target_name, e.tumor AS indication, COUNT(*) AS n
FROM expanded e JOIN top_t ON e.t = top_t.t JOIN top_i ON e.tumor = top_i.tumor
GROUP BY 1,2 ORDER BY target_name, n DESC;
"""
target_indication_matrix_df = register("table10C_target_indication_crowding_matrix", "C. Target x Indication Crowding Matrix (Top 12 x Top 12)", con.sql(sql_target_indication_matrix).df())

sql_target_quadrant = f"""
WITH base AS (
  SELECT COALESCE(target_corrected, target) AS t, filing_year
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
),
agg AS (
  SELECT t AS target_name, COUNT(*) AS total_patents,
         SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END) AS recent_3y,
         ROUND(100.0*SUM(CASE WHEN filing_year >= 2023 THEN 1 ELSE 0 END)/COUNT(*),1) AS pct_recent
  FROM base GROUP BY 1 HAVING COUNT(*) >= 8
)
SELECT target_name, total_patents, recent_3y, pct_recent,
  CASE
    WHEN total_patents >= 40 AND pct_recent >= 30 THEN 'Hot/Crowded (high volume, high momentum)'
    WHEN total_patents >= 40 AND pct_recent < 30 THEN 'Mature/Legacy (high volume, cooling)'
    WHEN total_patents < 40 AND pct_recent >= 30 THEN 'Emerging Whitespace (low volume, accelerating)'
    ELSE 'Quiet/Underexplored (low volume, low momentum)'
  END AS whitespace_quadrant
FROM agg ORDER BY pct_recent DESC, total_patents ASC;
"""
target_quadrant_df = register("table10D_whitespace_quadrant_classification", "D. Whitespace Quadrant Classification", con.sql(sql_target_quadrant).df())

# ----------------------------------------------------------------------------
# Emerging whitespace deep-dive: E, F
# ----------------------------------------------------------------------------
sql_emerging_deepdive = f"""
WITH base AS (
  SELECT COALESCE(target_corrected, target) AS t, current_assignee, filing_year, modality_only,
         CASE WHEN indications IS NOT NULL AND TRIM(indications) NOT IN ('', 'oncology TA query hit') THEN TRIM(indications) ELSE NULL END AS ind
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
),
class AS (
  SELECT t, COUNT(*) total_patents,
         SUM(CASE WHEN filing_year>=2023 THEN 1 ELSE 0 END) recent_3y,
         ROUND(100.0*SUM(CASE WHEN filing_year>=2023 THEN 1 ELSE 0 END)/COUNT(*),1) pct_recent
  FROM base GROUP BY 1 HAVING COUNT(*)>=8
),
emerging AS (SELECT t FROM class WHERE total_patents < 40 AND pct_recent >= 30),
assignee_rank AS (
  SELECT t, current_assignee, COUNT(*) n, ROW_NUMBER() OVER (PARTITION BY t ORDER BY COUNT(*) DESC) rn
  FROM base WHERE t IN (SELECT t FROM emerging) GROUP BY 1,2
),
top_assignee AS (SELECT t, current_assignee AS top_assignee, n AS top_assignee_patents FROM assignee_rank WHERE rn = 1),
modality_rank AS (
  SELECT t, modality_only, COUNT(*) n, ROW_NUMBER() OVER (PARTITION BY t ORDER BY COUNT(*) DESC) rn
  FROM base WHERE t IN (SELECT t FROM emerging) GROUP BY 1,2
),
top_modality AS (SELECT t, modality_only AS dominant_modality, n AS dominant_modality_patents FROM modality_rank WHERE rn = 1),
extra AS (
  SELECT t, MIN(filing_year) min_year, MAX(filing_year) max_year,
         COUNT(DISTINCT current_assignee) unique_assignees,
         SUM(CASE WHEN ind IS NOT NULL THEN 1 ELSE 0 END) specific_indication_patents
  FROM base WHERE t IN (SELECT t FROM emerging) GROUP BY 1
)
SELECT c.t AS target_name, c.total_patents, c.recent_3y, c.pct_recent,
       e.min_year AS first_filing_year, e.max_year AS latest_filing_year, e.unique_assignees,
       ta.top_assignee, ta.top_assignee_patents, ROUND(100.0*ta.top_assignee_patents/c.total_patents,1) AS top_assignee_share_pct,
       tm.dominant_modality, ROUND(100.0*tm.dominant_modality_patents/c.total_patents,1) AS dominant_modality_pct,
       e.specific_indication_patents, ROUND(100.0*e.specific_indication_patents/c.total_patents,1) AS pct_indication_resolved
FROM class c JOIN emerging em ON c.t = em.t JOIN extra e ON e.t = c.t
JOIN top_assignee ta ON ta.t = c.t JOIN top_modality tm ON tm.t = c.t
ORDER BY c.pct_recent DESC, c.total_patents DESC;
"""
emerging_deepdive_df = register("table11E_emerging_whitespace_deepdive", "E. Emerging Whitespace Deep-Dive", con.sql(sql_emerging_deepdive).df())

sql_emerging_trend = f"""
WITH base AS (
  SELECT COALESCE(target_corrected, target) AS t, filing_year
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
),
class AS (
  SELECT t, COUNT(*) total_patents,
         ROUND(100.0*SUM(CASE WHEN filing_year>=2023 THEN 1 ELSE 0 END)/COUNT(*),1) pct_recent
  FROM base GROUP BY 1 HAVING COUNT(*)>=8
),
emerging AS (SELECT t FROM class WHERE total_patents < 40 AND pct_recent >= 30)
SELECT t AS target_name, filing_year, COUNT(*) AS n
FROM base WHERE t IN (SELECT t FROM emerging) AND filing_year BETWEEN 2015 AND 2025
GROUP BY 1,2 ORDER BY target_name, filing_year;
"""
emerging_trend_df = register("table11F_emerging_whitespace_filing_trend", "F. Emerging Whitespace Filing Trend (2015-2025)", con.sql(sql_emerging_trend).df())

# ----------------------------------------------------------------------------
# Target x Modality x Indication grid: G
# ----------------------------------------------------------------------------
sql_target_modality_indication_grid = f"""
WITH expanded AS (
  SELECT COALESCE(target_corrected, target) AS t, modality_only, TRIM(UNNEST(STR_SPLIT(TRIM(indications), '|'))) AS tumor
  FROM read_csv_auto('{csv_path.as_posix()}')
  WHERE indications IS NOT NULL AND TRIM(indications) NOT IN ('', 'oncology TA query hit')
    AND COALESCE(target_corrected, target) IS NOT NULL AND modality_only IS NOT NULL
),
top_t AS (
  SELECT COALESCE(target_corrected, target) AS t, COUNT(*) n
  FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
  GROUP BY 1 ORDER BY n DESC LIMIT 10
),
top_i AS (SELECT tumor, COUNT(*) n FROM expanded GROUP BY 1 ORDER BY n DESC LIMIT 8),
top_m AS (SELECT modality_only, COUNT(*) n FROM expanded GROUP BY 1 ORDER BY n DESC LIMIT 5),
combos AS (SELECT top_t.t, top_m.modality_only, top_i.tumor FROM top_t CROSS JOIN top_m CROSS JOIN top_i),
actual AS (
  SELECT t, modality_only, tumor, COUNT(*) n FROM expanded
  WHERE t IN (SELECT t FROM top_t) AND modality_only IN (SELECT modality_only FROM top_m) AND tumor IN (SELECT tumor FROM top_i)
  GROUP BY 1,2,3
)
SELECT combos.t AS target_name, combos.modality_only, combos.tumor AS indication, COALESCE(actual.n, 0) AS patent_count
FROM combos LEFT JOIN actual ON actual.t = combos.t AND actual.modality_only = combos.modality_only AND actual.tumor = combos.tumor
ORDER BY combos.t, combos.modality_only, patent_count DESC;
"""
target_modality_indication_grid_df = register("table12G_target_modality_indication_grid", "G. Target x Modality x Indication Whitespace Grid", con.sql(sql_target_modality_indication_grid).df())

whitespace_cells_per_target_df = register(
    "table_whitespace_breadth_by_target",
    "Whitespace Breadth by Target",
    (
        target_modality_indication_grid_df.assign(is_zero=lambda d: d["patent_count"] == 0)
        .groupby("target_name")["is_zero"].sum().sort_values(ascending=False).rename("zero_count_combos").reset_index()
    ),
)

# ----------------------------------------------------------------------------
# Solid tumor indication x target whitespace grid: H
# ----------------------------------------------------------------------------
sql_solid_indication_target_grid = f"""
WITH expanded AS (
    SELECT COALESCE(target_corrected, target) AS t, TRIM(UNNEST(STR_SPLIT(TRIM(indications), '|'))) AS tumor
    FROM read_csv_auto('{csv_path.as_posix()}')
    WHERE indications IS NOT NULL AND TRIM(indications) NOT IN ('', 'oncology TA query hit')
      AND COALESCE(target_corrected, target) IS NOT NULL
),
solid AS (
    SELECT t, tumor FROM expanded
    WHERE tumor NOT ILIKE '%leukemia%' AND tumor NOT ILIKE '%lymphoma%' AND tumor NOT ILIKE '%myeloma%'
      AND tumor NOT ILIKE '%CLL%' AND tumor NOT ILIKE '%AML%' AND tumor NOT ILIKE '%ALL%'
      AND tumor NOT ILIKE '%MDS%' AND tumor NOT ILIKE '%hodgkin%' AND tumor NOT ILIKE '%waldenstrom%'
      AND tumor NOT ILIKE '%blood%'
),
top_indications AS (SELECT tumor, COUNT(*) AS n FROM solid GROUP BY 1 ORDER BY n DESC LIMIT 12),
top_targets AS (
    SELECT COALESCE(target_corrected, target) AS t, COUNT(*) AS n
    FROM read_csv_auto('{csv_path.as_posix()}') WHERE COALESCE(target_corrected, target) IS NOT NULL
    GROUP BY 1 ORDER BY n DESC LIMIT 20
),
combos AS (SELECT ti.tumor, tt.t FROM top_indications ti CROSS JOIN top_targets tt),
actual AS (
    SELECT tumor, t, COUNT(*) AS n FROM solid
    WHERE tumor IN (SELECT tumor FROM top_indications) AND t IN (SELECT t FROM top_targets)
    GROUP BY 1, 2
)
SELECT combos.tumor AS indication, combos.t AS target_name, COALESCE(actual.n, 0) AS patent_count
FROM combos LEFT JOIN actual ON actual.tumor = combos.tumor AND actual.t = combos.t
ORDER BY combos.tumor, patent_count DESC;
"""
solid_indication_target_grid_df = register("table13H_solid_tumor_indication_target_grid", "H. Solid Tumor Indication x Target Whitespace Grid", con.sql(sql_solid_indication_target_grid).df())

all_indications = solid_indication_target_grid_df["indication"].unique()
zero_df = solid_indication_target_grid_df[solid_indication_target_grid_df["patent_count"] == 0]
whitespace_targets_map = zero_df.groupby("indication")["target_name"].apply(lambda s: ", ".join(sorted(s)))
whitespace_count_map = zero_df.groupby("indication").size()
top_crowded_df = (
    solid_indication_target_grid_df.sort_values("patent_count", ascending=False)
    .groupby("indication").first()[["target_name", "patent_count"]]
    .rename(columns={"target_name": "most_crowded_target", "patent_count": "most_crowded_target_patents"})
)
whitespace_per_indication_df = pd.DataFrame({"indication": all_indications})
whitespace_per_indication_df["whitespace_target_count"] = whitespace_per_indication_df["indication"].map(whitespace_count_map).fillna(0).astype(int)
whitespace_per_indication_df["whitespace_targets"] = whitespace_per_indication_df["indication"].map(whitespace_targets_map).fillna("(none - all 20 targets already claimed)")
whitespace_per_indication_df = whitespace_per_indication_df.merge(top_crowded_df, on="indication", how="left")
whitespace_per_indication_df = whitespace_per_indication_df.sort_values("whitespace_target_count", ascending=False).reset_index(drop=True)
register("table14_whitespace_targets_per_solid_tumor_indication", "Whitespace Targets Available per Solid Tumor Indication", whitespace_per_indication_df)

print(f"Recomputed {len(TABLE_REGISTRY)} tables; {sum(1 for t in TABLE_REGISTRY if t['csv_path'])} exported as CSV.")

# ----------------------------------------------------------------------------
# Combined Excel workbook (all tables, one sheet each) - the "entire list" file
# ----------------------------------------------------------------------------
xlsx_path = DATA / "IP_Landscape_All_Tables.xlsx"
used_sheet_names = set()
with pd.ExcelWriter(xlsx_path, engine="openpyxl") as xw:
    for t in TABLE_REGISTRY:
        sheet_name = t["key"][:31]
        base_name, i = sheet_name, 1
        while sheet_name in used_sheet_names:
            i += 1
            sheet_name = f"{base_name[:28]}_{i}"
        used_sheet_names.add(sheet_name)
        t["df"].to_excel(xw, sheet_name=sheet_name, index=False)
print(f"Wrote combined workbook: {xlsx_path}")

# ============================================================================
# CHARTS
# ============================================================================
print("Rendering charts ...")

# ---- Figure 1: Overview dashboard (6 panels) ----
top_targets_chart_df = top_targets_df.dropna(subset=["target_name"]).head(15)
top_assignees_chart_df = top_assignees_df.head(15)
indication_chart_df = indication_whitespace_df.head(15)
matrix_df = target_modality_matrix_df.pivot(index="target_name", columns="modality_only", values="n").fillna(0)

fig, axes = plt.subplots(3, 2, figsize=(16, 20))
axes[0, 0].plot(filing_trend_df["filing_year"], filing_trend_df["n"], marker="o", color="#2563eb")
axes[0, 0].set_title("Filing Trend by Year"); axes[0, 0].set_xlabel("Filing Year"); axes[0, 0].set_ylabel("Patent Count"); axes[0, 0].grid(alpha=0.3)
axes[0, 1].bar(modality_df["modality_only"], modality_df["n"], color="#16a34a")
axes[0, 1].set_title("Modality Distribution"); axes[0, 1].set_ylabel("Patent Count"); axes[0, 1].tick_params(axis="x", rotation=45)
axes[1, 0].barh(top_targets_chart_df["target_name"][::-1], top_targets_chart_df["n"][::-1], color="#d97706")
axes[1, 0].set_title("Top 15 Targets"); axes[1, 0].set_xlabel("Patent Count")
axes[1, 1].barh(top_assignees_chart_df["current_assignee"][::-1], top_assignees_chart_df["n"][::-1], color="#7c3aed")
axes[1, 1].set_title("Top 15 Assignees"); axes[1, 1].set_xlabel("Patent Count"); axes[1, 1].tick_params(axis="y", labelsize=8)
axes[2, 0].barh(indication_chart_df["tumor_type"][::-1], indication_chart_df["n"][::-1], color="#dc2626")
axes[2, 0].set_title("Indication Whitespace (Top 15 Tumor Types)"); axes[2, 0].set_xlabel("Patent Count")
matrix_df.plot(kind="barh", stacked=True, ax=axes[2, 1], colormap="tab20")
axes[2, 1].set_title("Target x Modality Crowding Matrix (Top 15 Targets)"); axes[2, 1].set_xlabel("Patent Count")
axes[2, 1].legend(title="Modality", fontsize=8, title_fontsize=8)
plt.tight_layout()
fig01_path = FIG / "fig01_overview_dashboard.png"
plt.savefig(fig01_path, dpi=150, bbox_inches="tight")
plt.close(fig)

# ---- Figure 2: Target whitespace multi-angle (quadrant scatter, indication heatmap, modality share, resolution) ----
quadrant_colors = {
    "Hot/Crowded (high volume, high momentum)": "#dc2626",
    "Mature/Legacy (high volume, cooling)": "#d97706",
    "Emerging Whitespace (low volume, accelerating)": "#16a34a",
    "Quiet/Underexplored (low volume, low momentum)": "#94a3b8",
}
fig, axes = plt.subplots(2, 2, figsize=(18, 16))
ax = axes[0, 0]
for label, color in quadrant_colors.items():
    subset = target_quadrant_df[target_quadrant_df["whitespace_quadrant"] == label]
    ax.scatter(subset["total_patents"], subset["pct_recent"], label=label, color=color, alpha=0.75, s=60)
top20 = target_quadrant_df.nlargest(20, "total_patents")
for _, row in top20.iterrows():
    ax.annotate(row["target_name"], (row["total_patents"], row["pct_recent"]), fontsize=7, xytext=(4, 4), textcoords="offset points")
ax.axvline(40, color="black", linestyle="--", linewidth=0.7, alpha=0.5)
ax.axhline(30, color="black", linestyle="--", linewidth=0.7, alpha=0.5)
ax.set_xlabel("Total Patents (volume / crowding)"); ax.set_ylabel("Percent Filed in Last 3 Years (momentum)")
ax.set_title("Whitespace Quadrant: Volume x Recent Momentum"); ax.legend(fontsize=7, loc="upper right")

ax = axes[0, 1]
pivot_ti = target_indication_matrix_df.pivot(index="target_name", columns="indication", values="n").fillna(0)
im = ax.imshow(pivot_ti.values, cmap="YlOrRd", aspect="auto")
ax.set_xticks(range(len(pivot_ti.columns))); ax.set_xticklabels(pivot_ti.columns, rotation=90, fontsize=8)
ax.set_yticks(range(len(pivot_ti.index))); ax.set_yticklabels(pivot_ti.index, fontsize=8)
ax.set_title("Target x Indication Crowding Heatmap (Top 12 x Top 12)"); fig.colorbar(im, ax=ax, label="Patent Count")

ax = axes[1, 0]
top15_targets = target_whitespace_summary_df.nlargest(15, "total_patents")["target_name"].tolist()
share_pivot = (
    target_modality_share_df[target_modality_share_df["target_name"].isin(top15_targets)]
    .pivot(index="target_name", columns="modality_only", values="pct_of_target").reindex(top15_targets).fillna(0)
)
share_pivot.plot(kind="barh", stacked=True, ax=ax, colormap="tab20")
ax.set_title("Modality Share by Target, % of Patents (Top 15 Targets)"); ax.set_xlabel("Percent of Patents")
ax.legend(title="Modality", fontsize=7, title_fontsize=7)

ax = axes[1, 1]
top20_res = target_whitespace_summary_df.nlargest(20, "total_patents").sort_values("pct_indication_resolved")
ax.barh(top20_res["target_name"], top20_res["pct_indication_resolved"], color="#0891b2")
ax.set_title("% of Patents with a Specific Indication Resolved (Top 20 Targets)"); ax.set_xlabel("Percent Indication-Resolved")
plt.tight_layout()
fig02_path = FIG / "fig02_target_whitespace_multiangle.png"
plt.savefig(fig02_path, dpi=150, bbox_inches="tight")
plt.close(fig)

# ---- Figure 3: Emerging whitespace deep-dive ----
fig, axes = plt.subplots(2, 2, figsize=(18, 16))
top25_emerging = emerging_deepdive_df.nlargest(25, "total_patents").sort_values("total_patents")
ax = axes[0, 0]
norm = mcolors.Normalize(vmin=top25_emerging["pct_recent"].min(), vmax=top25_emerging["pct_recent"].max())
colors = cm.viridis(norm(top25_emerging["pct_recent"]))
ax.barh(top25_emerging["target_name"], top25_emerging["total_patents"], color=colors)
ax.set_title("Emerging Whitespace Targets: Volume (color = % filed since 2023)"); ax.set_xlabel("Total Patents")
sm = cm.ScalarMappable(cmap="viridis", norm=norm)
fig.colorbar(sm, ax=ax, label="% Filed Since 2023")

ax = axes[0, 1]
top10_names = emerging_deepdive_df.nlargest(10, "total_patents")["target_name"].tolist()
trend_pivot = (
    emerging_trend_df[emerging_trend_df["target_name"].isin(top10_names)]
    .pivot(index="filing_year", columns="target_name", values="n").fillna(0).reindex(columns=top10_names)
)
for name in top10_names:
    ax.plot(trend_pivot.index, trend_pivot[name], marker="o", label=name, linewidth=1.5)
ax.set_title("Filing Trend: Top 10 Emerging Whitespace Targets"); ax.set_xlabel("Filing Year"); ax.set_ylabel("Patents Filed")
ax.legend(fontsize=7, ncol=2)

ax = axes[1, 0]
conc_sorted = top25_emerging.sort_values("top_assignee_share_pct")
ax.barh(conc_sorted["target_name"], conc_sorted["top_assignee_share_pct"], color="#7c3aed")
ax.set_title("Competitive Concentration: Top Assignee Share of Target IP"); ax.set_xlabel("% of Target Patents Held by Single Leading Assignee")
ax.axvline(50, color="black", linestyle="--", linewidth=0.7, alpha=0.5)

ax = axes[1, 1]
modality_list = top25_emerging["dominant_modality"].unique().tolist()
modality_color_map = {m: cm.tab10(i / max(len(modality_list) - 1, 1)) for i, m in enumerate(modality_list)}
bar_colors = top25_emerging["dominant_modality"].map(modality_color_map)
ax.barh(top25_emerging["target_name"], top25_emerging["dominant_modality_pct"], color=bar_colors)
ax.set_title("Dominant Modality Share per Emerging Whitespace Target"); ax.set_xlabel("% of Target Patents in Dominant Modality")
handles = [plt.Rectangle((0, 0), 1, 1, color=modality_color_map[m]) for m in modality_list]
ax.legend(handles, modality_list, title="Modality", fontsize=7, title_fontsize=7, loc="lower right")
plt.tight_layout()
fig03_path = FIG / "fig03_emerging_whitespace_deepdive.png"
plt.savefig(fig03_path, dpi=150, bbox_inches="tight")
plt.close(fig)

# ---- Figure 4a: Target x Modality x Indication small-multiple heatmaps ----
target_order = target_modality_indication_grid_df.groupby("target_name")["patent_count"].sum().sort_values(ascending=False).index.tolist()
modality_order = target_modality_indication_grid_df.groupby("modality_only")["patent_count"].sum().sort_values(ascending=False).index.tolist()
indication_order_g = target_modality_indication_grid_df.groupby("indication")["patent_count"].sum().sort_values(ascending=False).index.tolist()

fig, axes = plt.subplots(2, 5, figsize=(28, 11))
vmax = target_modality_indication_grid_df["patent_count"].max()
for ax, target in zip(axes.flat, target_order):
    sub = target_modality_indication_grid_df[target_modality_indication_grid_df["target_name"] == target]
    pivot = sub.pivot(index="modality_only", columns="indication", values="patent_count").reindex(index=modality_order, columns=indication_order_g).fillna(0)
    im = ax.imshow(pivot.values, cmap="RdYlGn_r", vmin=0, vmax=vmax, aspect="auto")
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = int(pivot.values[i, j])
            ax.text(j, i, str(val), ha="center", va="center", fontsize=7, color="white" if val > vmax * 0.5 else "black")
    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns, rotation=90, fontsize=6)
    ax.set_yticks(range(len(pivot.index))); ax.set_yticklabels(pivot.index, fontsize=7)
    ax.set_title(target, fontsize=10, fontweight="bold")
fig.suptitle("Target x Modality x Indication Whitespace (red = crowded, green/0 = whitespace)", fontsize=13)
fig.colorbar(im, ax=axes, shrink=0.6, label="Patent Count")
fig04a_path = FIG / "fig04a_target_modality_indication_heatmaps.png"
plt.savefig(fig04a_path, dpi=150, bbox_inches="tight")
plt.close(fig)

fig2, ax2 = plt.subplots(figsize=(9, 5))
wb = whitespace_cells_per_target_df.sort_values("zero_count_combos")
ax2.barh(wb["target_name"], wb["zero_count_combos"], color="#059669")
ax2.set_title("Whitespace Breadth: Empty Modality x Indication Cells per Target (of 40 possible)")
ax2.set_xlabel("Count of Unclaimed Modality x Indication Combinations")
plt.tight_layout()
fig04b_path = FIG / "fig04b_whitespace_breadth_by_target.png"
plt.savefig(fig04b_path, dpi=150, bbox_inches="tight")
plt.close(fig2)

# ---- Figure 5: Solid tumor indication whitespace ----
indication_order = whitespace_per_indication_df.sort_values("whitespace_target_count", ascending=False)["indication"].tolist()
target_order_h = solid_indication_target_grid_df.groupby("target_name")["patent_count"].sum().sort_values(ascending=False).index.tolist()
pivot_it = solid_indication_target_grid_df.pivot(index="indication", columns="target_name", values="patent_count").reindex(index=indication_order, columns=target_order_h)

fig, axes = plt.subplots(1, 2, figsize=(20, 8), gridspec_kw={"width_ratios": [3, 1]})
ax = axes[0]
vmax = pivot_it.values.max()
im = ax.imshow(pivot_it.values, cmap="RdYlGn_r", vmin=0, vmax=vmax, aspect="auto")
for i in range(pivot_it.shape[0]):
    for j in range(pivot_it.shape[1]):
        val = int(pivot_it.values[i, j])
        ax.text(j, i, str(val), ha="center", va="center", fontsize=7, color="white" if val > vmax * 0.5 else "black")
ax.set_xticks(range(len(pivot_it.columns))); ax.set_xticklabels(pivot_it.columns, rotation=90, fontsize=8)
ax.set_yticks(range(len(pivot_it.index))); ax.set_yticklabels(pivot_it.index, fontsize=9)
ax.set_title("Solid Tumor Indication x Target Whitespace (red = crowded, green/0 = unclaimed)")
fig.colorbar(im, ax=ax, label="Patent Count")
ax2 = axes[1]
wb2 = whitespace_per_indication_df.sort_values("whitespace_target_count")
ax2.barh(wb2["indication"], wb2["whitespace_target_count"], color="#059669")
ax2.set_title("Whitespace Breadth per Indication\n(of 20 established targets)"); ax2.set_xlabel("Count of Unclaimed Targets")
plt.tight_layout()
fig05_path = FIG / "fig05_solid_tumor_indication_whitespace.png"
plt.savefig(fig05_path, dpi=150, bbox_inches="tight")
plt.close(fig)

print("All figures rendered.")

# ============================================================================
# Copy notebook into output package
# ============================================================================
shutil.copy2(NOTEBOOK_SRC, OUT / NOTEBOOK_SRC.name)
print(f"Copied notebook to {OUT / NOTEBOOK_SRC.name}")

print("Data + figures build complete.")
