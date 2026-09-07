"""
End-to-end integration test of chat.py's control flow, with the LLM
boundary mocked out (dsd_interview and governed_reply) and stdin scripted.
This is the test that would have caught chat.py wiring bugs — wrong
argument order, a forgotten normalisation step, a control-flow branch that
never gets hit — before ever running it against a live API key.
"""
import itertools

import pytest

import chat
from core.apex_supervisor import HeuristicIDSDetector
from core.ksp1_operator_kernel import AdjudicationStage
from llm.governed_reply import GovernedTurnResult
from models.schemas import ConfirmationResult, KSPFinalityResult, KSPOutcome, classify_confirmation
from storage import db


def _fake_ksp_finality_pass_through(dsd, candidate_claim):
    """A stand-in for core.ksp_finality.run_ksp_finality that clears every
    time without changing the text — used by tests that aren't exercising
    KSP behaviour itself, so their existing assertions about the exact
    artifact text keep holding."""
    return KSPFinalityResult(dsd_ref=dsd.dsd_id, outcome=KSPOutcome.CLEARED, compaction_text=candidate_claim)


def _complete_interview_step(conversation):
    return {
        "extracted_fields": {
            "decision_locus": "Whether to respond to a client billing question",
            "operational_domain": "Client billing",
            "authority_boundary": "Operator decides",
            "time_horizon": "Today",
            "constraints_and_invariants": ["No unilateral discounts"],
            "risk_posture": "Low tolerance for pricing errors",
        },
        "missing_fields": [],
        "next_utterance": "",
    }


def _scripted_inputs(*answers):
    it = iter(answers)

    def _fake_input(prompt=""):
        return next(it)

    return _fake_input


def test_dsd_discovery_completes_in_one_step_and_locks(monkeypatch):
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())
    monkeypatch.setattr("builtins.input", _scripted_inputs("confirmed"))

    dsd = chat.run_dsd_discovery()
    assert dsd.locked is True
    assert dsd.confirmed is True
    assert dsd.decision_locus == "Whether to respond to a client billing question"
    assert dsd.constraints_and_invariants == ["No unilateral discounts"]


def test_ambiguous_then_clear_confirmation(monkeypatch):
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())
    monkeypatch.setattr("builtins.input", _scripted_inputs("not sure honestly", "confirmed"))

    dsd = chat.run_dsd_discovery()
    assert dsd.locked is True


def test_classify_confirmation_distinguishes_decline_from_ambiguous_noise():
    """A live test against a real model surfaced that a plain "no" (and even
    "exit") both fell through to the same generic "not a clear yes or no"
    re-prompt as pure noise — root cause was classify_confirmation only
    ever distinguishing CONFIRMED from "everything else." DECLINED is a
    real third outcome now, exact-match only, same discipline as
    CONFIRMATION_VOCABULARY — a verbose sentence that merely contains "no"
    still doesn't count, same as a verbose sentence that merely contains
    "yes" never counted as CONFIRMED."""
    assert classify_confirmation("no") == ConfirmationResult.DECLINED
    assert classify_confirmation("Incorrect") == ConfirmationResult.DECLINED
    assert classify_confirmation("wrong") == ConfirmationResult.DECLINED
    assert classify_confirmation("not sure honestly") == ConfirmationResult.AMBIGUOUS
    assert classify_confirmation("no, that's totally wrong") == ConfirmationResult.AMBIGUOUS
    assert classify_confirmation("confirmed") == ConfirmationResult.CONFIRMED


def test_exit_during_dsd_reflection_ends_the_session_without_locking(monkeypatch, capsys):
    """Found live: typing 'exit' at the "Is that correct?" prompt used to
    just be treated as more ambiguous noise and re-prompted forever — the
    only documented way out was one of the five exact confirmation words."""
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())
    monkeypatch.setattr("builtins.input", _scripted_inputs("exit"))

    with pytest.raises(SystemExit):
        chat.run_dsd_discovery()
    out = capsys.readouterr().out
    assert "nothing was locked" in out


def test_exit_during_dsd_interview_also_ends_the_session(monkeypatch, capsys):
    def _incomplete_interview_step(conversation):
        return {
            "extracted_fields": {},
            "missing_fields": ["decision_locus"],
            "next_utterance": "What are we deciding?",
        }

    monkeypatch.setattr(chat, "run_interview_step", _incomplete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())
    monkeypatch.setattr("builtins.input", _scripted_inputs("exit"))

    with pytest.raises(SystemExit):
        chat.run_dsd_discovery()
    out = capsys.readouterr().out
    assert "nothing was locked" in out


def test_a_plain_no_declines_and_restarts_discovery_instead_of_looping_forever(monkeypatch, capsys):
    """The other half of the same live-test finding: a genuine "no" now goes
    back to re-establish the DSD correctly — reusing the exact same
    reprint-the-banner-and-recur recovery path a DSDGateError already used a
    few lines above in chat.py, not a second restart mechanism — instead of
    being trapped in an infinite "that wasn't a clear yes or no" loop."""
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())
    monkeypatch.setattr("builtins.input", _scripted_inputs("no", "confirmed"))

    dsd = chat.run_dsd_discovery()
    out = capsys.readouterr().out
    assert "Understood — that's not right" in out
    assert dsd.locked is True


def test_ordinary_turn_shows_output_without_adjudication(monkeypatch, capsys):
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())

    ordinary_result = GovernedTurnResult(
        reasoning="r", output_text="Here's a quick recap of the meeting.",
        self_reported_scope="exploration", self_reported_categories=frozenset(),
        scanned_categories=frozenset(),
    )

    call_count = itertools.count()

    def _fake_governed_turn(dsd, conversation, user_message, profile_fragment=""):
        next(call_count)
        return ordinary_result

    monkeypatch.setattr(chat, "run_governed_turn", _fake_governed_turn)
    monkeypatch.setattr("builtins.input", _scripted_inputs("confirmed", "summarize the meeting", "exit"))

    chat.main()
    out = capsys.readouterr().out
    assert "Here's a quick recap of the meeting." in out
    assert "ADJUDICATION REQUIRED" not in out


def test_permanent_category_turn_requires_two_confirmations(monkeypatch, capsys):
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())

    pricing_result = GovernedTurnResult(
        reasoning="r", output_text="I'll set the price to $5,000 for that package.",
        self_reported_scope="exploration", self_reported_categories=frozenset(),
        scanned_categories=frozenset({"pricing_or_cost_commitment"}),
    )

    def _fake_governed_turn(dsd, conversation, user_message, profile_fragment=""):
        return pricing_result

    monkeypatch.setattr(chat, "run_governed_turn", _fake_governed_turn)
    monkeypatch.setattr(chat, "run_ksp_finality", _fake_ksp_finality_pass_through)
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "confirmed",              # DSD confirmation
            "what should we charge",  # user turn
            "confirmed",              # content confirmation
            "send it",                # emission authorization
            "exit",
        ),
    )

    chat.main()
    out = capsys.readouterr().out
    assert "ADJUDICATION REQUIRED" in out
    assert "I'll set the price to $5,000 for that package." in out


def test_rejecting_a_permanent_category_artifact_never_emits_it(monkeypatch, capsys):
    """A rejected artifact IS shown to the operator as an unconfirmed draft
    (KSP-1 Section 3.2: the operator must see the full artifact to
    adjudicate it) — but it must never cross into the "ALDRIC: <text>"
    emitted/final form, and must never enter conversation history as if it
    were an accepted reply."""
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())

    pricing_result = GovernedTurnResult(
        reasoning="r", output_text="UNIQUE_MARKER_should_never_be_printed $9,999",
        self_reported_scope="exploration", self_reported_categories=frozenset(),
        scanned_categories=frozenset({"pricing_or_cost_commitment"}),
    )

    def _fake_governed_turn(dsd, conversation, user_message, profile_fragment=""):
        return pricing_result

    monkeypatch.setattr(chat, "run_governed_turn", _fake_governed_turn)
    monkeypatch.setattr(chat, "run_ksp_finality", _fake_ksp_finality_pass_through)
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "what should we charge", "rejected", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out
    # Shown once, as an explicitly-labelled non-binding draft:
    assert "[Draft — not yet binding]" in out
    assert "Discarded." in out
    # Never shown in the emitted/final form:
    assert "ALDRIC: UNIQUE_MARKER_should_never_be_printed" not in out
