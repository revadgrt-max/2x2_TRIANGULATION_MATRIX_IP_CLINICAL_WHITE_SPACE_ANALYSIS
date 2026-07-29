"""Loads the Phase-0 trimmed, app-ready CSVs (built by input/app_data_analytics.ipynb) and derives the
same helper columns / known-vocab used by the Phase-1 notebook's validated tool functions. Ported
verbatim from that notebook so the web app's answers match the local prototype exactly."""
import re
from pathlib import Path

import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"

IP_LANDSCAPE_CSV = DATA_DIR / "ip_landscape_app.csv"
IP_WHITESPACE_CSV = DATA_DIR / "ip_whitespace_app.csv"
CLINICAL_CSV = DATA_DIR / "clinical_app.csv"

_ROMAN_PHASE = {"IV": 4, "III": 3, "II": 2, "I": 1}

# PCT application numbers (e.g. "PCT/US2010/026300") carry a receiving-office code that indicates where
# the applicant filed -- almost always the applicant/sponsor's own home country's patent office. This is
# a much more meaningful "source country" than `authority`, which is ~100% "WO" for these international
# publications and carries no per-country signal at all.
_RECEIVING_OFFICE_TO_COUNTRY = {
    "US": "United States", "CN": "China", "JP": "Japan", "KR": "South Korea", "GB": "United Kingdom",
    "CA": "Canada", "IL": "Israel", "NL": "Netherlands", "AU": "Australia", "SG": "Singapore",
    "RU": "Russia", "DK": "Denmark", "SE": "Sweden", "FI": "Finland", "TR": "Turkey", "FR": "France",
    "BR": "Brazil", "CU": "Cuba", "ES": "Spain", "IN": "India", "PT": "Portugal", "IT": "Italy",
    "PL": "Poland", "CZ": "Czech Republic", "CL": "Chile", "CH": "Switzerland", "NZ": "New Zealand",
    "DE": "Germany", "BE": "Belgium", "AT": "Austria", "IE": "Ireland", "NO": "Norway", "HU": "Hungary",
    "EP": "Europe (EPO regional filing)", "IB": "International (WIPO direct filing, no single country)",
    "EA": "Eurasia (EAPO regional filing)",
}


def _receiving_office_country(application_number) -> str | None:
    if pd.isna(application_number):
        return None
    m = re.match(r"PCT/([A-Z]{2})", str(application_number))
    if not m:
        return None
    return _RECEIVING_OFFICE_TO_COUNTRY.get(m.group(1), m.group(1))


def canonicalize_phase(raw) -> str:
    """Best-effort furthest-phase canonicalization -> PHASE1/2/3/4, EARLY_PHASE1, or UNKNOWN."""
    if pd.isna(raw) or not str(raw).strip():
        return "UNKNOWN"
    s = str(raw).upper()
    if "EARLY_PHASE1" in s or "EARLY PHASE 1" in s or "EARLY PHASE1" in s:
        return "EARLY_PHASE1"
    nums = set(int(n) for n in re.findall(r"PHASE\s*-?\s*([1-4])", s))
    for roman, yn in re.findall(r"\(?PHASE\s*([IV]{1,3})\)?\s*:?\s*(YES|NO)", s):
        if yn == "YES" and roman in _ROMAN_PHASE:
            nums.add(_ROMAN_PHASE[roman])
    if not nums:
        if s.strip() in {"1", "2", "3", "4"}:
            nums.add(int(s.strip()))
        else:
            for roman, val in _ROMAN_PHASE.items():
                if re.search(rf"\b{roman}\b", s):
                    nums.add(val)
                    break
    return f"PHASE{max(nums)}" if nums else "UNKNOWN"


def load_data():
    """Returns (ip_landscape_df, ip_whitespace_df, clinical_df, KNOWN_VOCAB)."""
    ip_landscape_df = pd.read_csv(IP_LANDSCAPE_CSV, low_memory=False)
    ip_whitespace_df = pd.read_csv(IP_WHITESPACE_CSV, low_memory=False)
    clinical_df = pd.read_csv(CLINICAL_CSV, low_memory=False)

    ip_landscape_df["primary_assignee"] = (
        ip_landscape_df["current_assignee"].fillna("").str.split("|").str[0].str.strip().replace("", None)
    )
    ip_whitespace_df["primary_assignee"] = (
        ip_whitespace_df["current_assignee"].fillna("").str.split("|").str[0].str.strip().replace("", None)
    )
    ip_landscape_df["source_country"] = ip_landscape_df["application_number"].apply(_receiving_office_country)
    ip_whitespace_df["source_country"] = ip_whitespace_df["application_number"].apply(_receiving_office_country)
    clinical_df["phase_group"] = clinical_df["phase"].apply(canonicalize_phase)

    known_vocab = {
        "ip_modality_code": sorted(ip_landscape_df["modality_code"].dropna().unique().tolist()),
        "ip_authority": sorted(ip_landscape_df["authority"].dropna().unique().tolist()),
        "clinical_modality_code": sorted(clinical_df["modality_code"].dropna().unique().tolist()),
        "clinical_outcome": sorted(clinical_df["outcome"].dropna().unique().tolist()),
        "clinical_phase": sorted(clinical_df["phase"].dropna().unique().tolist()),
        "clinical_phase_group": sorted(clinical_df["phase_group"].unique().tolist()),
    }
    return ip_landscape_df, ip_whitespace_df, clinical_df, known_vocab
