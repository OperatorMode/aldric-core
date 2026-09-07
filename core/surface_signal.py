"""
Surface Signal — wiring live ALDRIC Mode conversation into Learning
Governance's self-model.

`core/learning_governance.py` implements the Learning Governance Document
faithfully but, until now, nothing in a real conversation ever called it —
`core/long_term_memory.py`'s own docstring says as much: the confidence-per-
Surface layer "isn't wired up here: it needs a real Surface to attach to."

That statement is true for one kind of Surface use and not the other, and
the distinction matters enough to spell out:

  * PA Action Kernel's Surface Matching (Section 2.2, "contextual fit") is
    the hard, still-open problem: given a brand-new situation with no label
    attached, decide semantically which of many existing surfaces (if any)
    it's an instance of. `core.pa_action_kernel.NullSurfaceMatcher` stands in
    for that and stays exactly as it is — this module does not touch it, and
    README gap-list item 1 (the real surface matcher) is still open work.
  * What this module does is a different, much simpler problem: ALDRIC Mode
    already tags a scope explicitly, by name, at the moment a standing
    preference is set or a clarifying question is answered ("client:acme",
    "team", "boss" — see `llm/aldric_reply.py`'s `memory_scope`/
    `related_scope`). There is no matching to do because the label is never
    ambiguous — it's supplied, not inferred from an untagged situation. Using
    that same scope string as a Surface's `surface_id` gives Learning
    Governance a real, stable identity to accumulate confirmation and
    correction history against, with no semantic judgment required at all.

So: this closes the "surfaces accumulate real confidence" half of README
gap-list items 5 and 7 for scope-tagged conversation, without needing item 1.
It does not, and cannot, close item 1 itself — a proposed tool call in
`llm/aldric_reply.py` still resolves through `classify_tier(..., surface=None,
...)`, deliberately, because deciding whether an UNTAGGED action belongs to
one of these surfaces is exactly the harder problem this module sidesteps
rather than solves.

No LLM calls live here — same "just data operations, gated by core/" shape
as `core/long_term_memory.py`. The one call this module accepts as
confirmation is a deterministic classification of the operator's own raw
utterance (`models.schemas.classify_confirmation`), performed by the caller
(`aldric_chat.py`) before it ever reaches `record_confirmation` — never
ALDRIC's own self-assessment. See `core.learning_governance.apply_confirmation`
for why that boundary matters (Learning Governance Section 6.3).
"""
from __future__ import annotations

import datetime as dt

from core import learning_governance
from core.pa_action_kernel import log_digest_entry
from models.schemas import Surface
from storage import db
from storage.event_log import write_event


def _get_or_new_surface(scope: str, db_path: str = db.DEFAULT_DB_PATH) -> tuple[Surface, bool]:
    """Looks up the surface keyed by this scope; constructs a fresh,
    unsaved OBSERVING one (Foundational Principle — the Empty Vessel: nothing
    pre-exists until something is actually established) if none exists yet.
    Returns (surface, existed_already) so the caller can tell "brand new"
    apart from "found but never actually given a description yet"."""
    existing = db.get_surface(scope, db_path)
    if existing:
        return Surface(**existing), True
    return Surface(surface_id=scope, description=""), False


def record_instruction_or_correction(scope: str, instruction: str, had_prior_preference: bool,
                                       db_path: str = db.DEFAULT_DB_PATH) -> Surface:
    """Call this from the exact point a standing preference is (re)written
    for `scope` — today that's aldric_chat.py's ask-once clarification
    round-trip, right alongside `long_term_memory.set_preference`.

    `had_prior_preference` tells the two Learning Governance signal types
    (Section 2.2) apart:

      * False — nothing existed for this scope before. This is Instruction
        Signal: applied immediately, no conflict record, because there is no
        prior position to record a conflict against.
      * True, and the surface already has a description on record — this is
        Correction Signal: the operator is overriding an established
        position, so it goes through `learning_governance.apply_correction`
        exactly like every other correction in this codebase (Correction
        Absolute, no confidence-gated resistance branch).

    A surface that exists but was never given a description (e.g. it
    predates this module) is treated as the Instruction case even if
    `had_prior_preference` is True — there is genuinely nothing on this
    Surface to have conflicted with yet."""
    surface, surface_existed = _get_or_new_surface(scope, db_path)

    if surface_existed and had_prior_preference and surface.description:
        surface, _entry = learning_governance.apply_correction(
            surface, instruction, domain=scope, db_path=db_path,
        )
        return surface

    surface.description = instruction
    surface.updated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    db.save_surface(surface, db_path)
    write_event("INSTRUCTION_APPLIED", {
        "surface_id": surface.surface_id, "domain": scope, "instruction": instruction,
    })

    if not surface_existed:
        # Section 6.2: "new surface candidates ALDRIC has identified" is part
        # of what the digest is meant to surface. A surface freshly created
        # from an Instruction is exactly that — logged once, at creation,
        # not on every later Instruction/Correction that touches it.
        log_digest_entry("new_surface_candidate", {
            "surface_id": surface.surface_id, "domain": scope, "description": instruction,
        }, db_path=db_path)

    return surface


def record_confirmation(scope: str, thresholds: dict | None = None,
                          db_path: str = db.DEFAULT_DB_PATH) -> Surface | None:
    """Call this only after the operator's own raw utterance has already
    been deterministically classified as confirmation vocabulary
    (`models.schemas.classify_confirmation`) — see this module's docstring
    and `learning_governance.apply_confirmation`'s for why that boundary is
    load-bearing, not a formality.

    Returns None if no surface exists yet for this scope — there is nothing
    to confirm, and this is not the place to invent one; a surface is only
    ever created by an actual instruction or correction
    (`record_instruction_or_correction`)."""
    existing = db.get_surface(scope, db_path)
    if existing is None:
        return None
    surface = Surface(**existing)
    return learning_governance.apply_confirmation(surface, thresholds=thresholds, db_path=db_path)
