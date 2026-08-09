# CAISO UI Design Skills

This bundle contains two portable Agent Skills:

1. `frontend-design/` — a general, design-first frontend skill written specifically for this project workflow. It is not a verbatim redistribution of Anthropic's official skill; it follows the same broad goal of deliberate, non-generic frontend design while adding a stricter design-before-code and screenshot-review process.
2. `caiso-trading-demo-ux/` — the project-specific UX contract for the CAISO Trading Decision Agent demo.

## Recommended usage

Load both skills for any UI/design task on the CAISO project.

The general skill decides **how to design well**.
The CAISO skill decides **what the product must communicate and what must remain frozen**.

Recommended instruction to the coding agent:

> Read and obey both `frontend-design/SKILL.md` and `caiso-trading-demo-ux/SKILL.md`. For the first pass, do not modify production UI code. Audit the current screenshots and deliver wireframes + a visual token proposal only. Wait for design approval before implementation.

## If your agent supports Agent Skills folders

Place each folder in the skill directory expected by that environment. If it does not, attach or paste both `SKILL.md` files into the task context and explicitly instruct the agent to follow them.

## Official frontend-design skill

Anthropic also publishes an official `frontend-design` Agent Skill in its public `anthropics/skills` repository under Apache-2.0. If your environment can install remote skills/plugins directly, you may prefer installing that official skill and using only this bundle's `caiso-trading-demo-ux` as the project-specific companion.
