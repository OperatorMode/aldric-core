"""
End-to-end integration test of aldric_chat.py's control flow, mirroring
tests/test_chat_flow.py's approach for chat.py: the LLM boundary mocked out,
stdin scripted. Proves two things wiring alone can hide: a casual session
that never triggers escalation never shows a DSD interview at all, and a
session that does escalate really hands off into chat.py's own (mocked) DSD
Discovery and governed session — not a second, parallel implementation of
either.
"""
import aldric_chat
import chat
from core.aldric_mode import EscalationSignal
from core.apex_supervisor import HeuristicIDSDetector
from llm.aldric_reply import CasualTurnResult
from storage import db


def _scripted_inputs(*answers):
    it = iter(answers)

    def _fake_input(prompt=""):
        return next(it)

    return _fake_input


def _casual_result(text, scope="exploration", scanned_categories=frozenset()) -> CasualTurnResult:
    signal = EscalationSignal(self_reported_scope=scope, scanned_categories=scanned_categories)
    return CasualTurnResult(output_text=text, signal=signal)


def test_casual_session_never_shows_dsd_interview_when_nothing_escalates(monkeypatch, capsys):
    results = iter([
        _casual_result("Here's a quick comparison of the two vendors."),
        _casual_result("Happy to draft that outline for you."),
    ])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs("compare these two vendors", "draft an outline", "exit"),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert "Governance Stack Mode" not in out
    assert "ADJUDICATION REQUIRED" not in out
    assert "Here's a quick comparison of the two vendors." in out
    assert "Happy to draft that outline for you." in out


def _complete_interview_step(conversation):
    return {
        "extracted_fields": {
            "decision_locus": "Whether to send the $3,200 proposal to the client",
            "operational_domain": "Client sales",
            "authority_boundary": "Operator decides",
            "time_horizon": "This week",
            "constraints_and_invariants": ["No unilateral discounts"],
            "risk_posture": "Low tolerance for pricing errors",
        },
        "missing_fields": [],
        "next_utterance": "",
    }


def test_escalating_turn_hands_off_into_real_dsd_discovery_and_governed_session(monkeypatch, capsys):
    db.init_db()
    escalating_result = _casual_result(
        "Sure, we'll do the package for $3,200.",
        scanned_categories=frozenset({"pricing_or_cost_commitment"}),
    )
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: escalating_result)
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())

    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "what would we charge for the package",  # casual turn -> escalates
            "confirmed",                              # DSD confirmation
            "exit",                                   # governed session exit
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert "This has stopped being casual" in out
    assert "pricing_or_cost_commitment" in out
    assert "Governance Stack Mode" in out
    assert "Decision Surface locked" in out
    assert "Governed chat is live" in out


def test_operator_can_exit_casual_session_without_ever_escalating(monkeypatch, capsys):
    monkeypatch.setattr(
        aldric_chat, "run_casual_turn",
        lambda conversation, user_message: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr("builtins.input", _scripted_inputs("exit"))

    aldric_chat.main()
    out = capsys.readouterr().out
    assert "Session ended" not in out  # exited via the 'exit' command, not EOF/KeyboardInterrupt
    assert "ALDRIC Mode (casual)" in out
