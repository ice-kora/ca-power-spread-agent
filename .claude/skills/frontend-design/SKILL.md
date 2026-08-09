---
name: frontend-design
description: Use when designing or redesigning a web UI, dashboard, demo, component, or frontend experience. Forces a design-first workflow, strong information hierarchy, deliberate visual direction, business-first copy, responsive behavior, and screenshot-based critique before implementation is accepted.
---

# Frontend Design

Treat every frontend task as a product-design problem before treating it as a coding problem.

The purpose of this skill is to prevent technically correct but visually generic, dense, template-like, or “AI-generated” interfaces.

## 1. Define the brief before coding

Before writing implementation code, state:

- **Subject**: what the product is actually about.
- **Audience**: who must understand or use it.
- **Primary job**: the single most important thing the page must help that person do.
- **Primary decision**: what the user should understand within 10 seconds.
- **Tone**: e.g. professional trading workstation, editorial research tool, restrained enterprise product, consumer assistant, etc.
- **Constraints**: framework, existing backend, accessibility, responsive targets, data density, latency.

If these are unclear, infer a reasonable draft and mark it as a design assumption.

## 2. Design before implementation

Do not begin by editing HTML/CSS.

First produce a compact design proposal containing:

### Information hierarchy
List what appears:
1. above the fold,
2. in the primary flow,
3. only after user interaction,
4. only in technical/debug detail.

### Visual system
Define:
- 4–6 named color tokens,
- typography roles (display, body, data/utility),
- spacing rhythm,
- corner/radius treatment,
- border/shadow treatment,
- status colors and their semantic meaning.

### Layout
Provide an ASCII wireframe for desktop and a short mobile/responsive rule.

### Signature element
Choose exactly one memorable visual interaction or composition that belongs to this product’s subject matter. Spend visual boldness there; keep the rest disciplined.

### Interaction model
Describe:
- primary CTA,
- progressive disclosure,
- loading state,
- success/warning/error states,
- streaming or live states if relevant,
- what remains sticky or persistent.

Do not implement until this design proposal is internally coherent.

## 3. Design from user language, not system internals

Name interface elements by what the user recognizes and controls.

Prefer:
- “预计价差” over `expected_return`
- “风险检查” over `risk_gate.decision`
- “未参与决策” over `decision_eligible=false`

Raw fields, reason codes, JSON, internal module names, version strings, and debug information should be hidden behind deliberate technical-detail affordances unless the user explicitly needs them.

Copy is part of the design. Use plain, specific language. Avoid filler, marketing slogans, and machine-generated sounding prose.

## 4. Build the visual hierarchy around the page’s primary job

The first viewport must answer the page’s primary question.

Do not let status dashboards, disclaimers, audit metadata, or configuration details dominate the first viewport unless they are the primary job.

Use size, spacing, typography, and placement to create a clear visual order:

1. primary decision/action,
2. key supporting evidence,
3. next action,
4. deeper explanation,
5. technical details.

Avoid presenting every backend module as a separate equal-weight section.

## 5. Avoid generic AI-dashboard patterns

Actively reject designs that rely on:

- endless equal-weight cards,
- `S1 / S2 / S3` section numbering without a true sequential user need,
- huge status panels before the primary task,
- raw tables as the default UI,
- excessive badges,
- gradients used only to make the page feel “modern”,
- generic chatbot sidebars disconnected from the product flow,
- raw JSON/tool traces in the normal view,
- long labels made from backend field names,
- decorative motion with no information purpose.

A restrained design is acceptable only when spacing, typography, data formatting, and interaction details are precise.

## 6. Typography and data formatting

Use a clear type hierarchy and format data for humans.

Rules:
- Round numbers to an appropriate business precision.
- Use units consistently.
- Keep labels short.
- Align comparable numeric values.
- Avoid wrapping technical identifiers in prominent cards.
- Use monospaced text only for IDs, code, timestamps, or raw technical data.
- Ensure Chinese and English mixed typography remains visually balanced.

## 7. Progressive disclosure

Default view should be understandable without scrolling through technical detail.

Prefer:
- summary → expand details,
- business explanation → technical evidence,
- human-readable trace → raw trace,
- audit summary → full audit panel.

Do not delete technical detail when it matters for auditability; hide it behind an explicit control.

## 8. Motion and streaming

Use motion only when it explains system state or causality.

Good uses:
- progressive reveal after a decision is generated,
- streaming answer text,
- real tool-execution status updates,
- lock → reveal state transitions,
- subtle emphasis when a status changes.

Bad uses:
- random glowing effects,
- decorative continuous animation,
- fake tool activity,
- fake “thinking” steps.

Never fabricate an execution step for visual effect.

## 9. Screenshot critique loop

After implementation:

1. Render at 1920×1080.
2. Render at 1440×900.
3. Inspect the first viewport before scrolling.
4. Ask:
   - Can the primary decision be understood in 10 seconds?
   - Is the next action obvious?
   - Is there any horizontal scrolling in the default experience?
   - Are internal field names visible unnecessarily?
   - Does any section feel like a debug console?
   - Does the interface tell one coherent story?
5. Remove or demote at least one nonessential visual element.
6. Repeat until the hierarchy is obvious without explanation.

## 10. Accessibility and responsiveness

Minimum quality floor:

- visible keyboard focus,
- semantic controls,
- sufficient text/background contrast,
- no status communicated by color alone,
- reduced-motion support where animation exists,
- no required horizontal scrolling at target desktop widths,
- usable layout on narrower screens.

## 11. Handoff rule

When the task is a redesign, separate the work into:

**Phase A — Design**
- audit current interface,
- propose hierarchy,
- propose wireframe,
- propose visual tokens,
- provide static/high-fidelity mock.

**Phase B — Implementation**
- implement only after the design direction is accepted,
- preserve existing business behavior unless explicitly authorized,
- verify with screenshots and interaction tests.

Do not silently redesign business logic while redesigning presentation.
