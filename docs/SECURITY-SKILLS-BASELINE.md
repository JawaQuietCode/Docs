# Skill scan baseline

Date: 2026-11-10
Scanner: `security/skill_guard.py` (offline static heuristic)

| Tier | Scanned | LOW_RISK | REVIEW | BLOCKED |
|---|---:|---:|---:|---:|
| Internal | 10 | 10 | 0 | 0 |
| External | 33 | 31 | 2 | 0 |
| Hidden | 9 | 8 | 1 | 0 |

## Quarantined for owner review

- `ai-research` (`REVIEW`, score 22): the skill bundle exceeds scanner file/size limits, so the audit is incomplete. This is not evidence of malware; it remains quarantined because incomplete scans are not treated as safe.
- `planning-with-files` (`REVIEW`, score 36): helper scripts use subprocess/shell and environment variables for legitimate-looking plan/session operations. These are privileged capabilities that require owner review before the skill is trusted.
- `impeccable` (`REVIEW`, score 28): bundled browser helpers communicate with a localhost service and contain a large minified screenshot helper. The network capability is not proof of exfiltration; the skill remains quarantined pending source/provenance review.

`encode-decode` is LOW_RISK after refining the scanner to distinguish ordinary `bytes.fromhex()` and regex `.exec()` usage from encoded dynamic execution. No repository skill received a confirmed critical malware finding in this scan. LOW_RISK is not a guarantee of safety.
