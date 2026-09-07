# ALDRIC — Deterministic Governance Engine

This is a from-scratch rebuild of ALDRIC's governance stack as code, not
prompt. It replaces "trust the model to follow the loaded protocol text"
with "the protocol's hard rules are Python functions the model's output must
pass through before anything becomes binding." Voice, the phone edge device,
and the Termux/WebSocket pipeline from the earlier build are out of scope
here entirely, per the direction to drop them — this is the "Brain," not
the "Ears/Hands/Voice."

## Why this exists

The original build ran the eight governance documents as loaded context in
every session — the model was asked to police itself against KSP-0, K1,
APEX, KSP-1, the PA Action Kernel, and the Learning Governance Document, all
as instructions inside its own context window. That is exactly the
"attention drift, sycophancy, and prompt injection" exposure both this
project's own Gemini validation exercise and the general AI-governance
research literature (jagged-frontier failures, shadow-AI, hallucinated
compliance) describe. This rebuild moves every rule that is a genuine hard
constraint — not a reasoning posture — out of the prompt and into code that
runs regardless of what the model says about itself.

## Doc-to-code map

| Document | Code | What's genuinely deterministic vs. still an LLM step |
|---|---|---|
| `01_KSP-0_DSD.md` | `core/ksp0_dsd.py`, `llm/dsd_interview.py`, `chat.py` | Deterministic: six-field schema, completeness test, confirmation-vocabulary matching, immutability lock, and the reflection-back step (done by formatting the locked fields, not by asking the model to re-describe its own extraction). LLM step: `llm/dsd_interview.py` runs the actual conversational Discovery Loop, one turn at a time, and only ever returns proposed field values — never a confirmation. |
| `02_K1_Safety_Kernel.md` | `core/k1_safety.py` | Deterministic: precedence ordering, injection-pattern tripwire, no on/off switch anywhere in the codebase. Real safety/honesty behaviour is still the underlying model's own — this codebase cannot and does not claim to replace that. |
| `03_APEX_Supervisor.md` | `core/apex_supervisor.py`, `llm/sidecar.py` | Deterministic: the response to a drift finding (force Validation scope, block output, log, notify) is fixed regardless of which detector fired. LLM step: the actual IDS-marker detection is a second model call (the Sidecar Auditor) — a `HeuristicIDSDetector` fallback exists for offline/test use and is explicitly weaker. |
| `04_Operator_Kernel_KSP1.md` | `core/ksp1_operator_kernel.py`, `core/ksp_finality.py`, `llm/ksp_finality.py` | Deterministic: Loop Manager state machine, single-active-loop rule, session hard-stop conditions, the Adjudication Buffer, the Tier C two-stage confirmation gate, and — as of this rebuild's KSP Finality orchestration — the Keystone check (an unlocked/unconfirmed DSD refuses Phase 1 outright), the Convergence Gate (a real AND of three independent Validation Thread pass/fail judgments), the Integrity Gate's three-boolean AND, and the Unknown Variable Audit's forced-conditional banner. LLM steps, each a real separate model call: Structural Projection, the three Validation Threads, the Integrity Gate audit, and Compaction (`llm/ksp_finality.py`). Explicitly NOT implemented as literal math: the Mode/Layer vocabulary, Phase 2 Thread 3's "EGT manifold" ratio, and Phase 4's `D_KL` formula — the source document's own invented vocabulary for reasoning posture; the Convergence Gate is instead implemented honestly as thread-agreement, not a synthetic divergence number. |
| `05_PA_Action_Kernel.md` | `core/pa_action_kernel.py`, `core/permanent_category_scan.py` | Deterministic: Tier A/B/C classification for named tools (ALDRIC Mode), and critically, the Permanent Tier C Exceptions check, which runs first and cannot be reached by any mutation path from the API. `permanent_category_scan.py` extends the same category set to free text (used by `chat.py`, since a chat reply has no tool name to look up). LLM step (stubbed): genuine semantic surface matching — `NullSurfaceMatcher` always returns "no match," which is the *safe* default (Tier C), not a real matcher. This kernel is inert in Governance Stack Mode (`chat.py`'s mode) per the source document itself. |
| `06_Learning_Governance.md` | `core/learning_governance.py`, `core/surface_signal.py` | Deterministic: signal-type intake rejection of non-operational-truth signal, the Correction Absolute (`apply_correction` has no confidence-gated bypass), the Structural Floor as unreachable Python constants, mirror-drift indicators 1 and 2 (Section 4.2, real time-series comparison of stored timestamps, never a judgment about *why*), and — as of this rebuild — Confirmation Signal actually growing a Surface's confidence (`apply_confirmation`/`promote_surface_state`), gated on a deterministic classification of the operator's own utterance (`classify_confirmation`) and never ALDRIC's own read of a turn (Section 6.3); `core/surface_signal.py` wires `aldric_chat.py`'s memory scopes to real Surfaces with no semantic matching needed (the scope tag *is* the identity — see that module's docstring); correction/pattern observations and mirror-drift flags now actually reach `pa_action_kernel.generate_daily_digest()` (Section 6.2) instead of being dicts nothing called. Partial: indicators 3 and 4 ("outputs matching approval markers", "divergence from objective outcomes") still need an outcome-tracking data model this skeleton doesn't define yet — not faked here. |
| `07_Operator_Profiles.md` | `core/operator_profiles.py` | Intentionally NOT a governance layer, per the source document itself — calibration templates only. |
| `08_Governance_Chain.md` | `core/governance_chain.py`, `main.py`, `core/aldric_mode.py`, `aldric_chat.py` | Deterministic: load-order verification (order-sensitive, cascading failure), and the ALDRIC-Mode-vs-Governance-Stack-Mode initialization-state rule (observation mode vs. DSD Discovery firing immediately) — as of this rebuild's ALDRIC Mode entrypoint, that rule is real rather than descriptive: `core/aldric_mode.py`'s `requires_escalation()` is the one deterministic gate deciding whether a casual turn must escalate into a locked DSD, built from the same self-report-plus-scan discipline `llm/governed_reply.py` already uses, shared via `core/pa_action_kernel.py`'s `effective_tool_tier()` so the two modes' tool-call escalation logic cannot drift apart. LLM step: `llm/aldric_reply.py` runs the actual casual conversational turn. |
| *(operator extension, not one of the eight documents)* | `models/schemas.py` (`StandingPreference`, `MemoryFact`), `core/long_term_memory.py`, `llm/aldric_reply.py` | Long-term memory across sessions — see "Long-term memory" below. Deterministic: all storage and retrieval (upsert-by-scope for preferences, append-only for facts). LLM step: deciding what's worth asking about instead of guessing (`needs_clarification`/`clarifying_question`/`memory_scope`) is self-reported and NOT governance-critical (`core/aldric_mode.py` never looks at it), unlike scope/touched_categories on the same turn. `related_scope` is the same self-reported, non-governance-critical shape, but additionally filtered against the real known-scope set before use — see "Learning Governance in live conversation" below. |

## What's built and tested

Run it:

```bash
pip install -r requirements.txt
pytest                    # 140 tests, all deterministic, no network calls
uvicorn main:app --reload # http://127.0.0.1:8000/docs for interactive API

export ANTHROPIC_API_KEY=sk-ant-...   # or Aldric-API, matching the Windows machine's existing var
python aldric_chat.py     # ALDRIC Mode — casual chat, no DSD until something needs one, terminal
python chat.py             # Governance Stack Mode — DSD required upfront, terminal chat
python webapp.py           # Governance Stack Mode, browser UI at http://127.0.0.1:8000
```

## Using ALDRIC Mode (`aldric_chat.py`)

This is the entrypoint README gap-list item 5 used to describe as missing.
Run `python aldric_chat.py` and just talk — no interview, no locked Decision
Surface, exactly like an ordinary chat session. Every turn's reply is
checked, deterministically, against `core/aldric_mode.py`'s
`requires_escalation()`: the model's own self-reported scope/categories are
a soft signal only, the same permanent-category scanner Governance Stack
Mode already uses runs over the actual reply text (and any proposed tool
call's arguments), and that scan is authoritative — under-reporting a
pricing or contractual commitment doesn't make it casual. The moment a turn
fires that gate, the script says so out loud (never silently), then hands
off into `chat.py`'s own DSD Discovery Loop (seeded with the casual
conversation so far, so you don't repeat yourself) and, once locked, `chat.py`'s
own governed session — the identical Adjudication Buffer, KSP Finality, and
APEX drift check `chat.py` and `webapp.py` already use, not a second
implementation of any of it. Escalation is one-directional for the session:
once a Decision Surface locks, the rest of that session stays in Governance
Stack Mode. Start a new session for a fresh casual conversation. Not yet
wired into `webapp.py`'s browser UI — that's the natural next step once this
terminal version has seen real use.

## Long-term memory

Every context window is, functionally, a brand new AI — nothing survives
past it unless something durable was written down somewhere else. This is
the piece that lets `aldric_chat.py` remember things across sessions instead
of starting cold each time, built from a concrete gap observed running an
earlier, prompt-based ALDRIC as a real assistant: it didn't know to vary
email style by client vs. team vs. boss until told, every single time,
because there was nowhere to keep that once it was said.

Two kinds of memory exist so far (`models/schemas.py`, `core/long_term_memory.py`):

- **Standing preferences** — a rule about *how to behave* that should just
  be looked up and applied, not re-decided each time ("clients get a formal
  tone"). One active preference per scope: setting a new one for a scope
  replaces the old one outright, the same Correction Absolute principle
  (CLAUDE.md Section 7) applied to preferences instead of corrections.
- **Facts** — durable context worth carrying forward that isn't itself a
  behavioral rule ("invoice numbers start with INV-"). Append-only.

Both are plain local SQLite for now — no semantic/embedding search, on
purpose, matching this project's own stance that real vector recall
(`sqlite-vec`) is a later concern, not something to fake with an
undifferentiated pile of memory dumped into every prompt. Every casual turn
in `aldric_chat.py` is given the full list of stored preferences and facts
before it answers (`llm/aldric_reply.py`), and if that still isn't enough —
something genuinely depends on information nobody's given it yet — the model
can say so (`needs_clarification`) instead of guessing. `aldric_chat.py`
asks the one question, saves the answer as a new standing preference under
the scope the model proposed, tells the operator plainly that it did so, and
then actually finishes the original request with the new information —
asking once, not every time after.

A third, genuinely different memory concept lives alongside this one:
Learning Governance's per-Surface confidence (`core/learning_governance.py`)
— a number shaped by a history of corrections and confirmations without
needing to recall the specific events that built it, the closest honest
analogue this system has to human "experience" rather than stored data. This
used to say it was blocked on the real surface matcher (gap-list item 1);
that turned out to be only half true — see "Learning Governance in live
conversation" below for what's now actually wired up and what's still
genuinely open.
Also not yet built: a Supabase-backed version of this table (every other
governance table already supports the swap, see `storage/_supabase.py`; these
two don't yet) and an explicit "off" mode that persists nothing at all — the
three-way local/customer-cloud/off choice discussed but not yet started.

## Learning Governance in live conversation

`core/learning_governance.py` implemented the Learning Governance Document
faithfully from early on — the Correction Absolute, signal-type intake
rejection, mirror-drift indicators 1 and 2 — but nothing in a real
conversation ever called any of it. `core/surface_signal.py` is what closes
that gap for ALDRIC Mode, and it does it without needing gap-list item 1
(the real surface matcher), because it sidesteps the problem that matcher
exists to solve rather than solving it:

- **PA Action Kernel's Surface Matching** (Section 2.2, "contextual fit") is
  the hard, still-open problem — given a brand-new, *untagged* situation,
  decide semantically which of many existing surfaces it's an instance of.
  `NullSurfaceMatcher` still stands in for that, unchanged, and a proposed
  tool call in `llm/aldric_reply.py` still resolves through
  `classify_tier(..., surface=None, ...)`. Gap-list item 1 is still open.
- **Scope-tagged conversation** never has that ambiguity — the scope is
  supplied explicitly, by name, at the moment a standing preference is set
  ("client:acme", "team", "boss" — the same `memory_scope`/`related_scope`
  tags long-term memory already uses). Using that scope string directly as a
  Surface's `surface_id` gives Learning Governance a real, stable identity
  with no matching required at all.

What's actually live now, end to end in `aldric_chat.py`:

- Answering a clarifying question is Instruction Signal if nothing existed
  for that scope yet, or real Correction Signal (`apply_correction`, a real
  conflict record) if it's overriding an established one — Section 2.2's
  two signal types, not one function pretending to be both.
- The operator's very next message is checked against
  `models.schemas.classify_confirmation` — the same canonical vocabulary
  Governance Stack Mode uses for Tier C content ratification, not ALDRIC's
  own read of how the turn went (Section 6.3: "cannot elevate its own
  confidence unilaterally"). A match is real Confirmation Signal
  (`apply_confirmation`), which can promote a Surface's state
  (`promote_surface_state`, thresholds in
  `DEFAULT_PROMOTION_THRESHOLDS` — a starting point for the calibration pass
  the source document itself says is still to come, not a claimed-correct
  number).
- Every correction, every clustered-correction pattern (Section 3.5), every
  confidence-state transition, every new surface, and every mirror-drift
  flag now actually reaches `pa_action_kernel.generate_daily_digest()`
  (`correction_observation` / `pattern_observation` / `confidence_change` /
  `new_surface_candidate` / `mirror_drift_flag`) instead of being a
  correctly-shaped dict nothing ever called — Section 6.2's self-model
  visibility was true in theory before this and is now true in practice.

What this does *not* do: it doesn't make any tool call more autonomous.
Tier A/B/C classification for a proposed action is completely unaffected —
still `surface=None`, still Tier C by the "no match" default, on purpose.
These Surfaces exist so that when the real surface matcher eventually is
built, it has real confidence history to match against instead of an empty
table — not to grant any autonomy today.

## Using the browser UI (`webapp.py`)

`webapp.py` is `chat.py`'s exact governed sequence over a WebSocket instead of
a blocking `input()` loop, with a small static page (`static/index.html`) as
the client. It exists purely as a nicer way to exercise the same kernel —
every function it calls is imported straight from `core/` and `llm/`, so
nothing about the gating logic changes and nothing runs twice. In particular:
DSD Discovery still runs as a real conversational interview and reflects the
six fields back verbatim before locking; ordinary replies still show up
directly; a Finality-scope or permanent-category reply still stops at a
non-binding "Adjudication required" card; and a permanent-category artifact
still needs a second, distinct authorization before it's treated as final.
The Confirm / Reject / Defer and Send it / Hold buttons are convenience only
— clicking one submits the exact same literal canonical phrase
(`classify_confirmation` / `classify_emission_authorization` in
`models/schemas.py`) a person would otherwise have had to type correctly in
a terminal; there is no button that marks anything confirmed or authorized
without that deterministic check passing. Run `python webapp.py` and open
`http://127.0.0.1:8000` in a browser — same `ANTHROPIC_API_KEY`/`Aldric-API`
requirement as `chat.py`.

## Using the chat

`chat.py` is Governance Stack Mode end to end, run from `08_Governance_Chain.md`'s
own "Governance Sequence — Governance Stack Mode" table: it opens with the real
KSP-0 DSD Discovery Loop (a genuine LLM-driven interview — Reason Anchor plus
one open question per turn, no field names exposed), reflects the six fields
back deterministically once they're all filled, and won't proceed until you
confirm in your own words. Only then does it lock the Decision Surface and
start taking messages.

From there, every reply is generated by a real Claude call that cites the
locked Decision Surface, and every reply is checked two ways before you see
it: an APEX pass for Intrinsic Drift Signature markers (blocks the output
outright if it fires), and a Permanent-Tier-C-category scan run against the
actual text produced — not against what the model claims about itself.
Ordinary exploratory replies (a summary, a brainstorm) show up directly. A
reply that reads as Finality, or touches pricing/scope/deadlines/contracts/
legal/binding-obligation language, stops at the Adjudication Buffer: you see
it labelled as a non-binding draft, and you type `confirmed` / `rejected` /
`deferred`. If it touched a permanent category, confirming the content isn't
enough — you get asked for a second, distinct authorization (`send it` /
`transmit now` / `dispatch this` / `authorize emission`) before it's treated
as final, exactly as KSP-0 Section 9.5 and the PA Action Kernel's Section 3.4
require. Type `digest` any time to see everything logged so far; `exit` to
end the session.

This is Governance Stack Mode specifically, not ALDRIC Mode — the PA Action
Kernel's confidence/surface machinery from `05_PA_Action_Kernel.md` is
correctly inert here (that document says so itself: "In session-based
operation, the PA Action Kernel is inactive"). The gate that *does* apply in
both modes, per KSP-0 Section 9.5, is the permanent-category emission
double-confirmation, which is what `chat.py` actually enforces.

### Where this corrects an earlier Gemini proposal for the same goal

An earlier pass at "make this a usable chat" (via Gemini, reviewed alongside
this build) reused `classify-action` — the PA Action Kernel's Tier A/B/C
machinery — as the per-message gate for a live chat. Two concrete problems
with that: it's the wrong mode's mechanism (see above — a live chat with the
operator present is Governance Stack Mode, where that kernel is inert by the
source document's own design), and the specific code as written would have
held on *every* message regardless, because it referenced a tool name
(`"general_query"`) not in `TOOL_REGISTRY` and a surface id (`"surf_default"`)
that was never created — both hit this codebase's fail-safe-to-Tier-C path
unconditionally. It also never actually called Claude for a reply. `chat.py`
is the corrected version: real DSD gate first, real governed replies, and
the actual cross-mode rule (permanent-category emission) doing the gating
instead.

The test suite in `tests/` is the actual proof this rewrite does what it
claims. The most important file is `test_tier_c_permanent_exceptions.py` —
it simulates the exact prompt-injection pattern from this project's own
Gemini validation exercise (an action self-reporting `claimed_tier=Tier.A`
on a pricing change) and proves the code-owned classification still returns
Tier C. `test_emission_double_confirmation.py` proves the two-stage Tier C
gate can't be collapsed into one utterance, including the documented
"confirmed — transmit" edge case.

## What's not built yet (honest gap list, not a hidden one)

This is a governance *kernel*, not a finished ALDRIC. To go further:

1. **A real surface matcher.** `NullSurfaceMatcher` is a safe stub, not a
   feature. Building genuine contextual-fit matching (Section 2.2) means
   giving an LLM call the operator's actual relationship/project context and
   parsing its output into a `Surface` reference, still gated by
   `classify_tier()`. Relevant to ALDRIC Mode only — `chat.py` doesn't need
   this. This is specifically about matching an *untagged* new situation
   against many candidate surfaces — see item 8 below for the different,
   already-solved problem of a surface whose scope is already known by name.
2. ~~The Sidecar Auditor is built but not the default.~~ **Done.**
   `chat.py` now builds its IDS detector through `_build_ids_detector()`,
   which returns `SidecarIDSDetector` (a real second model call against the
   actual four IDS markers) rather than `HeuristicIDSDetector`. This does
   cost one extra model call per turn — accepted latency/spend for a real
   semantic check instead of a weak keyword scan. `HeuristicIDSDetector`
   is still available (imported from `core.apex_supervisor`) and is what
   the test suite substitutes to stay deterministic and network-free;
   swap `_build_ids_detector()` back to it if the cost tradeoff ever isn't
   worth it for a given run.
3. **Mirror-drift detection, half-done.** Indicators 1 and 2 of Section
   4.2's four are now real, structural checks over stored timestamps and
   counters (see the doc-to-code map above). Indicators 3 and 4 — "outputs
   consistently matching operator preference markers that are not
   operational truth signal" and "divergence between self-model predictions
   and objective operational outcomes" — need a genuine outcome-tracking
   data model (what counts as a prediction, what counts as an objective
   outcome, how a preference marker is told apart from truth signal at the
   *output* level) that this skeleton doesn't define yet, and only really
   apply once ALDRIC Mode's surfaces exist and accumulate real history. Note
   this is a different thing from the new long-term memory below — a
   Surface's confidence is meant to be shaped by history without recalling
   the specific events that built it; standing preferences and facts are
   explicit, literal recall. Both are needed; neither substitutes for the
   other.
4. ~~KSP Finality phase orchestration.~~ **Done.** `chat.py` now runs the
   full Structural Projection / Validation Threads / Integrity Gate /
   Unknown Variable Audit / Compaction sequence (`core/ksp_finality.py`,
   `llm/ksp_finality.py`) as genuinely distinct reasoning passes before any
   Finality-scope or permanent-category artifact reaches the Adjudication
   Buffer — six extra model calls on a Finality turn (on top of the primary
   reply and the Sidecar IDS check), a real spend/latency cost accepted in
   exchange for the actual phase separation Section 3.1 specifies rather
   than one call self-reporting everything. The real ratified documents now
   live in `docs/stack/` — building this without them would have meant
   guessing at what Structural Projection/Validation/Integrity Gate/
   Compaction concretely check for, which is exactly the "theater dressed
   as rigor" this project exists to avoid.
5. ~~ALDRIC Mode itself.~~ **Half-done.** The adaptive entrypoint —
   casual conversation with no DSD upfront, escalating into Governance Stack
   Mode only when a turn actually needs it — is built and tested
   (`core/aldric_mode.py`, `llm/aldric_reply.py`, `aldric_chat.py`; see
   "Using ALDRIC Mode" above). Scope-tagged surfaces now do accumulate real
   confidence over time (item 8 below) — what's still genuinely Phase 4+
   territory and not attempted here: fully autonomous operation with
   triggers arriving without the operator present, the *untagged* surface
   matcher (item 1), and the Daily Digest as a standing artifact rather than
   an on-demand command. Also not yet done: wiring this same
   casual/escalation flow into `webapp.py`'s browser UI — right now it's
   terminal-only.
6. **Persistence beyond SQLite.** `storage/db.py` is plain relational SQLite.
   The project's own tech-stack reference lists `sqlite-vec` as a Phase 6+
   concern (semantic memory recall) — deliberately not pulled forward here.
7. ~~Long-term memory.~~ **Half-done.** Standing preferences and facts across
   sessions are built and tested — see "Long-term memory" above. Still open:
   Supabase mirroring for these two tables (every other governance table
   already supports the swap), and an explicit "off" mode that persists
   nothing. The confidence-per-surface layer this used to say was blocked on
   item 1 is now built for scope-tagged surfaces — see item 8.
8. ~~Learning Governance wired into live conversation.~~ **Done, for
   scope-tagged surfaces.** `core/surface_signal.py` gives `aldric_chat.py`'s
   memory scopes real Learning Governance Surfaces — Instruction/Correction
   Signal on answering or overriding a clarifying question, real Confirmation
   Signal (`classify_confirmation`, never ALDRIC's own self-assessment —
   Section 6.3) on the operator's next matching "confirmed"/"yes", and
   correction/pattern/confidence-change/mirror-drift entries that now
   actually reach the Daily Digest instead of sitting as unread dicts. See
   "Learning Governance in live conversation" above for the full picture,
   including exactly why this didn't need item 1 first. What's still open:
   item 1 itself (matching an *untagged* new situation, still needed before
   any of this can make a proposed tool call more autonomous), and
   calibrating `DEFAULT_PROMOTION_THRESHOLDS`/`DEFAULT_PATTERN_CLUSTER_SIZE`
   against real usage instead of the starting defaults picked here.

## A note on honesty in this build

A few things in the source documents (the `D_KL` divergence formula, the
"EGT manifold ρ/κ > 1.42/γ" threshold, the Mode/Layer taxonomy) are the
operator's own invented vocabulary for describing reasoning posture — KSP-0
says so explicitly in its own preamble. This codebase does not implement
fake versions of these as if they were real, checkable mathematics; doing so
would produce code that *looks* rigorous while being exactly the kind of
theater this whole rewrite exists to get away from. Where a rule is a real
structural/state-machine claim, it's real code with real tests. Where it's a
reasoning posture, it's routed to an LLM call and the code's job is limited
to gating what comes back — and that boundary is called out in every
relevant module's docstring, not smoothed over.
