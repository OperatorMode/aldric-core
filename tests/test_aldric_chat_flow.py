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


def _casual_result(text, scope="exploration", scanned_categories=frozenset(), related_scope="") -> CasualTurnResult:
    signal = EscalationSignal(self_reported_scope=scope, scanned_categories=scanned_categories)
    return CasualTurnResult(output_text=text, signal=signal, related_scope=related_scope)


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


def test_clarification_round_trip_saves_preference_and_completes_original_request(monkeypatch, capsys):
    """Proves the full loop the operator described from real use: ALDRIC
    can't answer confidently, asks one question instead of guessing, the
    answer gets remembered (visibly, not silently), and the original request
    then actually gets completed using that new information rather than
    just filed away for a future turn."""
    first_result = CasualTurnResult(
        output_text="",
        signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True,
        clarifying_question="What tone should I use for this client?",
        memory_scope="client:acme",
    )
    follow_up_result = CasualTurnResult(
        output_text="Here's your formal email draft for Acme.",
        signal=EscalationSignal(self_reported_scope="exploration"),
    )
    results = iter([first_result, follow_up_result])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "write an email to Acme",   # triggers the clarification
            "Formal tone, no jokes",    # operator's answer
            "exit",
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert "What tone should I use for this client?" in out
    assert 'Remembered that for next time (under "client:acme")' in out
    assert "Here's your formal email draft for Acme." in out

    import core.long_term_memory as long_term_memory
    saved = long_term_memory.get_preference("client:acme")
    assert saved is not None
    assert saved.content == "Formal tone, no jokes"


def test_clarification_answer_creates_a_real_surface_and_next_turn_confirms_it(monkeypatch, capsys):
    """The full loop end to end: answering a clarifying question creates a
    real Learning Governance Surface (not just a StandingPreference), and
    the operator's very next "yes, perfect" is recorded as real confirmation
    signal against it — proving core.surface_signal is actually wired into
    aldric_chat.py's loop, not just independently testable."""
    db.init_db()
    first_result = CasualTurnResult(
        output_text="",
        signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True,
        clarifying_question="What tone should I use for this client?",
        memory_scope="client:acme",
    )
    follow_up_result = _casual_result("Here's your formal email draft for Acme.")
    results = iter([first_result, follow_up_result])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "write an email to Acme",
            "Formal tone, no jokes",
            "confirmed",
            "exit",
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert 'Remembered that for next time (under "client:acme")' in out
    assert 'Noted — confirmed for "client:acme"' in out

    import core.surface_signal as surface_signal
    from storage import db as storage_db
    stored = storage_db.get_surface("client:acme")
    assert stored is not None
    assert stored["confirmation_count"] == 1
    assert surface_signal.record_confirmation("client:acme").confirmation_count == 2


def test_a_second_clarification_for_the_same_scope_goes_through_the_reflective_cascade(monkeypatch, capsys):
    """Answering a clarifying question a second time for a scope that
    already has real memory behind it (a Surface with confirmation/
    correction history, or a standing preference) must not be treated as a
    second fresh instruction — core.confidence_cascade intercepts it, and
    an explicit 'no, ask every time' answer is recorded as a real
    correction (a real conflict record on the Surface), not a plain
    decline. See core/confidence_cascade.py's module docstring, point 1."""
    db.init_db()
    first_result = CasualTurnResult(
        output_text="",
        signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True,
        clarifying_question="What tone for this client?",
        memory_scope="client:acme",
        blocking=True,
    )
    second_result = CasualTurnResult(
        output_text="",
        signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True,
        clarifying_question="We've used a formal tone for Acme before — keep that as the default?",
        memory_scope="client:acme",
        blocking=True,
    )
    follow_up_1 = _casual_result("Formal draft ready.")
    follow_up_2 = _casual_result("Casual draft ready.")
    results = iter([first_result, follow_up_1, second_result, follow_up_2])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "write an email to Acme",
            "Formal tone",
            "write another email to Acme",  # not confirmation vocabulary -> falls through normally
            "no, ask every time",
            "exit",
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert "that's a correction, not just a decline" in out

    from storage import db as storage_db
    records = storage_db.list_conflict_records("client:acme")
    assert len(records) == 1
    assert records[0]["operator_instruction"] == "no, ask every time"
    stored = storage_db.get_surface("client:acme")
    assert stored["correction_count"] == 1
    assert stored["description"] == "no, ask every time"


def test_surface_reaching_executable_presents_first_crossing_and_grants_rights_on_confirm(monkeypatch, capsys):
    """End to end: a scope's Surface accumulating enough confirmations to
    cross into Executable state triggers PA Action Kernel Section 4.2's
    'First Executable Crossing' presentation, and a real 'confirmed' answer
    to THAT prompt (not the earlier confirmations that built up to it) is
    what actually grants execution rights."""
    db.init_db()
    first_result = CasualTurnResult(
        output_text="", signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True, clarifying_question="What tone for Acme?", memory_scope="client:acme",
    )
    follow_ups = [
        _casual_result(f"Draft {i}", related_scope="client:acme") for i in range(1, 6)
    ]
    results = iter([first_result, *follow_ups])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "write an email to Acme", "Formal tone, no jokes",
            "confirmed", "anything1",
            "confirmed", "anything2",
            "confirmed", "anything3",
            "confirmed", "anything4",
            "confirmed",             # 5th confirmation -> crosses into Executable
            "confirmed",             # answer to the first-crossing prompt itself
            "exit",
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert '"client:acme" has reached a stable, consistent pattern' in out
    assert 'Execution rights granted for "client:acme"' in out

    from storage import db as storage_db
    stored = storage_db.get_surface("client:acme")
    assert stored["state"] == "executable"
    assert stored["execution_rights_confirmed"] is True


def test_declining_the_first_crossing_prompt_does_not_grant_execution_rights(monkeypatch, capsys):
    db.init_db()
    first_result = CasualTurnResult(
        output_text="", signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True, clarifying_question="What tone for Acme?", memory_scope="client:acme",
    )
    follow_ups = [
        _casual_result(f"Draft {i}", related_scope="client:acme") for i in range(1, 6)
    ]
    results = iter([first_result, *follow_ups])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "write an email to Acme", "Formal tone, no jokes",
            "confirmed", "anything1",
            "confirmed", "anything2",
            "confirmed", "anything3",
            "confirmed", "anything4",
            "confirmed",              # 5th confirmation -> crosses into Executable
            "not right now",          # declines the first-crossing prompt
            "exit",
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert 'Understood — holding off on "client:acme"' in out

    from storage import db as storage_db
    stored = storage_db.get_surface("client:acme")
    assert stored["state"] == "executable"
    assert stored["execution_rights_confirmed"] is False


def test_clarification_that_itself_escalates_hands_off_correctly(monkeypatch, capsys):
    """The follow-up turn after answering a clarifying question can still
    turn out to need Governance Stack Mode (e.g. the answer itself revealed
    a real commitment) — proves that path is checked too, not just the
    first attempt."""
    db.init_db()
    first_result = CasualTurnResult(
        output_text="",
        signal=EscalationSignal(self_reported_scope="exploration"),
        needs_clarification=True,
        clarifying_question="What should we charge for this package?",
        memory_scope="pricing",
    )
    escalating_follow_up = CasualTurnResult(
        output_text="Sure, we'll do the package for $3,200.",
        signal=EscalationSignal(
            self_reported_scope="exploration",
            scanned_categories=frozenset({"pricing_or_cost_commitment"}),
        ),
    )
    results = iter([first_result, escalating_follow_up])
    monkeypatch.setattr(aldric_chat, "run_casual_turn", lambda conversation, user_message: next(results))
    monkeypatch.setattr(chat, "run_interview_step", _complete_interview_step)
    monkeypatch.setattr(chat, "_build_ids_detector", lambda: HeuristicIDSDetector())
    monkeypatch.setattr(
        "builtins.input",
        _scripted_inputs(
            "what should we charge for the package",
            "$3,200",
            "confirmed",
            "exit",
        ),
    )

    aldric_chat.main()
    out = capsys.readouterr().out
    assert "What should we charge for this package?" in out
    assert "This has stopped being casual" in out
    assert "Governance Stack Mode" in out
