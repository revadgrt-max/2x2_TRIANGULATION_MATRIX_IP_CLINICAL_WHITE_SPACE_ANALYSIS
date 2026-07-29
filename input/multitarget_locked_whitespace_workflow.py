#!/usr/bin/env python3
"""
multitarget_locked_whitespace_workflow.py
=============================================================================
Multi-target generalization of locked_whitespace_workflow_csv_v3_deterministic.py.

Scores the ENTIRE target universe = union of an IP dataset (x-axis, FTO/epitope
crowding) and a clinical dataset (y-axis, clinical performance), joined on a
harmonized target key. The x-axis math is IDENTICAL to v3's deterministic
weighted three-layer FTO (imported from the v3 module: same weights, scales,
L1 hard floor). NO LLM anywhere; full-text claims are not read at run time.

TWO node granularities
----------------------
  default            : one node per harmonized target (modality collapsed)
  --by-modality      : one node per (target_harmonized x modality_code), so
                       BISPECIFIC / ADC / BiTE / PROTAC / MAB become distinct
                       nodes. L1 (cross-modality epitope lock) stays target-level;
                       L2/L3 are computed within the node's modality.

JOIN KEY
--------
--key-col (default "target_harmonized") is matched VERBATIM (trim + uppercase),
NOT split — a bispecific pair like "RANKL|VEGF" or an ADC "Mesothelin-PE38" is
one identity. Modality comes from --modality-col (default "modality_code").
If a file lacks the key column, pass the column it does have (e.g. "target").

SCALE NOTE
----------
Row counts are trials/patents, not targets. These files aggregate to ~1,300-1,700
harmonized targets (~1,400-2,150 target x modality nodes). Everything is indexed
in one pass, so it scales to any N.

Run (defaults point at the vibe-analytics final-version files):
    python3 multitarget_locked_whitespace_workflow.py --by-modality
    python3 multitarget_locked_whitespace_workflow.py --by-modality --sample 50 --seed 7
Flags: --ip-csv --clin-csv --key-col --modality-col --by-modality --sample N
       --seed S --as-of YYYY-MM-DD --indication a,b --outdir DIR
Out (in OUTDIR): master_multitarget_whitespace.csv · MAP_2x2_REPORT.md · multitarget_2x2.png
"""
from __future__ import annotations
import argparse
import csv
import math
import os
import random
import re
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
csv.field_size_limit(10 ** 9)

# Reuse the v3 deterministic engine verbatim (x-axis math, layer predicates,
# phase weights, 2x2 placement). Keeps X byte-identical to v3_deterministic.
import locked_whitespace_workflow_csv_v3_param as W  # noqa: E402


# --- defaults: the vibe-analytics final-version files -----------------------
DEF_IP_CSV = os.path.join(_HERE, "ip_final_version3.csv")
DEF_CLIN_CSV = os.path.join(_HERE, "clinical_final_version1.csv")
DEF_KEY_COL = "target_harmonized"
DEF_MOD_COL = "modality_code"
DEF_AS_OF = W.AS_OF
DEF_INDICATION = ",".join(W._INDICATION_TERMS)
DEF_OUTDIR = os.path.join(_HERE, "results_multitarget")


def norm_key(s: str) -> str:
    """Verbatim harmonized key, case/space-normalized. NOT split on '|'/'-'."""
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def _apd(row) -> "str | None":
    """Application date as YYYYMMDD. Tolerant of float-formatted values —
    v4 stores '20100330.0' / filing_year '2010.0', which W._ip_apd rejects
    (it wants pure digits), silently dropping every patent in the leakage
    filter. Strip a trailing '.0' first, then parse."""
    d = re.sub(r"\.0+$", "", str(row.get("application_date", "") or "").strip())
    if d.isdigit() and len(d) >= 4:
        return d[:8].ljust(8, "0")
    y = re.sub(r"\.0+$", "", str(row.get("filing_year", "") or "").strip())
    return (y + "0101") if y.isdigit() else None


# =============================================================================
# X-AXIS — deterministic weighted three-layer FTO, indexed by target (one pass)
# =============================================================================
def _in_indication(row, terms):
    ind = (row.get("indications") or "").lower()
    return any(t in ind for t in terms)


def build_ip_index(ip_csv, *, cut, indication_terms, key_col, mod_col):
    """One pass over the IP master. Returns by_target[key] with a cross-modality
    lock count (L1) and per-modality grounded / use counts (L2 / L3)."""
    idx = defaultdict(lambda: {"lock": 0, "grounded_all": 0, "broad": 0,
                               "by_mod": defaultdict(lambda: {"grounded": 0, "l3": 0}),
                               "modalities": set(), "key_patent": None,
                               "assignee": None, "recent_apd": ""})
    n = 0
    with open(ip_csv, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            n += 1
            key = norm_key(r.get(key_col) or r.get("target") or "")
            if not key:
                continue
            mod = (str(r.get(mod_col, "")).strip().upper() or "UNSPEC")
            apd = _apd(r)
            dated = bool(apd) and apd <= cut
            grounded = W._claims_grounded(r)
            b = idx[key]
            b["broad"] += 1
            b["modalities"].add(mod)
            if not dated or not grounded:
                continue
            b["grounded_all"] += 1
            if W._is_locking_claim(r):
                b["lock"] += 1                                  # L1: cross-modality
            m = b["by_mod"][mod]
            m["grounded"] += 1                                  # L2: this modality
            if _in_indication(r, indication_terms) and W._is_use_combo(r):
                m["l3"] += 1                                    # L3: this modality x indication
            if apd >= (b["recent_apd"] or ""):
                b["recent_apd"] = apd
                b["key_patent"] = (r.get("publication_number") or "").strip() or None
                b["assignee"] = next((a.strip() for a in str(r.get("current_assignee", "")).split("|")
                                      if a.strip()), None)
    print(f"    [ip] {n} patent rows -> {len(idx)} harmonized targets", file=sys.stderr)
    return idx


def fto_x(ipb, mod=None):
    """Deterministic X (same density fn, weights, scales, hard floor as v3).
    mod=None -> modality-collapsed (L2 = all grounded, L3 = sum over modalities).
    mod set  -> that modality's L2/L3; L1 stays cross-modality."""
    n_l1 = ipb["lock"]
    if mod is None:
        n_l2 = ipb["grounded_all"]
        n_l3 = sum(m["l3"] for m in ipb["by_mod"].values())
    else:
        m = ipb["by_mod"].get(mod, {"grounded": 0, "l3": 0})
        n_l2, n_l3 = m["grounded"], m["l3"]
    l1 = W.fto_layer_density(n_l1, scale=W.FTO_SCALE_L1)
    l2 = W.fto_layer_density(n_l2, scale=W.FTO_SCALE_L2)
    l3 = W.fto_layer_density(n_l3, scale=W.FTO_SCALE_L3)
    blend = W.FTO_W_L1 * l1 + W.FTO_W_L2 * l2 + W.FTO_W_L3 * l3
    x_det = round(min(1.0, max(0.0, max(blend, l1) if W.FTO_L1_IS_HARD_FLOOR else blend)), 3)
    grounded = (ipb["grounded_all"] if mod is None else
                ipb["by_mod"].get(mod, {"grounded": 0})["grounded"])
    if grounded == 0:
        return None, l1, l2, l3, n_l1, "UNRESOLVED_no_grounded_patents"
    return x_det, l1, l2, l3, n_l1, "weighted_fto_deterministic"


# =============================================================================
# Y-AXIS — deterministic clinical performance from the outcome column
# =============================================================================
# outcome label -> (sign, attenuation). Tune here in ONE place. Prefer
# outcome_enriched if present, else outcome. Covers both the FINAL and the
# vibe-analytics final-version label vocabularies.
OUTCOME_SIGN = {
    "approved":                     (+1.0, 1.00),
    "positive":                     (+1.0, 1.00),
    "negative":                     (-1.0, 1.00),
    "terminated":                   (-1.0, 0.50),   # ambiguous stop -> half weight
    "trial halted":                 (-1.0, 0.50),
    "halted":                       (-1.0, 0.50),
    # non-decisive -> abstain
    "pending": (0.0, 0.0), "ongoing": (0.0, 0.0), "unclear": (0.0, 0.0),
    "unknown": (0.0, 0.0), "no_mature_data": (0.0, 0.0),
    "completed_no_results": (0.0, 0.0), "completed_results_available": (0.0, 0.0),
    "": (0.0, 0.0),
}


def build_clin_index(clin_csv, *, cut, key_col, mod_col):
    cut_year = int(cut[:4]) if cut[:4].isdigit() else 9999
    idx = defaultdict(lambda: defaultdict(lambda: {"trials": [], "n": 0}))
    n = 0
    with open(clin_csv, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            n += 1
            key = norm_key(r.get(key_col) or r.get("target") or "")
            if not key:
                continue
            mod = (str(r.get(mod_col, "")).strip().upper() or "UNSPEC")
            sd = (r.get("start_date") or r.get("study_first_posted") or "").strip()
            sy = int(sd[:4]) if sd[:4].isdigit() else None
            if sy is not None and sy > cut_year:
                continue
            label = (str(r.get("outcome_enriched", "")).strip().lower()
                     or str(r.get("outcome", "")).strip().lower())
            sign, atten = OUTCOME_SIGN.get(label, (0.0, 0.0))
            ph, _ = W._phase_from_csv(r.get("phase") or r.get("ctgov_phase"))
            pw = W.PHASE_WEIGHT.get(ph or W.Phase.PH2)
            idx[key][mod]["n"] += 1
            idx[key][mod]["trials"].append((sign, atten, pw))
    print(f"    [clin] {n} trial rows -> {len(idx)} harmonized targets", file=sys.stderr)
    return idx


def clinical_y(trials):
    net = 0.0; dec = 0; pos_ph3 = neg = False
    for sign, atten, pw in trials:
        if sign == 0.0:
            continue
        dec += 1
        net += sign * pw * atten
        neg |= sign < 0
        pos_ph3 |= (sign > 0 and pw >= W.PHASE_WEIGHT[W.Phase.PH3])
    if dec == 0:
        return None, "ABSTAIN - no decisive readout", 0, 0.0
    perf = round(0.5 + 0.5 * math.tanh(net / W.SQUASH), 3)
    if pos_ph3:
        perf = max(perf, 0.75)
    conf = round(min(1.0, dec / W.COVERAGE_K), 2)
    label = ("VALIDATED" if perf >= 0.60 else
             ("FAILED / CONTESTED (late)" if perf <= 0.40 and neg else
              ("WEAK" if perf <= 0.40 else "CONTESTED")))
    return perf, label, dec, conf


# =============================================================================
# JOIN + PLACE + WRITE
# =============================================================================
def run(ip_csv, clin_csv, as_of, indication_terms, outdir, key_col, mod_col,
        by_modality, sample_n, seed):
    cut = as_of.replace("-", "")
    mode = "target x modality" if by_modality else "target (modality-collapsed)"
    print(f"\nMULTI-TARGET white-space (deterministic) — as of {as_of} — mode: {mode}", file=sys.stderr)
    ip = build_ip_index(ip_csv, cut=cut, indication_terms=indication_terms,
                        key_col=key_col, mod_col=mod_col)
    clin = build_clin_index(clin_csv, cut=cut, key_col=key_col, mod_col=mod_col)

    # ---- build the node list ----
    nodes = []   # (target_key, modality_or_None)
    if by_modality:
        keys = set()
        for t, b in ip.items():
            for m in b["by_mod"]:
                keys.add((t, m))
        for t, mods in clin.items():
            for m in mods:
                keys.add((t, m))
        nodes = sorted(keys)
    else:
        nodes = [(t, None) for t in sorted(set(ip) | set(clin))]

    # ---- optional reproducible sample, stratified by modality ----
    if sample_n and sample_n < len(nodes):
        rng = random.Random(seed)
        if by_modality:
            by_m = defaultdict(list)
            for nd in nodes:
                by_m[nd[1]].append(nd)
            for v in by_m.values():
                rng.shuffle(v)
            picked, mods = [], sorted(by_m)          # round-robin across modalities
            i = 0
            while len(picked) < sample_n and any(by_m.values()):
                m = mods[i % len(mods)]
                if by_m[m]:
                    picked.append(by_m[m].pop())
                i += 1
            nodes = sorted(picked)
        else:
            nodes = sorted(rng.sample(nodes, sample_n))
        print(f"    [sample] {len(nodes)} nodes (seed={seed})", file=sys.stderr)

    rows = []
    for t, m in nodes:
        ipb = ip.get(t)
        if ipb is None:
            x = None; l1 = l2 = l3 = 0.0; lock = 0; xstatus = "UNRESOLVED_no_grounded_patents"
        else:
            x, l1, l2, l3, lock, xstatus = fto_x(ipb, mod=m)
        trials = clin.get(t, {}).get(m, {"trials": [], "n": 0}) if by_modality else \
            {"trials": [tr for mm in clin.get(t, {}).values() for tr in mm["trials"]],
             "n": sum(mm["n"] for mm in clin.get(t, {}).values())}
        y, ylabel, dec, yconf = clinical_y(trials["trials"])
        q = W.quadrant(y, x)
        rows.append({
            "target_harmonized": t, "modality": m or "(all)",
            "quadrant": q,
            "clinical_performance_Y": y, "clinical_label": ylabel,
            "clinical_decisive_n": dec, "clinical_confidence": yconf, "n_trials": trials["n"],
            "epitope_crowding_X": x, "x_status": xstatus,
            "fto_l1_epitope": l1, "fto_l2_format": l2, "fto_l3_use": l3, "fto_l1_lock_count": lock,
            "patents_grounded": (ipb["grounded_all"] if ipb else 0),
            "ip_modalities": "|".join(sorted(ipb["modalities"])) if ipb else None,
            "key_patent": ipb["key_patent"] if ipb else None,
            "blocking_assignee": ipb["assignee"] if ipb else None,
            "x_resolved": x is not None, "y_resolved": y is not None,
        })

    os.makedirs(outdir, exist_ok=True)
    _write_master(rows, outdir)
    _write_report(rows, outdir, as_of, mode, len(ip), len(clin))
    _plot(rows, outdir, by_modality)
    print("done.", file=sys.stderr)
    return rows


def _write_master(rows, outdir):
    p = os.path.join(outdir, "master_multitarget_whitespace.csv")
    order = {q: i for i, q in enumerate(W.ALL_QUADRANTS)}
    rows_sorted = sorted(rows, key=lambda r: (order.get(r["quadrant"], 99),
                                              -(r["clinical_performance_Y"] or -1)))
    with open(p, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader(); wr.writerows(rows_sorted)
    print(f"    [out] {p} ({len(rows)} nodes)", file=sys.stderr)


def _write_report(rows, outdir, as_of, mode, n_ip, n_clin):
    placed = [r for r in rows if r["x_resolved"] and r["y_resolved"]]
    from collections import Counter
    modc = Counter(r["modality"] for r in rows)
    lines = [f"# Multi-target white-space 2x2 — deterministic (as of {as_of})", "",
             f"Mode: **{mode}**  ·  IP targets {n_ip} · clinical targets {n_clin}",
             f"Nodes: **{len(rows)}**  ·  placed on 2x2 (both axes): **{len(placed)}**", "",
             f"Modality breakdown of nodes: " +
             ", ".join(f"{m} {c}" for m, c in modc.most_common()), ""]
    for q in W.ALL_QUADRANTS:
        members = sorted([r for r in placed if r["quadrant"] == q],
                         key=lambda r: -(r["clinical_performance_Y"] or 0))
        lines.append(f"## {q}  ({len(members)})")
        for r in members:
            lines.append(f"- **{r['target_harmonized']}** [{r['modality']}]  "
                         f"Y={r['clinical_performance_Y']} X={r['epitope_crowding_X']} "
                         f"({r['clinical_label']}; {r['patents_grounded']} grounded patents, {r['n_trials']} trials)")
        lines.append("")
    x_only = sum(1 for r in rows if r["x_resolved"] and not r["y_resolved"])
    y_only = sum(1 for r in rows if r["y_resolved"] and not r["x_resolved"])
    neither = sum(1 for r in rows if not r["x_resolved"] and not r["y_resolved"])
    lines += ["## Coverage (flagged, not hidden)",
              f"- X only (patents, no decisive trial): {x_only}",
              f"- Y only (clinical, no grounded patents): {y_only}",
              f"- neither axis resolved: {neither}", ""]
    p = os.path.join(outdir, "MAP_2x2_REPORT.md")
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    print(f"    [out] {p}", file=sys.stderr)


_MOD_COLOR = {"MAB": "tab:blue", "BISPECIFIC": "tab:orange", "ADC": "tab:red",
              "BITE": "tab:green", "PROTAC": "tab:purple", "CAR-T": "tab:brown",
              "RADIOLIGAND": "tab:pink"}


def _plot(rows, outdir, by_modality):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"    [plot] skipped ({e})", file=sys.stderr); return
    placed = [r for r in rows if r["x_resolved"] and r["y_resolved"]]
    if not placed:
        print("    [plot] skipped (no placed nodes)", file=sys.stderr); return
    fig, ax = plt.subplots(figsize=(9, 8))
    seen = set()
    for r in placed:
        m = r["modality"]
        c = _MOD_COLOR.get(m, "grey")
        ax.scatter(r["epitope_crowding_X"], r["clinical_performance_Y"], s=30, alpha=0.75,
                   color=c, label=m if m not in seen else None)
        seen.add(m)
        ax.annotate(f"{r['target_harmonized']}", (r["epitope_crowding_X"], r["clinical_performance_Y"]),
                    fontsize=5.5, alpha=0.7)
    ax.axvline(W.CROWDING_SPLIT, color="grey", lw=0.8, ls="--")
    ax.axhline(W.PERF_SPLIT, color="grey", lw=0.8, ls="--")
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
    ax.set_xlabel("FTO / epitope crowding  X  (0 open → 1 crowded)")
    ax.set_ylabel("Clinical performance  Y  (0 failed → 1 validated)")
    ax.set_title(f"Multi-target 2x2 (deterministic) — {len(placed)} nodes"
                 + (" — by modality" if by_modality else ""))
    if by_modality:
        ax.legend(fontsize=7, loc="lower left", title="modality")
    p = os.path.join(outdir, "multitarget_2x2.png")
    fig.tight_layout(); fig.savefig(p, dpi=150); plt.close(fig)
    print(f"    [out] {p}", file=sys.stderr)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Multi-target deterministic white-space 2x2")
    ap.add_argument("--ip-csv", default=DEF_IP_CSV)
    ap.add_argument("--clin-csv", default=DEF_CLIN_CSV)
    ap.add_argument("--key-col", default=DEF_KEY_COL, help="harmonized join column (verbatim match)")
    ap.add_argument("--modality-col", default=DEF_MOD_COL)
    ap.add_argument("--by-modality", action="store_true",
                    help="one node per (target x modality) instead of per target")
    ap.add_argument("--sample", type=int, default=0, help="random N nodes (0=all)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--as-of", default=DEF_AS_OF)
    ap.add_argument("--indication", default=DEF_INDICATION)
    ap.add_argument("--outdir", default=DEF_OUTDIR)
    return ap.parse_args(argv)


if __name__ == "__main__":
    a = _parse_args()
    terms = [t.strip().lower() for t in a.indication.split(",") if t.strip()]
    run(a.ip_csv, a.clin_csv, a.as_of, terms, a.outdir, a.key_col, a.modality_col,
        a.by_modality, a.sample, a.seed)
