"""
ALDRIC Governance Stack Mode — terminal chat.

This is the corrected version of the "usable chat" idea Gemini proposed.
Where that proposal skipped the DSD gate, applied the wrong mode's tier
machinery (PA Action Kernel is ALDRIC-Mode/autonomous-only — inert here),
and never actually called Claude for a reply, this script does the real
sequence from 08_Governance_Chain.md's "Governance Sequence — Governance
Stack Mode" table:

    1. Operator invokes (running this script IS the invocation)
    2. KSP-0 DSD Discovery Loop activates immediately and unconditionally
    3. Conversational interview maps responses to the six DSD fields
    4. All six fields mapped -> reflected back verbatim -> operator confirms
    5. DSD locked
    6-11. Governed reasoning turns, each citing the locked DSD; anything
          Finality-scope or touching a Permanent Tier C category first
          clears the KSP Finality sequence (core/ksp_finality.py — Phases
          1-3 and 5; Phase 6 is the Adjudication Buffer immediately below),
          then goes through the Adjudication Buffer, with the two-stage
          confirmation for permanent-category artifacts
    12. Operator may feed a ratified output back to ALDRIC's self-model —
        not wired up in this skeleton (that requires a running ALDRIC-Mode
        instance to feed; see PA Action Kernel/Learning Governance).

Requires ANTHROPIC_API_KEY or Aldric-API in the environment. Run:

    export ANTHROPIC_API_KEY=sk-ant-...
    python chat.py
"""
from __future__ import annotations

import dataclasses
import sys

from core.apex_supervisor import IDSDetector, apply_apex_response
from core.ksp0_dsd import DSDGate, DSDGateError, build_dsd
from core.ksp1_operator_kernel import AdjudicationBuffer
from core.ksp_finality import run_ksp_finality
from core.pa_action_kernel import generate_daily_digest, log_digest_entry
from llm.dsd_interview import run_interview_step
from llm.governed_reply import run_governed_turn
from llm.sidecar import SidecarIDSDetector
from models.schemas import (
    AdjudicationRecord,
    ConfirmationResult,
    DSDField,
    KSPOutcome,
    PERMANENT_TIER_C_CATEGORIES,
    Tier,
    classify_confirmation,
)
from storage import db

FIELD_LABELS = {
    DSDField.DECISION_LOCUS.value: "Decision Locus",
    DSDField.OPERATIONAL_DOMAIN.value: "Operational Domain",
    DSDField.AUTHORITY_BOUNDARY.value: "Authority Boundary",
    DSDField.TIME_HORIZON.value: "Time Horizon",
    DSDField.CONSTRAINTS_AND_INVARIANTS.value: "Constraints & Invariants",
    DSDField.RISK_POSTURE.value: "Risk Posture",
}


def _print(label: str, text: str = "") -> None:
    print(f"\n{label}{text}")


def run_dsd_discovery(
    seed_conversation: list[dict[str, str]] | None = None,
) -> "DecisionSurfaceDocument":  # noqa: F821 (imported below, forward ref only for readability)
    from models.schemas import DecisionSurfaceDocument  # local import to keep top imports lean

    print("=" * 70)
    print("ALDRIC — Governance Stack Mode")
    print("Before anything else, a few questions to establish what we're deciding.")
    print("=" * 70)

    # `seed_conversation`: aldric_chat.py's on-demand escalation passes the
    # casual conversation that triggered it, so run_interview_step can
    # extract DSD fields the operator already stated instead of asking them
    # to repeat context they just gave. chat.py's own callers never pass
    # this — plain DSD Discovery always starts cold, exactly as before.
    conversation: list[dict[str, str]] = list(seed_conversation) if seed_conversation else []
    fields: dict[str, object] = {}

    while True:
        step = run_interview_step(conversation)
        fields.update(step["extracted_fields"])
        if not step["missing_fields"]:
            break
        question = step["next_utterance"] or "Could you say more about that?"
        print(f"\nALDRIC: {question}")
        try:
            answer = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nSession abandoned during DSD Discovery.")
            sys.exit(0)
        if answer.strip().lower() in ("exit", "quit"):
            print("\nSession ended during DSD Discovery — nothing was locked.")
            sys.exit(0)
        conversation.append({"role": "assistant", "content": question})
        conversation.append({"role": "user", "content": answer})

    # Normalise constraints_and_invariants to a list before construction.
    raw_constraints = fields.get(DSDField.CONSTRAINTS_AND_INVARIANTS.value, [])
    if isinstance(raw_constraints, str):
        fields[DSDField.CONSTRAINTS_AND_INVARIANTS.value] = [raw_constraints]

    try:
        dsd = build_dsd(**fields)
    except DSDGateError as exc:
        print(f"\nCould not construct a valid Decision Surface yet: {exc}")
        print("Restarting discovery for the incomplete parts.\n")
        return run_dsd_discovery(seed_conversation)

    # Deterministic reflection (Section 11) — NOT another LLM call. We show
    # back exactly what was structurally extracted, in canonical order.
    print("\nHere is what I've understood:")
    for field in DSDField:
        value = getattr(dsd, field.value)
        rendered = "; ".join(value) if isinstance(value, list) else value
        print(f"  {FIELD_LABELS[field.value]}: {rendered}")

    gate = DSDGate(dsd)
    while True:
        try:
            utterance = input("\nIs that correct? ")
        except (EOFError, KeyboardInterrupt):
            print("\nSession abandoned before confirmation.")
            sys.exit(0)
        # Found live: neither "exit" nor a plain "no" broke out of this loop
        # before — classify_confirmation only ever distinguished CONFIRMED
        # from "everything else," so both fell through to the generic
        # re-prompt below, forever, with no way out except one of the five
        # exact confirmation words. Checked before classification, same as
        # every other input() site in this codebase already handles exit.
        if utterance.strip().lower() in ("exit", "quit"):
            print("\nSession ended before the Decision Surface was confirmed — nothing was locked.")
            sys.exit(0)
        result = classify_confirmation(utterance)
        if result == ConfirmationResult.CONFIRMED:
            try:
                gate.confirm(utterance)
                locked = gate.lock()
                print("\nDecision Surface locked. Proceeding.\n")
                return locked
            except DSDGateError as exc:
                print(f"\n{exc}")
        elif result == ConfirmationResult.DECLINED:
            # A genuine "no" — not noise, not a re-ask. Section 8.3's
            # immutability is about a LOCKED DSD; nothing here is locked
            # yet, so there's no rule being bent by going back to establish
            # it correctly. Reuses the exact same "reprint the banner, recur
            # with the accumulated conversation" recovery path a few lines
            # above already uses for a DSDGateError — one restart mechanism,
            # not two — so nothing gathered so far (including this decline)
            # is thrown away; the model tries again with it in context.
            print("\nUnderstood — that's not right. Let's go back over what needs to change.\n")
            summary_lines = [
                f'{FIELD_LABELS[field.value]}: '
                f'{"; ".join(getattr(dsd, field.value)) if isinstance(getattr(dsd, field.value), list) else getattr(dsd, field.value)}'
                for field in DSDField
            ]
            conversation.append({
                "role": "assistant",
                "content": "Here is what I understood: " + "; ".join(summary_lines),
            })
            conversation.append({"role": "user", "content": utterance})
            return run_dsd_discovery(seed_conversation=conversation)
        else:
            print("\nThat wasn't a clear yes or no in your own words — please confirm plainly (e.g. 'confirmed', 'yes').")


def _render_adjudication(record: AdjudicationRecord) -> str:
    """The complete proposed action the operator is being asked to confirm —
    not just the response text. This exists because a governed turn can
    carry a `proposed_tool_call` alongside its prose, classified and
    (if warranted) escalated to Tier C independently of the text; before
    this, `_handle_adjudication` only ever showed `output_text`, so an
    operator could type 'confirmed' having seen no indication that a
    structured tool call was attached to that turn at all. Pure function —
    no I/O — so it's directly testable without mocking `input()`."""
    lines = ["[Draft — not yet binding]", "", "Response:", record.proposed_output or "(empty)"]

    if record.proposed_tool_call:
        lines.append("")
        lines.append("Proposed action:")
        lines.append(f"  Tool: {record.proposed_tool_call.get('tool_name')}")
        arguments = record.proposed_tool_call.get("arguments") or {}
        if arguments:
            lines.append("  Arguments:")
            for key in sorted(arguments):
                lines.append(f"    {key}: {arguments[key]}")
        else:
            lines.append("  Arguments: (none)")

    lines.append("")
    lines.append("Risk classification:")
    lines.append(f"  Scope: {record.self_reported_scope or 'unknown'}")
    if record.proposed_tool_call:
        lines.append(
            f"  Tool effective tier: {record.tool_effective_tier.value if record.tool_effective_tier else 'n/a'}"
        )
    lines.append(f"  Touches Permanent Tier C: {record.touches_permanent_tier_c}")
    if record.permanent_categories:
        lines.append(f"  Categories: {sorted(record.permanent_categories)}")
    lines.append(f"  Action fingerprint: {record.action_hash[:16]}...")

    return "\n".join(lines)


def _handle_adjudication(buffer: AdjudicationBuffer, dsd_ref: str, result) -> str | None:
    """Returns the text to show the operator, or None if rejected/deferred."""
    summary = (result.output_text[:140] + "...") if len(result.output_text) > 140 else result.output_text
    record = buffer.open(
        dsd_ref=dsd_ref,
        summary=summary,
        touches_permanent_tier_c=result.touches_permanent_tier_c,
        proposed_output=result.output_text,
        proposed_tool_call=result.proposed_tool_call,
        tool_effective_tier=result.tool_effective_tier,
        permanent_categories=result.all_categories & PERMANENT_TIER_C_CATEGORIES,
        self_reported_scope=result.self_reported_scope,
    )
    print(f"\n! ADJUDICATION REQUIRED — {record.summary}")
    print(f"\n{_render_adjudication(record)}\n")
    print("The confirmation below applies exactly to the proposed action shown above —")
    print("response text and any proposed tool call together, identified by its fingerprint.")

    utterance = input("Confirm content? ('confirmed' / 'rejected' / 'deferred'): ")
    if utterance.strip().lower() == "rejected":
        buffer.reject(record.adjudication_id)
        print("Discarded.")
        log_digest_entry("held_thread", {
            "adjudication_id": record.adjudication_id, "outcome": "rejected", "summary": record.summary,
        })
        return None
    if utterance.strip().lower() == "deferred":
        buffer.defer(record.adjudication_id)
        print("Held. Not shown as final.")
        log_digest_entry("held_thread", {
            "adjudication_id": record.adjudication_id, "outcome": "deferred", "summary": record.summary,
        })
        return None

    try:
        record = buffer.confirm_content(record.adjudication_id, utterance)
    except Exception as exc:
        print(f"Not accepted: {exc}")
        return None

    if not result.touches_permanent_tier_c:
        log_digest_entry("executed_action", {
            "adjudication_id": record.adjudication_id, "outcome": "emitted", "summary": record.summary,
        })
        return result.output_text  # emitted immediately on single confirmation

    print("\nThis touches a Permanent Tier C exception. A second, distinct authorization is")
    print("required before it is treated as final: 'send it' / 'transmit now' / 'dispatch this' /")
    print("'authorize emission'. This cannot be satisfied by repeating 'confirmed'.")
    print(f"Authorizing emission releases exactly the action shown above — fingerprint "
          f"{record.action_hash[:16]}... — nothing else.")
    emission_utterance = input("Authorize emission? ")
    try:
        buffer.authorize_emission(record.adjudication_id, emission_utterance)
    except Exception as exc:
        print(f"Emission not authorized: {exc}")
        log_digest_entry("held_thread", {
            "adjudication_id": record.adjudication_id, "outcome": "emission_not_authorized",
            "summary": record.summary,
        })
        return None
    log_digest_entry("executed_action", {
        "adjudication_id": record.adjudication_id, "outcome": "emitted", "summary": record.summary,
        "touches_permanent_tier_c": True,
    })
    return result.output_text


def _build_ids_detector() -> IDSDetector:
    """The real APEX drift check, promoted to Governance Stack Mode's
    default per README gap-list item 2: a second, independently-invoked
    model call against the actual four IDS markers (llm/sidecar.py),
    not the offline HeuristicIDSDetector keyword scan. This costs one
    extra model call per turn (latency + spend) in exchange for a real
    semantic check instead of a weak surface-level one — tests substitute
    HeuristicIDSDetector here to stay deterministic and network-free."""
    return SidecarIDSDetector()


def run_governed_session(
    dsd: "DecisionSurfaceDocument",  # noqa: F821 (forward ref only for readability)
    buffer: AdjudicationBuffer | None = None,
    ids_detector: IDSDetector | None = None,
    conversation: list[dict[str, str]] | None = None,
) -> None:
    """The Governance Stack Mode conversation loop, once a DSD is already
    locked — factored out of main() so that aldric_mode's on-demand
    escalation path (aldric_chat.py) can hand off into this exact, tested
    loop after locking a DSD mid-session, instead of a second, divergence-
    prone copy of the same adjudication/KSP-Finality/APEX control flow.
    `buffer`/`ids_detector`/`conversation` are only ever overridden by
    callers that need to seed or continue state (aldric_chat.py); chat.py's
    own `main()` below always starts all three fresh."""
    buffer = buffer if buffer is not None else AdjudicationBuffer()
    ids_detector = ids_detector if ids_detector is not None else _build_ids_detector()
    conversation = conversation if conversation is not None else []

    print("Governed chat is live. Commands: 'digest', 'exit'.\n")
    while True:
        try:
            user_message = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break

        if user_message.strip().lower() in ("exit", "quit"):
            break
        if user_message.strip().lower() == "digest":
            entries = generate_daily_digest()
            print(f"\n[Digest — {len(entries)} entries]")
            for e in entries:
                print(f"  - {e['category']}: {e['payload']}")
            continue

        try:
            result = run_governed_turn(dsd, conversation, user_message)
        except Exception as exc:
            print(f"\n(Governed turn failed: {exc})")
            continue

        conversation.append({"role": "user", "content": user_message})

        assessment = ids_detector.detect(result.output_text, dsd.model_dump())
        apex_response = apply_apex_response(assessment)
        if apex_response.output_blocked:
            print(f"\n[APEX] Drift detected ({[m.value for m in assessment.markers_detected]}) — "
                  "output withheld, forced to Validation scope. Rephrase or clarify.")
            continue

        if result.requires_adjudication:
            ksp_result = None
            if result.self_reported_scope == "finality" or result.touches_permanent_tier_c:
                ksp_result = run_ksp_finality(dsd, result.output_text)

            permanent_gate_applies = result.touches_permanent_tier_c or result.tool_effective_tier == Tier.C

            if ksp_result is not None and ksp_result.outcome == KSPOutcome.DOWNGRADED_TO_VALIDATION:
                print(f"\n[KSP] {ksp_result.downgrade_reason}")
                if not permanent_gate_applies:
                    print("Forced downgrade to Validation scope — not treated as Finality; "
                          "showing as an ordinary reply.")
                    print(f"\nALDRIC: {result.output_text}")
                    conversation.append({"role": "assistant", "content": result.output_text})
                    continue
                print("Still held for adjudication: this touches a Permanent Tier C category, "
                      "which requires confirmation regardless of KSP's own outcome.")
            elif ksp_result is not None and ksp_result.outcome == KSPOutcome.CONDITIONAL:
                unresolved = [f.unknown for f in ksp_result.unknown_audit if not f.resolved]
                print(f"\n[KSP] Conditional — depends on unresolved unknown(s): {unresolved}")

            if ksp_result is not None and ksp_result.outcome != KSPOutcome.DOWNGRADED_TO_VALIDATION:
                result = dataclasses.replace(result, output_text=ksp_result.compaction_text)

            final_text = _handle_adjudication(buffer, dsd.dsd_id, result)
            if final_text is None:
                continue
            print(f"\nALDRIC: {final_text}")
            conversation.append({"role": "assistant", "content": final_text})
        else:
            print(f"\nALDRIC: {result.output_text}")
            conversation.append({"role": "assistant", "content": result.output_text})


def main() -> None:
    db.init_db()
    dsd = run_dsd_discovery()
    run_governed_session(dsd)


if __name__ == "__main__":
    main()
