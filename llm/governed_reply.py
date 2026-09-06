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

Structured tool calls (`proposed_tool_call`) plug into `core.pa_action_kernel`
the same way: `classify_tier()` decides a tier from tool identity + surface
state, exactly as it does for the (still separate, autonomous-only) PA
Action Kernel path. But an adversarial test
(tests/test_classify_tier_adversarial.py::
test_registered_safe_tool_can_carry_unscanned_pricing_commitment_in_arguments)
proved that tool-identity classification alone is blind to what's actually
in `arguments` — a tool registered as an ordinary Tier B action can still
carry a pricing/contractual/legal commitment in its arguments text, and
`classify_tier()` has no way to see that. So the same deterministic scan
that covers free-form `output` text also runs over the JSON-serialized
`arguments`, and — this is the part that must never be weakened — that
scan is AUTHORITATIVE for escalation: if it (or self-report, or the tool's
own registry categories) finds a permanent category, the effective tier for
that tool call is forced to Tier C regardless of what `classify_tier()`
concluded from tool identity and surface state alone. Escalation only ever
raises a tier to C; nothing here is allowed to lower a tier `classify_tier()`
already set. `NullSurfaceMatcher` still always returns "no match" (no real
surface tracking exists in this mode yet), which by itself already forces
every tool call to Tier C per PA Action Kernel Section 2.4 — that fail-closed
behaviour is deliberate and must not be relaxed just to make this path feel
more permissive.

Executing a tool call is a separate, later milestone this module does not
touch: `proposed_tool_call` is classification-and-authorization metadata
carried on `GovernedTurnResult`, not a capability. Nothing here calls an
external API, and nothing should, until a real capability-execution
boundary exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from core.pa_action_kernel import build_action_request, classify_tier, effective_tool_tier
from core.permanent_category_scan import scan_for_permanent_categories
from llm.client import DEFAULT_MODEL, complete, strip_json_code_fence
from models.schemas import DecisionSurfaceDocument, PERMANENT_TIER_C_CATEGORIES, Tier


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
- "proposed_tool_call": if (and only if) you are proposing a concrete action
  be taken on the operator's behalf (not merely discussing one), give
  {{"tool_name": "<name>", "arguments": {{...}}}}. Otherwise set this to null.
  This is classified and audited independently of your prose — it is not
  executed by you saying so.

Respond with ONLY this JSON:
{{"reasoning": "<brief reasoning trace>", "output": "<your actual reply to
the operator>", "scope": "exploration|validation|finality",
"touched_categories": [...], "proposed_tool_call": {{"tool_name": "...",
"arguments": {{...}}}} or null}}"""


@dataclass
class GovernedTurnResult:
    reasoning: str
    output_text: str
    self_reported_scope: str
    self_reported_categories: frozenset[str]
    scanned_categories: frozenset[str]
    proposed_tool_call: Optional[dict] = None
    # classify_tier()'s own conclusion from tool identity + surface state
    # alone — never trusted in isolation, see tool_effective_tier below.
    tool_identity_tier: Optional[Tier] = None
    # Deterministic scan of the tool call's arguments text (JSON-serialized),
    # the fix for the gap test_registered_safe_tool_can_carry_unscanned_...
    # exposed: classify_tier() cannot see into arguments, only tool identity.
    tool_argument_categories: frozenset[str] = frozenset()

    @property
    def all_categories(self) -> frozenset[str]:
        return self.self_reported_categories | self.scanned_categories | self.tool_argument_categories

    @property
    def touches_permanent_tier_c(self) -> bool:
        return bool(self.all_categories & PERMANENT_TIER_C_CATEGORIES)

    @property
    def tool_effective_tier(self) -> Optional[Tier]:
        """The tier that actually governs a proposed tool call. See
        core.pa_action_kernel.effective_tool_tier for the shared arithmetic
        (also used by ALDRIC Mode's llm/aldric_reply.py) — escalation is
        one-directional and authoritative; this can only ever raise a tier
        to C, never lower one classify_tier() already set."""
        if self.proposed_tool_call is None:
            return None
        return effective_tool_tier(self.tool_identity_tier, self.touches_permanent_tier_c)

    @property
    def requires_adjudication(self) -> bool:
        """Finality-scope claims, anything touching a permanent category, and
        any proposed tool call whose effective tier is C all require the
        Adjudication Buffer. Ordinary exploration/validation replies with no
        tool call and no permanent category do not — Scope Taxonomy (KSP-1
        Section 2): 'Finality requires KSP activation,' not every
        utterance."""
        return (
            self.self_reported_scope == "finality"
            or self.touches_permanent_tier_c
            or self.tool_effective_tier == Tier.C
        )


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
    # See llm/dsd_interview.py's run_interview_step for why this is higher
    # than the "no thinking" size it was originally written for: max_tokens
    # caps thinking + text together, and DEFAULT_MODEL thinks by default.
    raw = complete(system=system, user_message=full_user_message, model=DEFAULT_MODEL, max_tokens=4000)

    try:
        parsed = json.loads(strip_json_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Governed turn returned non-JSON output: {raw!r}") from exc

    output_text = parsed.get("output", "")
    self_reported = frozenset(
        c for c in parsed.get("touched_categories", []) if c in PERMANENT_TIER_C_CATEGORIES
    )
    scanned = scan_for_permanent_categories(output_text)

    proposed_tool_call = parsed.get("proposed_tool_call") or None
    tool_identity_tier: Optional[Tier] = None
    tool_argument_categories: frozenset[str] = frozenset()
    if isinstance(proposed_tool_call, dict) and proposed_tool_call.get("tool_name"):
        arguments = proposed_tool_call.get("arguments") or {}
        action = build_action_request(tool_name=proposed_tool_call["tool_name"], arguments=arguments)
        # surface=None: NullSurfaceMatcher's stub is honest about not doing
        # real surface matching yet, and PA Action Kernel 2.4 says "no
        # match -> Tier C" for exactly this reason. Do not pass a real
        # Surface here until real surface tracking exists in this mode.
        decision = classify_tier(action, surface=None, drift_level=None, mirror_drift_flagged=False)
        tool_identity_tier = decision.tier
        tool_argument_categories = scan_for_permanent_categories(json.dumps(arguments, default=str))
    else:
        proposed_tool_call = None

    return GovernedTurnResult(
        reasoning=parsed.get("reasoning", ""),
        output_text=output_text,
        self_reported_scope=parsed.get("scope", "exploration"),
        self_reported_categories=self_reported,
        scanned_categories=scanned,
        proposed_tool_call=proposed_tool_call,
        tool_identity_tier=tool_identity_tier,
        tool_argument_categories=tool_argument_categories,
    )
