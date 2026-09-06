"""
APEX Supervisor.

Maps to 03_APEX_Supervisor.md.

Honesty note: detecting the four Intrinsic Drift Signature (IDS) markers —
Projection Match, Validation Hype, Hidden Truth Claims, Momentum
Hallucination — is a semantic judgment about a block of text. That is not
something a regex can do reliably, and pretending otherwise would just move
the "trust the prompt" problem one layer down instead of removing it.

So this module is deliberately split in two:

  * The DETECTION step (`IDSDetector` / `SidecarIDSDetector`) is a real,
    separate model call — a second, independently-invoked model scoring the
    primary model's output against the four markers — exactly Gemini's
    "Sidecar Auditor" idea, wired to the real APEX criteria instead of the
    generic ones. See llm/sidecar.py. A `HeuristicIDSDetector` fallback is
    provided for running without API access (e.g. in tests), and it is
    explicitly weaker — say so if asked, do not oversell it.
  * The RESPONSE step (`apply_apex_response`) is fully deterministic. Once
    ANY detector says IDS is present, the response (force M7, downgrade to
    Validation, log, block output, notify) does not depend on which
    detector produced the finding or how confident it sounded.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from models.schemas import Scope
from storage.event_log import write_event


class IDSMarker(str, Enum):
    PROJECTION_MATCH = "projection_match"
    VALIDATION_HYPE = "validation_hype"
    HIDDEN_TRUTH_CLAIMS = "hidden_truth_claims"
    MOMENTUM_HALLUCINATION = "momentum_hallucination"


@dataclass
class IDSAssessment:
    markers_detected: list[IDSMarker]
    rationale: str

    @property
    def is_drifting(self) -> bool:
        return len(self.markers_detected) > 0


class IDSDetector(Protocol):
    def detect(self, candidate_output: str, dsd_context: dict) -> IDSAssessment: ...


class HeuristicIDSDetector:
    """A deliberately weak, offline fallback. Flags gross, surface-level
    cues only (excess exclamation/superlatives as a crude Validation Hype
    proxy; absolute claims like 'guaranteed'/'proven'/'always works' as a
    crude Hidden Truth Claims proxy). This is NOT a substitute for the real
    sidecar model call in llm/sidecar.py — use that in production."""

    _HYPE_MARKERS = ("amazing", "incredible", "perfect", "flawless", "guaranteed to")
    _HIDDEN_TRUTH_MARKERS = ("guaranteed", "proven fact", "always works", "never fails")

    def detect(self, candidate_output: str, dsd_context: dict) -> IDSAssessment:
        lowered = candidate_output.lower()
        markers = []
        if any(m in lowered for m in self._HYPE_MARKERS):
            markers.append(IDSMarker.VALIDATION_HYPE)
        if any(m in lowered for m in self._HIDDEN_TRUTH_MARKERS):
            markers.append(IDSMarker.HIDDEN_TRUTH_CLAIMS)
        return IDSAssessment(
            markers_detected=markers,
            rationale="heuristic keyword scan (weak fallback — no semantic model call made)",
        )


class APEXPassiveModeViolation(Exception):
    """Section 6: during DSD Discovery, APEX may log anomalies internally
    but may not interrupt, redirect, or modify the discovery conversation."""


def apex_observe_during_dsd(candidate_action: str) -> None:
    """Call sites inside the KSP-0 discovery loop must route any APEX
    interaction through here. Anything other than passive logging raises."""
    if candidate_action not in ("log_anomaly",):
        raise APEXPassiveModeViolation(
            f"APEX attempted '{candidate_action}' during DSD Discovery. Section 6 permits "
            "passive logging only — no interrupt, redirect, or modification of the discovery "
            "conversation is allowed while DSD Supremacy holds."
        )


@dataclass
class APEXResponse:
    forced_scope: Scope
    output_blocked: bool
    operator_notified: bool


def apply_apex_response(assessment: IDSAssessment) -> APEXResponse:
    """Section 5, 'APEX Response to IDS Detection'. Deterministic — does not
    branch on which marker(s) fired or how the detector phrased its
    rationale, only on whether any marker fired at all."""
    if not assessment.is_drifting:
        return APEXResponse(forced_scope=Scope.EXPLORATION, output_blocked=False, operator_notified=False)

    write_event(
        "APEX_IDS_DETECTED",
        {
            "markers": [m.value for m in assessment.markers_detected],
            "rationale": assessment.rationale,
        },
    )
    return APEXResponse(forced_scope=Scope.VALIDATION, output_blocked=True, operator_notified=True)
