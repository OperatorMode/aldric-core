"""
End-to-end tests of llm.aldric_reply.run_casual_turn with the model call
mocked out — same pattern as tests/test_tool_argument_tier_escalation.py's
Layer 2 for Governance Stack Mode. Proves the wiring from a canned model
response through to a CasualTurnResult whose requires_escalation is driven
by the same deterministic gate tests/test_aldric_mode_escalation.py tests
directly, and that a truncated/invalid model response raises rather than
being silently guessed at.
"""
import json

import core.long_term_memory as long_term_memory
import llm.aldric_reply as aldric_reply_module
from llm.aldric_reply import run_casual_turn
from models.schemas import Tier


def _mock_complete(canned: dict):
    def _fake(system, user_message, model=None, max_tokens=None):
        return json.dumps(canned)
    return _fake


def _capturing_complete(canned: dict, captured: dict):
    """Like _mock_complete, but records the exact system/user_message the
    call received, so a test can assert on what actually reached the model —
    used to prove stored memory is really injected, not just fetched."""
    def _fake(system, user_message, model=None, max_tokens=None):
        captured["system"] = system
        captured["user_message"] = user_message
        return json.dumps(canned)
    return _fake


def test_ordinary_research_question_does_not_escalate(monkeypatch):
    canned = {
        "output": "Here's a comparison of the two vendors' feature sets.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": None,
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="compare these two vendors")
    assert result.requires_escalation is False
    assert "vendors" in result.output_text


def test_self_reported_finality_escalates(monkeypatch):
    canned = {
        "output": "Here's the final proposal, ready to send.",
        "scope": "finality",
        "touched_categories": [],
        "proposed_tool_call": None,
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="finalize the proposal")
    assert result.requires_escalation is True


def test_scanned_pricing_commitment_escalates_even_if_self_report_says_exploration(monkeypatch):
    """Under-reporting gains nothing: the deterministic scan of the actual
    output text is authoritative, exactly as in Governance Stack Mode."""
    canned = {
        "output": "Sure, we'll do it for $3,200.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": None,
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="what would we charge")
    assert result.signal.touches_permanent_tier_c is True
    assert result.requires_escalation is True


def test_proposed_tool_call_with_no_surfaces_on_record_escalates_via_no_match(monkeypatch):
    """The real surface matcher (llm/surface_matcher.py) is wired in now, but
    with nothing in storage to match against it still, correctly, never
    finds a match — and does so without a second model call at all (see
    tests/test_surface_matcher.py for that guarantee's own direct coverage).
    """
    canned = {
        "output": "I'll log that internally.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": {"tool_name": "log_internal_note", "arguments": {"text": "met with client"}},
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="log that")
    assert result.signal.tool_identity_tier == Tier.C  # no surfaces on record -> no match
    assert result.requires_escalation is True


def test_proposed_tool_call_matched_to_a_real_executable_surface_can_reach_a_lower_tier(monkeypatch):
    """Proves the matcher is actually consulted and its result actually
    reaches classify_tier() — not just that the no-match path still works."""
    import core.surface_signal as surface_signal
    surface_signal.record_instruction_or_correction(
        "client:acme", "Log internal notes about Acme freely.", had_prior_preference=False,
    )
    # One correction on record before confirming keeps this out of Mirror
    # Drift Indicator 1's territory (Section 4.2: confidence climbing with
    # *zero* corrections ever) — this test is about the matcher wiring, not
    # a second, accidental proof of the mirror-drift detector.
    surface_signal.record_instruction_or_correction(
        "client:acme", "Log internal notes about Acme freely, but skip vendor names.", had_prior_preference=True,
    )
    for _ in range(5):
        surface_signal.record_confirmation("client:acme")

    from core.learning_governance import confirm_execution_rights
    from storage import db as storage_db
    from models.schemas import Surface as SurfaceModel
    stored = storage_db.get_surface("client:acme")
    assert stored["mirror_drift_flagged"] is False  # sanity check on the setup above
    confirm_execution_rights(SurfaceModel(**stored))

    canned = {
        "output": "I'll log that internally.",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": {"tool_name": "log_internal_note", "arguments": {"text": "met with Acme"}},
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    monkeypatch.setattr(aldric_reply_module, "match_surface", lambda context, candidate_surfaces: next(
        (s for s in candidate_surfaces if s.surface_id == "client:acme"), None
    ))

    result = run_casual_turn(conversation=[], user_message="log that meeting with Acme")
    assert result.signal.tool_identity_tier == Tier.A
    assert result.requires_escalation is False


def test_surface_matcher_is_not_consulted_when_no_tool_call_is_proposed(monkeypatch):
    """Cheap but worth proving directly: ordinary conversation never pays
    for (or risks) a surface-matching call it doesn't need."""
    def _explode(context, candidate_surfaces):
        raise AssertionError("match_surface should not be called with no proposed_tool_call")
    monkeypatch.setattr(aldric_reply_module, "match_surface", _explode)

    canned = {"output": "Just a plain reply.", "scope": "exploration", "touched_categories": [], "proposed_tool_call": None}
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="hello")
    assert result.output_text == "Just a plain reply."


def test_invented_category_string_does_not_leak_into_self_reported_categories(monkeypatch):
    """touched_categories is filtered to PERMANENT_TIER_C_CATEGORIES only —
    an unrecognised string the model invents cannot silently pass through
    and cannot, by itself, trigger escalation."""
    canned = {
        "output": "Just thinking out loud here.",
        "scope": "exploration",
        "touched_categories": ["not_a_real_category"],
        "proposed_tool_call": None,
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="brainstorm with me")
    assert result.signal.self_reported_categories == frozenset()
    assert result.requires_escalation is False


def test_non_json_output_raises_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(aldric_reply_module, "complete", lambda **kwargs: "not json at all")
    try:
        run_casual_turn(conversation=[], user_message="hi")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "non-JSON" in str(exc)


# --- Long-term memory injection ------------------------------------------

def test_no_stored_memory_omits_the_known_memory_header(monkeypatch):
    captured = {}
    canned = {"output": "Sure.", "scope": "exploration", "touched_categories": [], "proposed_tool_call": None}
    monkeypatch.setattr(aldric_reply_module, "complete", _capturing_complete(canned, captured))
    run_casual_turn(conversation=[], user_message="hello")
    assert "Known long-term memory" not in captured["system"]


def test_stored_preference_reaches_the_models_system_prompt(monkeypatch):
    long_term_memory.set_preference("client", "Formal tone, no jokes.")
    captured = {}
    canned = {"output": "Sure.", "scope": "exploration", "touched_categories": [], "proposed_tool_call": None}
    monkeypatch.setattr(aldric_reply_module, "complete", _capturing_complete(canned, captured))
    run_casual_turn(conversation=[], user_message="draft an email to a client")
    assert "Formal tone, no jokes." in captured["system"]
    assert "[client]" in captured["system"]


def test_stored_fact_reaches_the_models_system_prompt(monkeypatch):
    long_term_memory.record_fact("general", "Invoice numbers start with INV-", source="test")
    captured = {}
    canned = {"output": "Sure.", "scope": "exploration", "touched_categories": [], "proposed_tool_call": None}
    monkeypatch.setattr(aldric_reply_module, "complete", _capturing_complete(canned, captured))
    run_casual_turn(conversation=[], user_message="what's our invoice numbering")
    assert "Invoice numbers start with INV-" in captured["system"]


# --- Memory-clarification signal ------------------------------------------

def test_needs_clarification_flows_through_to_result(monkeypatch):
    canned = {
        "output": "",
        "scope": "exploration",
        "touched_categories": [],
        "proposed_tool_call": None,
        "needs_clarification": True,
        "clarifying_question": "What tone does this recipient prefer?",
        "memory_scope": "client:acme",
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="write an email to Acme")
    assert result.needs_clarification is True
    assert result.clarifying_question == "What tone does this recipient prefer?"
    assert result.memory_scope == "client:acme"
    # A quality signal only — must never by itself trigger governance escalation.
    assert result.requires_escalation is False


def test_needs_clarification_defaults_to_false_when_absent(monkeypatch):
    canned = {"output": "Here you go.", "scope": "exploration", "touched_categories": [], "proposed_tool_call": None}
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="hello")
    assert result.needs_clarification is False
    assert result.clarifying_question == ""
    assert result.memory_scope == ""


# --- related_scope (Learning Governance wiring) ----------------------------

def test_related_scope_passes_through_when_it_names_a_real_known_scope(monkeypatch):
    long_term_memory.set_preference("client:acme", "Formal tone, no jokes.")
    canned = {
        "output": "Here's the formal draft.", "scope": "exploration", "touched_categories": [],
        "proposed_tool_call": None, "related_scope": "client:acme",
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="draft an email to Acme")
    assert result.related_scope == "client:acme"


def test_related_scope_is_dropped_when_it_names_a_scope_that_is_not_actually_known(monkeypatch):
    """Same discipline as touched_categories being filtered against
    PERMANENT_TIER_C_CATEGORIES: an invented or misremembered scope name
    must fail closed rather than being treated as something real to
    confirm later."""
    canned = {
        "output": "Sure.", "scope": "exploration", "touched_categories": [],
        "proposed_tool_call": None, "related_scope": "client:not_a_real_scope",
    }
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="hello")
    assert result.related_scope == ""


def test_related_scope_defaults_to_empty_when_absent(monkeypatch):
    canned = {"output": "Sure.", "scope": "exploration", "touched_categories": [], "proposed_tool_call": None}
    monkeypatch.setattr(aldric_reply_module, "complete", _mock_complete(canned))
    result = run_casual_turn(conversation=[], user_message="hello")
    assert result.related_scope == ""
