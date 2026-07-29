"""
epitope.py — the epitope-crowding layer, split into DETERMINISTIC and LLM parts.

The whole point of this file: an epitope_open=True/False call may ONLY reach
core.score() if it is backed by VERBATIM claim text. A call with no cited claim
is rejected by the deterministic gate and forced to None ("abstain / layer
skipped"). This makes "epitope-closed without reading the patents" structurally
impossible — the failure mode that prompted this module.

  judge_epitope_crowding()  -> LLM REASONING (non-deterministic). Reads claim
                               text, returns a verdict that MUST cite claims.
  verified_epitope_flag()   -> DETERMINISTIC gate. Converts a verdict into the
                               (epitope_open, epitope_confidence) pair core.score
                               accepts — or abstains. Same input -> same output.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable
import json


# ===========================================================================
# Data contracts
# ===========================================================================
@dataclass(frozen=True)
class ClaimEvidence:
    """One cited claim. `quote` is verbatim text the model was actually given —
    no quote, no evidence, no flag."""
    patent_number: str
    quote: str
    reading: str          # e.g. "genus", "competition", "cdr-specific", "isoform-selective"


@dataclass(frozen=True)
class EpitopeVerdict:
    open: Optional[bool]              # True (room remains) / False (locked) / None (can't tell)
    confidence: float                 # 0..1
    rationale: str
    evidence: tuple[ClaimEvidence, ...] = field(default_factory=tuple)


# ===========================================================================
# DETERMINISTIC gate  — same input -> same output, every run
# ===========================================================================
PROVENANCE_MIN = 1   # at least one cited claim required to assert any non-None flag


def verified_epitope_flag(v: EpitopeVerdict) -> tuple[Optional[bool], float]:
    """
    Turn an LLM verdict into the (epitope_open, epitope_confidence) pair that
    core.score() accepts — but ONLY if it carries claim provenance.

      - open is None                       -> abstain (None, 0.0)
      - open is True/False but no evidence -> REJECT the unbacked guess -> abstain
      - open is True/False with evidence   -> pass through, confidence clamped 0..1

    This is the anti-fabrication guarantee: a flag without claim text cannot
    enter the score. A language instruction is a request; this is a guarantee.
    """
    if v.open is None:
        return None, 0.0
    if len(v.evidence) < PROVENANCE_MIN:
        # asserted "open" or "closed" without citing a single claim -> not allowed
        return None, 0.0
    return v.open, max(0.0, min(1.0, float(v.confidence)))


# ===========================================================================
# LLM REASONING  — non-deterministic; isolated behind a single function
# ===========================================================================
EPITOPE_TOOL = {
    "name": "report_epitope_verdict",
    "description": "Report whether an unclaimed antibody binding region plausibly remains, grounded ONLY in the supplied claim text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "open": {"type": ["boolean", "null"],
                     "description": "True if an unclaimed binding region plausibly remains; False if the binding face is locked by broad/competition claims; null if the supplied claims are insufficient to tell."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "patent_number": {"type": "string"},
                        "quote": {"type": "string", "description": "verbatim snippet from the supplied claims"},
                        "reading": {"type": "string"}
                    },
                    "required": ["patent_number", "quote", "reading"]
                },
                "description": "Claims that justify the call. MUST be non-empty unless open=null."
            }
        },
        "required": ["open", "confidence", "rationale", "evidence"]
    }
}

_SYSTEM = (
    "You are a patent analyst judging EPITOPE crowding for an antibody target. "
    "You are given the actual claim text of representative patents. Decide whether an "
    "unclaimed binding region plausibly remains (open=True), the binding face is locked "
    "by broad genus/competition claims (open=False), or the supplied claims are "
    "insufficient (open=null). Rules: (1) Ground every True/False call in verbatim quotes "
    "from the supplied claims via the evidence array. (2) If you were given no claims, or "
    "they don't speak to the binding region, you MUST return open=null with empty evidence "
    "— never guess. (3) A broad 'antibody that competes for binding with reference mAb X' "
    "or 'any antibody that binds <target>' claim locks a bin (evidence of closure); narrow "
    "CDR-sequence or isoform-selective claims leave room (evidence of openness)."
)


def judge_epitope_crowding(
    target: str,
    claim_records: list[dict],
    *,
    model: str = "claude-opus-4-8",
    client: "Callable | None" = None,
) -> EpitopeVerdict:
    """
    LLM reasoning step. `claim_records` is a list of {"patent_number","claims_text"}
    pulled from PatSnap. If it is EMPTY, we short-circuit to an abstention WITHOUT
    calling the model — you cannot read claims you never pulled.

    `client` is an anthropic.Anthropic() instance (injected for testability). When
    None, the function still refuses to invent a flag: it returns open=None.
    """
    if not claim_records:
        return EpitopeVerdict(open=None, confidence=0.0,
                              rationale=f"No claims pulled for {target}; epitope layer abstains (None).")
    if client is None:
        return EpitopeVerdict(open=None, confidence=0.0,
                              rationale=f"No LLM client supplied for {target}; refusing to guess (None).")

    payload = "\n\n".join(
        f"=== {r['patent_number']} ===\n{r['claims_text'][:6000]}" for r in claim_records
    )
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM,
        tools=[EPITOPE_TOOL],
        tool_choice={"type": "tool", "name": "report_epitope_verdict"},
        messages=[{"role": "user",
                   "content": f"Target: {target}\n\nRepresentative claim text:\n{payload}"}],
    )
    block = next(b for b in msg.content if getattr(b, "type", None) == "tool_use")
    out = block.input
    ev = tuple(ClaimEvidence(**e) for e in out.get("evidence", []))
    return EpitopeVerdict(open=out["open"], confidence=float(out["confidence"]),
                          rationale=out["rationale"], evidence=ev)
