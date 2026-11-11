# Kwee v0.3.1 — RuangBelajar

## Changes
- Version metadata synchronized across Python and Node package descriptors.
- Release notes clarify that peer/team support is native Kwee functionality and startup already checks that `subagent` is registered.
- No extra environment variables, Claude credentials, or separate proxy service are required.
- Peer roles (`peer`, `researcher`, `architect`, `critic`, `mentor`, `builder`) reuse Kwee's configured provider/model. Tenant-shared learning notes retain `unverified` provenance; secret-like content is rejected.
- AI Engineering from Scratch is a design/learning reference. Free Claude Code is not installed or started: it is a separate protocol proxy/harness and not a peer-agent community. No proprietary algorithm reverse engineering is performed.

## Verification
- Tests: see verification results accompanying the release package; do not treat historical v0.3.0 baseline notes as tests run for this release.
- Startup self-test requires `subagent` to be present in the native tool registry.
