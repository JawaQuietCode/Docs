# Kwee v0.3.0

## Changes

- Added native peer roles (`peer`, `researcher`, `architect`, `critic`, `mentor`, `builder`) and parallel team tasks through the existing `subagent` tool.
- Fixed sub-agent provider authentication: use the trusted runtime credential after redacted tool config is received; preserve the same provider/model/fallback/retry/pacing settings as the main agent.
- Store successful peer learning outputs in the tenant-shared journal library with `unverified` provenance; secret-like content is rejected.
- Discover nested `SKILL.md` packages at arbitrary depth. Skill prompt telemetry now distinguishes `loading`, `prepared`, `injected`, `not_injected`, and `failed` rather than claiming success during preparation.
- Apply the same prepared-versus-injected verification to the OpenHands preamble path.
- Update progress UI to a feminine Velvet Noir palette and give the final dossier a distinct immersive deep-plum treatment.

## Verification target

- Nested skill discovery, symlink rejection, quarantine, and prompt inclusion status.
- Sub-agent credential source, same-provider pacing, role prompts, tenant-library persistence, and secret rejection.
- Python unit/integration suite, Node test suite, syntax checks, and browser-rendered live/final UI.

## Upstream design references

The implementation uses ideas from the AI Engineering from Scratch curriculum (agent loops, role-based teams, memory, and evaluation) and reviews Free Claude Code's protocol-proxy and messaging-bridge architecture. Neither repository is installed as a runtime dependency.
