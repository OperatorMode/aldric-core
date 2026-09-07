# ALDRIC Engineering Constraints

These constraints govern anyone (human or AI) writing code in this repository.
They are derived directly from the eight ratified governance documents,
which now live in `docs/stack/01`–`08` alongside this code (originally copied
in from the operator's own source folder on 6 Sept 2026 — keep this
directory in sync with any future amendment to those documents), and from
the lesson
this project already learned the hard way: a rule that only lives in a prompt
is a rule a long enough session, a confident-sounding drift, or a well-worded
injection can talk past. A rule that lives in a Python module a request must
pass through is not.

## 1. Deterministic rule enforcement

Never rely on the LLM's own output to decide a governance-critical fact:
whether an action is Tier A/B/C, whether a DSD is complete, whether a
confirmation was given, whether an artifact may emit. Every one of those
decisions is made by a function in `core/`, from data the caller supplies
structurally (a static tool registry entry, a stored surface state, an
operator utterance checked against a canonical vocabulary) — never from a
`claimed_tier` or similar self-report field. Those fields exist for audit
logging only.

## 2. Permanent Tier C exceptions are unconditional

`models.schemas.PERMANENT_TIER_C_CATEGORIES` (pricing/cost commitments, scope
commitments, deadline commitments, contractual obligations, legal matters,
any action binding the operator) is a Python constant, not a database row,
not a config value, not something exposed through any API endpoint's request
body. There is no `POST /permanent-exceptions` in this codebase and there
must never be one. If a new category needs to be added, that is a protocol
amendment the operator makes to the source document first, then a one-line
code change — never a runtime toggle.

## 3. Tier C emission is a two-stage, two-utterance gate

Content ratification (`confirmed`/`correct`/`yes`/`proceed`/`locked`) and
emission authorization (`send it`/`transmit now`/`dispatch this`/`authorize
emission`) are disjoint vocabularies checked by disjoint functions
(`classify_confirmation` vs `classify_emission_authorization` in
`models/schemas.py`), and `AdjudicationBuffer.confirm_content()` /
`authorize_emission()` in `core/ksp1_operator_kernel.py` are separate method
calls with no shared code path. Do not "simplify" this into one endpoint that
takes a single utterance and decides which stage it satisfies — that
simplification is exactly the vulnerability KSP-0 Section 9.5 was written to
close.

## 4. No OneDrive paths (carried over from the physical build)

If this codebase is ever deployed back onto the Windows machine referenced in
the ALDRIC project instructions, it must live at a local, non-OneDrive-synced
path (e.g. `C:\Users\hello\Aldric\aldric-core\`). This rule predates this
rewrite and still applies to anything that touches that machine.

## 5. Say what's real and what's a reasoning step

Several documents (KSP-1 especially) use invented vocabulary — Modes M1–MX,
Layers L0–LX, `D_KL` divergence, an "EGT manifold" threshold — to describe
reasoning *posture*, not literal computable mathematics. KSP-0's own preamble
says as much: "the following is just language... no claim of truth." Do not
write code that pretends to compute a real KL-divergence number or a real
physical threshold check for these — that is theater dressed as rigor, and
worse than admitting the step is an LLM judgment call. Where a step is
genuinely a reasoning step (DSD field extraction from conversation, IDS
detection, semantic surface matching, KSP Finality synthesis), say so in the
docstring and route it through `llm/`, then gate its output through the
relevant `core/` module before it can become binding.

## 6. Digest is not filterable

`pa_action_kernel.generate_daily_digest()` takes no parameters that could
hide, delay, or soften an entry. Do not add one. If a caller wants a
filtered view for display purposes, filter client-side after retrieving the
complete digest — never inside the function that produces it.

## 7. Correction Absolute has no bypass branch

`learning_governance.apply_correction()` must never grow an `if
surface.confidence > X: resist` branch, a retry limit, or a "confirm you
really mean it" step. Operator instruction is applied immediately and
completely, every time, and the conflict record is written after, not as a
gate before.

## 8. The Capability Broker never decides a tier

`core/capability_broker.py` is the only module in this codebase allowed to
call a real external API (Gmail, Google Calendar). Its functions must never
grow their own tier/permission logic, a `dry_run` flag that quietly skips
the classify_tier() check, or any argument inspection that decides whether
an action is "safe enough" to run. A caller (`aldric_chat.py` today) decides
whether an action may run, by calling `core.pa_action_kernel.classify_tier()`
first; the broker's only job is to run what it's told, and to raise
`CapabilityBrokerError` — never swallow an exception — when it can't.

## 9. Tests are the spec

`tests/` exists to prove the above claims, not just to exercise code paths.
When you change a `core/` module, the relevant test in `tests/` should be the
thing that tells you whether you preserved the invariant or quietly broke it.
Run `pytest` before considering any change to `core/` done.

## 10. The Tool Registry is declarative; the Permanent Category Set is not

`core/pa_action_kernel.py`'s `TOOL_REGISTRY` is loaded from
`config/tool_registry.yaml` by `core/tool_registry_loader.py`, not written
as a Python dict literal. This is a deliberate, narrow exception to Section
1's "never rely on the LLM's own output" framing extended one step further:
it is fine — good, even — for a *human, reviewed, version-controlled* file
to own which tools exist and whether each is external-facing, reversible,
and which Permanent Tier C categories it touches, because none of that can
ever reach above what Section 2's structural floor allows. A compliance
team editing this file can misclassify a tool (mark something
external-facing that shouldn't be); that is a real risk to manage with
normal review discipline on the file, same as any other config change. What
they cannot do, structurally, is make a permanent-category tool stop being
Tier C, or invent a category outside the six `PERMANENT_TIER_C_CATEGORIES`
already defines — `tool_registry_loader.load_tool_registry()` rejects any
unrecognized category tag at load time (`ToolRegistryError`, fail closed,
process refuses to start) rather than accepting it silently.

Do not extend this pattern to `PERMANENT_TIER_C_CATEGORIES` itself, to the
confirmation/emission vocabularies (Section 3), or to anything else Section
1 and 2 already fix in Python. Those are the structural floor precisely
because they are *not* reachable from a file a request, a misconfiguration,
or a support ticket could quietly edit. "Can a compliance team manage this
without touching source?" is the right question to ask of a *new* piece of
declarative data (which tool maps to which tag); it is the wrong question
to ask of the tags' own meaning or the closed set they're drawn from — the
answer there stays "no, that's a protocol amendment," same as Section 2
already says.
