"""
Live session demo — a runnable, readable walkthrough of ALDRIC Mode with no
Claude API key, no Google credentials, and no network required.

Not a test (no assertions — tests/test_aldric_chat_flow.py already covers
this ground with real pytest checks); this is a *demo*, kept in the repo so
"does this actually work end to end, and what does it look like" has a
one-command answer that doesn't require standing up real credentials:

    python demos/live_session_demo.py

It drives the real aldric_chat.py control flow (run_casual_session, the
needs_clarification/cascade branch, pending_scope confirmation, First
Executable Crossing, Capability Broker execution, the Idempotency Ledger,
the Daily Digest) exactly the way tests/test_aldric_chat_flow.py does:
llm/aldric_reply.run_casual_turn is replaced with a scripted queue of
CasualTurnResult objects (standing in for what a real Claude API call would
return), builtins.input is replaced with a scripted queue of operator
utterances, and core.capability_broker.execute_action is replaced with a
canned success response (standing in for a real Gmail/Calendar API call).

Nothing about aldric_chat.py, llm/aldric_reply.py, core/confidence_cascade.py,
core/surface_signal.py, core/learning_governance.py, core/pa_action_kernel.py,
or core/capability_broker.py is modified or bypassed — only the two real
I/O boundaries (the LLM call and the operator's keyboard) and the one real
network boundary (Gmail/Calendar) are swapped for scripted/canned values,
same as the test suite does. Everything printed below is the real code's
real print() output.

Turn-by-turn design notes (why each scripted result/input is what it is)
live inline as comments — this was hand-traced against aldric_chat.py's
actual pending_scope / needs_clarification control flow so the operator
inputs land exactly where they're meant to.

This script is what first surfaced two real findings, kept as printed
output at the end of a run rather than filed as a silent TODO:

  1. A Surface that accumulates confirmations with zero corrections
     self-flags for mirror drift (Learning Governance Section 4.2,
     indicator 1) without `_present_first_executable_crossing` ever
     mentioning it to the operator. Still open — see README's gap list.
  2. `aldric_chat.py`'s live wiring only ever branched on
     `CascadeDecision.ask_now` — `.resolve` and `.cite_memory` were
     computed by `core.confidence_cascade.decide_cascade` but never read
     anywhere in the live loop, so a genuinely trusted, non-flagged
     surface took the exact same "park for later" branch as a surface
     with no memory behind it at all. FIXED — `run_casual_session` now
     branches on `decision.resolve` first and answers from the surface's
     own recorded description (falling back to it only when the model
     itself left `output_text` empty) instead of asking or parking at
     all; see `tests/test_aldric_chat_flow.py`'s
     `test_a_trusted_nonflagged_surface_resolves_from_memory_without_asking`
     and the section near the end of this script that verifies it live.

WARNING: resets the real dev DB (storage/aldric.db) and event log to a
clean slate before running, the same way tests/conftest.py's reset_storage
fixture does for the test suite — don't run this against a database you
care about.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import db
from storage import event_log

# Fresh state, same technique tests/conftest.py's reset_storage fixture
# uses — this is the real dev DB path, so start clean.
for _p in (db.DEFAULT_DB_PATH, event_log.DEFAULT_LOG_PATH):
    if os.path.exists(_p):
        os.remove(_p)

import aldric_chat
import core.capability_broker as capability_broker
from core.aldric_mode import EscalationSignal
from core.pa_action_kernel import generate_daily_digest
from llm.aldric_reply import CasualTurnResult
from models.schemas import ConfidenceState, Surface, Tier


def chapter(title: str) -> None:
    print("\n\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def casual(
    text,
    scope="exploration",
    scanned=frozenset(),
    related_scope="",
    needs_clarification=False,
    clarifying_question="",
    memory_scope="",
    blocking=True,
    proposed_tool_call=None,
    tool_identity_tier=None,
    has_proposed_tool_call=False,
) -> CasualTurnResult:
    signal = EscalationSignal(
        self_reported_scope=scope,
        scanned_categories=scanned,
        tool_identity_tier=tool_identity_tier,
        has_proposed_tool_call=has_proposed_tool_call,
    )
    return CasualTurnResult(
        output_text=text,
        signal=signal,
        proposed_tool_call=proposed_tool_call,
        needs_clarification=needs_clarification,
        clarifying_question=clarifying_question,
        memory_scope=memory_scope,
        related_scope=related_scope,
        blocking=blocking,
    )


results = iter([
    # 1. Plain turn, nothing governance-relevant.
    casual("Sure — the confidence cascade decides whether to resolve a "
           "question from memory, ask about it now, or park it for later, "
           "and it turns your answer into real confidence growth instead "
           "of a one-off reply."),

    # 2. client:acme — brand-new scope, no memory anywhere -> decide_cascade
    #    asks now (surface=None, has_relevant_memory=False, urgent=True).
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="I don't have anything on file for Acme yet — "
                                "what tone should this email use, formal or casual?",
           memory_scope="client:acme"),
    # 2 follow-up, after the operator's answer (Instruction Signal path).
    casual("Done — formal draft ready: \"Dear Acme Corp team, further to our "
           "prior correspondence...\""),

    # 3. Falls through pending_scope (not confirmation vocab) into an
    #    ordinary turn that draws on the new preference.
    casual("Here's a quick note to Acme about the meeting time change, "
           "formal tone as usual.", related_scope="client:acme"),

    # 5. Ordinary turn, draws on it again (needed to re-arm pending_scope
    #    after confirmation #1 clears it).
    casual("Another Acme note drafted, same formal tone.", related_scope="client:acme"),

    # 7. Ordinary turn again (re-arm pending_scope for confirmation #3).
    casual("One more Acme email drafted, formal as always.", related_scope="client:acme"),

    # 9. needs_clarification AGAIN for client:acme — real history now
    #    exists (3 confirmations), so this is the REFLECTIVE cascade, not
    #    "genuinely new." Surface is DEVELOPING, not EXECUTABLE yet, and
    #    urgent=True -> decide_cascade asks now rather than parking.
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="We've used a formal tone for Acme a few times "
                                "now — keep that as the standing default going forward?",
           memory_scope="client:acme"),
    # 9 follow-up.
    casual("Good — formal it is, draft's ready."),

    # 11. pricing_discount — brand-new scope.
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="What discount, if any, should repeat clients get?",
           memory_scope="pricing_discount"),
    casual("Understood — quoting full rate."),

    # 12. Reflective question for pricing_discount -> SCOPE_NARROW answer.
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="We don't do repeat-client discounts by default "
                                "— should Acme Corp specifically get one going forward?",
           memory_scope="pricing_discount"),
    casual("Great — I'll apply that discount for Acme's orders specifically."),

    # 13. meeting_notes — brand-new scope.
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="Should meeting notes go out same-day, or next morning?",
           memory_scope="meeting_notes"),
    casual("Understood — same-day it is."),

    # 14. Reflective question for meeting_notes -> ALWAYS_ASK (real correction).
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="Same-day again for this one, as the standing default?",
           memory_scope="meeting_notes"),
    casual("Understood — I'll check with you on timing every time from now on."),

    # 15. invoice_format — brand-new scope.
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="Should invoices show line-item tax, or just a total?",
           memory_scope="invoice_format"),
    casual("Noted — itemized tax it is."),

    # 16. Reflective question for invoice_format -> AMBIGUOUS then a clean
    #     retry answer.
    casual("", needs_clarification=True, blocking=True,
           clarifying_question="Itemized tax again as the default for invoices?",
           memory_scope="invoice_format"),
    casual("Locked in — itemized tax as the default."),

    # 17. report_structure — brand-new scope, NOT urgent -> decide_cascade
    #     parks instead of asking. blocking=False means output_text is a
    #     real best-effort answer, not empty.
    casual("Here's a draft structure to start with — happy to adjust once "
           "you've had a look.",
           needs_clarification=True, blocking=False,
           memory_scope="report_structure",
           clarifying_question="Should quarterly reports always lead with the "
                                "revenue summary, or vary by audience?"),

    # 18. Tier A tool call: internal, reversible, non-external-facing ->
    #     executes silently (only logged to the digest, no "Done" print).
    casual("Logged an internal note about the Acme follow-up.",
           proposed_tool_call={"tool_name": "log_internal_note",
                                "arguments": {"note": "Follow up with Acme next week."}},
           has_proposed_tool_call=True, tool_identity_tier=Tier.A),

    # 19. Tier B tool call: external-facing -> executes, then notifies with
    #     a PA-signature disclosure appended to the visible text.
    casual("Calendar event created for the Acme check-in.",
           proposed_tool_call={"tool_name": "create_calendar_event",
                                "arguments": {"title": "Acme check-in",
                                              "description": "Quarterly check-in call with Acme Corp.",
                                              "start": "2026-09-14T10:00:00",
                                              "end": "2026-09-14T10:30:00"}},
           has_proposed_tool_call=True, tool_identity_tier=Tier.B),
])

operator_inputs = iter([
    "Morning — quick one, what does the new confidence cascade actually do?",   # 1
    "I need to draft an email to Acme Corp. What tone should I use?",           # 2 (asks)
    "Formal tone, always used for Acme.",                                       # 2 (answer)
    "Great, also draft a quick note to Acme about the meeting time change.",     # 3 (falls through)
    "confirmed",                                                                 # 4 (confirm #1 -> observing)
    "One more note to Acme, similar to the others.",                            # 5 (ordinary, re-arms)
    "yes",                                                                       # 6 (confirm #2 -> developing)
    "Draft one more Acme email for the road.",                                   # 7 (ordinary, re-arms)
    "confirmed",                                                                 # 8 (confirm #3 -> developing)
    "Send another one to Acme, please.",                                         # 9 (asks reflective)
    "yes",                                                                       # 9 (GENERALIZE answer -> confirm #4)
    "confirmed",                                                                 # 10 (confirm #5 -> EXECUTABLE, crossing fires)
    "confirmed",                                                                 # 10 (grants execution rights)
    "What discount, if any, should repeat clients get?",                        # 11 (asks)
    "No standard discount — quote full rate unless I say otherwise.",           # 11 (answer)
    "Could you quote a price for our next Acme order?",                         # 12 (falls through, asks reflective)
    "yes, just for Acme Corp",                                                   # 12 (SCOPE_NARROW answer)
    "Should meeting notes go out same-day, or next morning?",                    # 13 (falls through, asks)
    "Same-day, always.",                                                         # 13 (answer)
    "Send out today's meeting notes.",                                           # 14 (falls through, asks reflective)
    "no, ask every time",                                                        # 14 (ALWAYS_ASK answer)
    "Should invoices show line-item tax, or just a total?",                      # 15 (falls through, asks)
    "Itemized tax, please.",                                                     # 15 (answer)
    "Put together an invoice for this job.",                                     # 16 (falls through, asks reflective)
    "hmm, not sure, let me think about it",                                      # 16 (AMBIGUOUS answer)
    "Always",                                                                     # 16 (retry -> GENERALIZE)
    "Can you get started on drafting next quarter's report structure?",          # 17 (falls through, parks)
    "Log an internal note to follow up with Acme next week.",                    # 18 (Tier A tool call)
    "Also set up our regular Acme check-in on the calendar for next week.",      # 19 (Tier B tool call)
    "exit",                                                                       # 20
])


def fake_input(prompt: str = "") -> str:
    answer = next(operator_inputs)
    print(f"{prompt}{answer}")
    return answer


def fake_run_casual_turn(conversation, user_message):
    return next(results)


def fake_execute_action(tool_name: str, arguments: dict) -> dict:
    # Stands in for a real Gmail/Calendar API call — no network, no
    # credentials, same shape of response core.capability_broker's real
    # execute_action would return on success.
    return {"tool": tool_name, "status": "ok", "simulated": True, "echo_arguments": arguments}


aldric_chat.run_casual_turn = fake_run_casual_turn
capability_broker.execute_action = fake_execute_action
import builtins
builtins.input = fake_input


chapter("LIVE SESSION TRANSCRIPT (aldric_chat.py, real code, scripted LLM + scripted operator)")
aldric_chat.main()

chapter("FINAL STORED STATE — every Surface on record")
for raw in db.list_surfaces():
    print(f"  {raw['surface_id']:24s} state={raw['state']:11s} "
          f"confirmations={raw['confirmation_count']} corrections={raw['correction_count']} "
          f"mirror_drift_flagged={raw['mirror_drift_flagged']} "
          f"execution_rights_confirmed={raw['execution_rights_confirmed']}")
    print(f"      description: {raw['description']!r}")

chapter("FINAL STORED STATE — standing preferences")
import core.long_term_memory as long_term_memory
for p in long_term_memory.list_preferences():
    print(f"  [{p.scope}] {p.content}")

chapter("DAILY DIGEST (core.pa_action_kernel.generate_daily_digest())")
for entry in generate_daily_digest():
    print(f"  [{entry['category']}] {entry['payload']}")

chapter("FINDING 1 — client:acme self-flagged mirror drift, unprompted, at the crossing")
acme_raw = db.get_surface("client:acme")
acme_surface = Surface(**acme_raw)
print(f"  client:acme reached Executable on 5 confirmations with 0 corrections ever recorded.")
print(f"  mirror_drift_flagged={acme_surface.mirror_drift_flagged} — set automatically inside "
      f"apply_confirmation()'s")
print(f"  own mirror-drift check (core.learning_governance._check_and_log_mirror_drift), the exact "
      f"moment the")
print(f"  5th confirmation landed — Section 4.2 indicator 1: 'confidence/confirmation climbing with "
      f"zero")
print(f"  corrections ever recorded — the model is not being tested, only confirmed.' Nobody scripted "
      f"this;")
print(f"  it's a real structural consequence of an operator who never needed to correct anything in "
      f"this run.")
print(f"  _present_first_executable_crossing() does not mention it at all — the operator granted "
      f"execution")
print(f"  rights above having seen confirmation/correction counts, but not this flag.")

chapter("FINDING 2 (FIXED) — does aldric_chat.py actually use decide_cascade's 'resolve' signal?")
from core import confidence_cascade

decision_flagged = confidence_cascade.decide_cascade(surface=acme_surface, has_relevant_memory=True, urgent=False)
print(f"  Using the real (now mirror-drift-flagged) client:acme surface:")
print(f"    decide_cascade(..., urgent=False) -> resolve={decision_flagged.resolve} "
      f"ask_now={decision_flagged.ask_now} cite_memory={decision_flagged.cite_memory}")
print(f"    reasons={decision_flagged.reasons}")
print(f"  (Mirror-drift correctly forces resolve=False here — design call #3 in the module docstring, "
      f"working as intended.)")

clean_surface = Surface(surface_id="demo:trusted_resolve_example",
                         description="Formal tone, always used for Acme.",
                         state=ConfidenceState.EXECUTABLE, confirmation_count=5, correction_count=1,
                         mirror_drift_flagged=False)
db.save_surface(clean_surface)
decision_clean = confidence_cascade.decide_cascade(surface=clean_surface, has_relevant_memory=True, urgent=False)
print(f"\n  Using a CLEAN Executable surface (same state, one correction on record so indicator 1 "
      f"never trips, not mirror-drift-flagged), saved for real under scope "
      f"\"demo:trusted_resolve_example\":")
print(f"    decide_cascade(..., urgent=False) -> resolve={decision_clean.resolve} "
      f"ask_now={decision_clean.ask_now} cite_memory={decision_clean.cite_memory}")
print(f"    reasons={decision_clean.reasons}")
print("\n  This used to be where the bug was: aldric_chat.py's needs_clarification block only ever")
print("  branched on `decision.ask_now`, never `decision.resolve` — a genuinely trusted surface took")
print("  the exact same 'park for later' branch as one with no memory at all. Now fixed (see")
print("  run_casual_session's new `if decision.resolve:` branch, ahead of the park/ask branches).")
print("  Proving it live, one more turn through the real code, for this exact surface:\n")

resolve_demo_result = CasualTurnResult(
    output_text="",  # left empty, same as a model self-reporting blocking=True
    signal=EscalationSignal(self_reported_scope="exploration"),
    needs_clarification=True, blocking=True,
    clarifying_question="Same formal default for this one too?",
    memory_scope="demo:trusted_resolve_example",
)
resolve_demo_inputs = iter(["One more thing for this trusted scope.", "exit"])


def _resolve_demo_input(prompt: str = "") -> str:
    answer = next(resolve_demo_inputs)
    print(f"{prompt}{answer}")
    return answer


aldric_chat.run_casual_turn = lambda conversation, user_message: resolve_demo_result
builtins.input = _resolve_demo_input
aldric_chat.main()

chapter("BONUS — Idempotency Ledger dedup (calling the exact same action twice)")
from core import idempotency_ledger

call_count = {"n": 0}


def counting_execute_action(tool_name: str, arguments: dict) -> dict:
    call_count["n"] += 1
    return {"tool": tool_name, "status": "ok", "call_number": call_count["n"]}


first = idempotency_ledger.run_idempotent("send_email", {"to": "acme@example.com", "body": "hi"},
                                           counting_execute_action)
print(f"First call executed: {first}  (real broker call count so far: {call_count['n']})")
second = idempotency_ledger.run_idempotent("send_email", {"to": "acme@example.com", "body": "hi"},
                                            counting_execute_action)
print(f"Second call (same tool+arguments) returned: {second}  "
      f"(real broker call count still: {call_count['n']} — it was NOT called again)")

print("\nDone.")
