"""
Operator Kernel (KSP-1).

Maps to 04_Operator_Kernel_KSP1.md.

This is the largest of the eight documents and much of it (Modes M1-M7/MX,
Layers L0-LX, the KSP Finality phases with their D_KL / EGT-manifold
language) is the operator's own vocabulary for describing reasoning
*posture*, not literal executable mathematics — KSP-0's own preamble says as
much ("The following is just language. No claim of truth."). Implementing a
fake `compute_d_kl_divergence()` that produces a number with no real
statistical meaning would be dishonest engineering — worse than not having
it, because it would look rigorous while being theater.

What this module implements for real, because these ARE structural/
state-machine claims rather than reasoning-posture claims:

  * The Loop Manager (Section 1.4) — ACTIVE/PARKED/CLOSED/SUSPENDED, the
    single-active-loop rule, and the three session-level hard stop
    conditions.
  * The Adjudication Buffer (Section 3.2) and the Tier C double-confirmation
    mechanism — the single most safety-critical, most concretely specified
    rule in the whole stack, and the one this build treats as non-negotiable.
  * Confirmation-vocabulary checking (re-exported from models.schemas).
  * The Session Log Layer and Indicator Layer as structured, machine-checked
    formats rather than free text the model might drift on.

The KSP Finality *phases themselves* (Structural Projection, Parallel
Validation, Integrity Gate, Compaction) remain LLM reasoning steps — this
module's job is to make sure nothing that comes out of them is binding
until it passes through the Adjudication Buffer below.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from models.schemas import (
    AdjudicationRecord,
    AdjudicationStage,
    LoopState,
    Scope,
    classify_confirmation,
    classify_emission_authorization,
    ConfirmationResult,
)
from storage import db
from storage.event_log import write_event


# ---------------------------------------------------------------------------
# Loop Manager — Section 1.4
# ---------------------------------------------------------------------------

class LoopManagerError(Exception):
    pass


class LoopManager:
    """Single Active Loop Rule: only one loop may be ACTIVE at any time.
    Opening a new loop automatically parks the current ACTIVE loop — this
    is not optional and does not require the operator to remember to park
    the old one themselves."""

    def __init__(self, db_path: str = db.DEFAULT_DB_PATH):
        self.db_path = db_path

    def _now(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat()

    def open_loop(self, loop_name: str) -> None:
        current_active = db.get_active_loop(self.db_path)
        if current_active and current_active != loop_name:
            db.set_loop_state(current_active, LoopState.PARKED.value, self._now(), self.db_path)
            write_event("LOOP_AUTO_PARKED", {"loop_name": current_active, "reason": f"opening {loop_name}"})
        db.set_loop_state(loop_name, LoopState.ACTIVE.value, self._now(), self.db_path)
        write_event("LOOP_OPENED", {"loop_name": loop_name})

    def reopen_loop(self, loop_name: str) -> None:
        # Reopening is identical to opening for the single-active-loop rule.
        self.open_loop(loop_name)

    def park_loop(self, loop_name: str) -> None:
        db.set_loop_state(loop_name, LoopState.PARKED.value, self._now(), self.db_path)
        write_event("LOOP_PARKED", {"loop_name": loop_name})

    def close_loop(self, loop_name: str) -> None:
        db.set_loop_state(loop_name, LoopState.CLOSED.value, self._now(), self.db_path)
        write_event("LOOP_CLOSED", {"loop_name": loop_name})

    def suspend_loop(self, loop_name: str, reason: str) -> None:
        db.set_loop_state(loop_name, LoopState.SUSPENDED.value, self._now(), self.db_path)
        write_event("LOOP_SUSPENDED", {"loop_name": loop_name, "reason": reason})

    def state_of(self, loop_name: str) -> Optional[LoopState]:
        for loop in db.list_loops(self.db_path):
            if loop["loop_name"] == loop_name:
                return LoopState(loop["state"])
        return None

    def all_loops(self) -> list[dict]:
        return db.list_loops(self.db_path)


# ---------------------------------------------------------------------------
# Session-level Hard Stop Conditions — Section 1.4
# ---------------------------------------------------------------------------

@dataclass
class SessionHaltState:
    halted: bool = False
    reason: Optional[str] = None
    halted_at: Optional[str] = None


class SessionManager:
    """Tracks the three explicit hard-stop conditions. Once halted, no new
    DSDs, KSP phases, or adjudications may open until the operator issues
    `resume: <...>` or `abandon session`."""

    def __init__(self):
        self.state = SessionHaltState()

    def halt(self, reason: str) -> None:
        self.state = SessionHaltState(halted=True, reason=reason, halted_at=dt.datetime.now(dt.timezone.utc).isoformat())
        write_event("SESSION_HALT", {"reason": reason})

    def resume(self, resolution: str) -> None:
        if not self.state.halted:
            return
        write_event("SESSION_RESUME", {"resolution": resolution, "prior_reason": self.state.reason})
        self.state = SessionHaltState()

    def abandon(self) -> None:
        write_event("SESSION_ABANDONED", {"prior_reason": self.state.reason})
        self.state = SessionHaltState()

    def require_not_halted(self) -> None:
        if self.state.halted:
            raise LoopManagerError(
                f"Session is halted ({self.state.reason}). No new DSDs, KSP phases, or "
                "adjudications may open until `resume: <address>` or `abandon session`."
            )


# ---------------------------------------------------------------------------
# Adjudication Buffer + Tier C double confirmation — Section 3.2
# ---------------------------------------------------------------------------

class AdjudicationError(Exception):
    pass


class AdjudicationBuffer:
    """No artifact is binding until explicitly confirmed. For an artifact
    touching a Permanent Tier C exception, content confirmation and emission
    authorization are two distinct operator actions from two distinct calls
    to this class — there is no code path where calling `confirm_content`
    also satisfies `authorize_emission`, regardless of what text is passed."""

    def __init__(self, db_path: str = db.DEFAULT_DB_PATH):
        self.db_path = db_path

    def open(self, dsd_ref: str, summary: str, touches_permanent_tier_c: bool) -> AdjudicationRecord:
        record = AdjudicationRecord(
            dsd_ref=dsd_ref, summary=summary, touches_permanent_tier_c=touches_permanent_tier_c
        )
        db.save_adjudication(record, self.db_path)
        write_event(
            "ADJUDICATION_OPENED",
            {"adjudication_id": record.adjudication_id, "summary": summary, "dsd_ref": dsd_ref,
             "touches_permanent_tier_c": touches_permanent_tier_c},
        )
        return record

    def _load(self, adjudication_id: str) -> AdjudicationRecord:
        data = db.get_adjudication(adjudication_id, self.db_path)
        if not data:
            raise AdjudicationError(f"No adjudication record {adjudication_id}")
        return AdjudicationRecord(**data)

    def confirm_content(self, adjudication_id: str, operator_utterance: str) -> AdjudicationRecord:
        record = self._load(adjudication_id)
        if record.stage != AdjudicationStage.PENDING_CONTENT_CONFIRMATION:
            raise AdjudicationError(
                f"Adjudication {adjudication_id} is in stage {record.stage}, not awaiting content "
                "confirmation."
            )
        if classify_confirmation(operator_utterance) != ConfirmationResult.CONFIRMED:
            raise AdjudicationError(
                "Ambiguous response — not a canonical confirmation. Re-ask plainly (Section 4)."
            )
        record.content_confirmed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        record.stage = (
            AdjudicationStage.PENDING_EMISSION_AUTHORIZATION
            if record.touches_permanent_tier_c
            else AdjudicationStage.EMITTED
        )
        db.save_adjudication(record, self.db_path)
        write_event(
            "ADJUDICATION_CONTENT_CONFIRMED",
            {
                "adjudication_id": adjudication_id,
                "next_stage": record.stage.value,
                "note": (
                    "Content ratified only — this does NOT authorize emission "
                    "(KSP-0 Section 9.5 / PA Action Kernel Section 3.4)."
                    if record.touches_permanent_tier_c
                    else "Not a Permanent Tier C artifact — content confirmation is sufficient."
                ),
            },
        )
        return record

    def authorize_emission(self, adjudication_id: str, operator_utterance: str) -> AdjudicationRecord:
        """The second, distinct confirmation. `operator_utterance` must be an
        EXACT match (case/space-normalized) against EMISSION_VOCABULARY —
        not merely contain an emission word alongside confirmation language.
        'confirmed — transmit' is rejected by design (see models.schemas
        docstring and the KSP-1 Section 3.2 worked example)."""
        record = self._load(adjudication_id)
        if not record.touches_permanent_tier_c:
            raise AdjudicationError(
                "This artifact does not touch a Permanent Tier C exception — there is no "
                "separate emission stage to authorize. Content confirmation already emitted it."
            )
        if record.stage != AdjudicationStage.PENDING_EMISSION_AUTHORIZATION:
            raise AdjudicationError(
                f"Adjudication {adjudication_id} is in stage {record.stage}, not awaiting "
                "emission authorization. Content must be confirmed first, in a separate call."
            )
        if not classify_emission_authorization(operator_utterance):
            raise AdjudicationError(
                "Not a valid emission authorization. Valid tokens: send it, transmit now, "
                "dispatch this, authorize emission. Confirmation vocabulary (confirmed/correct/"
                "yes/proceed/locked) does not count, even combined with other words."
            )
        record.emission_authorized_at = dt.datetime.now(dt.timezone.utc).isoformat()
        record.stage = AdjudicationStage.EMITTED
        db.save_adjudication(record, self.db_path)
        write_event("ADJUDICATION_EMISSION_AUTHORIZED", {"adjudication_id": adjudication_id})
        return record

    def reject(self, adjudication_id: str) -> AdjudicationRecord:
        record = self._load(adjudication_id)
        record.stage = AdjudicationStage.REJECTED
        db.save_adjudication(record, self.db_path)
        write_event("ADJUDICATION_REJECTED", {"adjudication_id": adjudication_id})
        return record

    def defer(self, adjudication_id: str) -> AdjudicationRecord:
        record = self._load(adjudication_id)
        record.stage = AdjudicationStage.DEFERRED
        db.save_adjudication(record, self.db_path)
        write_event("ADJUDICATION_DEFERRED", {"adjudication_id": adjudication_id})
        return record

    def is_binding(self, adjudication_id: str) -> bool:
        return self._load(adjudication_id).stage == AdjudicationStage.EMITTED


def format_indicator_flag(adjudication: AdjudicationRecord) -> str:
    """Section 3.2, 'The Indicator'. Must always include a one-line
    plain-language summary, visible inline without issuing a command."""
    return f"! ADJUDICATION REQUIRED — {adjudication.summary}"


# ---------------------------------------------------------------------------
# Session Log Layer + Indicator Layer — Structured Observability & Control
# ---------------------------------------------------------------------------

@dataclass
class SessionLogEntry:
    profile: str
    mode_posture: str
    layer_emphasis: str
    scope: Scope
    loop_state_note: str
    note: str
    timestamp: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    def render(self) -> str:
        return (
            "[SESSION LOG]\n"
            f"Time: {self.timestamp}\n"
            f"Profile: {self.profile}\n"
            f"Mode Posture: {self.mode_posture}\n"
            f"Layer Emphasis: {self.layer_emphasis}\n"
            f"Scope: {self.scope.value}\n"
            f"Loop State: {self.loop_state_note}\n"
            f"Note: {self.note}"
        )
