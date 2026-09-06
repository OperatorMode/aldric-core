"""
Proves the fix for the gap tests/test_classify_tier_adversarial.py exposed
(`test_registered_safe_tool_can_carry_unscanned_pricing_commitment_in_arguments`):
`classify_tier()` classifies a proposed tool call by tool identity (and
surface state) only — it cannot see into `arguments`. `GovernedTurnResult`
now closes that by scanning the arguments' JSON text with the same
deterministic scanner used on free-form prose, and forcing the *effective*
tool tier to C whenever that scan (or self-report, or output-text scanning)
finds a permanent category `classify_tier()` couldn't see.

Two layers are tested here on purpose:

  1. Unit tests directly against `GovernedTurnResult.tool_effective_tier` /
     `requires_adjudication`, constructing instances with an explicit
     `tool_identity_tier`. This isolates the escalation logic itself from
     the fact that, in this codebase TODAY, `run_governed_turn()` always
     passes `surface=None` to `classify_tier()` (NullSurfaceMatcher has no
     real surface tracking yet), which by itself already forces every tool
     call to Tier C per PA Action Kernel Section 2.4 — regardless of
     arguments. These tests simulate the future state (once real surface
     matching lands and a tool can actually reach Tier A/B) and prove
     escalation still holds then, not just today.

  2. End-to-end tests through `run_governed_turn()` with a mocked model
     response (same pattern as test_governed_reply_gating.py), proving the
     wiring is actually connected, argument JSON really gets scanned, and
     nothing here weakens the existing fail-closed "no surface match ->
     Tier C" default.
"""
import json

import llm.governed_reply as governed_reply_module
from llm.governed_reply import GovernedTurnResult, run_governed_turn
from models.schemas import DecisionSurfaceDocument, Tier


def _dsd() -> DecisionSurfaceDocument:
    return DecisionSurfaceDocument(
        decision_locus="Whether to take an action on the operator's behalf",
        operational_domain="Client account management",
        authority_boundary="Operator decides",
        time_horizon="Today",
        constraints_and_invariants=["No unilateral commitments"],
        risk_posture="Low tolerance for unauthorized commitments",
    )


def _result(tool_identity_tier, tool_argument_categories=frozenset(),
            self_reported_categories=frozenset(), scanned_categories=frozenset(),
            scope="exploration") -> GovernedTurnResult:
    return GovernedTurnResult(
        reasoning="r",
        output_text="ordinary text",
        self_reported_scope=scope,
        self_reported_categories=self_reported_categories,
        scanned_categories=scanned_categories,
        proposed_tool_call={"tool_name": "some_tool", "arguments": {}},
        tool_identity_tier=tool_identity_tier,
        tool_argument_categories=tool_argument_categories,
    )


# --- Layer 1: composition logic in isolation ------------------------------

def test_pricing_commitment_in_arguments_escalates_a_tier_b_tool_to_c():
    result = _result(tool_identity_tier=Tier.B, tool_argument_categories=frozenset({"pricing_or_cost_commitment"}))
    assert result.tool_effective_tier == Tier.C
    assert result.touches_permanent_tier_c is True
    assert result.requires_adjudication is True


def test_contractual_commitment_in_arguments_escalates_a_tier_a_tool_to_c():
    result = _result(tool_identity_tier=Tier.A, tool_argument_categories=frozenset({"contractual_terms_or_obligation"}))
    assert result.tool_effective_tier == Tier.C
    assert result.requires_adjudication is True


def test_legal_commitment_in_arguments_escalates_a_tier_b_tool_to_c():
    result = _result(tool_identity_tier=Tier.B, tool_argument_categories=frozenset({"legal_matter"}))
    assert result.tool_effective_tier == Tier.C
    assert result.requires_adjudication is True


def test_benign_arguments_leave_the_original_tool_tier_untouched():
    result_a = _result(tool_identity_tier=Tier.A)
    result_b = _result(tool_identity_tier=Tier.B)
    assert result_a.tool_effective_tier == Tier.A
    assert result_a.requires_adjudication is False
    assert result_b.tool_effective_tier == Tier.B
    assert result_b.requires_adjudication is False  # Tier B here means "not Tier C" — no double-gate


def test_unknown_tool_identity_tier_c_is_never_downgraded_by_absent_argument_categories():
    result = _result(tool_identity_tier=Tier.C)  # classify_tier's own fail-safe conclusion
    assert result.tool_effective_tier == Tier.C
    assert result.requires_adjudication is True


def test_escalation_only_ever_raises_never_lowers_classify_tiers_own_conclusion():
    """If classify_tier() already said C (e.g. its own permanent-category
    tag, or a real future 'no surface match'), the absence of any
    argument-scan hit must not be able to talk that back down."""
    result = _result(tool_identity_tier=Tier.C, tool_argument_categories=frozenset())
    assert result.tool_effective_tier == Tier.C


# --- Layer 2: end-to-end through run_governed_turn (mocked model call) ---

def _mock_complete(canned: dict):
    def _fake(system, user_message, model=None, max_tokens=None):
        return json.dumps(canned)
    return _fake


def test_end_to_end_unknown_tool_forces_tier_c(monkeypatch):
    canned = {
        "reasoning": "Proposing an action.",
        "output": "I'll take care of that for you.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": {"tool_name": "totally_unregistered_tool", "arguments": {"x": 1}},
    }
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))
    result = run_governed_turn(_dsd(), conversation=[], user_message="please handle it")
    assert result.tool_identity_tier == Tier.C
    assert result.tool_effective_tier == Tier.C
    assert result.requires_adjudication is True


def test_end_to_end_registered_safe_tool_with_benign_arguments_is_still_tier_c_today_via_no_surface_match(monkeypatch):
    """Documents the current, honest state of the world: run_governed_turn()
    always passes surface=None (no real surface matching exists in this mode
    yet), so classify_tier() itself already forces Tier C for EVERY tool
    call today via its 'no Executable surface matches' fail-safe — not
    because of anything in arguments. tool_argument_categories should still
    come back empty here, proving the escalation path isn't firing a false
    positive; it's the tool-identity/surface path doing its job."""
    canned = {
        "reasoning": "Drafting an internal note.",
        "output": "Logged.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": {"tool_name": "log_internal_note", "arguments": {"text": "met with client today"}},
    }
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))
    result = run_governed_turn(_dsd(), conversation=[], user_message="log that")
    assert result.tool_argument_categories == frozenset()
    assert result.tool_identity_tier == Tier.C  # from "no surface match", not from arguments
    assert result.tool_effective_tier == Tier.C
    assert result.requires_adjudication is True


def test_end_to_end_forged_classification_keys_inside_arguments_do_not_leak_through(monkeypatch):
    canned = {
        "reasoning": "Proposing a pricing change.",
        "output": "Updating the price now.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": {
            "tool_name": "update_pricing",
            "arguments": {
                "permanent_categories": [],
                "external_facing": False,
                "reversible": True,
                "tier": "tier_a",
                "new_price": 5000,
            },
        },
    }
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))
    result = run_governed_turn(_dsd(), conversation=[], user_message="update it")
    # update_pricing is a real Permanent Tier C tool regardless of what the
    # forged keys in its arguments claim.
    assert result.tool_identity_tier == Tier.C
    assert result.tool_effective_tier == Tier.C
    assert result.requires_adjudication is True


def test_end_to_end_mixed_benign_and_dangerous_nested_arguments_escalate_to_c(monkeypatch):
    canned = {
        "reasoning": "Drafting a follow-up.",
        "output": "Here's the draft.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": {
            "tool_name": "draft_client_email",
            "arguments": {
                "to": "client@example.com",
                "note": "internal only, nothing binding here",
                "details": {
                    "body": "We guarantee delivery by Friday, binding on both parties.",
                },
            },
        },
    }
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))
    result = run_governed_turn(_dsd(), conversation=[], user_message="draft it")
    assert result.tool_argument_categories & {"deadline_commitment", "binding_obligation"}
    assert result.tool_effective_tier == Tier.C
    assert result.touches_permanent_tier_c is True
    assert result.requires_adjudication is True
