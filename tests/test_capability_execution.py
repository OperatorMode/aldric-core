"""
Proves the tier-gated execution wiring in aldric_chat.py (README gap-list
item 9): a proposed tool call surviving past core.aldric_mode.requires_escalation
is guaranteed Tier A or Tier B (see aldric_chat._execute_cleared_tool_call's
own docstring for why), and this is where it actually happens for the first
time in this codebase. Tier A executes silently; Tier B executes, applies
the PA signature disclosure to externally-visible text, and notifies the
operator; a broker failure is caught and reported, never left to crash the
session; anything that isn't A or B is a defensive no-op, not an execution.
core.capability_broker.execute_action is monkeypatched everywhere here —
nothing in this file touches the network or a real Google account.
"""
import aldric_chat
from core import capability_broker
from core.aldric_mode import EscalationSignal
from llm.aldric_reply import CasualTurnResult
from models.schemas import Tier
from storage import db


def _scripted_inputs(*answers):
    it = iter(answers)

    def _fake_input(prompt=""):
        return next(it)

    return _fake_input


def _tool_result(tool_name, arguments, tier, output_text="Done.") -> CasualTurnResult:
    signal = EscalationSignal(
        self_reported_scope="exploration",
        tool_identity_tier=tier,
        has_proposed_tool_call=True,
    )
    return CasualTurnResult(
        output_text=output_text,
        signal=signal,
        proposed_tool_call={"tool_name": tool_name, "arguments": arguments},
    )


def test_no_proposed_tool_call_is_a_no_op(monkeypatch):
    monkeypatch.setattr(
        capability_broker, "execute_action",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    result = CasualTurnResult(output_text="just chatting", signal=EscalationSignal(self_reported_scope="exploration"))
    aldric_chat._execute_cleared_tool_call(result)  # must not raise


def test_tier_a_tool_call_executes_silently_and_logs_digest(monkeypatch, capsys):
    db.init_db()
    calls = []

    def fake_execute_action(tool_name, arguments):
        calls.append((tool_name, arguments))
        return {"draft_id": "d1"}

    monkeypatch.setattr(capability_broker, "execute_action", fake_execute_action)
    arguments = {"to": "x@example.com", "subject": "s", "body": "Original body"}
    result = _tool_result("create_email_draft", arguments, Tier.A)

    aldric_chat._execute_cleared_tool_call(result)

    assert calls == [("create_email_draft", arguments)]  # unchanged: Tier A gets no PA signature
    assert "Done —" not in capsys.readouterr().out  # Tier A stays silent, per PA Action Kernel

    entries = db.list_undigested_entries()
    executed = [e for e in entries if e["category"] == "executed_action"]
    assert len(executed) == 1
    assert executed[0]["payload"]["status"] == "success"
    assert executed[0]["payload"]["tier"] == "tier_a"


def test_tier_b_tool_call_executes_applies_pa_signature_and_notifies(monkeypatch, capsys):
    db.init_db()
    captured = {}

    def fake_execute_action(tool_name, arguments):
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return {"message_id": "m1"}

    monkeypatch.setattr(capability_broker, "execute_action", fake_execute_action)
    result = _tool_result(
        "send_email", {"to": "client@example.com", "subject": "Update", "body": "Here's the update."}, Tier.B,
    )

    aldric_chat._execute_cleared_tool_call(result)

    assert captured["arguments"]["body"].startswith("Here's the update.")
    assert "Drafted and sent by ALDRIC" in captured["arguments"]["body"]
    out = capsys.readouterr().out
    assert "Done — send_email executed" in out

    entries = db.list_undigested_entries()
    executed = [e for e in entries if e["category"] == "executed_action"]
    assert executed[0]["payload"]["tier"] == "tier_b"
    assert executed[0]["payload"]["status"] == "success"


def test_tier_a_draft_gets_no_pa_signature():
    """create_email_draft is Tier A: nothing external has happened yet, so
    the disclosure line (due only when something reaches an external party)
    must not be applied — see _EXTERNAL_TEXT_ARGUMENT's own comment."""
    assert "create_email_draft" not in aldric_chat._EXTERNAL_TEXT_ARGUMENT


def test_broker_failure_is_caught_logged_and_reported_not_raised(monkeypatch, capsys):
    db.init_db()

    def fake_execute_action(tool_name, arguments):
        raise capability_broker.CapabilityBrokerError("send quota exceeded")

    monkeypatch.setattr(capability_broker, "execute_action", fake_execute_action)
    result = _tool_result("send_email", {"to": "x@example.com", "subject": "s", "body": "b"}, Tier.B)

    aldric_chat._execute_cleared_tool_call(result)  # must not raise

    out = capsys.readouterr().out
    assert "failed to actually run" in out
    assert "send quota exceeded" in out

    entries = db.list_undigested_entries()
    executed = [e for e in entries if e["category"] == "executed_action"]
    assert executed[0]["payload"]["status"] == "failed"


def test_defensive_skip_if_an_unexpected_tier_ever_reaches_execution(monkeypatch):
    """Belt-and-braces: core.aldric_mode.requires_escalation should already
    have raised _Escalate before a Tier C (or higher) proposed tool call
    ever reaches this function — see its docstring. If that ever regresses,
    this must fail closed (skip + log), never execute."""
    db.init_db()
    monkeypatch.setattr(
        capability_broker, "execute_action",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must never execute a Tier C action here")),
    )
    result = _tool_result("send_email", {"to": "x@example.com", "subject": "s", "body": "b"}, Tier.C)

    aldric_chat._execute_cleared_tool_call(result)  # must not raise, must not execute

    entries = db.list_undigested_entries()
    executed = [e for e in entries if e["category"] == "executed_action"]
    assert executed[0]["payload"]["status"] == "skipped"


def test_full_casual_session_executes_a_tier_a_tool_call_end_to_end(monkeypatch, capsys):
    """Not just the helper in isolation — proves this is really wired into
    aldric_chat.run_casual_session's actual loop."""
    db.init_db()
    calls = []
    monkeypatch.setattr(
        capability_broker, "execute_action",
        lambda tool_name, arguments: calls.append((tool_name, arguments)) or {"draft_id": "d1"},
    )
    arguments = {"to": "x@example.com", "subject": "Follow-up", "body": "Draft body"}
    result = _tool_result("create_email_draft", arguments, Tier.A, output_text="I've drafted that for you.")
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: result)
    monkeypatch.setattr("builtins.input", _scripted_inputs("draft a follow-up email", "exit"))

    aldric_chat.main()

    out = capsys.readouterr().out
    assert "I've drafted that for you." in out
    assert calls == [("create_email_draft", arguments)]
