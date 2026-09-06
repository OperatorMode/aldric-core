"""
Deterministic free-text scan for the six Permanent Tier C categories.

`core/pa_action_kernel.py`'s TOOL_REGISTRY handles the case where an action
comes through a known, named tool — the category tags are looked up, not
inferred. Free-form conversational text (an assistant reply, a drafted
paragraph) has no tool name to look up. This module is the equivalent
fail-safe check for that case: a keyword/pattern scan over the text itself.

This is explicitly NOT a substitute for real semantic classification —
it will have false positives (a message that mentions "the contract" in
passing, not proposing one) and it can have false negatives (a permanent
commitment phrased in a way none of these patterns catch). The bias is
deliberate and matches the rest of this codebase's fail-safe posture:
prefer a false positive (one extra confirmation step) over a false
negative (a real commitment slipping through ungated). Combine this scan's
result with the model's own self-reported category flags via UNION, never
intersection — see llm/governed_reply.py.
"""
from __future__ import annotations

import re

_PATTERNS: dict[str, tuple[re.Pattern, ...]] = {
    "pricing_or_cost_commitment": tuple(re.compile(p, re.IGNORECASE) for p in [
        r"\$\s?\d",
        r"\bprice\b", r"\bpricing\b", r"\bcost\b", r"\bfee\b", r"\bquote\b",
        r"\bdiscount\b", r"\bretainer\b", r"\binvoice\b", r"\bbudget of\b",
    ]),
    "scope_commitment_or_change": tuple(re.compile(p, re.IGNORECASE) for p in [
        r"\bscope\b", r"\bdeliverables?\b", r"\bin[- ]scope\b", r"\bout[- ]of[- ]scope\b",
        r"\badd(?:itional)? (work|feature|deliverable)\b",
    ]),
    "deadline_commitment": tuple(re.compile(p, re.IGNORECASE) for p in [
        r"\bdeadline\b", r"\bdue date\b", r"\bby (monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\bby end of (day|week|month)\b", r"\bcommit to (a|the) date\b",
    ]),
    "contractual_terms_or_obligation": tuple(re.compile(p, re.IGNORECASE) for p in [
        r"\bcontract\b", r"\bagreement\b", r"\bterms and conditions\b", r"\bsign(ed|ing)?\b.{0,20}\b(contract|agreement)\b",
        r"\bsow\b", r"\bstatement of work\b",
    ]),
    "legal_matter": tuple(re.compile(p, re.IGNORECASE) for p in [
        r"\blegal\b", r"\bliabilit(y|ies)\b", r"\bcompliance\b", r"\blawsuit\b", r"\bindemnif",
        r"\bnda\b", r"\bnon-disclosure\b",
    ]),
    "binding_obligation": tuple(re.compile(p, re.IGNORECASE) for p in [
        r"\bguarantee\b", r"\bpromise\b", r"\bcommit(ted)? to\b", r"\bbinding\b", r"\bon behalf of\b",
    ]),
}


def scan_for_permanent_categories(text: str) -> frozenset[str]:
    """Returns the set of category tags whose patterns matched. Empty set
    means the scan found nothing — it does NOT mean the text is safe, only
    that this particular heuristic didn't flag it (see module docstring)."""
    hits = set()
    for category, patterns in _PATTERNS.items():
        if any(p.search(text) for p in patterns):
            hits.add(category)
    return frozenset(hits)
