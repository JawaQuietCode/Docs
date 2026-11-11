# Kwee Skill Security Boundary

## Default behavior

- All repository skill tiers (internal, hidden, external, and `always: true`) pass through `security/skill_guard.py` before they are added to an agent prompt or made available through the skill catalog.
- The scanner never imports, sources, evaluates, or executes the skill being inspected.
- `LOW_RISK` is eligible for loading. `REVIEW` and `BLOCKED` are quarantined and excluded from prompt injection, matching, catalog listing, and `get`.
- Owners can inspect a quarantined skill with the native `skill` tool: `action=audit`, `name=<exact skill name>`. This reports evidence; it does not approve or install the skill.
- `.zip` and `.skill` archives are inspected in a temporary directory with entry-count, size, path-traversal, and symlink checks. The archive contents are never executed.
- `SKILL.md` packages are discovered recursively at arbitrary depth; symlink components and paths escaping the skill root are skipped. Each scanned file receives a SHA-256 entry in the report. Cache entries are invalidated by recursive file metadata signatures; use `skill action=reload` after manual changes.
- Skill prompt status is not inferred from selection. The loader reports `prepared` while composing the prompt and `injected` only after checking that every selected skill heading is present in the final prompt passed to the native engine. The OpenHands preamble is confirmed only after the request is accepted. A skill trimmed out by the prompt budget is not counted as injected. Native telemetry is keyed by tenant/chat request so simultaneous chats cannot overwrite each other's load status; the internal key is not returned by the skills API.

## Signals

The offline scanner looks for credential-read plus outbound-network patterns, reverse shells, destructive disk commands, dynamic execution, encoded/obfuscated payloads, persistence mechanisms, prompt-injection instructions, network access, shell execution, and secret access. Python files also receive AST inspection for selected dangerous calls.

## Verdicts

- `LOW_RISK`: no known signal matched. Eligible for loading, not a guarantee of safety. Prompt-budget trimming can still defer an otherwise LOW_RISK skill for a given turn.
- `REVIEW`: suspicious capability or accumulated medium-risk signals. Quarantined pending owner review.
- `BLOCKED`: critical signal, high accumulated score, unsafe archive path/symlink, scanner failure, or hard size limit. Quarantined.

There is deliberately no automatic trust based on GitHub stars, author reputation, marketplace badges, file names, or skill frontmatter claims.

## Limitations

This is static heuristic analysis, not an antivirus product. It cannot guarantee detection of novel malware, encrypted/staged payloads, malicious native binaries, runtime-only behavior, or prompt injection expressed in ways not covered by signatures. It does not replace OS malware protection, dependency/SBOM scanning, signed provenance, container isolation, least-privilege execution, or network egress controls. Do not run an untrusted skill to "see what it does". Keep tool execution sandboxed and secrets outside agent-visible environments.

A future stronger tier should add an isolated dynamic-analysis runner, dependency advisory database, YARA backend, signed approval manifest, and per-skill capability permissions. These are not claimed to be implemented by this release.
