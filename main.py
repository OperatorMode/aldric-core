"""
ALDRIC Deterministic Governance Engine — FastAPI entrypoint.

Maps to the Governance Chain's role as top-level orchestrator. Every
endpoint here is a thin HTTP wrapper around the deterministic core/ modules;
none of them contain governance logic of their own. If you find yourself
wanting to add an `if` statement in this file that decides a tier, a
confirmation, or an exception, it belongs in core/, not here.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from core import governance_chain, ksp0_dsd, learning_governance, pa_action_kernel
from core.governance_chain import OperatingMode
from core.ksp1_operator_kernel import AdjudicationBuffer, LoopManager, SessionManager
from models.schemas import ActionRequest, ConfidenceState, DriftLevel, Surface, Tier
from storage import db
from storage.event_log import write_event

app = FastAPI(title="ALDRIC Deterministic Governance Engine", version="0.1.0")

loop_manager = LoopManager()
adjudication_buffer = AdjudicationBuffer()
session_manager = SessionManager()

_dsd_registry: dict[str, ksp0_dsd.DecisionSurfaceDocument] = {}
_surface_registry: dict[str, Surface] = {}


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    write_event("SERVER_STARTUP", {})


# ---------------------------------------------------------------------------
# KSP-0 / DSD
# ---------------------------------------------------------------------------

class BuildDSDRequest(BaseModel):
    decision_locus: str
    operational_domain: str
    authority_boundary: str
    time_horizon: str
    constraints_and_invariants: list[str]
    risk_posture: str


@app.post("/v1/dsd")
def create_dsd(req: BuildDSDRequest):
    try:
        dsd = ksp0_dsd.build_dsd(**req.model_dump())
    except ksp0_dsd.DSDGateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _dsd_registry[dsd.dsd_id] = dsd
    return dsd.model_dump()


class ConfirmDSDRequest(BaseModel):
    operator_utterance: str


@app.post("/v1/dsd/{dsd_id}/confirm")
def confirm_dsd(dsd_id: str, req: ConfirmDSDRequest):
    session_manager.require_not_halted()
    dsd = _dsd_registry.get(dsd_id)
    if dsd is None:
        raise HTTPException(status_code=404, detail="Unknown DSD")
    gate = ksp0_dsd.DSDGate(dsd)
    try:
        gate.confirm(req.operator_utterance)
        gate.lock()
    except ksp0_dsd.DSDGateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dsd.model_dump()


# ---------------------------------------------------------------------------
# PA Action Kernel — ALDRIC Mode action classification
# ---------------------------------------------------------------------------

class ActionClassifyRequest(BaseModel):
    tool_name: str
    arguments: dict = {}
    surface_id: str | None = None
    claimed_tier: Tier | None = None
    drift_level: DriftLevel | None = None
    mirror_drift_flagged: bool = False


@app.post("/v1/aldric/classify-action")
def classify_action(req: ActionClassifyRequest):
    action = pa_action_kernel.build_action_request(
        tool_name=req.tool_name, arguments=req.arguments, surface_id=req.surface_id,
        claimed_tier=req.claimed_tier,
    )
    surface = _surface_registry.get(req.surface_id) if req.surface_id else None
    decision = pa_action_kernel.classify_tier(
        action=action, surface=surface, drift_level=req.drift_level,
        mirror_drift_flagged=req.mirror_drift_flagged,
    )
    write_event("ACTION_CLASSIFIED", {
        "action_id": action.action_id, "tool_name": req.tool_name,
        "claimed_tier": req.claimed_tier.value if req.claimed_tier else None,
        "actual_tier": decision.tier.value, "reasons": decision.reasons,
    })
    if decision.tier == Tier.C:
        # Tier C never executes silently — a hold requires an adjudication.
        adjudication = adjudication_buffer.open(
            dsd_ref=req.surface_id or "no-dsd-ref",
            summary=f"Tier C hold on '{req.tool_name}': {'; '.join(decision.reasons)}",
            touches_permanent_tier_c=bool(action.permanent_categories),
        )
        return {
            "tier": decision.tier.value, "reasons": decision.reasons,
            "adjudication_id": adjudication.adjudication_id,
            "indicator": f"! ADJUDICATION REQUIRED — {adjudication.summary}",
        }
    return {"tier": decision.tier.value, "reasons": decision.reasons}


# ---------------------------------------------------------------------------
# Surfaces (minimal CRUD for the skeleton — a real build wires this to
# genuine observation/confirmation flows)
# ---------------------------------------------------------------------------

class CreateSurfaceRequest(BaseModel):
    description: str
    state: ConfidenceState = ConfidenceState.OBSERVING


@app.post("/v1/surfaces")
def create_surface(req: CreateSurfaceRequest):
    surface = Surface(description=req.description, state=req.state)
    _surface_registry[surface.surface_id] = surface
    db.save_surface(surface)
    return surface.model_dump()


class CorrectSurfaceRequest(BaseModel):
    operator_instruction: str
    domain: str


@app.post("/v1/surfaces/{surface_id}/correct")
def correct_surface(surface_id: str, req: CorrectSurfaceRequest):
    surface = _surface_registry.get(surface_id)
    if surface is None:
        raise HTTPException(status_code=404, detail="Unknown surface")
    surface, entry = learning_governance.apply_correction(surface, req.operator_instruction, req.domain)
    return {
        "surface": surface.model_dump(),
        "correction_observation": learning_governance.individual_correction_observation(entry),
    }


# ---------------------------------------------------------------------------
# Adjudication Buffer — Tier C double confirmation
# ---------------------------------------------------------------------------

class OpenAdjudicationRequest(BaseModel):
    dsd_ref: str
    summary: str
    touches_permanent_tier_c: bool


@app.post("/v1/adjudication")
def open_adjudication(req: OpenAdjudicationRequest):
    record = adjudication_buffer.open(req.dsd_ref, req.summary, req.touches_permanent_tier_c)
    return record.model_dump()


class UtteranceRequest(BaseModel):
    operator_utterance: str


@app.post("/v1/adjudication/{adjudication_id}/confirm-content")
def confirm_content(adjudication_id: str, req: UtteranceRequest):
    try:
        record = adjudication_buffer.confirm_content(adjudication_id, req.operator_utterance)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.model_dump()


@app.post("/v1/adjudication/{adjudication_id}/authorize-emission")
def authorize_emission(adjudication_id: str, req: UtteranceRequest):
    try:
        record = adjudication_buffer.authorize_emission(adjudication_id, req.operator_utterance)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.model_dump()


# ---------------------------------------------------------------------------
# Daily Digest
# ---------------------------------------------------------------------------

@app.get("/v1/digest")
def get_digest():
    entries = pa_action_kernel.generate_daily_digest()
    return {"entries": entries, "count": len(entries)}


# ---------------------------------------------------------------------------
# Stack load verification
# ---------------------------------------------------------------------------

class VerifyStackLoadRequest(BaseModel):
    loaded_components: list[str]
    mode: OperatingMode


@app.post("/v1/stack/verify-load")
def verify_stack_load(req: VerifyStackLoadRequest):
    report = governance_chain.verify_stack_load(req.loaded_components, req.mode)
    return {"report": report.render(), "status": report.stack_status}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
