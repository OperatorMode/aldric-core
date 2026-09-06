"""
Proves README gap-list item 2 is actually closed, not just narrated:
`chat.py` now defaults to the real Sidecar Auditor (a second model call
against the actual four IDS markers) rather than `HeuristicIDSDetector`
(an offline keyword scan) — and proves the wiring end-to-end, not just
that the right class is importable.

Two things are proven here, deliberately kept separate:

  1. `chat._build_ids_detector()` — the actual factory `chat.main()` calls
     — constructs a `SidecarIDSDetector`, not a `HeuristicIDSDetector`. This
     is a fast, offline check of the promotion itself.

  2. A live `chat.main()` run, with the detector factory left at its real
     default (not overridden the way every other test in this suite
     overrides it to stay network-free), actually reaches
     `llm.sidecar.complete()` — proven by mocking that one function, the
     same pattern `test_tool_argument_tier_escalation.py` uses for
     `governed_reply_module.complete` — and that the APEX response to a
     detected marker (block output, force Validation scope) fires exactly
     as it does for the heuristic detector. A second case proves a clean
     candidate output still passes straight through under the real default.
"""
import json

import chat
import llm.sidecar as sidecar_module
from core.apex_supervisor import HeuristicIDSDetector
from llm.governed_reply import GovernedTurnResult
from llm.sidecar import SidecarIDSDetector
from storage import db


def _complete_interview_step(conversation):
    return {
        "extracted_fields": {
            "decision_locus": "Whether to take an action on the operator's behalf",
            "operational_domain": "Client account management",
            "authority_boundary": "Operator decides",
            "time_horizon": "Today",
            "constraints_and_invariants": ["No unilateral commitments"],
            "risk_posture": "Low tolerance for unauthorized commitments",
        },
        "missing_fields": [],
        "next_utterance": "",
    }


def _scripted_inputs(*answers):
    it = iter(answers)

    def _fake_input(prompt=""):
        return next(it)

    return _fake_input


def _mock_sidecar_complete(canned: dict):
    def _fake(system, user_message, model=None, max_tokens=None):
        return json.dumps(canned)
    return _fake


# --- 1: the factory itself promotes the real detector, not the fallback --

def test_default_ids_detector_is_the_real_sidecar_not_the_heuristic_fallback():
    detector = chat._build_ids_detector()
    assert isinstance(detector, SidecarIDSDetector)
    assert not isinstance(detector, HeuristicIDSDetector)


# --- 2: a live run, default left untouched, really calls the sidecar ----

def test_end_to_end_default_sidecar_detector_blocks_a_real_drift_finding(monkeypatch, capsys):
    """The default detector factory is deliberately NOT overridden here —
    every other end-to-end test in this suite substitutes
    HeuristicIDSDetector to stay network-free; this one proves the real
    default actually does something by mocking only the network boundary,
    llm.sidecar.complete, exactly the way governed_reply's own tests mock
    llm.governed_reply.complete."""
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)

    ordinary_result = GovernedTurnResult(
        reasoning="r",
        output_text="This is absolutely guaranteed to work perfectly, every single time!",
        self_reported_scope="exploration",
        self_reported_categories=frozenset(),
        scanned_categories=frozenset(),
    )

    def _fake_governed_turn(dsd, conversation, user_message, profile_fragment=""):
        return ordinary_result

    monkeypatch.setattr(chat, "run_governed_turn", _fake_governed_turn)
    monkeypatch.setattr(
        sidecar_module, "complete",
        _mock_sidecar_complete({"markers": ["validation_hype"], "rationale": "disproportionate enthusiasm"}),
    )
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "how confident are you", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out

    assert "[APEX] Drift detected" in out
    assert "validation_hype" in out
    # The drifting text must never reach the operator as a final reply:
    assert "ALDRIC: This is absolutely guaranteed" not in out


def test_end_to_end_default_sidecar_detector_passes_clean_output_through(monkeypatch, capsys):
    """Same real, un-overridden default detector; this time the mocked
    sidecar call reports no markers, proving ordinary turns aren't
    penalized just because the real (pricier) detector is now in the loop."""
    db.init_db()
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)

    ordinary_result = GovernedTurnResult(
        reasoning="r",
        output_text="Here's a plain recap of what we discussed.",
        self_reported_scope="exploration",
        self_reported_categories=frozenset(),
        scanned_categories=frozenset(),
    )

    def _fake_governed_turn(dsd, conversation, user_message, profile_fragment=""):
        return ordinary_result

    monkeypatch.setattr(chat, "run_governed_turn", _fake_governed_turn)
    monkeypatch.setattr(
        sidecar_module, "complete",
        _mock_sidecar_complete({"markers": [], "rationale": "no drift detected"}),
    )
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("confirmed", "recap that for me", "exit"),
    )

    chat.main()
    out = capsys.readouterr().out

    assert "ALDRIC: Here's a plain recap of what we discussed." in out
    assert "[APEX] Drift detected" not in out
