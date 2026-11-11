# Autonomous 24/7 hand-off profile

## Approval model

The owner profile is already configured as `security.accessMode: owner` and `approvalPolicy: never`; this rebuild makes the intended boundary explicit in the system prompt. Routine requested work should not trigger a second approval prompt. Destructive/high-impact actions retain their own tool-level checks. `cron add` may proceed when the original owner message explicitly asks to create/schedule a job; unrelated schedule mutations and destructive actions still require confirmation.

## Request pacing and HTTP 429

`llm.requestIntervalSeconds` defaults to 10 seconds and is enforced process-wide per provider base URL across chats and threads. Non-stream retries use bounded exponential backoff; streaming retries now honor `Retry-After` when supplied. Set the interval to 0 only when the provider's documented quota supports higher throughput. This is a pacing safeguard, not a guarantee against provider-side 429s.

## Skill observability

`skills.loader.skills_block()` records a prompt-load snapshot. `GET /skills` and `!skills` expose the last sequence, timestamp, and actual internal/contextual skill names injected. Catalogue presence alone is not proof of loading. The snapshot is process-local and reflects the most recently built prompt.

## Memory

SQLite uses WAL, NORMAL synchronous mode, and a 15-second busy timeout. Summary compaction retains the newest tail so recent facts are not discarded. This source archive contains no `data/memory/kwee.db`; runtime creates/migrates it on first start. Do not restore a database from another owner or tenant.

## External skill sources

Run `bash scripts/install-system-skills.sh` on a network-enabled VPS to fetch Agent-Reach and Scientific Agent Skills. The current build includes reviewed router skills; the upstream full skill bodies are fetched by that installer because this build environment had no GitHub network access. Review the lockfile and upstream changes before updating. Agent-Reach CLI package installation is opt-in (`INSTALL_AGENT_REACH_CLI=1`).
