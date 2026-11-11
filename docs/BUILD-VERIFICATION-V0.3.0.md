# Build verification — Kwee v0.3.0

Date: 2026-11-10

## Automated checks

- Python: `python -m pytest -q` → **176 passed**.
- Node: `node --test tests/*.test.js` → **72 passed, 1 skipped, 0 failed**. The skipped integration test requires the Baileys runtime dependency, which is not installed in this build environment.
- Python compilation: `python -m compileall -q agent tools security skills` → pass.
- JavaScript syntax: `node --check` across `transport`, `plugins`, and `tests` → pass.
- Shell syntax: `bash -n` across setup/start and scripts → pass.
- JSON/YAML parse validation → pass.
- Browser rendering: Chromium headless rendered the Velvet Noir live progress panel, deep-plum final dossier, and runtime card at desktop/mobile card dimensions. Previews are in `docs/ui-previews/`.

## Regression coverage

- Nested skill discovery, symlink rejection, skill quarantine, prompt-budget handling, and verified prompt injection.
- Sub-agent use of the trusted runtime API key after tool config redaction; provider/model and request pacing remain shared with Kwee.
- Peer role prompts, parallel team execution, tenant-library memory sharing across chat IDs, and secret-like content rejection.
- Owner/tenant verification and inherited tool allowlists for the `!team` HTTP endpoint.
- Honest UI telemetry for prepared versus injected skills, plus distinct live and final progress surfaces.

## Skill security baseline

See `SECURITY-SKILLS-BASELINE-V0.3.0.md`: internal 10 LOW_RISK; external 127 LOW_RISK, 4 REVIEW, 2 BLOCKED; hidden 8 LOW_RISK, 1 REVIEW. Quarantined skills are excluded from automatic prompt injection.

## Scope and caveats

This is a source-level build and test verification, not a production WhatsApp login test. Provider credentials, Baileys live session, third-party APIs, operating-system antivirus, sandbox isolation, and runtime network egress were not tested here. Static skill scanning is heuristic and is not an antivirus guarantee.
