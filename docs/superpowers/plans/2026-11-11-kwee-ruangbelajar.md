# Kwee RuangBelajar Release Implementation Plan

> **For agentic workers:** Tasks use checkbox syntax. Native implementation is retained; do not vendor or launch an unrelated proxy.

**Goal:** Package the existing native peer/team learning integration as a clearly versioned Kwee release with auditable startup, documentation, and tests.

**Architecture:** Keep Kwee's native `subagent` tool, existing trusted LLM configuration, tenant-local journal library, and startup self-test. Update release metadata and provide a descriptive full ZIP without adding provider credentials or runtime dependencies.

**Tech Stack:** Python, Node.js, Bash, SQLite, ZIP.

**Spec:** User request in this session; source archive `/home/ubuntu/upload/kwee-agentic.zip`.

## Global Constraints

- No additional environment variables or provider credentials.
- Peer notes remain explicitly unverified until reviewed.
- Do not reproduce proprietary model internals or reverse-engineer secret algorithms.
- Do not install Free Claude Code as a startup service; use native peer roles.

## Review Focus

- Redacted tool configuration must not lose trusted provider credentials.
- Peer learning must reject secret-like content and preserve tenant scope.
- Startup must fail visibly if the required peer tool is unavailable.
- Release versions must match Python and Node package metadata.
- Archive must be complete and pass integrity checks.

---

### Task 1: Release metadata and log

**Files:** Modify `agent/version.py`, `package.json`, `README.md`; create release note.

- [ ] Set synchronized patch version `0.3.1` and explain native peer/startup behavior and upstream boundaries.
- [ ] Verify version strings agree.

### Task 2: Package and verification

**Files:** Include the full source tree in a non-generic ZIP.

- [ ] Run available test suites and syntax checks.
- [ ] Create ZIP and test archive integrity; report any unavailable checks honestly.
