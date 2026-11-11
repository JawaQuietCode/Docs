# Kwee v0.4.0 — RuangBelajar

- Bundles upstream AI Engineering from Scratch tutor skills as native on-demand Kwee skills under `skills/external/ai-engineering-from-scratch/` (MIT; attribution and license included). Lessons are read from upstream, not mirrored.
- `start.sh` best-effort installs Free Claude Code CLI at a pinned upstream commit. It requires Python 3.14.10 and uv. The CLI is not started as a server.
- FCC has its own provider configuration and is not automatically wired to `LLM_BASE_URL`/`LLM_API_KEY`. Native Kwee `subagent` is the in-process peer that uses the existing Kwee provider/model and saves tenant-local unverified notes.
- No credentials or environment variables were added. No proprietary model internals are reverse-engineered.

## Verification record for this package
- Free Claude Code CLI installed successfully from the pinned commit using `uv tool install --python 3.14.10`; `fcc-doctor` reported version 6.19.6. Its diagnostic showed configuration `missing` and Claude harness `not_installed` in the verification sandbox. FCC server/Admin UI did start and return HTTP 200 for `/admin` and its configuration/status/model endpoints. No live model request was possible without provider configuration and a Claude Code harness.
- Native peer E2E uses the real Kwee `run_turn` dispatch path with deterministic model responses, then confirms a tenant-library SQLite note with `peer-learning`, role, and `unverified` tags.
- The repository test suite has one known unrelated failure: `test_mem0_stays_opt_in` expects `memory.mem0.enabled` false, while `config/default.yaml` sets it true.
