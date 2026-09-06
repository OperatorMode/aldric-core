"""
Proves the union-not-intersection gating rule in llm/governed_reply.py: a
model that under-reports (says "exploration", reports no touched
categories) does not get to suppress adjudication if the deterministic
scan finds a permanent category in the text it actually produced.

The Anthropic call itself is mocked — these tests assert the gating logic
around a canned model response, not the model's actual behaviour.
"""
import json

import llm.governed_reply as governed_reply_module
from llm.governed_reply import run_governed_turn
from models.schemas import DecisionSurfaceDocument


def _dsd() -> DecisionSurfaceDocument:
    return DecisionSurfaceDocument(
        decision_locus="Whether to respond to a client pricing question",
        operational_domain="Client billing",
        authority_boundary="Operator decides",
        time_horizon="Today",
        constraints_and_invariants=["No unilateral discounts"],
        risk_posture="Low tolerance for pricing errors",
    )


def _mock_complete(canned_json: str):
    def _fake(system, user_message, model=None, max_tokens=None):
        return canned_json
    return _fake


def test_under_reported_pricing_still_forces_adjudication(monkeypatch):
    canned = json.dumps({
        "reasoning": "Just answering normally.",
        "output": "Sure — I'll set the price to $5,000 for that package.",
        "scope": "exploration",       # under-reported
        "touched_categories": [],      # under-reported
    })
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))

    result = run_governed_turn(_dsd(), conversation=[], user_message="What should we charge?")
    assert result.self_reported_scope == "exploration"
    assert result.self_reported_categories == frozenset()
    assert "pricing_or_cost_commitment" in result.scanned_categories
    assert result.touches_permanent_tier_c is True
    assert result.requires_adjudication is True


def test_ordinary_reply_does_not_require_adjudication(monkeypatch):
    canned = json.dumps({
        "reasoning": "General summary.",
        "output": "Here's a recap of the meeting notes from today.",
        "scope": "exploration",
        "touched_categories": [],
    })
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))

    result = run_governed_turn(_dsd(), conversation=[], user_message="Summarize the meeting.")
    assert result.requires_adjudication is False
    assert result.touches_permanent_tier_c is False


def test_self_reported_finality_alone_triggers_adjudication_without_a_permanent_category():
    canned = json.dumps({
        "reasoning": "Producing a final recommendation.",
        "output": "My final recommendation is to proceed with option B.",
        "scope": "finality",
        "touched_categories": [],
    })

    def _fake(system, user_message, model=None, max_tokens=None):
        return canned

    import llm.governed_reply as mod
    original = mod.complete
    mod.complete = _fake
    try:
        result = run_governed_turn(_dsd(), conversation=[], user_message="What should we do?")
    finally:
        mod.complete = original

    assert result.requires_adjudication is True
    assert result.touches_permanent_tier_c is False  # finality alone, no permanent category


def test_bogus_self_reported_category_outside_the_known_set_is_ignored(monkeypatch):
    canned = json.dumps({
        "reasoning": "r",
        "output": "Ordinary text with no flagged content.",
        "scope": "exploration",
        "touched_categories": ["made_up_category_the_model_invented"],
    })
    monkeypatch.setattr(governed_reply_module, "complete", _mock_complete(canned))

    result = run_governed_turn(_dsd(), conversation=[], user_message="hi")
    assert result.self_reported_categories == frozenset()
    assert result.requires_adjudication is False
