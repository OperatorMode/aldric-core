"""
ALDRIC Governance Stack Mode — browser chat UI.

This is `chat.py`'s exact governed sequence (08_Governance_Chain.md's
"Governance Sequence — Governance Stack Mode" table), reshaped for a
WebSocket instead of a blocking `input()` loop, so the same governance
kernel can be exercised from a browser instead of a terminal. Nothing about
the gating logic changes: every function this module calls is imported
directly from `core/` and `llm/` — the exact same DSDGate, AdjudicationBuffer,
APEX response, and KSP Finality sequence `chat.py` uses. This file adds a
transport and a nicer rendering of the same events `chat.py` prints; it does
not reimplement, relax, or shortcut any gate.

In particular, the "nicer UI" buttons (Confirm / Reject / Defer, Send it /
Hold) do not bypass `classify_confirmation` / `classify_emission_authorization`.
A button click just submits the same literal canonical phrase a person would
otherwise have had to type exactly right in a terminal (e.g. the "Confirm"
button sends the literal string "confirmed", "Send it" sends the literal
string "send it") — the deterministic vocabulary check in models/schemas.py
still runs on it unchanged. This module has no code path that marks content
confirmed or emission authorized without that check passing.

Run:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...   # or Aldric-API
    python webapp.py
    # then open http://127.0.0.1:8000 in a browser
"""
from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from core.apex_supervisor import apply_apex_response
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

_STATIC_DIR = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="ALDRIC Governed Chat — Web UI")

Send = Callable[[dict[str, Any]], Awaitable[None]]


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


class GovernedWebSession:
    """One browser tab's worth of state. Mirrors chat.py's `main()` +
    `run_dsd_discovery()` local variables, just held on an object instead of
    living as locals in a blocking loop."""

    def __init__(self) -> None:
        self.phase = "dsd_discovery"  # dsd_discovery -> dsd_confirm -> chat -> (awaiting_confirm | awaiting_emission) -> chat...
        self.dsd_conversation: list[dict[str, str]] = []
        self.fields: dict[str, object] = {}
        self.last_question: str = ""
        self.dsd = None
        self.chat_conversation: list[dict[str, str]] = []
        self.buffer = AdjudicationBuffer()
        self.ids_detector = SidecarIDSDetector()
        self.pending_record_id: str | None = None


def _render_record(record: AdjudicationRecord) -> dict:
    """The complete proposed action, for the confirmation card — the web
    analogue of chat.py's `_render_adjudication`."""
    return {
        "adjudication_id": record.adjudication_id,
        "response": record.proposed_output or "(empty)",
        "tool_call": record.proposed_tool_call,
        "scope": record.self_reported_scope or "unknown",
        "tool_effective_tier": record.tool_effective_tier.value if record.tool_effective_tier else None,
        "touches_permanent_tier_c": record.touches_permanent_tier_c,
        "categories": sorted(record.permanent_categories) if record.permanent_categories else [],
        "fingerprint": (record.action_hash[:16] + "...") if record.action_hash else "",
        "stage": record.stage.value,
    }


def _render_dsd_fields(dsd) -> dict[str, str]:
    rendered = {}
    for field in DSDField:
        value = getattr(dsd, field.value)
        rendered[FIELD_LABELS[field.value]] = "; ".join(value) if isinstance(value, list) else value
    return rendered


async def _process_interview_step(session: GovernedWebSession, step: dict, send: Send) -> None:
    """Shared by the opening turn and every subsequent turn — mirrors
    chat.py's `while True: step = run_interview_step(...); if not
    step["missing_fields"]: break` exactly: missing_fields is checked
    BEFORE ever asking a question, including on the very first call, so a
    fully-specified opening message never gets an unnecessary follow-up
    question."""
    session.fields.update(step["extracted_fields"])

    if step["missing_fields"]:
        question = step["next_utterance"] or "Could you say more about that?"
        session.last_question = question
        await send({"type": "assistant", "text": question})
        return

    raw_constraints = session.fields.get(DSDField.CONSTRAINTS_AND_INVARIANTS.value, [])
    if isinstance(raw_constraints, str):
        session.fields[DSDField.CONSTRAINTS_AND_INVARIANTS.value] = [raw_constraints]

    try:
        dsd = build_dsd(**session.fields)
    except DSDGateError as exc:
        await send({
            "type": "system",
            "text": f"Could not construct a valid Decision Surface yet: {exc}. Restarting discovery for the incomplete parts.",
        })
        session.dsd_conversation = []
        session.fields = {}
        restart_step = await run_in_threadpool(run_interview_step, [])
        await _process_interview_step(session, restart_step, send)
        return

    session.dsd = dsd
    session.phase = "dsd_confirm"
    await send({"type": "dsd_reflection", "fields": _render_dsd_fields(dsd)})


async def _start_dsd_discovery(session: GovernedWebSession, send: Send) -> None:
    await send({"type": "system", "text": "ALDRIC — Governance Stack Mode"})
    await send({"type": "system", "text": "Before anything else, a few questions to establish what we're deciding."})
    step = await run_in_threadpool(run_interview_step, [])
    await _process_interview_step(session, step, send)


async def _handle_dsd_discovery(session: GovernedWebSession, text: str, send: Send) -> None:
    session.dsd_conversation.append({"role": "assistant", "content": session.last_question})
    session.dsd_conversation.append({"role": "user", "content": text})
    step = await run_in_threadpool(run_interview_step, session.dsd_conversation)
    await _process_interview_step(session, step, send)


async def _handle_dsd_confirm(session: GovernedWebSession, text: str, send: Send) -> None:
    result = classify_confirmation(text)
    if result != ConfirmationResult.CONFIRMED:
        await send({
            "type": "system",
            "text": "That wasn't a clear yes or no in your own words — please confirm plainly (e.g. 'confirmed', 'yes').",
        })
        return

    gate = DSDGate(session.dsd)
    try:
        gate.confirm(text)
        gate.lock()
    except DSDGateError as exc:
        await send({"type": "error", "text": str(exc)})
        return

    session.phase = "chat"
    await send({"type": "system", "text": "Decision Surface locked. Proceeding."})
    await send({"type": "system", "text": "Governed chat is live. Use the Digest button any time to see everything logged so far."})


async def _open_adjudication(session: GovernedWebSession, result, send: Send) -> None:
    summary = (result.output_text[:140] + "...") if len(result.output_text) > 140 else result.output_text
    record = await run_in_threadpool(
        session.buffer.open,
        dsd_ref=session.dsd.dsd_id,
        summary=summary,
        touches_permanent_tier_c=result.touches_permanent_tier_c,
        proposed_output=result.output_text,
        proposed_tool_call=result.proposed_tool_call,
        tool_effective_tier=result.tool_effective_tier,
        permanent_categories=result.all_categories & PERMANENT_TIER_C_CATEGORIES,
        self_reported_scope=result.self_reported_scope,
    )
    session.pending_record_id = record.adjudication_id
    session.phase = "awaiting_confirm"
    await send({
        "type": "adjudication",
        "text": "Response text and any proposed tool call together, identified by its fingerprint below — "
                "the confirmation you give applies exactly to what's shown, nothing else.",
        "record": _render_record(record),
    })


async def _handle_chat(session: GovernedWebSession, msg: dict, send: Send) -> None:
    if msg.get("type") == "action" and msg.get("name") == "digest":
        entries = await run_in_threadpool(generate_daily_digest)
        await send({"type": "digest", "entries": entries})
        return

    if msg.get("type") != "text":
        return
    user_message = msg["text"]

    if user_message.strip().lower() in ("exit", "quit"):
        session.phase = "ended"
        await send({"type": "system", "text": "Session ended."})
        return
    if user_message.strip().lower() == "digest":
        entries = await run_in_threadpool(generate_daily_digest)
        await send({"type": "digest", "entries": entries})
        return

    try:
        result = await run_in_threadpool(run_governed_turn, session.dsd, session.chat_conversation, user_message)
    except Exception as exc:
        await send({"type": "error", "text": f"Governed turn failed: {exc}"})
        return

    session.chat_conversation.append({"role": "user", "content": user_message})

    assessment = await run_in_threadpool(session.ids_detector.detect, result.output_text, session.dsd.model_dump())
    apex_response = apply_apex_response(assessment)
    if apex_response.output_blocked:
        await send({
            "type": "apex_blocked",
            "markers": [m.value for m in assessment.markers_detected],
        })
        return

    if not result.requires_adjudication:
        await send({"type": "assistant", "text": result.output_text})
        session.chat_conversation.append({"role": "assistant", "content": result.output_text})
        return

    ksp_result = None
    if result.self_reported_scope == "finality" or result.touches_permanent_tier_c:
        ksp_result = await run_in_threadpool(run_ksp_finality, session.dsd, result.output_text)

    permanent_gate_applies = result.touches_permanent_tier_c or result.tool_effective_tier == Tier.C

    if ksp_result is not None and ksp_result.outcome == KSPOutcome.DOWNGRADED_TO_VALIDATION:
        await send({"type": "system", "text": f"[KSP] {ksp_result.downgrade_reason}"})
        if not permanent_gate_applies:
            await send({
                "type": "system",
                "text": "Forced downgrade to Validation scope — not treated as Finality; showing as an ordinary reply.",
            })
            await send({"type": "assistant", "text": result.output_text})
            session.chat_conversation.append({"role": "assistant", "content": result.output_text})
            return
        await send({
            "type": "system",
            "text": "Still held for adjudication: this touches a Permanent Tier C category, which requires "
                    "confirmation regardless of KSP's own outcome.",
        })
    elif ksp_result is not None and ksp_result.outcome == KSPOutcome.CONDITIONAL:
        unresolved = [f.unknown for f in ksp_result.unknown_audit if not f.resolved]
        await send({"type": "system", "text": f"[KSP] Conditional — depends on unresolved unknown(s): {unresolved}"})

    if ksp_result is not None and ksp_result.outcome != KSPOutcome.DOWNGRADED_TO_VALIDATION:
        result = dataclasses.replace(result, output_text=ksp_result.compaction_text)

    await _open_adjudication(session, result, send)


async def _handle_awaiting_confirm(session: GovernedWebSession, msg: dict, send: Send) -> None:
    record_id = session.pending_record_id
    action = msg.get("name") if msg.get("type") == "action" else None

    if action == "reject":
        record = await run_in_threadpool(session.buffer.reject, record_id)
        log_digest_entry("held_thread", {"adjudication_id": record.adjudication_id, "outcome": "rejected", "summary": record.summary})
        session.phase, session.pending_record_id = "chat", None
        await send({"type": "system", "text": "Discarded."})
        return

    if action == "defer":
        record = await run_in_threadpool(session.buffer.defer, record_id)
        log_digest_entry("held_thread", {"adjudication_id": record.adjudication_id, "outcome": "deferred", "summary": record.summary})
        session.phase, session.pending_record_id = "chat", None
        await send({"type": "system", "text": "Held. Not shown as final."})
        return

    if action == "confirm":
        utterance = "confirmed"
    elif msg.get("type") == "text":
        utterance = msg["text"]
    else:
        return

    try:
        record = await run_in_threadpool(session.buffer.confirm_content, record_id, utterance)
    except Exception as exc:
        await send({"type": "error", "text": f"Not accepted: {exc}"})
        return

    if not record.touches_permanent_tier_c:
        log_digest_entry("executed_action", {"adjudication_id": record.adjudication_id, "outcome": "emitted", "summary": record.summary})
        session.chat_conversation.append({"role": "assistant", "content": record.proposed_output})
        session.phase, session.pending_record_id = "chat", None
        await send({"type": "assistant", "text": record.proposed_output})
        return

    session.phase = "awaiting_emission"
    await send({
        "type": "emission_request",
        "text": "This touches a Permanent Tier C exception. A second, distinct authorization is required "
                "before it's treated as final — content confirmation alone does not satisfy it.",
        "record": _render_record(record),
    })


async def _handle_awaiting_emission(session: GovernedWebSession, msg: dict, send: Send) -> None:
    record_id = session.pending_record_id
    action = msg.get("name") if msg.get("type") == "action" else None

    if action == "cancel_emission":
        record = await run_in_threadpool(session.buffer.defer, record_id)
        log_digest_entry("held_thread", {"adjudication_id": record.adjudication_id, "outcome": "deferred_at_emission", "summary": record.summary})
        session.phase, session.pending_record_id = "chat", None
        await send({"type": "system", "text": "Held. Not authorized for emission."})
        return

    if action == "authorize_emission":
        utterance = "send it"
    elif msg.get("type") == "text":
        utterance = msg["text"]
    else:
        return

    try:
        record = await run_in_threadpool(session.buffer.authorize_emission, record_id, utterance)
    except Exception as exc:
        log_digest_entry("held_thread", {"adjudication_id": record_id, "outcome": "emission_not_authorized"})
        session.phase, session.pending_record_id = "chat", None
        await send({"type": "error", "text": f"Emission not authorized: {exc}"})
        return

    log_digest_entry("executed_action", {
        "adjudication_id": record.adjudication_id, "outcome": "emitted",
        "summary": record.summary, "touches_permanent_tier_c": True,
    })
    session.chat_conversation.append({"role": "assistant", "content": record.proposed_output})
    session.phase, session.pending_record_id = "chat", None
    await send({"type": "assistant", "text": record.proposed_output})


async def handle_message(session: GovernedWebSession, msg: dict, send: Send) -> None:
    if session.phase == "ended":
        await send({"type": "system", "text": "Session already ended. Refresh the page to start a new one."})
        return
    if session.phase == "dsd_discovery":
        if msg.get("type") == "text":
            await _handle_dsd_discovery(session, msg["text"], send)
        return
    if session.phase == "dsd_confirm":
        if msg.get("type") == "action" and msg.get("name") == "confirm":
            await _handle_dsd_confirm(session, "confirmed", send)
        elif msg.get("type") == "text":
            await _handle_dsd_confirm(session, msg["text"], send)
        return
    if session.phase == "chat":
        await _handle_chat(session, msg, send)
        return
    if session.phase == "awaiting_confirm":
        await _handle_awaiting_confirm(session, msg, send)
        return
    if session.phase == "awaiting_emission":
        await _handle_awaiting_emission(session, msg, send)
        return


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    session = GovernedWebSession()

    async def send(event: dict) -> None:
        await websocket.send_json(event)

    try:
        await _start_dsd_discovery(session, send)
        while True:
            msg = await websocket.receive_json()
            try:
                await handle_message(session, msg, send)
            except Exception as exc:  # keep the socket alive on an unexpected error
                await send({"type": "error", "text": f"Internal error: {exc}"})
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webapp:app", host="127.0.0.1", port=8000, reload=False)
