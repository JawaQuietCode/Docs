# Build verification

Date: 2026-11-10

## Automated checks

- Python: `python -m pytest -q` → **162 passed**.
- Node: `node --test tests/*.test.js` → **70 passed, 1 skipped, 0 failed**. The skipped integration test requires the Baileys runtime dependency, which is not installed in this build environment.
- Python compilation: `python -m compileall -q agent tools security skills` → pass.
- JavaScript syntax: `node --check` across `transport`, `plugins`, and `tests` → pass.
- Shell syntax: `bash -n` across setup/start and scripts → pass.
- JSON/YAML parse validation → pass.
- Browser rendering: Chromium headless rendered the live progress panel, final dossier, and runtime card from representative test data. The three previews are in `docs/ui-previews/`.

## Skill security baseline

See `SECURITY-SKILLS-BASELINE.md`. Internal: 10 LOW_RISK. External: 31 LOW_RISK, 2 REVIEW. Hidden: 8 LOW_RISK, 1 REVIEW. REVIEW items are quarantined, not loaded into prompts. No repository skill received a confirmed critical malware finding in this scan.

## Scope and caveats

This is a source-level build and test verification, not a production WhatsApp login test. Provider credentials, Baileys live session, third-party APIs, OS-level antivirus, sandbox isolation, and runtime network egress were not tested here. Static skill scanning is heuristic and is not an antivirus guarantee. See `SECURITY-SKILLS.md`.
