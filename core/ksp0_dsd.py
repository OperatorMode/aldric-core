"""
KSP-0 / DSD Protocol — the Primary Fuse.

Maps to 01_KSP-0_DSD.md.

What is genuinely deterministic here (enforced in code, cannot be talked
around by a prompt): the six-field schema, the completeness test, the
confirmation-vocabulary matcher, and immutability once locked.

What is NOT deterministic, and is not pretended to be: the DSD Discovery
Loop itself (the "skilled interviewer" conversational extraction of the six
fields from natural language — Sections 5-7) is a reasoning task. It stays
an LLM call. This module's job is to make sure that call's output is
worthless until it passes the gate below — the LLM proposes field values,
this module is the only thing that can mark a DSD confirmed and locked.
"""
from __future__ import annotations

import datetime as dt

from models.schemas import (
    ConfirmationResult,
    DecisionSurfaceDocument,
    classify_confirmation,
)
from storage import db
from storage.event_log import write_event


class DSDGateError(Exception):
    """Raised whenever code — not a prompt — refuses to proceed."""


class DSDGate:
    """Section 9 — Supremacy Enforcement.

    'All inference, routing, generation, and interpretation are suspended
    until the Decision Surface is fully bound and confirmed.'

    Nothing downstream (APEX unlocking, KSP arming, Finality artifacts) is
    reachable from this class except through `lock()`, and `lock()` refuses
    unless the completeness test passes and a canonical confirmation has
    been supplied.
    """

    def __init__(self, dsd: DecisionSurfaceDocument):
        self.dsd = dsd

    def completeness_check(self) -> None:
        passed, failures = self.dsd.completeness_test()
        if not passed:
            raise DSDGateError(
                f"DSD Completeness Test failed (Section 8.1): missing/invalid fields {failures}. "
                "Forced downgrade to Validation scope — no Finality artifact may cite this DSD."
            )

    def confirm(self, operator_utterance: str) -> DecisionSurfaceDocument:
        """Section 8.4 (Verification) + Section 10 (Confirmation Vocabulary).

        Confirmation is checked against the literal canonical vocabulary.
        It is never inferred from tone, enthusiasm, or conversational
        momentum — that is precisely the IDS 'Projection Match' / 'Validation
        Hype' failure mode APEX exists to catch (03_APEX_Supervisor.md
        Section 5), and it is not left to the LLM to self-police here.
        """
        if self.dsd.locked:
            raise DSDGateError(
                "Section 8.3 Immutability: this DSD is already locked. "
                "To change it you must abort KSP entirely and restart from surface binding — "
                "there is no in-place edit path."
            )
        self.completeness_check()
        result = classify_confirmation(operator_utterance)
        if result != ConfirmationResult.CONFIRMED:
            raise DSDGateError(
                "Ambiguous response. Section 10: silence and non-response do not constitute "
                "confirmation, and protocol language cannot substitute for it. Re-ask plainly."
            )
        self.dsd.confirmed = True
        self.dsd.confirmed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        write_event("KSP0_DSD_CONFIRMED", {"dsd_id": self.dsd.dsd_id})
        return self.dsd

    def lock(self) -> DecisionSurfaceDocument:
        """Only reachable after `confirm()`. Locking is what allows APEX to
        unlock and KSP to arm (Section 12, Exit Condition)."""
        if not self.dsd.confirmed:
            raise DSDGateError(
                "Cannot lock an unconfirmed DSD. Exit Condition (Section 12) requires operator "
                "confirmation before APEX unlock, mode routing resume, or KSP Phase 1."
            )
        self.dsd.locked = True
        db.save_dsd(self.dsd)
        write_event("KSP0_DSD_LOCKED", {"dsd_id": self.dsd.dsd_id})
        return self.dsd


def build_dsd(**fields) -> DecisionSurfaceDocument:
    """Construct a DSD from already-extracted field values.

    Field extraction from free-form conversation (the actual Discovery
    Loop interview) is expected to happen in the LLM layer (see
    llm/client.py) and be passed in here as plain field values — this
    function does not itself talk to a model. It exists as the single
    point where a dict of proposed fields becomes a real DecisionSurfaceDocument,
    subject to the pydantic validators in models/schemas.py.
    """
    try:
        return DecisionSurfaceDocument(**fields)
    except Exception as exc:  # re-raise as a DSDGateError for a uniform error surface
        raise DSDGateError(f"DSD construction failed: {exc}") from exc
