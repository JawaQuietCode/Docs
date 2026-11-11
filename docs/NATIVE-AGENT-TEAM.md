# Native Kwee agent team

## Runtime contract

Kwee remains the runtime owner. Its main turn loop, tool registry, permission checks, audit trail, tenant boundaries, and memory store stay in-process. Peer agents are isolated `run_turn` sessions with their own short context and a role-specific instruction set.

Supported roles:

- `peer`: general discussion and idea development
- `researcher`: source-first research; separates facts, inference, and uncertainty
- `architect`: module boundaries, data flow, and minimal system design
- `critic`: counterexamples, security risks, and verification criteria
- `mentor`: stepwise explanation and reusable lessons
- `builder`: small implementation slices and testable plans

A `subagent` call can run one role or up to five `tasks` concurrently. Tool permissions are intersected with the parent turn's `tool_allow`; recursive subagents and external side-effect tools remain blocked. Peer workers use the same `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, fallback models, retry and request pacing configuration as the main agent. The API key is read from the trusted process configuration because tool-visible config is redacted; it is never copied into tool output or prompts.

## Memory and sharing

Successful peer results can be written to the tenant's shared journal library. Each note carries `peer-learning`, the role name, and `unverified` tags. Later peers receive a small set of relevant library notes as explicitly untrusted context; they cannot write to the journal through their read path. The main Kwee agent can also search notes through `research_journal.search_notes`. Notes are not automatically injected into the trusted long-term memory prompt. A human or later verification step must establish a fact before it is promoted.

This gives the roles a common local learning shelf without sharing one tenant's data with another tenant. It does not create a public internet community or connect to third-party agents automatically.

## Upstream references and adaptation

- [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch) and [curriculum](https://aiengineeringfromscratch.com/): used as the design reference for agent loops, planning, tool use, memory, skill libraries, durable orchestration, role-based teams, and evaluation. Its README describes 542 lessons across 20 phases and provides a `npx skills add` installer for tutor skills in compatible coding-agent hosts. Those tutor skills are not a drop-in runtime for Kwee's Python/WhatsApp host; Kwee adopts the runtime patterns natively instead of installing a second coding-agent host or copying the full course.
- [Free Claude Code](https://github.com/alishahryar1/free-claude-code): reviewed as a provider-backed protocol proxy for Claude Code/Codex clients, with optional Discord/Telegram remote-session wrappers. Its README documents separate provider configuration and messaging credentials. It is not itself a peer-agent social network. Kwee does not vendor or launch it because that would add a separate process and credentials while the native peer tool already uses Kwee's existing provider settings.

## Operational limits

- Peer calls consume additional LLM requests and provider quota; use them only when a second perspective or parallel work has value.
- Shared notes are model-generated and explicitly marked unverified. Search results are context, not proof.
- The community is tenant-local and currently consists of Kwee's named peer roles. Cross-instance or public-network peer discovery is not implemented.
- No new environment variables are required.
