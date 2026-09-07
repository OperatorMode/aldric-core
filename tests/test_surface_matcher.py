"""
Tests for llm.surface_matcher.match_surface — the real Surface Matcher (PA
Action Kernel Section 2.2). Model call mocked out, same pattern as
tests/test_aldric_reply.py. Proves: no network call at all when there's
nothing to match against (keeps every pre-existing tool-call test
network-free without needing new mocks), only Executable surfaces are ever
offered as candidates, and the model can point at a real candidate by id but
can never invent or modify one.
"""
import json

import llm.surface_matcher as surface_matcher_module
from llm.surface_matcher import match_surface
from models.schemas import ConfidenceState, Surface


def _mock_complete(canned: dict):
    def _fake(system, user_message, model=None, max_tokens=None):
        return json.dumps(canned)
    return _fake


def test_empty_candidate_list_returns_none_without_calling_the_model(monkeypatch):
    def _explode(**kwargs):
        raise AssertionError("should not call the model with no candidates")
    monkeypatch.setattr(surface_matcher_module, "complete", _explode)

    assert match_surface(context={"tool_name": "log_internal_note", "arguments": {}}, candidate_surfaces=[]) is None


def test_all_non_executable_candidates_returns_none_without_calling_the_model(monkeypatch):
    def _explode(**kwargs):
        raise AssertionError("should not call the model with no Executable candidates")
    monkeypatch.setattr(surface_matcher_module, "complete", _explode)

    developing = Surface(surface_id="client:acme", description="still learning", state=ConfidenceState.DEVELOPING)
    assert match_surface(context={"tool_name": "log_internal_note", "arguments": {}}, candidate_surfaces=[developing]) is None


def test_model_pointing_at_a_real_candidate_id_returns_that_real_surface(monkeypatch):
    canned = {"surface_id": "client:acme", "reasoning": "Same client, same kind of ask."}
    monkeypatch.setattr(surface_matcher_module, "complete", _mock_complete(canned))

    acme = Surface(surface_id="client:acme", description="Formal tone, no jokes.",
                    state=ConfidenceState.EXECUTABLE, confirmation_count=5)
    result = match_surface(
        context={"tool_name": "send_client_email", "arguments": {"to": "acme"}, "conversation_excerpt": "..."},
        candidate_surfaces=[acme],
    )
    assert result is acme


def test_model_claiming_a_null_surface_id_is_no_match(monkeypatch):
    canned = {"surface_id": None, "reasoning": "Doesn't fit anything on record."}
    monkeypatch.setattr(surface_matcher_module, "complete", _mock_complete(canned))

    acme = Surface(surface_id="client:acme", description="Formal tone.", state=ConfidenceState.EXECUTABLE)
    result = match_surface(context={"tool_name": "x", "arguments": {}}, candidate_surfaces=[acme])
    assert result is None


def test_model_inventing_a_surface_id_that_does_not_exist_fails_closed(monkeypatch):
    """The model can only ever point at a real candidate — it cannot cause a
    match to a surface that was never actually offered to it."""
    canned = {"surface_id": "client:totally_made_up", "reasoning": "..."}
    monkeypatch.setattr(surface_matcher_module, "complete", _mock_complete(canned))

    acme = Surface(surface_id="client:acme", description="Formal tone.", state=ConfidenceState.EXECUTABLE)
    result = match_surface(context={"tool_name": "x", "arguments": {}}, candidate_surfaces=[acme])
    assert result is None


def test_developing_candidate_is_never_offered_even_alongside_a_real_executable_one(monkeypatch):
    captured = {}

    def _fake(system, user_message, model=None, max_tokens=None):
        captured["user_message"] = user_message
        return json.dumps({"surface_id": "client:acme", "reasoning": "fits"})

    monkeypatch.setattr(surface_matcher_module, "complete", _fake)

    acme = Surface(surface_id="client:acme", description="Formal tone.", state=ConfidenceState.EXECUTABLE)
    beta = Surface(surface_id="client:beta", description="still learning", state=ConfidenceState.DEVELOPING)
    match_surface(context={"tool_name": "x", "arguments": {}}, candidate_surfaces=[acme, beta])

    assert "client:acme" in captured["user_message"]
    assert "client:beta" not in captured["user_message"]


def test_non_json_model_output_fails_closed_to_none(monkeypatch):
    monkeypatch.setattr(surface_matcher_module, "complete", lambda **kwargs: "not json")
    acme = Surface(surface_id="client:acme", description="Formal tone.", state=ConfidenceState.EXECUTABLE)
    assert match_surface(context={"tool_name": "x", "arguments": {}}, candidate_surfaces=[acme]) is None
