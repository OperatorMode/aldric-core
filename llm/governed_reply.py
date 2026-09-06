"""
One governed conversation turn — Governance Stack Mode, post-DSD-lock.

This is the module Gemini's proposal skipped straight past: it called
Claude with a system prompt and showed the reply directly (or a canned
placeholder), with no path from "the model said something" to "the model's
own claim about what it said is being trusted for a safety-relevant
decision." Here, the model's self-reported `scope` and `touched_categories`
are used only as a soft triage signal for whether to bother opening an
Adjudication at all in borderline cases. The actual gating decision —
whether this artifact requires the Tier C double confirmation — is the
UNION of that self-report with `core.permanent_category_scan`, a
deterministic scan of the text the model actually produced. A model that
under-reports (deliberately or by drift) does not get to suppress the scan;
a model that over-reports just costs the operator one extra confirmation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from core.permanent_category_scan import scan_for_permanent_categories
from llm.client import DEFAULT_MODEL, complete, strip_json_code_fence
from models.schemas import DecisionSurfaceDocument, PERMANENT_TIER_C_CATEGORIES


_SYSTEM_TEMPLATE = """You are ALDRIC operating in Governance Stack Mode, reasoning for the
operator under a locked Decision Surface. Every response you give must cite
this Decision Surface — you are not machining claims in a vacuum.

Decision Surface (locked, immutable this session):
- Decision Locus: {decision_locus}
- Operational Domain: {operational_domain}
- Authority Boundary: {authority_boundary}
- Time Horizon: {time_horizon}
- Constraints & Invariants: {constraints}
- Risk Posture: {risk_posture}

{profile_fragment}

Respond to the operator's message. Classify your own response honestly:
- "scope": "exploration" (hypothesis/open-ended), "validation" (constraint
  checking), or "finality" (a claim or artifact intended to be treated as
  settled/binding, e.g. a completed draft, a final recommendation, anything
  the operator could reasonably act on directly).
- "touched_categories": any of ["pricing_or_cost_commitment",
  "scope_commitment_or_change", "deadline_commitment",
  "contractual_terms_or_obligation", "legal_matter", "binding_obligation"]
  that your response discusses or proposes committing to. Empty list if none.
  Report this honestly — it is cross-checked against your actual text
  independently, so under-reporting gains nothing and just looks bad in the
  audit log.

Respond with ONLY this JSON:
{{"reasoning": "<brief reasoning trace>", "output": "<your actual reply to
the operator>", "scope": "exploration|validation|finality",
"touched_categories": [...]}}"""


@dataclass
class GovernedTurnResult:
    reasoning: str
    output_text: str
    self_reported_scope: str
    self_reported_categories: frozenset[str]
    scanned_categories: frozenset[str]

    @property
    def touches_permanent_tier_c(self) -> bool:
        return bool(self.all_categories & PERMANENT_TIER_C_CATEGORIES)

    @property
    def all_categories(self) -> frozenset[str]:
        return self.self_reported_categories | self.scanned_categories

    @property
    def requires_adjudication(self) -> bool:
        """Finality-scope claims and anything touching a permanent category
        both require the Adjudication Buffer. Ordinary exploration/
        validation replies that don't touch a permanent category do not —
        Scope Taxonomy (KSP-1 Section 2): 'Finality requires KSP
        activation,' not every utterance."""
        return self.self_reported_scope == "finality" or self.touches_permanent_tier_c


def run_governed_turn(
    dsd: DecisionSurfaceDocument,
    conversation: list[dict[str, str]],
    user_message: str,
    profile_fragment: str = "",
) -> GovernedTurnResult:
    system = _SYSTEM_TEMPLATE.format(
        decision_locus=dsd.decision_locus,
        operational_domain=dsd.operational_domain,
        authority_boundary=dsd.authority_boundary,
        time_horizon=dsd.time_horizon,
        constraints=", ".join(dsd.constraints_and_invariants),
        risk_posture=dsd.risk_posture,
        profile_fragment=profile_fragment,
    )
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in conversation)
    full_user_message = f"{transcript}\nuser: {user_message}" if transcript else user_message
    raw = complete(system=system, user_message=full_user_message, model=DEFAULT_MODEL, max_tokens=1500)

    try:
        parsed = json.loads(strip_json_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Governed turn returned non-JSON output: {raw!r}") from exc

    output_text = parsed.get("output", "")
    self_reported = frozenset(
        c for c in parsed.get("touched_categories", []) if c in PERMANENT_TIER_C_CATEGORIES
    )
    scanned = scan_for_permanent_categories(output_text)

    return GovernedTurnResult(
        reasoning=parsed.get("reasoning", ""),
        output_text=output_text,
        self_reported_scope=parsed.get("scope", "exploration"),
        self_reported_categories=self_reported,
        scanned_categories=scanned,
    )
