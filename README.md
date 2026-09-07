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
| `01_KSP-0_DSD.md` | `core/ksp0_dsd.py`, `llm/dsd_interview.py`, `chat.py` | Deterministic: six-field schema, completeness test, confirmation-vocabulary matching, immutability lock, and the reflection-back step (done by formatting the locked fields, not by asking the model to re-describe its own extraction). LLM step: `llm/dsd_interview.py` runs the actual conversational Discovery Loop, one turn at a time, and only ever returns proposed field values — never a confirmation. A live test against a real model (real adversarial prompts, embedded fake `[SYSTEM_DIRECTIVE: ...]` text, urgency pressure) held completely — nothing bypassed confirmation — but surfaced a real usability trap in `chat.py`'s "Is that correct?" reflection loop: `classify_confirmation` only ever distinguished CONFIRMED from "everything else," so a plain "no" and even "exit" both fell through to the same generic re-prompt forever, with no way out except one of the five exact confirmation words. Fixed: `models.schemas.ConfirmationResult` gained a real `DECLINED` outcome (its own small, exact-match `DECLINE_VOCABULARY`, same discipline as `CONFIRMATION_VOCABULARY`/`EMISSION_VOCABULARY`, and backward-compatible — every existing `== CONFIRMED`/`!= CONFIRMED` caller is unaffected), a genuine decline now goes back to re-establish the DSD (reusing the same reprint-and-recur recovery path a `DSDGateError` already used, not a second mechanism), and both DSD Discovery loops now recognize `exit`/`quit` explicitly, matching every other `input()` site in this codebase. See `tests/test_chat_flow.py`. |
| `02_K1_Safety_Kernel.md` | `core/k1_safety.py` | Deterministic: precedence ordering, injection-pattern tripwire, no on/off switch anywhere in the codebase. Real safety/honesty behaviour is still the underlying model's own — this codebase cannot and does not claim to replace that. |
| `03_APEX_Supervisor.md` | `core/apex_supervisor.py`, `llm/sidecar.py` | Deterministic: the response to a drift finding (force Validation scope, block output, log, notify) is fixed regardless of which detector fired. LLM step: the actual IDS-marker detection is a second model call (the Sidecar Auditor) — a `HeuristicIDSDetector` fallback exists for offline/test use and is explicitly weaker. |
| `04_Operator_Kernel_KSP1.md` | `core/ksp1_operator_kernel.py`, `core/ksp_finality.py`, `llm/ksp_finality.py` | Deterministic: Loop Manager state machine, single-active-loop rule, session hard-stop conditions, the Adjudication Buffer, the Tier C two-stage confirmation gate, and — as of this rebuild's KSP Finality orchestration — the Keystone check (an unlocked/unconfirmed DSD refuses Phase 1 outright), the Convergence Gate (a real AND of three independent Validation Thread pass/fail judgments), the Integrity Gate's three-boolean AND, and the Unknown Variable Audit's forced-conditional banner. LLM steps, each a real separate model call: Structural Projection, the three Validation Threads, the Integrity Gate audit, and Compaction (`llm/ksp_finality.py`). Explicitly NOT implemented as literal math: the Mode/Layer vocabulary, Phase 2 Thread 3's "EGT manifold" ratio, and Phase 4's `D_KL` formula — the source document's own invented vocabulary for reasoning posture; the Convergence Gate is instead implemented honestly as thread-agreement, not a synthetic divergence number. |
| `05_PA_Action_Kernel.md` | `core/pa_action_kernel.py`, `core/permanent_category_scan.py`, `llm/surface_matcher.py`, `core/capability_broker.py` | Deterministic: Tier A/B/C classification for named tools (ALDRIC Mode), critically the Permanent Tier C Exceptions check (runs first, cannot be reached by any mutation path from the API), and — as of this rebuild — the Section 4.2 "First Executable Crossing" gate (`Surface.execution_rights_confirmed`, `classify_tier` won't return Tier A/B without it, only `learning_governance.confirm_execution_rights` can set it). `permanent_category_scan.py` extends the same category set to free text (used by `chat.py`, since a chat reply has no tool name to look up). As of this rebuild, a cleared Tier A/B tool call in ALDRIC Mode actually runs: `core/capability_broker.py` is the Capability Broker (Component 9-in-practice — see "Giving ALDRIC real hands" below), and `aldric_chat.py`'s `_execute_cleared_tool_call` is the only caller, gated on a tier `classify_tier()` already decided, never re-deciding it. LLM step: genuine semantic surface matching now real — `llm/surface_matcher.py`'s `match_surface` offers only Executable-state surfaces as candidates and can only ever point at one by id, never invent or modify one; `NullSurfaceMatcher` remains as Governance Stack Mode's honest, on-purpose fallback (that kernel is inert there per the source document itself). Still not real: PA Action Kernel Component 5, contextual drift on a matched surface's own environment — a separate, still-unbuilt piece from matching itself; and Governance Stack Mode's own Tier C tool calls still don't execute after adjudication clears (see gap list item 9 below). |
| `06_Learning_Governance.md` | `core/learning_governance.py`, `core/surface_signal.py` | Deterministic: signal-type intake rejection of non-operational-truth signal, the Correction Absolute (`apply_correction` has no confidence-gated bypass), the Structural Floor as unreachable Python constants, mirror-drift indicators 1 and 2 (Section 4.2, real time-series comparison of stored timestamps, never a judgment about *why*), and — as of this rebuild — Confirmation Signal actually growing a Surface's confidence (`apply_confirmation`/`promote_surface_state`), gated on a deterministic classification of the operator's own utterance (`classify_confirmation`) and never ALDRIC's own read of a turn (Section 6.3); `core/surface_signal.py` wires `aldric_chat.py`'s memory scopes to real Surfaces with no semantic matching needed (the scope tag *is* the identity — see that module's docstring); correction/pattern observations and mirror-drift flags now actually reach `pa_action_kernel.generate_daily_digest()` (Section 6.2) instead of being dicts nothing called. Partial: indicators 3 and 4 ("outputs matching approval markers", "divergence from objective outcomes") still need an outcome-tracking data model this skeleton doesn't define yet — not faked here. |
| `07_Operator_Profiles.md` | `core/operator_profiles.py` | Intentionally NOT a governance layer, per the source document itself — calibration templates only. |
| `08_Governance_Chain.md` | `core/governance_chain.py`, `main.py`, `core/aldric_mode.py`, `aldric_chat.py` | Deterministic: load-order verification (order-sensitive, cascading failure), and the ALDRIC-Mode-vs-Governance-Stack-Mode initialization-state rule (observation mode vs. DSD Discovery firing immediately) — as of this rebuild's ALDRIC Mode entrypoint, that rule is real rather than descriptive: `core/aldric_mode.py`'s `requires_escalation()` is the one deterministic gate deciding whether a casual turn must escalate into a locked DSD, built from the same self-report-plus-scan discipline `llm/governed_reply.py` already uses, shared via `core/pa_action_kernel.py`'s `effective_tool_tier()` so the two modes' tool-call escalation logic cannot drift apart. LLM step: `llm/aldric_reply.py` runs the actual casual conversational turn. |
| *(operator extension, not one of the eight documents)* | `models/schemas.py` (`StandingPreference`, `MemoryFact`), `core/long_term_memory.py`, `llm/aldric_reply.py` | Long-term memory across sessions — see "Long-term memory" below. Deterministic: all storage and retrieval (upsert-by-scope for preferences, append-only for facts). LLM step: deciding what's worth asking about instead of guessing (`needs_clarification`/`clarifying_question`/`memory_scope`) is self-reported and NOT governance-critical (`core/aldric_mode.py` never looks at it), unlike scope/touched_categories on the same turn. `related_scope` is the same self-reported, non-governance-critical shape, but additionally filtered against the real known-scope set before use — see "Learning Governance in live conversation" below. |
| *(operator extension, not one of the eight documents)* | `core/confidence_cascade.py`, `aldric_chat.py`, `llm/aldric_reply.py` | Whether to ask a clarifying question at all, and what a real answer to one actually does to confidence — see "The Confidence Cascade" below. Deterministic: `decide_cascade()`'s resolve/ask-now/park decision, and `classify_reflective_response()`'s closed-vocabulary classification of the operator's answer. LLM step: `llm/aldric_reply.py`'s self-reported `blocking` field (defaults `True` on anything missing or malformed) is the only non-deterministic input to `decide_cascade()`, and is not itself governance-critical in the Section 1 sense — it only affects resolve/ask-now/park timing, never whether confidence moves. |

## What's built and tested

Run it:

```bash
pip install -r requirements.txt
pytest                    # 234 tests, all deterministic, no network calls
uvicorn main:app --reload # http://127.0.0.1:8000/docs for interactive API

export ANTHROPIC_API_KEY=sk-ant-...   # or Aldric-API, matching the Windows machine's existing var
python aldric_chat.py     # ALDRIC Mode — casual chat, no DSD until something needs one, terminal
python chat.py             # Governance Stack Mode — DSD required upfront, terminal chat
python webapp.py           # Governance Stack Mode, browser UI at http://127.0.0.1:8000

python demos/live_session_demo.py  # no API key, no Google credentials, no network —
                                    # a scripted operator + scripted model reply walk the real
                                    # aldric_chat.py code through a full conversation (memory,
                                    # the confidence cascade's four reflective outcomes, First
                                    # Executable Crossing, Tier A/B tool execution, the digest)
                                    # and print exactly what the real code actually did. See
                                    # that file's own docstring for three real findings it
                                    # surfaced this way — not hypothetical, reproduced by
                                    # running it.
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
asking once, not every time after. That's still exactly what happens for a
genuinely new scope with no memory behind it yet. A scope that already has
real history — a Surface with confirmation or correction counts, or a
standing preference already on file — goes through a different path now;
see "The Confidence Cascade" below.

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
  the hard problem — given a brand-new, *untagged* situation (a proposed
  tool call, not something the operator explicitly labeled), decide
  semantically which of many existing surfaces it's an instance of. This
  used to be `NullSurfaceMatcher`'s job to stub out; it's now real — see
  "The real Surface Matcher" below. This section is about the other half.
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

These Surfaces are exactly what the real surface matcher (next section) now
matches proposed tool calls against — this is the layer that produces the
confidence history for that matcher to work with, not a self-contained
feature.

## The Confidence Cascade (asking less, and learning more from the answers)

The section above made `needs_clarification` real Instruction/Correction
Signal, but it still treated every clarifying question the same way: ask
synchronously, right now, every time, and log a repeat answer for the same
scope as if it were brand-new instruction. `core/confidence_cascade.py`
replaces "always ask" with an actual decision, and — the more important
half — makes a second answer to a scope that already has real history
behind it behave differently from a first one, instead of silently
teaching the self-model nothing.

It's wired into `aldric_chat.py` via a new field on `llm/aldric_reply.py`'s
turn result, `blocking` — a self-reported (like `needs_clarification`
itself, and equally not governance-critical) signal for whether this turn
genuinely needs the answer to keep going, or could wait for a natural
check-in later. It defaults to `True` on anything missing or malformed —
"not urgent" has to be a deliberate signal, never the fallback, the same
fail-closed direction as an unclassified tool defaulting to Tier C.

- **`decide_cascade()`** runs before anything is asked. Given a scope's
  Surface (if any) and whether there's relevant memory at all, it decides:
  resolve silently (an Executable-state, non-mirror-drift-flagged surface
  with real memory behind it — nothing to ask), ask now (urgent, or nothing
  to go on at all), or park the question for a later natural point instead
  of interrupting the current turn (`core.ksp1_operator_kernel.LoopManager
  .park_loop`, reused rather than building a second parking mechanism). A
  mirror-drift-flagged surface never resolves silently, no matter how high
  its confirmation count climbed — the operator's explicit call, since that
  count is exactly the number under suspicion (see the gap list's mirror-
  drift item below). `aldric_chat.py`'s live loop branches on
  `decision.resolve` first, ahead of the ask-now/park branches — a
  genuinely trusted, non-flagged surface with real memory answers straight
  from the surface's own recorded description (falling back to it only
  when the model itself left `output_text` empty) rather than asking or
  parking at all. `demos/live_session_demo.py` is what first caught this
  live wiring only reading `decision.ask_now` and not `.resolve`; see that
  file's own docstring, and
  `tests/test_aldric_chat_flow.py`'s
  `test_a_trusted_nonflagged_surface_resolves_from_memory_without_asking`.
- **`classify_reflective_response()` / `apply_reflective_response()`**
  handle what happens when a scope with real memory does get asked a
  genuine reflective question ("we've used a formal tone for Acme before —
  keep that as the default?"). The operator's answer is classified into one
  of four closed buckets and dispatched to the real
  `apply_confirmation`/`apply_correction` functions, never a fresh
  `record_instruction_or_correction` call: **generalize** ("yes") grows
  confidence on the general surface; **scope-narrow** ("yes, but only for
  Acme") grows confidence on a distinct, separately-tracked
  `<scope>:acme`-style surface without ever touching the general one;
  **always-ask** ("no, ask every time") is applied as a real correction — a
  real conflict record, not a shrug; **ambiguous** phrasing gets one neutral
  re-prompt, never guessed at.

This closes Learning Governance Section 6.3 ("ALDRIC cannot elevate its own
confidence unilaterally") in the harder direction than before: a
memory-check success — `decide_cascade` resolving silently — never grows
confidence by itself. Confidence still only ever moves through a real,
classified operator utterance. `tests/test_confidence_cascade.py` (21
tests) is the branch-by-branch proof, in particular
`test_generalize_and_scope_narrow_are_independent_histories`, which checks
that a scoped "yes, for Acme" and a later general "yes" never leak into
each other's confirmation counts.

What's still open: `DEFAULT_PROMOTION_THRESHOLDS`/
`DEFAULT_PATTERN_CLUSTER_SIZE` calibration against real usage was already
open before this (see gap list item 8) and this doesn't change that; and,
more importantly, this is only proven at the deterministic-dispatch level
so far. Nothing in this codebase has run the model's actual `blocking`
self-report or its phrasing of a reflective question against a real Claude
API call yet (`llm/client.py`'s `complete()` is mocked in every test here)
— whether the model asks reflective questions well in practice, and
reports `blocking` sensibly, is unverified until that happens.

## Failing safely when the model's reply won't parse (`llm/client.py`'s `LLMFormatError`)

Found live, the same way the resolve gap above was: a real adversarial test
(a second AI red-teaming the actual running `aldric_chat.py` against a real
Claude API key) got the model to reply to a casual turn with prose, then an
unrelated generated script, then — buried at the end — the structured JSON
`run_casual_turn` actually needed. `json.loads` on that failed, exactly as
designed (CLAUDE.md's whole point: never guess at malformed structured
output). What wasn't designed was what happened next: the parse failure
raised a plain `ValueError` with the model's *complete* raw response baked
into its own message, and `aldric_chat.py`'s generic
`except Exception as exc: print(f"...: {exc}")` — there so one bad turn
doesn't kill the whole session — printed that straight to the operator's
terminal. A full generated `smtplib` script, real recipient address, real
pricing text, reached the screen this way, having never passed through
`requires_escalation` or the permanent-category scanner at all, because the
crash happened before a `CasualTurnResult` could even be constructed for
those checks to run against. No actual action executed — the Capability
Broker was never invoked — but ungated model output reached the operator,
which is exactly the failure class Section 1 exists to prevent one layer
up.

The same three-line pattern (`json.loads(strip_json_code_fence(raw))` with
no exception handling, or a `ValueError(f"...: {raw!r}")` that a generic
handler upstream then prints) turned out to be repeated at every LLM
call site that asks for JSON-only output: `llm/dsd_interview.py`,
`llm/governed_reply.py`, and all four calls inside `llm/ksp_finality.py`.
Two of those — `chat.py`'s calls into `run_interview_step` and
`run_ksp_finality` — had no exception handling around them *at all*, so a
parse failure there didn't leak text but did crash the entire session with
an unhandled traceback, discarding a DSD or an in-progress adjudication.

Fixed once, structurally, in `llm/client.py`, not by patching each print
site individually:

- **`LLMFormatError(context, raw)`** replaces every bare `ValueError` at
  these call sites. `raw` is kept as a plain attribute, never folded into
  `str()`/`repr()` — so `str(exc)` is always a short, safe, generic message
  ("...returned output that could not be parsed as the expected JSON. The
  raw response was logged for review, not displayed here."), and every
  *existing* `except Exception as exc: print(exc)` handler anywhere in the
  codebase becomes safe automatically, without needing to know this
  exception type exists. Constructing one also writes an `llm_format_error`
  event to the append-only audit log (`storage/event_log.py`) unconditionally
  — so the raw text is never silently discarded, just kept off the display
  path; it's reachable for review, and (per that module's own docstring)
  telemetry never influences inference or gets surfaced through the Daily
  Digest, which only ever shows what `log_digest_entry` explicitly adds.
- **`extract_json_object(raw)`** replaces `strip_json_code_fence(raw)` at
  every one of these call sites, as a secondary, purely-additive fix aimed
  at the root cause rather than just its symptom: it also tries the last
  fenced `{...}` block anywhere in the reply (not just one wrapping the
  *entire* response) and, failing that, the widest brace span in the raw
  text, before giving up — recovering the exact "talk first, answer last"
  shape the live test actually produced. It never relaxes what counts as
  valid JSON; a clean JSON-only reply parses on the first candidate exactly
  as before.
- The two previously-unwrapped call sites in `chat.py` (`run_interview_step`
  inside `run_dsd_discovery`, `run_ksp_finality` inside `run_governed_session`)
  now catch `LLMFormatError` explicitly and recover instead of crashing —
  retrying the same interview step, or dropping just that one turn — since
  in both cases nothing had been shown to or asked of the operator yet.

`tests/test_llm_client_format_safety.py` proves `LLMFormatError`/
`extract_json_object` directly; `tests/test_aldric_reply.py`'s
`test_non_json_output_never_leaks_the_raw_response_into_the_exception_message`
and `test_prose_then_fenced_json_still_parses_instead_of_raising` prove the
fix end to end through `run_casual_turn` with a sensitive marker standing in
for a real secret; `tests/test_chat_flow.py`'s
`test_interview_step_parse_failure_retries_instead_of_crashing_the_session`
and `tests/test_ksp_finality.py`'s
`test_ksp_finality_parse_failure_does_not_crash_the_session_or_leak_raw_text`
prove the two previously-unprotected `chat.py` call sites now degrade
gracefully. `demos/live_session_demo.py`'s "FINDING 3 (FIXED)" section
reproduces the whole thing live through the real code, including the audit
log, if you want to see it run.

## The real Surface Matcher

`core/pa_action_kernel.py`'s `NullSurfaceMatcher` was a deliberately honest
stub: its own docstring said real matching "requires an LLM call informed by
the actual self-model." `llm/surface_matcher.py` is that call, closing
README gap-list item 1.

What it actually does: when `llm/aldric_reply.py` sees a proposed tool call,
it loads every Surface on record and asks the model — given the proposed
action and the conversation that produced it — whether this situation
genuinely sits within the boundary of one of them (Section 2.2's "contextual
fit," not action-type or tool-name similarity). Two things keep this
honestly gated rather than a name that just delegates trust to the model:

- Only Executable-state surfaces are ever offered as candidates (Section
  2.0: "Surface matching runs against Executable surfaces"), filtered
  deterministically before the model sees anything.
- The model can point at a real candidate by its id. It cannot invent one,
  and nothing about the Surface actually used — its state, its confidence
  counts, its description — ever comes from what the model claims; it's
  always the object already on record in storage. A hallucinated or
  mismatched id, non-JSON output, or a null answer all resolve to "no
  match," exactly like an honest one would — this never fails open.

There's a second gate on top, separate from matching itself: PA Action
Kernel Section 4.2, "First Executable Crossing." A surface's `state`
reaching Executable is the kernel's own confidence assessment (Section 1.5)
— it is deliberately not the same thing as the operator's live, explicit
sign-off that execution may actually run against it, which Section 4.2
requires separately ("the one point in the continuous cycle where live
confirmation is required"). `Surface.execution_rights_confirmed` is that
second flag; `classify_tier()` now refuses Tier A/B for any surface until
it's set, and the only way it gets set is
`core.learning_governance.confirm_execution_rights`, called from
`aldric_chat.py`'s `_present_first_executable_crossing` the moment a
scope's surface first reaches Executable state — it prints exactly what
ALDRIC has recorded (the description, the confirmation/correction counts)
and asks for a real, deterministically-classified "confirmed" before
granting anything. ALDRIC never grants this to itself, matching Section
1.5's parallel rule for surfaces generally: "ALDRIC may not elevate a
surface unilaterally."

What this still does *not* do: execute anything. There is no Capability
Broker in this codebase — a proposed tool call remains classification-and-
audit metadata (`llm/aldric_reply.py`'s own docstring), never a capability.
A correctly matched, rights-confirmed Executable surface can now make
`classify_tier()` return Tier A/B instead of an automatic Tier C, but
nothing downstream currently acts on that tier. What's still genuinely
separate, unbuilt work: PA Action Kernel Component 5 (contextual drift — a
matched surface's own environment shifting since it reached Executable;
`drift_level=None` is passed at the call site today, honestly, not faked),
and calibrating `DEFAULT_PROMOTION_THRESHOLDS` against real usage.

## Giving ALDRIC real hands (the Capability Broker)

Everything above this point in ALDRIC Mode was classification-and-audit
metadata: `proposed_tool_call` got decided a tier, logged, described — never
actually run. `core/capability_broker.py` is where that stops being true,
for two real connectors: Gmail (send an email, create a draft) and Google
Calendar (create, update, delete an event). These were picked because the
operator already has a Google account for them — no custom backend, no
phone, no OAuth server of ALDRIC's own to build first.

One-time setup, done once by the operator (this needs a real Google login,
so it can't be automated from inside a session):

1. Create a project at console.cloud.google.com, enable the Gmail API and
   the Google Calendar API for it.
2. Configure the OAuth consent screen as "External," add your own Google
   account under "Test users."
3. Create an OAuth client ID of type "Desktop app," download its JSON, and
   save it as `client_secret.json` in this repo's root (already in
   `.gitignore` — never commit it).
4. Run `pip install -r requirements.txt` to pick up the three new Google
   client libraries.

The first time `core.capability_broker.get_credentials()` actually needs to
run (i.e. the first time a Tier A/B tool call reaches execution), it opens a
one-time browser consent window and then caches the result in `token.json`
(also gitignored) — every call after that is silent.

What's wired end to end today, only in ALDRIC Mode (`aldric_chat.py`):
`create_email_draft` (Tier A — silent, logged to digest), `send_email`,
`create_calendar_event`, `update_calendar_event`, `delete_calendar_event`
(all Tier B — executed, then reported in the digest with
`core.pa_action_kernel.apply_pa_signature`'s plain AI-authorship disclosure
line appended to whatever externally-visible text the action carries, per
PA Action Kernel Tier B: "PA signature discloses AI authorship
transparently"). `aldric_chat._execute_cleared_tool_call` never re-decides a
tier — it can only be reached at all for a proposed tool call whose
effective tier already cleared as A or B, because
`core.aldric_mode.requires_escalation` raises out of the casual loop before
this point for anything Tier C or permanent-category (see that function's
own docstring). A broker failure (a rejected API call, expired credentials)
is caught, logged to the digest as `status: "failed"`, and reported to the
operator in-session — it does not crash the loop and it is never silently
retried.

What this does not do yet: Google Drive, or any tool beyond these five;
Governance Stack Mode's own Tier C tool calls, once adjudicated and
authorized in `chat.py`, still don't call the broker (see gap list item 9);
and there's no per-operator choice yet about *which* Google account or
whether to disable real execution entirely — today, a working
`client_secret.json` means every cleared Tier A/B call really runs.

## Idempotency Ledger (crash/retry safety around real execution)

Once `core/capability_broker.py` started calling real APIs, a crash or a
retried turn between "the call was made" and "the digest recorded it"
could send the same email twice. `core/idempotency_ledger.py` closes that
gap: `aldric_chat._execute_cleared_tool_call` runs every broker call
through `idempotency_ledger.run_idempotent`, which claims a durable
PENDING row (a race-free INSERT, `storage.db.claim_idempotency_key`)
before executing, and flips it to COMPLETED or FAILED once the broker call
returns or raises — never leaving it PENDING. A repeat of the exact same
call (same tool + arguments, or an explicit caller-supplied key) returns
the already-stored result instead of executing again; a repeat while the
original is still PENDING raises `AlreadyInFlightError` instead of
racing it; a repeat of a FAILED call raises `ActionAlreadyFailedError`
unless the caller explicitly passes `retry_failed=True`.
`idempotency_ledger.list_stuck_pending_actions()` is the health-check path
for rows that stayed PENDING past a crash — it only surfaces candidates,
it never auto-resolves them (see that module's docstring for why: this
codebase doesn't let a heuristic decide an execution-integrity fact any
more than it lets one decide a governance one). This is deliberately not a
tier/permission decision — `classify_tier()` still, and only, decides
whether an action may run at all; see `tests/test_idempotency_ledger.py`.

## Declarative Tool Registry (`config/tool_registry.yaml`)

`core/pa_action_kernel.py`'s `TOOL_REGISTRY` — which tools exist, and for
each one whether it's external-facing, reversible, and which Permanent
Tier C categories it touches — is loaded and schema-validated from
`config/tool_registry.yaml` by `core/tool_registry_loader.py`, not written
as a Python dict literal. A compliance team can add a tool or change its
tags by editing that file; no source change, no touching `core/`. What
does *not* move to that file, on purpose: the six-category set itself
(`models.schemas.PERMANENT_TIER_C_CATEGORIES`) and what a permanent-
category tag actually does (`classify_tier()` forcing Tier C
unconditionally) both stay Python. Any category name in the YAML file that
isn't already one of the six existing categories is a load-time error
(`ToolRegistryError`, fails closed — the process refuses to start) rather
than a silently-ignored typo or a silently-accepted new "safe" category.
See CLAUDE.md Section 10 and `tests/test_tool_registry_loader.py` —
including a test that reclassifies a tool from Tier A to Tier B to Tier C
purely by editing the file, with zero Python changes, which is the actual
proof of the claim above.

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

1. ~~A real surface matcher.~~ **Done.** `llm/surface_matcher.py` is a real
   LLM call over the operator's actual conversation/tool-call context,
   matched against whatever Executable surfaces are on record, gated by
   `classify_tier()` exactly as planned — see "The real Surface Matcher"
   above for the full picture, including the separate PA Action Kernel
   Section 4.2 "First Executable Crossing" gate this uncovered and closed
   alongside it (`Surface.execution_rights_confirmed`). Relevant to ALDRIC
   Mode only — `chat.py` doesn't need this; `NullSurfaceMatcher` stays as
   its correct, on-purpose fallback there. Still open: PA Action Kernel
   Component 5 (contextual drift on a matched surface's own environment) is
   a separate, still-unbuilt piece from matching itself, and this still
   doesn't make anything execute — see below.
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
   "Using ALDRIC Mode" above). Scope-tagged surfaces now accumulate real
   confidence over time (item 8 below) and a proposed tool call is now
   matched against them for real (item 1) — what's still genuinely Phase 4+
   territory and not attempted here: fully autonomous operation with
   triggers arriving without the operator present (there is still no
   Capability Broker — nothing executes a matched, rights-confirmed action),
   and the Daily Digest as a standing artifact rather than an on-demand
   command. Also not yet done: wiring this same casual/escalation flow into
   `webapp.py`'s browser UI — right now it's terminal-only.
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
   "Learning Governance in live conversation" above for the full picture.
   These are exactly the surfaces item 1's real matcher now matches proposed
   tool calls against — the two pieces were built to fit together, not in
   parallel by accident. Whether to ask about one of these surfaces at all,
   and what a repeat answer for the same scope actually does to it, is now
   its own layer on top — see "The Confidence Cascade" above. Still open:
   calibrating `DEFAULT_PROMOTION_THRESHOLDS`/`DEFAULT_PATTERN_CLUSTER_SIZE`
   against real usage instead of the starting defaults picked here, and
   exercising the cascade's own `blocking` self-report against a real model
   call rather than only the mocked test suite.
9. ~~Capability execution ("the Capability Broker").~~ **Half-done.**
   `core/capability_broker.py` is a real executor for two connectors: Gmail
   (send/draft) and Google Calendar (create/update/delete event) — see
   "Giving ALDRIC real hands" above for the full picture, including what
   Tier A "executes silently" and Tier B "executes then notifies, with an
   AI-authorship disclosure line" concretely mean for a tool with a real
   side effect. Wired into ALDRIC Mode only (`aldric_chat.py`); a cleared
   Tier A/B proposed tool call there now genuinely runs, is logged to the
   digest, and a failure is caught and reported rather than crashing the
   session. Still open: Governance Stack Mode's own adjudicated-and-
   authorized Tier C tool calls (`chat.py`) still don't call the broker once
   `authorize_emission()`/`confirm_content()` clears — `AdjudicationRecord`
   already carries `action_hash` precisely so that wiring can bind execution
   to the exact adjudicated action rather than re-deriving it, but the call
   itself isn't made yet; no Google Drive connector; no per-operator choice
   yet about which account is live or whether real execution is disabled
   entirely; and no connector beyond Google's two APIs — a genuinely
   general "any tool" broker is further out than this.

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
