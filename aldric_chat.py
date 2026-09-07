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
this doesn't need the still-open real surface matcher (README gap-list item
1) to do this honestly.

Requires ANTHROPIC_API_KEY or Aldric-API in the environment. Run:

    export ANTHROPIC_API_KEY=sk-ant-...
    python aldric_chat.py
"""
from __future__ import annotations

import chat  # reuse Governance Stack Mode's DSD discovery + governed session loop rather than duplicating them
import core.long_term_memory as long_term_memory
import core.surface_signal as surface_signal
from llm.aldric_reply import run_casual_turn
from models.schemas import PERMANENT_TIER_C_CATEGORIES, ConfirmationResult, Tier, classify_confirmation
from storage import db


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
