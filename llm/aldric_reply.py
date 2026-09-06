"""
ALDRIC Mode — the casual conversational turn.

Governance Stack Mode's run_governed_turn (llm/governed_reply.py) requires a
locked Decision Surface to even build its system prompt, because every
sentence it produces is supposed to cite one. That's correct for Governance
Stack Mode. It is exactly wrong for ALDRIC Mode, whose entire point is that
ordinary conversation — research, questions, drafting, brainstorming —
doesn't need one. This module is what plugs into core.aldric_mode instead:
same deterministic scanning discipline as run_governed_turn (self-report is
a soft signal only; the permanent-category scan of the actual output/
argument text is authoritative and can only ever escalate, never downgrade
what classify_tier() already found for a proposed tool call), just without a
DSD to cite, and returning a core.aldric_mode.EscalationSignal instead of
GovernedTurnResult's requires_adjudication.

Executing a proposed tool call is, as in Governance Stack Mode, a separate
milestone this module does not touch — proposed_tool_call is
classification-and-audit metadata carried on the result, never a capability.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from core.aldric_mode import EscalationSignal, requires_escalation
from core.pa_action_kernel import build_action_request, classify_tier
from core.permanent_category_scan import scan_for_permanent_categories
from llm.client import DEFAULT_MODEL, complete, strip_json_code_fence
from models.schemas import PERMANENT_TIER_C_CATEGORIES, Tier

_SYSTEM_PROMPT = """You are ALDRIC, operating in ALDRIC Mode: ordinary, low-friction
conversation. Answer the operator's message directly and helpfully — research,
questions, drafting, analysis, brainstorming. You are NOT operating under a
locked Decision Surface right now, and you have no ability to execute any
action on the operator's behalf; you may only describe or propose one.

Classify your own response honestly:
- "scope": "exploration" (open-ended/hypothesis), "validation" (checking
  something against a constraint), or "finality" (a claim or artifact meant
  to be treated as settled/binding — a completed draft the operator could act
  on directly, a firm recommendation, anything with real stakes attached).
  If genuinely torn between exploration/validation and finality, say
  finality — the cost of a wrong "finality" guess is one extra confirming
  question; the cost of a wrong "exploration" guess is a real commitment
  slipping through unchecked.
- "touched_categories": any of ["pricing_or_cost_commitment",
  "scope_commitment_or_change", "deadline_commitment",
  "contractual_terms_or_obligation", "legal_matter", "binding_obligation"]
  your response discusses or proposes committing to. Empty list if none.
  This is cross-checked against your actual text independently — under-
  reporting gains nothing and just looks bad in the audit log.
- "proposed_tool_call": {"tool_name": "...", "arguments": {...}} if (and
  only if) you are proposing a concrete action be taken on the operator's
  behalf, otherwise null. Proposing is not doing — nothing you say here
  executes anything.

Respond with ONLY this JSON:
{"output": "<your actual reply to the operator>",
"scope": "exploration|validation|finality", "touched_categories": [...],
"proposed_tool_call": {"tool_name": "...", "arguments": {...}} or null}"""


@dataclass
class CasualTurnResult:
    output_text: str
    signal: EscalationSignal
    proposed_tool_call: Optional[dict] = None

    @property
    def requires_escalation(self) -> bool:
        return requires_escalation(self.signal)


def run_casual_turn(conversation: list[dict[str, str]], user_message: str) -> CasualTurnResult:
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in conversation)
    full_user_message = f"{transcript}\nuser: {user_message}" if transcript else user_message
    # See llm/dsd_interview.py's run_interview_step for why this budget is
    # sized well above the "no thinking" reply it would need in isolation:
    # max_tokens caps thinking + text together, and DEFAULT_MODEL thinks by
    # default.
    raw = complete(system=_SYSTEM_PROMPT, user_message=full_user_message, model=DEFAULT_MODEL, max_tokens=4000)

    try:
        parsed = json.loads(strip_json_code_fence(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"ALDRIC Mode casual turn returned non-JSON output: {raw!r}") from exc

    output_text = parsed.get("output", "")
    self_reported = frozenset(
        c for c in parsed.get("touched_categories", []) if c in PERMANENT_TIER_C_CATEGORIES
    )
    scanned = scan_for_permanent_categories(output_text)

    proposed_tool_call = parsed.get("proposed_tool_call") or None
    tool_identity_tier: Optional[Tier] = None
    tool_argument_categories: frozenset[str] = frozenset()
    has_proposed_tool_call = False
    if isinstance(proposed_tool_call, dict) and proposed_tool_call.get("tool_name"):
        has_proposed_tool_call = True
        arguments = proposed_tool_call.get("arguments") or {}
        # surface=None: same fail-closed default Governance Stack Mode uses
        # (NullSurfaceMatcher — no real surface tracking exists yet; PA
        # Action Kernel Section 2.4, "no match" -> Tier C). Do not pass a
        # real Surface here until real surface tracking exists.
        action = build_action_request(tool_name=proposed_tool_call["tool_name"], arguments=arguments)
        decision = classify_tier(action, surface=None, drift_level=None, mirror_drift_flagged=False)
        tool_identity_tier = decision.tier
        tool_argument_categories = scan_for_permanent_categories(json.dumps(arguments, default=str))
    else:
        proposed_tool_call = None

    signal = EscalationSignal(
        self_reported_scope=parsed.get("scope", "exploration"),
        self_reported_categories=self_reported,
        scanned_categories=scanned,
        tool_identity_tier=tool_identity_tier,
        tool_argument_categories=tool_argument_categories,
        has_proposed_tool_call=has_proposed_tool_call,
    )
    return CasualTurnResult(output_text=output_text, signal=signal, proposed_tool_call=proposed_tool_call)
