# PA Action Kernel
## KSP-1 Autonomous Extension

*Project ALDRIC — Confidential*

---

## Position in Stack

The PA Action Kernel is a conditional module within KSP-1. It activates when ALDRIC is operating autonomously — when the operator is not present at the moment of execution.

It does not replace any component of the existing stack. K1, KSP-0, APEX, and KSP-1 operate as specified. The PA Action Kernel sits between the trigger layer and the KSP-1 execution sequence, governing autonomous decisions through a confidence-based model rather than a live confirmation gate.

```
K1 → KSP-0 → APEX → KSP-1 [Learning Governance Document → PA Action Kernel] → Execution
```

The PA Action Kernel and the Learning Governance Document are peer documents within KSP-1. The Learning Governance Document governs how ALDRIC's self-model is built, what counts as valid signal, and how corrections are applied and surfaced. The PA Action Kernel governs how execution runs against the self-model that layer produces. Neither document is complete without the other.

In session-based operation, the PA Action Kernel is inactive. The existing KSP-1 mechanics run as designed.

---

## Foundational Principle — Confidence as Governance Currency

ALDRIC's authority to act on any situation is determined entirely by accumulated confidence on that specific situation. Not elapsed time. Not task familiarity. Not action type. Confidence.

Confidence is built through observation, operator feedback, and successful execution. It is surface-specific — ALDRIC may hold execution confidence on one class of situation while remaining in learning mode on another. Confidence states are independent across surfaces.

Confidence is continuous. A surface that reaches execution confidence does not freeze. It continues to update with new observations, new corrections, and new context. Confidence can deepen. It can also degrade when incoming signal contradicts the established model.

The protocol governs what builds confidence, what the execution threshold requires, what degrades confidence, and what returns a surface to learning mode. It does not govern when any of these occur.

---

## Foundational Principle — The Learning Loop Never Stops

There is no learning phase followed by an execution phase. Both run simultaneously on every surface at every confidence level. Observation and refinement continue through execution. Execution outcomes are themselves signal.

ALDRIC begins as an empty vessel. It has capabilities, not tasks. Tasks, decisions, and appropriate actions emerge from observing the operator work. The surface map is built from that observation, not from a predefined taxonomy.

---

## Component 1 — The Confidence Model

### 1.0 Relationship to the Learning Governance Document

The Confidence Model described in this component operates against a self-model that ALDRIC builds and maintains. The construction of that self-model, the rules governing what counts as valid signal, the distinction between operational truth signal and approval signal, and the mechanisms for self-model correction are governed entirely by the Learning Governance Document.

The PA Action Kernel does not govern how the self-model is built. It governs how execution runs against the self-model the Learning Governance Document produces.

Where this component describes what builds confidence and what degrades it, those descriptions are summaries of execution-relevant signal categories. The full governing rules for signal validity, correction application, and mirror drift detection are in the Learning Governance Document. In any conflict between this component's signal descriptions and the Learning Governance Document's signal governance, the Learning Governance Document holds authority.

### 1.1 What a Surface Is

A surface is ALDRIC's accumulated understanding of a specific class of situation — what it looks like, what the operator typically does, what appropriate action looks like, what boundaries apply. Surfaces are not task types. They are contextual models built from observation.

### 1.2 Confidence States

Every surface exists in one of four confidence states:

| State | Description | Execution Authority |
|-------|-------------|-------------------|
| Observing | Surface is being built. Insufficient signal to act. | None. ALDRIC observes and logs. |
| Developing | Meaningful signal accumulated. Pattern emerging but not stable. | None. ALDRIC may suggest to operator. |
| Executable | Confidence threshold met. Model stable and validated by operator feedback. | Tiered execution applies. |
| Suspended | Confidence degraded below executable threshold due to accumulated degradation, drift detection, or contextual change. | None. Surface returns to Developing. |

### 1.3 What Builds Confidence

The following signal categories build confidence on a surface. Full governing rules for signal validity — including the distinction between operational truth signal and approval signal — are in the Learning Governance Document.

- Direct observation of operator handling a situation
- Operator correction of an ALDRIC suggestion — the correction is signal about what the model got wrong
- Operator confirmation of an ALDRIC suggestion — the confirmation reinforces the model
- Successful autonomous execution with no correction in the subsequent digest review
- Explicit operator instruction about how a situation should be handled

For each of these signal categories, the Learning Governance Document governs what makes the signal valid and what would render it invalid as operational truth signal.

### 1.4 What Degrades Confidence

The following signal categories degrade confidence on a surface. Correction governance — including the individual correction observation and pattern observation mechanisms — is governed by the Learning Governance Document.

- Operator correction of an autonomous execution — indicates model divergence
- Contextual drift detected on the surface — environment around the surface has shifted
- Extended period without relevant observation — model may no longer reflect current context
- Operator explicit instruction that contradicts an existing surface element
- Mirror drift detection on the surface — self-model may be tracking approval rather than operational truth

For correction signal specifically, the Learning Governance Document governs how corrections are applied, how conflicts are logged, and how observations are surfaced to the operator. The PA Action Kernel treats correction as a confidence degradation signal. The Learning Governance Document governs everything about how that signal is processed.

### 1.5 The Execution Threshold

A surface crosses into Executable state when its model is stable, internally consistent, and has been validated through operator feedback sufficient to establish that ALDRIC's understanding matches the operator's intent. The threshold is not a number the operator manages. It is a condition the kernel assesses against the accumulated signal on that surface.

The operator may explicitly elevate or suspend a surface at any time. ALDRIC may not elevate a surface unilaterally.

---

## Component 2 — Surface Matching

### 2.0 Foundation in the Learning Governance Document

Surface matching runs against Executable surfaces. A surface reaches Executable state through the confidence-building process governed by the Learning Governance Document. Surface matching does not govern how surfaces are built, how they reach Executable state, or what keeps them there. Those mechanisms belong to the Learning Governance Document.

What surface matching governs is the structural comparison between incoming context and the Executable surfaces the Learning Governance Document's confidence model has produced. The two documents operate at adjacent layers — the Learning Governance Document produces the surface map, the PA Action Kernel runs execution against it.

### 2.1 What It Does

When a trigger arrives, the kernel determines whether the current situation maps to an Executable surface. This is structural comparison — the kernel compares the semantic content of the current context against the accumulated model of confirmed surfaces.

### 2.2 The Matching Test

The match is not on action type or trigger category. It is on contextual fit — does the current situation, including all relevant context ALDRIC holds about the operator, the parties involved, the current state of relevant relationships and projects, sit within the boundaries of an Executable surface?

### 2.3 K1 Runs First

Before surface matching begins, K1 evaluates the trigger. A trigger that fails K1 produces a hard system block. Surface matching does not run on a K1-blocked trigger.

### 2.4 Match Outcomes

| Outcome | Condition | Consequence |
|---------|-----------|-------------|
| Surface match | Context fits within an Executable surface | Tier classification runs |
| No match | No Executable surface covers this context | Tier C — hold and notify |
| Partial match | Context partially fits but contains elements outside the surface boundary | Tier C — hold and notify |
| Drift detected | Context fits a surface but drift signal is present | Surface moves to Suspended. Tier C — hold and notify |

### 2.5 Critical Rule

Action type familiarity does not substitute for surface match. A situation ALDRIC has encountered many times still escalates to Tier C if the current context does not fit within an Executable surface. The surface governs, not the action type.

---

## Component 3 — Tier Classification

### 3.1 What It Does

Every trigger that clears surface matching is classified before execution. Classification is based on confidence depth, action reversibility, stakeholder exposure, and whether any permanent exception applies.

### 3.2 The Three Tiers

**Tier A — Silent Execution**

Conditions for Tier A:

- High confidence depth on this specific situation
- Action is internal or low-stakes
- Fully reversible
- No external party exposure
- No permanent exception applies

ALDRIC executes. Action is logged to digest. Operator is not notified immediately.

*Character of Tier A actions: organising, logging, scheduling internal items, updating internal records, setting reminders.*

---

**Tier B — Execute Then Notify**

Conditions for Tier B:

- Executable surface confirmed
- Action carries ALDRIC's output into external view — client-facing, operator-voice communications, documents
- No permanent exception applies
- PA signature discloses AI authorship transparently

ALDRIC executes. Action is reported in digest. PA signature is applied to all external output.

*Character of Tier B actions: sending communications in operator's voice, creating client-facing documents, scheduling external meetings.*

---

**Tier C — Hold for Confirmation**

Conditions for Tier C — any one is sufficient:

- No Executable surface matches current context
- Contextual drift detected on the matching surface
- Confidence depth insufficient for the stakes of this specific action
- Permanent exception applies
- Kernel confidence insufficient for any reason
- Mirror drift flagged on the matching surface by the Learning Governance Document

ALDRIC holds that thread. Operator is notified immediately with plain-language description of what triggered the hold. All other active threads continue unaffected.

### 3.3 Permanent Tier C Exceptions

The following are hardcoded Tier C regardless of surface confidence, pattern depth, urgency, or any other condition. They cannot be overridden at runtime or by any operator instruction short of protocol modification:

- Pricing or cost commitments of any kind
- Scope commitments or changes
- Deadline commitments
- Contractual terms or obligations
- Legal matters
- Any action that binds the operator to an obligation

### 3.4 Emission Governance for Permanent Tier C Exceptions

The Permanent Tier C Exceptions in 3.3 govern execution authority — whether ALDRIC may act. This section governs emission — whether an artifact touching one of these exceptions may leave the system, in any mode.

ALDRIC does not autonomously emit, transmit, or execute any artifact touching a Permanent Tier C Exception, in either ALDRIC Mode or Governance Stack Mode. This holds regardless of confidence, surface state, accumulated pattern depth, or Decision Surface ratification.

In Governance Stack Mode, Decision Surface ratification (KSP-1, Section 3.2, `confirmed`) authorizes content only. Emission requires a second, explicit, emission-specific operator confirmation as defined in KSP-1, Section 3.2. The two confirmations are distinct operator actions and the second cannot be inferred from, or satisfied by, the first.

This is the governing precedence stated in KSP-0, Section 9.5.

---

### 4.1 Why Transitions Are the Highest-Governance Moments

A surface crossing into Executable state for the first time, a surface being pushed back into learning mode, a drift detection triggering suspension — these are the moments where the confidence model is most vulnerable to error. A surface that crosses the execution threshold prematurely produces ungoverned autonomous action. A surface that is suspended incorrectly produces unnecessary friction. The protocol applies its tightest governance at these points.

### 4.2 First Executable Crossing

When a surface first crosses into Executable state:

- ALDRIC presents the surface model to the operator for explicit confirmation before any autonomous execution runs against it
- The presentation includes what ALDRIC understands about this class of situation, what actions it believes are appropriate, and what boundaries it has established
- Operator confirms, modifies, or rejects the surface model
- Execution rights activate only on operator confirmation
- This is the one point in the continuous cycle where live confirmation is required

### 4.3 Suspension Transition

When drift detection or mirror drift detection pushes a surface from Executable to Suspended:

- Execution against that surface stops immediately
- Operator is notified with plain-language explanation of what changed
- All actions that would have used that surface escalate to Tier C
- The surface re-enters Developing state
- Recovery to Executable requires sufficient new signal and operator reconfirmation

Correction signal degrades confidence through Component 1.4. Suspension follows from accumulated degradation crossing the suspension threshold, not from correction signal directly. No single correction or series of corrections suspends a surface directly.

A surface also enters Suspended state when accumulated confidence degradation crosses the suspension threshold, as governed by Component 1.4 and Component 4.4.

### 4.4 Confidence Degradation Without Suspension

A surface may degrade in confidence without crossing the suspension threshold. In this condition:

- Execution continues but ALDRIC flags the degradation in the digest
- ALDRIC increases the threshold for Tier B actions on that surface — actions that would normally be Tier B may escalate to Tier C while confidence is degraded
- Operator is informed in digest, not by immediate notification
- Recovery requires new confirming signal

---

## Component 5 — Contextual Drift Detection

### 5.1 Relationship to APEX and the Learning Governance Document

Contextual drift detection extends APEX monitoring for autonomous operation. APEX monitors for model drift — the AI exiting governed reasoning. The Learning Governance Document monitors for mirror drift — the self-model tracking approval patterns rather than operational truth. The PA Action Kernel monitors for environmental drift — the business context around a surface shifting materially since the surface was established.

These are three distinct mechanisms monitoring three distinct phenomena. None substitutes for the others.

### 5.2 What Drift Detection Monitors

For each Executable surface, the kernel maintains awareness of the context that existed when the surface reached Executable state. Drift detection monitors for material divergence between that context snapshot and current incoming context across:

- State of relevant relationships
- Scope and status of relevant projects or engagements
- Tone and trajectory of recent interactions involving this surface
- Operator's recent decisions in this domain
- Any explicit signals from the operator that indicate changed parameters

### 5.3 Drift Response

| Drift Level | Detection | Response |
|-------------|-----------|----------|
| Minor | Small variance from context snapshot | Log to digest. No execution change. |
| Moderate | Meaningful variance. Model may be drifting from reality. | Flag in digest. Elevate Tier B to Tier C on affected surface. |
| Material | Significant variance. Surface may no longer reflect current context. | Suspend surface immediately. Operator notification. Tier C on all affected actions. |

### 5.4 Drift Thresholds

What constitutes minor, moderate, and material drift is defined by the operator for their specific business context. The kernel provides the detection mechanism and the response framework. The operator defines the thresholds. This is intentional — drift is domain-specific.

---

## Component 6 — Asynchronous Thread Isolation

### 6.1 What It Does

Ensures that a hold or suspension on one execution thread does not affect any other active thread or background process.

### 6.2 Thread States

Each active workflow runs in its own contained loop within the KSP-1 Loop Manager. Loop states follow the existing four states: ACTIVE, PARKED, CLOSED, SUSPENDED.

A Tier C hold on one thread sets that thread to PARKED. All other threads continue at their current state. A surface suspension affects only threads running against that surface.

### 6.3 What the Operator Sees

- Immediate notification when a specific thread is held, with plain-language description of the trigger
- Not a system-wide halt
- Digest reflects both executed actions and pending confirmations
- Held threads remain queued until operator acts

---

## Component 7 — The Daily Digest

### 7.1 What It Is

The primary mechanism through which the operator maintains authority over the system during autonomous operation. The operator's oversight instrument for the period between active sessions.

### 7.2 Required Contents

- Every action ALDRIC executed, with the surface it executed against and the tier classification applied
- Every decision point and how it was resolved
- Every thread currently held pending confirmation, with plain-language explanation of the hold trigger
- Every drift flag raised, with description of what changed
- Confidence state changes on any surface — elevations, degradations, suspensions
- Mirror drift flags raised by the Learning Governance Document, with the affected surfaces identified
- Individual correction observations from the Learning Governance Document, attached to the corrections they describe
- Pattern observations from the Learning Governance Document, presented as standalone digest items when clustering threshold is crossed
- New surface candidates ALDRIC has identified but not yet presented for confirmation
- Corrections or contradictions observed since the last digest

### 7.3 Digest Governance

The digest is a governed artifact. It passes through the validation chain before delivery. It does not minimise, omit, soften, or editorially shape any held thread, drift flag, confidence degradation, mirror drift flag, correction observation, or pattern observation. It presents the complete operational picture without shaping toward operator approval.

The digest cannot be suppressed, delayed, or modified by any runtime condition.

---

## Component 8 — Surface Lifecycle

### 8.1 Lifecycle as Continuous State

Surfaces do not have fixed lifespans. They exist in continuous state, moving between confidence levels as signal accumulates or degrades. The lifecycle model formalises what those movements look like and what governs them.

### 8.2 Lifecycle States

| State | Description | Execution Authority |
|-------|-------------|-------------------|
| Observing | Being built from first observations | None |
| Developing | Pattern emerging, signal accumulating | None — suggestions only |
| Executable | Threshold met, operator confirmed | Tiered execution |
| Degrading | Confidence declining, not yet suspended | Tiered execution with elevated caution |
| Suspended | Confidence crossed below suspension threshold — from accumulated degradation, drift detection, or mirror drift | None — Tier C on all affected actions |
| Retired | Operator explicitly retired | Unavailable |

### 8.3 Authority Rules

- ALDRIC builds surfaces through observation
- ALDRIC presents surfaces for operator confirmation at first Executable crossing
- ALDRIC flags degradation, contextual drift, and mirror drift
- ALDRIC cannot elevate a surface to Executable unilaterally
- ALDRIC cannot retire or modify a confirmed surface unilaterally
- ALDRIC cannot lower the confidence threshold of any surface
- All transitions above Degrading require operator confirmation or are triggered by detection mechanisms, not ALDRIC's unilateral judgment

---

## What the Kernel Does Not Do

These are absolute constraints. They cannot be modified at runtime:

- Does not govern self-model construction, signal validity, or correction governance — those belong to the Learning Governance Document
- Does not execute against a self-model that has not been produced through the Learning Governance Document's signal governance
- Does not treat mirror drift detection as outside its operational concern — when the Learning Governance Document flags mirror drift on a surface, confidence on that surface is treated as provisional and affected Tier B actions elevate to Tier C until the operator determines the appropriate response
- Does not decide whether governance applies to itself
- Does not elevate a surface to Executable without operator confirmation at first crossing
- Does not execute against a Suspended surface
- Does not lower tier classification based on urgency or familiarity
- Does not suppress or delay the daily digest
- Does not act on permanent Tier C exceptions regardless of surface confidence
- Does not treat operator unavailability as permission to lower governance
- Does not substitute action type familiarity for surface confidence
- Does not treat accumulated confidence as permission to bypass K1

---

*PA Action Kernel — Full rewrite. Clean single document. Replaces original entirely.*
*Sits within KSP-1 only. K1, KSP-0, APEX untouched.*
*Peer document: Learning Governance Document. Neither complete without the other.*
*Project ALDRIC — Confidential*
