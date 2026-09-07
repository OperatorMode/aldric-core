"""
Long-term memory — standing preferences and facts.

Not one of the eight ratified governance documents; this module exists
because of a concrete gap the operator observed running an earlier,
prompt-based ALDRIC as a real personal assistant: every fresh context window
is, functionally, a brand new AI with no memory of anything the previous one
learned. A conversation can carry a lot of history while it's open, but
nothing survives past it unless something durable was written down
somewhere else. That earlier ALDRIC solved this well enough with nothing
more than a connected Google Drive folder it read from and wrote to.

The operator and an earlier AI conversation partner also worked out a real
distinction worth keeping separate in code: human memory is mostly
*experience* — a reaction shaped by history that was never consciously
re-derived each time — while anything an LLM can be given is necessarily
*data*, something explicit fed into its context. For a system built the way
ALDRIC is (a frozen model called over an API, not a fine-tuned one), there
is no honest way to fake the first kind. What can be built honestly is two
different flavours of the second kind, which map onto two different real
needs:

  1. StandingPreference — a rule about *how to behave* that shouldn't be
     re-decided or asked about each time once it's known ("clients get a
     formal tone, the team gets a casual one"). This is the piece that was
     missing when the operator's earlier ALDRIC didn't know to vary email
     style by audience until told explicitly every time — not a DSD-worthy
     decision (KSP-0's own activation conditions are about an incomplete or
     ambiguous surface, not routine stylistic variation), just something
     that had nowhere to be remembered.
  2. MemoryFact — a durable fact or piece of context worth carrying forward
     that isn't itself a behavioral rule ("invoice numbers start with
     INV-").

A third, genuinely different memory concept — Learning Governance's
per-Surface confidence, a number shaped by a history of corrections and
confirmations without needing to recall the specific events that built it,
the closest honest analogue this system has to human "experience" — already
exists in core/learning_governance.py. It doesn't live in this module (this
module stays "just data operations" for preferences and facts only), but it
is wired up now, in core/surface_signal.py: every scope this module already
tracks (the same "client:acme"/"team"/"boss" tags used for
memory_scope/related_scope) doubles as a Learning Governance Surface's
identity, with no semantic surface matching required — the scope is already
an explicit tag, not something to be inferred. That's a different, easier
problem than core/pa_action_kernel.py's NullSurfaceMatcher (matching a
brand-new, *untagged* situation against many candidate surfaces), which is
still an open stub — see core/surface_signal.py's module docstring for the
full distinction, and the README gap list for what that harder problem still
blocks.

This module is the deterministic storage-facing layer only — no LLM calls.
llm/aldric_reply.py is what actually fetches from here before a casual turn
and writes back after one; core/ stays the same "just data operations, no
reasoning" shape as core/pa_action_kernel.py's digest functions.
"""
from __future__ import annotations

from models.schemas import MemoryFact, StandingPreference
from storage import db


def set_preference(scope: str, content: str, db_path: str = db.DEFAULT_DB_PATH) -> StandingPreference:
    """Replaces whatever preference (if any) already exists for this scope —
    see StandingPreference's own docstring on why that's Correction Absolute
    applied to preferences, not a bug: the latest thing the operator said
    applies immediately and completely."""
    preference = StandingPreference(scope=scope, content=content)
    db.set_preference(preference, db_path)
    return preference


def get_preference(scope: str, db_path: str = db.DEFAULT_DB_PATH) -> StandingPreference | None:
    raw = db.get_preference(scope, db_path)
    return StandingPreference(**raw) if raw else None


def list_preferences(db_path: str = db.DEFAULT_DB_PATH) -> list[StandingPreference]:
    return [StandingPreference(**raw) for raw in db.list_preferences(db_path)]


def record_fact(scope: str, content: str, source: str, db_path: str = db.DEFAULT_DB_PATH) -> MemoryFact:
    fact = MemoryFact(scope=scope, content=content, source=source)
    db.append_fact(fact, db_path)
    return fact


def list_facts(scope: str | None = None, db_path: str = db.DEFAULT_DB_PATH) -> list[MemoryFact]:
    return [MemoryFact(**raw) for raw in db.list_facts(scope, db_path)]
