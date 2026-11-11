# Skill security baseline — v0.3.0

Date: 2026-11-10

The scanner inspected each external root Markdown skill and each nested `SKILL.md` package independently. It did not execute any content.

| Tier | LOW_RISK | REVIEW | BLOCKED | Total |
|---|---:|---:|---:|---:|
| Internal | 10 | 0 | 0 | 10 |
| External | 127 | 4 | 2 | 133 |
| Hidden | 8 | 1 | 0 | 9 |

## External quarantine evidence

- `ai-research` — REVIEW, filesystem size/file-count limit. The aggregate package is too large for the scanner; this is not proof of malware.
- `ai-research/03-fine-tuning/unsloth` — REVIEW, filesystem size/file-count limit.
- `planning-with-files` — REVIEW, shell execution and environment/secret access signals. These capabilities may be legitimate for a planning skill, but require owner review.
- `ai-research/06-post-training/grpo-rl-training` — REVIEW, selected Python dangerous-call AST signal.
- `ai-research/07-safety-alignment/nemo-guardrails` — BLOCKED, prompt-injection signature matched in the skill content.
- `ai-research/07-safety-alignment/prompt-guard` — BLOCKED, prompt-injection signature matched in the skill content.
- Hidden `impeccable` — REVIEW, browser/network capability and bundled helper code; it remains quarantined.

A `BLOCKED` or `REVIEW` verdict is a policy decision from static heuristics, not a malware attribution. An owner can inspect the exact evidence with `skill action=audit`; the tool does not silently approve a quarantined skill.

## Loader verification

- Nested `SKILL.md` packages are discovered recursively, without following symlink escapes.
- The native prompt budget is 32,000 characters for the skill block. External skills are trimmed by available budget; a skill is counted only if its exact heading is present in the final system prompt.
- OpenHands has the same distinction: `prepared` after building a preamble, `injected` only after the accepted request body contains all selected skill headings.
- The current prompt check for a representative multi-agent/CrewAI request verified 10 internal and 3 external skill headings, with no missing selected headings.
- Prompt-injection telemetry is isolated by tenant/chat request ID in both native and OpenHands paths; API responses do not expose the internal request key.

Static scanning is not antivirus and does not guarantee malware detection. Keep operating-system protection, sandboxing, least privilege, and outbound-network controls enabled.
