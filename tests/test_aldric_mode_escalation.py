"""
Tests for core.aldric_mode — the deterministic gate ALDRIC Mode uses to
decide, on every casual turn, whether it must stop being casual conversation
and open a real Decision Surface. Deliberately the same shape as
tests/test_tool_argument_tier_escalation.py's Layer 1 (which tests the
identical decision for Governance Stack Mode's GovernedTurnResult) — this
file proves core.aldric_mode.requires_escalation implements the same rule
for the mode that doesn't start with a DSD already locked.
"""
from core.aldric_mode import EscalationSignal, requires_escalation
from models.schemas import Tier


def _signal(
    scope="exploration",
    self_reported_categories=frozenset(),
    scanned_categories=frozenset(),
    tool_identity_tier=None,
    tool_argument_categories=frozenset(),
    has_proposed_tool_call=False,
) -> EscalationSignal:
    return EscalationSignal(
        self_reported_scope=scope,
        self_reported_categories=self_reported_categories,
        scanned_categories=scanned_categories,
        tool_identity_tier=tool_identity_tier,
        tool_argument_categories=tool_argument_categories,
        has_proposed_tool_call=has_proposed_tool_call,
    )


def test_ordinary_exploration_turn_does_not_escalate():
    assert requires_escalation(_signal(scope="exploration")) is False


def test_validation_scope_alone_does_not_escalate():
    assert requires_escalation(_signal(scope="validation")) is False


def test_self_reported_finality_scope_escalates():
    assert requires_escalation(_signal(scope="finality")) is True


def test_scanned_permanent_category_in_output_text_escalates_even_if_self_report_says_exploration():
    signal = _signal(scope="exploration", scanned_categories=frozenset({"pricing_or_cost_commitment"}))
    assert signal.touches_permanent_tier_c is True
    assert requires_escalation(signal) is True


def test_self_reported_permanent_category_escalates():
    assert requires_escalation(_signal(scope="exploration", self_reported_categories=frozenset({"legal_matter"}))) is True


def test_proposed_tool_call_with_no_surface_match_escalates():
    """Every proposed tool call is Tier C today via NullSurfaceMatcher's
    fail-closed 'no surface match' default (PA Action Kernel Section 2.4) —
    proves ALDRIC Mode inherits that fail-closed behaviour, exactly as
    Governance Stack Mode does, not a weaker version of it."""
    signal = _signal(has_proposed_tool_call=True, tool_identity_tier=Tier.C)
    assert signal.tool_effective_tier == Tier.C
    assert requires_escalation(signal) is True


def test_benign_tool_call_at_a_future_tier_a_does_not_escalate():
    """Simulates the future state once a real surface matcher exists and a
    tool call can actually reach Tier A — proves escalation isn't hard-wired
    to fire just because a tool call exists, only because of what tier it
    resolves to."""
    signal = _signal(has_proposed_tool_call=True, tool_identity_tier=Tier.A)
    assert signal.tool_effective_tier == Tier.A
    assert requires_escalation(signal) is False


def test_pricing_commitment_in_tool_arguments_escalates_a_future_tier_b_tool():
    signal = _signal(
        has_proposed_tool_call=True,
        tool_identity_tier=Tier.B,
        tool_argument_categories=frozenset({"pricing_or_cost_commitment"}),
    )
    assert signal.tool_effective_tier == Tier.C
    assert requires_escalation(signal) is True


def test_no_tool_call_no_categories_no_finality_never_escalates():
    signal = _signal()
    assert signal.tool_effective_tier is None
    assert requires_escalation(signal) is False


def test_escalation_only_ever_raises_never_lowers_a_tool_tier_classify_tier_already_set():
    signal = _signal(has_proposed_tool_call=True, tool_identity_tier=Tier.C, tool_argument_categories=frozenset())
    assert signal.tool_effective_tier == Tier.C
