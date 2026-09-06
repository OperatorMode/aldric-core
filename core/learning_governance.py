"""
Learning Governance Document — KSP-1 Autonomous Extension.

Maps to 06_Learning_Governance.md. Companion to the PA Action Kernel;
governs the self-model that kernel executes against.

The foundational principle of this whole document is a single sentence:
"ALDRIC learns what is right. Not what is approved." Everything below exists
to make that sentence a property of the code, not a hope about the model's
behaviour:

  * Signal intake (`classify_signal`) rejects approval/tone/frequency signal
    at the boundary — it is simply not representable as a `Signal` object
    that reaches the self-model. There is no downstream filter to bypass
    because there is no upstream path for it to travel on.
  * `apply_correction()` has no confidence-gated branch. Read the function:
    there is no `if surface.confidence > threshold: resist` anywhere in it.
    That absence is the "Correction Absolute" (Section 3.1) implemented as
    an engineering fact rather than an instruction to an LLM to please not
    resist correction.
  * The Structural Floor (Section 5) is represented as the union of
    `PERMANENT_TIER_C_CATEGORIES` (models.schemas, also used by the PA
    Action Kernel) and the K1 precedence constant (core.k1_safety) — both
    plain Python module-level constants with no setter function anywhere in
    this codebase.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from core.k1_safety import PRECEDENCE_ORDER
from models.schemas import (
    PERMANENT_TIER_C_CATEGORIES,
    ConfidenceState,
    ConflictRecordEntry,
    Surface,
)
from storage import db
from storage.event_log import write_event


# ---------------------------------------------------------------------------
# Component 2 — Signal Validity
# ---------------------------------------------------------------------------

class SignalType(str, Enum):
    """Section 2.2. Only these five exist as constructible signal — there is
    deliberately no ApprovalSignal / ToneSignal / ConvenienceSignal type
    anywhere in this module (Section 2.3, 'Invalid Signal')."""

    OBSERVATION = "observation"
    CORRECTION_EXPLICIT = "correction_explicit"
    CORRECTION_IMPLICIT = "correction_implicit"
    CONFIRMATION = "confirmation"
    INSTRUCTION = "instruction"
    GOVERNED_INSTRUCTION = "governed_instruction"


# Section 2.3 — kept here only as a documentation/rejection list so intake
# code has something concrete to check free-form signal descriptions
# against, e.g. when a caller tries to log something like "operator seemed
# happy with the tone". These strings are never turned into a SignalType.
INVALID_SIGNAL_DESCRIPTIONS = (
    "tone_satisfaction",
    "emotional_response_to_style",
    "engagement_frequency",
    "convenience_selection",
)


class SignalRejected(Exception):
    pass


@dataclass
class Signal:
    signal_type: SignalType
    surface_id: str
    content: str
    created_at: str


def intake_signal(signal_type: SignalType | str, surface_id: str, content: str) -> Signal:
    """The intake boundary. Section 2.1: 'The intake layer must maintain
    this distinction before signal reaches the self-model.' If `signal_type`
    is not one of the five valid SignalType values, this raises rather than
    coercing it into the nearest valid type — silently reinterpreting an
    invalid signal as a valid one would be exactly the mirror-drift failure
    mode this document exists to prevent."""
    try:
        resolved = SignalType(signal_type)
    except ValueError as exc:
        raise SignalRejected(
            f"'{signal_type}' is not valid operational truth signal (Section 2.3). "
            "Approval, tone, engagement-frequency, and convenience signal must not reach "
            "the self-model. It may be valid for the Operator Profile calibration layer instead."
        ) from exc
    return Signal(signal_type=resolved, surface_id=surface_id, content=content,
                   created_at=dt.datetime.now(dt.timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Component 3 — Operator Correction Governance (the Correction Absolute)
# ---------------------------------------------------------------------------

def apply_correction(surface: Surface, operator_instruction: str, domain: str,
                      db_path: str = db.DEFAULT_DB_PATH) -> tuple[Surface, ConflictRecordEntry]:
    """Section 3.1-3.2. 'No resistance, no delay, no condition.'

    Notice what is absent from this function's signature and body: there is
    no `surface.confidence` check that could early-return without applying
    the correction. The correction is applied first; the conflict record is
    written second (Section 3.4, 'Timing' — the observation is
    post-compliance, never a precondition for it).
    """
    model_held_before = surface.description
    surface.correction_count += 1
    surface.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    # The correction IS the update — Section 3.2: "The self-model updates to
    # reflect the correction." We record the instruction as the new
    # description; a richer implementation would merge structured fields,
    # but no implementation may add a branch that skips this update.
    surface.description = operator_instruction

    entry = ConflictRecordEntry(
        surface_id=surface.surface_id,
        model_held_before=model_held_before,
        operator_instruction=operator_instruction,
        domain=domain,
    )
    db.save_surface(surface, db_path)
    db.append_conflict_record(entry, db_path)
    write_event("CORRECTION_APPLIED", {
        "surface_id": surface.surface_id, "domain": domain,
        "model_held_before": model_held_before, "operator_instruction": operator_instruction,
    })
    return surface, entry


def individual_correction_observation(entry: ConflictRecordEntry) -> dict:
    """Section 3.4. Non-optional, informational, post-compliance. Never a
    request for explanation, never framed as an error."""
    return {
        "category": "correction_observation",
        "surface_id": entry.surface_id,
        "model_held_before": entry.model_held_before,
        "operator_instruction": entry.operator_instruction,
        "domain": entry.domain,
        "note": "Self-model has been updated accordingly.",
    }


def pattern_observation(surface_id: str, domain: str, cluster_size: int, window_description: str,
                          db_path: str = db.DEFAULT_DB_PATH) -> dict | None:
    """Section 3.5. Trigger and threshold are operator-defined per the
    document; this function does not decide what counts as clustering, it
    only renders the observation once the caller has determined clustering
    occurred. It never interprets the cluster (Section 3.5, 'Non-Interpretation
    Rule') — no 'likely cause' field, no recommendation field."""
    records = db.list_conflict_records(surface_id, db_path)
    if len(records) < cluster_size:
        return None
    return {
        "category": "pattern_observation",
        "surface_id": surface_id,
        "domain": domain,
        "correction_count": len(records),
        "window": window_description,
        "note": "Pattern surfaced for operator determination. No interpretation applied.",
    }


# ---------------------------------------------------------------------------
# Component 4 — Mirror Drift Detection
# ---------------------------------------------------------------------------

@dataclass
class MirrorDriftAssessment:
    indicators: list[str]

    @property
    def flagged(self) -> bool:
        return len(self.indicators) > 0


def _corrections_split_at_midpoint(surface: Surface, records: list[dict]) -> tuple[int, int]:
    """Split a surface's correction history into an earlier and a later
    half by time, using the surface's own lifetime midpoint
    (`created_at` -> now) as the boundary. Purely structural — no
    operator-defined window exists for this indicator any more than for
    indicator 1, so the surface's own age is the only honest boundary
    available without inventing a threshold nobody set.

    `records` are the plain dicts `storage.db.list_conflict_records` returns
    (parsed JSON, not `ConflictRecordEntry` instances) — indexed by key,
    not by attribute."""
    if not records:
        return (0, 0)
    created = dt.datetime.fromisoformat(surface.created_at)
    now = dt.datetime.now(dt.timezone.utc)
    midpoint = created + (now - created) / 2
    earlier = sum(1 for r in records if dt.datetime.fromisoformat(r["created_at"]) < midpoint)
    return (earlier, len(records) - earlier)


def detect_mirror_drift(surface: Surface, db_path: str = db.DEFAULT_DB_PATH) -> MirrorDriftAssessment:
    """Section 4.2. Honesty note: two of the four documented indicators
    ('self-model producing outputs that match approval markers' and
    'divergence between self-model predictions and objective outcomes')
    require semantic/outcome analysis and an outcome-tracking data model
    this codebase does not define yet — they are not faked here. What IS
    implemented, because both are structural facts about stored counters
    and timestamps, are indicators 1 and 2:

      1. Confidence/confirmation climbing with zero corrections ever
         recorded — the model is not being tested, only confirmed.
      2. Corrections tapering off over the surface's own lifetime while
         confirmations keep climbing — a real time-series comparison of
         the conflict record's timestamps, not a semantic judgment. Per
         Section 3.5's Non-Interpretation Rule elsewhere in this document,
         this function does not decide WHY the rate dropped (genuine
         learning vs. approval-seeking) — it only detects and surfaces the
         structural pattern; Section 4.3 reserves that determination for
         the operator explicitly ('the operator determines what the
         pattern means').

    Treat a clean result from this function as 'no structural indicator
    found', not as 'mirror drift ruled out'.
    """
    indicators: list[str] = []
    records = db.list_conflict_records(surface.surface_id, db_path)

    if surface.state == ConfidenceState.EXECUTABLE and surface.confirmation_count >= 5 and len(records) == 0:
        indicators.append(
            "confidence/confirmation climbing with zero corrections on record — "
            "model is not being tested, only confirmed (Section 4.2, indicator 1)"
        )

    if len(records) >= 2:
        earlier, later = _corrections_split_at_midpoint(surface, records)
        if earlier > 0 and later < earlier and surface.confirmation_count >= 5:
            indicators.append(
                f"corrections tapering over time ({earlier} earlier vs {later} more recent on record) "
                "while confirmations keep climbing, no interpretation applied — operator determines "
                "whether this reflects genuine learning or approval-seeking (Section 4.2, indicator 2)"
            )

    if indicators:
        write_event("MIRROR_DRIFT_FLAGGED", {"surface_id": surface.surface_id, "indicators": indicators})
    return MirrorDriftAssessment(indicators=indicators)


def apply_mirror_drift_response(surface: Surface, assessment: MirrorDriftAssessment,
                                  db_path: str = db.DEFAULT_DB_PATH) -> Surface:
    """Section 4.3: flagged surfaces have their confidence treated as
    provisional. This module does not "self-correct" — it flags and leaves
    the response determination to the operator, per 'ALDRIC does not
    self-correct mirror drift unilaterally.'"""
    if assessment.flagged:
        surface.mirror_drift_flagged = True
        surface.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
        db.save_surface(surface, db_path)
    return surface


# ---------------------------------------------------------------------------
# Component 5 — The Structural Floor
# ---------------------------------------------------------------------------

def structural_floor_summary() -> dict:
    """A read-only view of what the floor consists of. There is
    deliberately no corresponding `set_structural_floor()` anywhere in this
    codebase."""
    return {
        "k1_precedence_first": PRECEDENCE_ORDER[0] == "k1_safety_kernel",
        "permanent_tier_c_categories": sorted(PERMANENT_TIER_C_CATEGORIES),
        "correction_absolute": "apply_correction() has no confidence-gated bypass branch",
    }
