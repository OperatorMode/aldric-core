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
| `04_Operator_Kernel_KSP1.md` | `core/ksp1_operator_kernel.py` | Deterministic: Loop Manager state machine, single-active-loop rule, session hard-stop conditions, the Adjudication Buffer, and — most importantly — the Tier C two-stage confirmation gate. Explicitly NOT implemented as literal math: the Mode/Layer vocabulary and the KSP Finality phases' `D_KL`/"EGT manifold" language, which the source document itself calls "just language, no claim of truth." |
| `05_PA_Action_Kernel.md` | `core/pa_action_kernel.py`, `core/permanent_category_scan.py` | Deterministic: Tier A/B/C classification for named tools (ALDRIC Mode), and critically, the Permanent Tier C Exceptions check, which runs first and cannot be reached by any mutation path from the API. `permanent_category_scan.py` extends the same category set to free text (used by `chat.py`, since a chat reply has no tool name to look up). LLM step (stubbed): genuine semantic surface matching — `NullSurfaceMatcher` always returns "no match," which is the *safe* default (Tier C), not a real matcher. This kernel is inert in Governance Stack Mode (`chat.py`'s mode) per the source document itself. |
| `06_Learning_Governance.md` | `core/learning_governance.py` | Deterministic: signal-type intake rejection of non-operational-truth signal, the Correction Absolute (`apply_correction` has no confidence-gated bypass), the Structural Floor as unreachable Python constants. Partial: mirror-drift detection implements one of the four documented indicators structurally; the other three need outcome-tracking inputs this skeleton doesn't yet collect. |
| `07_Operator_Profiles.md` | `core/operator_profiles.py` | Intentionally NOT a governance layer, per the source document itself — calibration templates only. |
| `08_Governance_Chain.md` | `core/governance_chain.py`, `main.py` | Deterministic: load-order verification (order-sensitive, cascading failure), and the ALDRIC-Mode-vs-Governance-Stack-Mode initialization-state rule (observation mode vs. DSD Discovery firing immediately). |

## What's built and tested

Run it:

```bash
pip install -r requirements.txt
pytest                    # 45 tests, all deterministic, no network calls
uvicorn main:app --reload # http://127.0.0.1:8000/docs for interactive API

export ANTHROPIC_API_KEY=sk-ant-...   # or Aldric-API, matching the Windows machine's existing var
python chat.py             # actual governed conversation, terminal chat
```

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
   this.
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
3. **Full mirror-drift detection.** Only the "confidence climbing without
   corresponding correction" indicator is implemented. The other three
   need an outcome-tracking data model this skeleton doesn't define yet, and
   only apply once ALDRIC Mode's surfaces exist.
4. **KSP Finality phase orchestration.** `chat.py`'s governed turns produce
   one LLM response per message rather than running the full Structural
   Projection / Parallel Validation / Integrity Gate / Compaction sequence
   as distinct reasoning phases. The Adjudication Buffer gate around the
   output is real either way; the multi-phase pipeline itself is future work
   if you want the phases to be genuinely distinct reasoning passes rather
   than one call.
5. **ALDRIC Mode itself.** Everything built so far is either mode-agnostic
   core or specifically wired for Governance Stack Mode (`chat.py`). Actual
   autonomous operation — triggers arriving without the operator present,
   surfaces accumulating confidence over time, the Daily Digest as a
   standing artifact rather than an on-demand command — is Phase 4+ territory
   and needs its own entrypoint, not `chat.py`.
6. **Persistence beyond SQLite.** `storage/db.py` is plain relational SQLite.
   The project's own tech-stack reference lists `sqlite-vec` as a Phase 6+
   concern (semantic memory recall) — deliberately not pulled forward here.

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
