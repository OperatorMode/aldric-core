"""
ALDRIC Mode — terminal chat.

Governance Stack Mode's chat.py always runs the DSD Discovery Loop
immediately and unconditionally, per 08_Governance_Chain.md's "Governance
Sequence — Governance Stack Mode" table. That's correct for Governance
Stack Mode. ALDRIC Mode — the other half of the same document's Two-Mode
Architecture — starts from the opposite condition: "autonomous, default,
observation-mode initialisation, NO DSD required upfront." This script is
that mode's entrypoint (closes README gap-list item 5) — ordinary
conversation runs immediately, no interview, no locked Decision Surface,
until core.aldric_mode.requires_escalation fires on some turn's output.

What happens at that point, in order:

    1. The escalation is announced to the operator plainly, never silently
       — the same "transitions are always visible" principle the Operator
       Profiles doc applies to profile switching, applied here at the mode
       level instead.
    2. The real DSD Discovery Loop runs (chat.run_dsd_discovery — reused,
       not duplicated), seeded with the casual conversation so far so the
       operator isn't made to repeat context they already gave.
    3. Once locked, the rest of the session runs through the exact same
       Governance Stack Mode machinery chat.py itself uses
       (chat.run_governed_session — also reused, not duplicated): the
       Adjudication Buffer, KSP Finality, APEX drift detection, all of it.

Escalation is one-directional for the whole session — the same principle PA
Action Kernel already applies to tiers (nothing downgrades a tier once
raised). Once locked into Governance Stack Mode, this session stays there;
it does not drop back into casual mode turn-by-turn, which would itself be
a way to game the gate (alternate casual/governed messages hoping a
commitment slips through labelled "casual"). Start a new session for a
fresh casual conversation.

Also wires Learning Governance (core/surface_signal.py) into this same loop:
answering a clarifying question is Instruction/Correction Signal on the
scope's Surface; the operator's next turn is checked for canonical
confirmation vocabulary (models.schemas.classify_confirmation — the same
deterministic check Governance Stack Mode uses for Tier C ratification, not
ALDRIC's own read of how the turn went) and, if it matches, recorded as real
Confirmation Signal. See core/surface_signal.py's module docstring for why
this didn't need the real surface matcher (README gap-list item 1) to do
this honestly.

That matcher (llm/surface_matcher.py) now exists, which makes the moment a
scope's Surface first reaches Executable state a real, live-governance
moment rather than a number changing quietly in storage: PA Action Kernel
Section 4.2 ("First Executable Crossing") requires the operator see the
surface model and explicitly grant execution rights before anything can
ever be classified above Tier C against it — see
`_present_first_executable_crossing` below. This is deliberately separate
from, and later than, the state transition itself (a confidence assessment,
not operator sign-off), and ALDRIC never grants it to itself.

Requires ANTHROPIC_API_KEY or Aldric-API in the environment. Run:

    export ANTHROPIC_API_KEY=sk-ant-...
    python aldric_chat.py
"""
from __future__ import annotations

import chat  # reuse Governance Stack Mode's DSD discovery + governed session loop rather than duplicating them
import core.long_term_memory as long_term_memory
import core.surface_signal as surface_signal
from core import capability_broker, idempotency_ledger, pa_action_kernel
from llm.aldric_reply import CasualTurnResult, run_casual_turn
from models.schemas import (
    PERMANENT_TIER_C_CATEGORIES,
    ConfidenceState,
    ConfirmationResult,
    Tier,
    classify_confirmation,
)
from storage import db

# Which argument of each real tool carries text a recipient/attendee would
# actually see — the only thing the Tier B PA signature (see
# core.pa_action_kernel.apply_pa_signature) ever gets applied to.
# create_email_draft is deliberately absent: it's Tier A (nothing external
# happens until a human sends it), so no disclosure is due yet.
_EXTERNAL_TEXT_ARGUMENT = {
    "send_email": "body",
    "create_calendar_event": "description",
    "update_calendar_event": "description",
}


def _with_pa_signature(tool_name: str, arguments: dict) -> dict:
    field = _EXTERNAL_TEXT_ARGUMENT.get(tool_name)
    if field and arguments.get(field):
        arguments = dict(arguments)
        arguments[field] = pa_action_kernel.apply_pa_signature(arguments[field])
    return arguments


class _Escalate(Exception):
    """Raised out of the casual loop the moment a turn requires escalation.
    Carries the casual conversation so far, to seed the DSD interview."""

    def __init__(self, conversation: list[dict[str, str]]):
        self.conversation = conversation


def _describe_escalation_reasons(signal) -> list[str]:
    """Plain-language reasons shown to the operator for why this turn
    triggered escalation — never silent, see module docstring. Pure
    function, no I/O, directly testable."""
    reasons = []
    if signal.self_reported_scope == "finality":
        reasons.append("this looks like a finished, actionable claim rather than open exploration")
    if signal.touches_permanent_tier_c:
        touched = sorted(signal.all_categories & PERMANENT_TIER_C_CATEGORIES)
        reasons.append(f"it touches: {touched}")
    if signal.tool_effective_tier == Tier.C:
        reasons.append("a real action was proposed that needs authorization first")
    return reasons or ["something about this turn needs a proper check-in"]


def _announce_and_escalate(conversation: list[dict[str, str]], result) -> None:
    """Shared by both the first attempt at a turn and the follow-up after a
    clarification round-trip — a turn can trigger escalation either way, and
    the announcement + handoff is identical either time. Raises _Escalate;
    never returns normally."""
    reasons = _describe_escalation_reasons(result.signal)
    print("\n[ALDRIC] This has stopped being casual — " + "; ".join(reasons) + ".")
    print("[ALDRIC] Switching to Governance Stack Mode to establish exactly what we're deciding first.\n")
    conversation.append({"role": "assistant", "content": result.output_text})
    raise _Escalate(conversation)


class _SessionEnded(Exception):
    """Raised out of the first-executable-crossing prompt on EOF/interrupt,
    so run_casual_session can end the same way every other input() site in
    this file does, without duplicating that handling here."""


def _present_first_executable_crossing(surface) -> None:
    """PA Action Kernel Section 4.2, First Executable Crossing — 'the one
    point in the continuous cycle where live confirmation is required'
    before any autonomous execution can ever run against a surface. Called
    the moment a scope's Surface reaches state=Executable (its state alone
    getting there is just the kernel's own confidence assessment — Section
    1.5 — never operator sign-off by itself). Presents exactly what ALDRIC
    has actually recorded, nothing more, and only grants execution rights on
    the same deterministic confirmation check used everywhere else in this
    codebase (models.schemas.classify_confirmation) — never on ALDRIC's own
    read of the moment. If the operator doesn't confirm, execution rights
    are simply not granted yet; this is asked again the next time
    confirmation activity touches this surface, rather than assuming a
    non-answer means anything in particular."""
    print(f'\n[ALDRIC] "{surface.surface_id}" has reached a stable, consistent pattern:')
    print(f'  What I\'ve learned: {surface.description}')
    print(f'  Built from {surface.confirmation_count} confirmation(s), '
          f'{surface.correction_count} correction(s) applied along the way.')
    print('  Before this can ever be treated as more than "hold for your confirmation," '
          'I need your explicit sign-off on that understanding.')
    try:
        answer = input('Grant execution rights for this? ("confirmed" to grant, anything else to hold off): ')
    except (EOFError, KeyboardInterrupt):
        raise _SessionEnded()

    if classify_confirmation(answer) == ConfirmationResult.CONFIRMED:
        surface_signal.grant_execution_rights(surface.surface_id)
        print(f'[ALDRIC] Execution rights granted for "{surface.surface_id}".')
    else:
        print(f'[ALDRIC] Understood — holding off on "{surface.surface_id}" for now.')


def _execute_cleared_tool_call(result: CasualTurnResult) -> None:
    """The only place in ALDRIC Mode where a proposed tool call actually
    happens (core.capability_broker, README gap-list item 9). Safe to call
    unconditionally at the end of every turn because of what already ran
    before this function is ever reached: `run_casual_session` always checks
    `result.requires_escalation` first, and core.aldric_mode.requires_escalation
    already raises _Escalate — before control gets here — for any proposed
    tool call whose effective tier is C, or that touches a permanent
    category at all. So a `proposed_tool_call` surviving to this point is
    guaranteed Tier A or Tier B; this function's only job is to run it and
    log what happened, never to re-decide whether it's allowed to. The
    `tier not in (A, B)` branch below is defensive-only — it should be
    unreachable given the above — and fails closed (skips execution, logs
    why) rather than executing on an assumption.

    The actual broker call is wrapped in core.idempotency_ledger.run_idempotent
    rather than called directly — a separate, execution-integrity concern
    from the tier gating above (see that module's docstring): it stops a
    crash-and-retry of this exact turn from sending the same email or
    creating the same calendar event twice. No idempotency_key is passed
    explicitly here (ALDRIC Mode's casual turns have no stable per-attempt
    id to hand it), so it falls back to a content hash of the tool call
    itself — still dedupes the case this call site actually needs to
    guard against."""
    if not result.proposed_tool_call:
        return

    tool_name = result.proposed_tool_call["tool_name"]
    arguments = result.proposed_tool_call.get("arguments") or {}
    tier = result.signal.tool_effective_tier

    if tier not in (Tier.A, Tier.B):
        pa_action_kernel.log_digest_entry("executed_action", {
            "tool": tool_name, "status": "skipped",
            "reason": f"unexpected tier ({tier}) reached the execution point untouched by escalation",
        })
        return

    if tier == Tier.B:
        arguments = _with_pa_signature(tool_name, arguments)

    try:
        outcome = idempotency_ledger.run_idempotent(
            tool_name, arguments, capability_broker.execute_action,
        )
    except capability_broker.CapabilityBrokerError as exc:
        pa_action_kernel.log_digest_entry("executed_action", {
            "tool": tool_name, "tier": tier.value, "status": "failed", "error": str(exc),
        })
        print(f"\n[ALDRIC] That action ({tool_name}) failed to actually run: {exc}")
        return
    except (idempotency_ledger.AlreadyInFlightError, idempotency_ledger.ActionAlreadyFailedError) as exc:
        pa_action_kernel.log_digest_entry("executed_action", {
            "tool": tool_name, "tier": tier.value, "status": "skipped", "reason": str(exc),
        })
        print(f"\n[ALDRIC] Not running {tool_name} again — {exc}")
        return

    pa_action_kernel.log_digest_entry("executed_action", {
        "tool": tool_name, "tier": tier.value, "status": "success", "result": outcome,
    })
    if tier == Tier.B:
        # Tier B, PA Action Kernel: "execute then notify" — Tier A stays
        # silent (logged to digest only) on purpose.
        print(f"\n[ALDRIC] Done — {tool_name} executed (Tier B: execute then notify).")


def run_casual_session(conversation: list[dict[str, str]] | None = None) -> None:
    """Ordinary ALDRIC Mode conversation: no DSD, no Adjudication Buffer, no
    KSP Finality — just the model's reply, checked every turn against
    core.aldric_mode.requires_escalation (via CasualTurnResult.requires_escalation).
    Also handles the memory-clarification round-trip (llm/aldric_reply.py's
    needs_clarification): asks the model's proposed question, saves the
    operator's answer as a standing preference so it isn't asked again, then
    re-runs the turn with that answer so the original request actually gets
    completed rather than just remembered for later. Returns normally on
    operator exit; raises _Escalate the moment a turn requires handing off
    to Governance Stack Mode."""
    conversation = conversation if conversation is not None else []
    # Set at the end of a turn whose reply drew on a real Surface — the very
    # next operator message is checked against canonical confirmation
    # vocabulary before it's treated as anything else. Cleared every time it
    # is checked, whether or not it matched: only the immediately-next turn
    # counts (Learning Governance Section 2.2's "operator uses ALDRIC's
    # output ... " is about that output, not something several turns back).
    pending_scope = ""

    print("=" * 70)
    print("ALDRIC — ALDRIC Mode (casual)")
    print("No setup needed — just talk. I'll only pause for a proper check-in")
    print("if something we're discussing starts to look like a real commitment.")
    print("Commands: 'exit'.")
    print("=" * 70)

    while True:
        try:
            user_message = input("\nYou: ")
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            return

        if user_message.strip().lower() in ("exit", "quit"):
            return

        if pending_scope:
            scope_to_confirm, pending_scope = pending_scope, ""
            if classify_confirmation(user_message) == ConfirmationResult.CONFIRMED:
                confirmed_surface = surface_signal.record_confirmation(scope_to_confirm)
                if confirmed_surface is not None:
                    print(
                        f'[ALDRIC] Noted — confirmed for "{confirmed_surface.surface_id}" '
                        f'(now {confirmed_surface.state.value}, '
                        f'{confirmed_surface.confirmation_count} confirmation(s) on record).'
                    )
                    if (confirmed_surface.state == ConfidenceState.EXECUTABLE
                            and not confirmed_surface.execution_rights_confirmed):
                        try:
                            _present_first_executable_crossing(confirmed_surface)
                        except _SessionEnded:
                            print("\nSession ended.")
                            return
                    continue
                # Nothing on record for that scope to confirm — fall through
                # and treat this message as an ordinary turn instead.

        try:
            result = run_casual_turn(conversation, user_message)
        except Exception as exc:
            print(f"\n(ALDRIC Mode turn failed: {exc})")
            continue

        conversation.append({"role": "user", "content": user_message})

        if result.requires_escalation:
            _announce_and_escalate(conversation, result)

        if result.needs_clarification:
            question = result.clarifying_question or "Could you clarify that for me?"
            print(f"\nALDRIC: {question}")
            conversation.append({"role": "assistant", "content": question})

            try:
                answer = input("You: ")
            except (EOFError, KeyboardInterrupt):
                print("\nSession ended.")
                return

            scope = result.memory_scope or "general"
            had_prior = long_term_memory.get_preference(scope) is not None
            long_term_memory.set_preference(scope, answer)
            surface_signal.record_instruction_or_correction(scope, answer, had_prior_preference=had_prior)
            print(f'[ALDRIC] Remembered that for next time (under "{scope}") — won\'t need to ask again.')
            conversation.append({"role": "user", "content": answer})

            try:
                result = run_casual_turn(conversation, answer)
            except Exception as exc:
                print(f"\n(ALDRIC Mode turn failed: {exc})")
                continue

            if result.requires_escalation:
                _announce_and_escalate(conversation, result)
            # A second needs_clarification here isn't chased further — shown
            # as-is below rather than looping again, so one user turn can't
            # turn into an unbounded back-and-forth. The scope now has a real
            # Surface behind it either way, so it's eligible for confirmation
            # on the operator's next turn.
            pending_scope = scope
        elif result.related_scope:
            pending_scope = result.related_scope

        _execute_cleared_tool_call(result)

        display_text = result.output_text or result.clarifying_question or "(no reply)"
        print(f"\nALDRIC: {display_text}")
        conversation.append({"role": "assistant", "content": display_text})


def main() -> None:
    db.init_db()
    try:
        run_casual_session()
    except _Escalate as escalation:
        dsd = chat.run_dsd_discovery(escalation.conversation)
        # Governed conversation continues fresh from the escalation point —
        # the DSD reflection above already surfaced what was established
        # during the casual phase, so re-feeding every prior casual line
        # through run_governed_turn would just re-litigate settled ground.
        chat.run_governed_session(dsd)


if __name__ == "__main__":
    main()
