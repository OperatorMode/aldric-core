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
          Finality-scope or touching a Permanent Tier C category goes
          through the Adjudication Buffer, with the two-stage confirmation
          for permanent-category artifacts
    12. Operator may feed a ratified output back to ALDRIC's self-model —
        not wired up in this skeleton (that requires a running ALDRIC-Mode
        instance to feed; see PA Action Kernel/Learning Governance).

Requires ANTHROPIC_API_KEY or Aldric-API in the environment. Run:

    export ANTHROPIC_API_KEY=sk-ant-...
    python chat.py
"""
from __future__ import annotations

import sys

from core.apex_supervisor import HeuristicIDSDetector, apply_apex_response
from core.ksp0_dsd import DSDGate, DSDGateError, build_dsd
from core.ksp1_operator_kernel import AdjudicationBuffer
from core.pa_action_kernel import generate_daily_digest, log_digest_entry
from llm.dsd_interview import run_interview_step
from llm.governed_reply import run_governed_turn
from models.schemas import (
    ConfirmationResult,
    DSDField,
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


def run_dsd_discovery() -> "DecisionSurfaceDocument":  # noqa: F821 (imported below, forward ref only for readability)
    from models.schemas import DecisionSurfaceDocument  # local import to keep top imports lean

    print("=" * 70)
    print("ALDRIC — Governance Stack Mode")
    print("Before anything else, a few questions to establish what we're deciding.")
    print("=" * 70)

    conversation: list[dict[str, str]] = []
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
        return run_dsd_discovery()

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
        result = classify_confirmation(utterance)
        if result == ConfirmationResult.CONFIRMED:
            try:
                gate.confirm(utterance)
                locked = gate.lock()
                print("\nDecision Surface locked. Proceeding.\n")
                return locked
            except DSDGateError as exc:
                print(f"\n{exc}")
        else:
            print("\nThat wasn't a clear yes or no in your own words — please confirm plainly (e.g. 'confirmed', 'yes').")


def _handle_adjudication(buffer: AdjudicationBuffer, dsd_ref: str, result) -> str | None:
    """Returns the text to show the operator, or None if rejected/deferred."""
    summary = (result.output_text[:140] + "...") if len(result.output_text) > 140 else result.output_text
    record = buffer.open(dsd_ref=dsd_ref, summary=summary, touches_permanent_tier_c=result.touches_permanent_tier_c)
    print(f"\n! ADJUDICATION REQUIRED — {record.summary}")
    if result.touches_permanent_tier_c:
        print(f"  (touches permanent categories: {sorted(result.all_categories)})")
    print(f"\n[Draft output — not yet binding]\n{result.output_text}\n")

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


def main() -> None:
    db.init_db()
    dsd = run_dsd_discovery()
    buffer = AdjudicationBuffer()
    ids_detector = HeuristicIDSDetector()
    conversation: list[dict[str, str]] = []

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
            final_text = _handle_adjudication(buffer, dsd.dsd_id, result)
            if final_text is None:
                continue
            print(f"\nALDRIC: {final_text}")
            conversation.append({"role": "assistant", "content": final_text})
        else:
            print(f"\nALDRIC: {result.output_text}")
            conversation.append({"role": "assistant", "content": result.output_text})


if __name__ == "__main__":
    main()
