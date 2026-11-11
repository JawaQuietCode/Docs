# Build verification record

Verified on 2026-11-09 in the supplied project workspace.

## Automated results

| Check | Result |
|---|---:|
| Python unittest suite | 149 passed, 0 failed |
| Node test suite | 66 passed, 1 skipped, 0 failed (67 total) |
| RTK setup shell smoke tests | passed (verified-binary path, name-collision/install fallback, opt-out) |
| Python compile check | passed |
| Shell syntax (`setup.sh`, `start.sh`, RTK helper and setup tests) | passed |
| JavaScript syntax checks (transport modules) | passed |
| YAML and package JSON parse | passed |
| `git diff --check` | passed |

The single skipped Node test requires the Baileys package, which is installed by
`setup.sh` but was not installed in the build workspace. It is not counted as a
pass.

The Python tests include deterministic agent-loop end-to-end flows for the
knowledge workbench and learning tools, CLI round trips, tenant isolation,
claim provenance/conflict/revision behavior, the bounded Welch calculation,
RTK rewrite/fallback behavior, and Kwee security-policy revalidation.

## External runtime checks not claimed

- The RTK setup test uses controlled local installer and binary doubles. The
  real upstream binary is verified and smoke-tested by `scripts/setup_rtk.sh`
  on the target host; this build did not install or execute a live RTK release.
- A real WhatsApp QR login, account session, and message delivery were not
  tested because no operator credentials/session were supplied.
- No LLM provider credentials were configured in this workspace. Agent-loop
  tests use deterministic fake-model tool calls; live model/provider behavior
  and billing were not tested.
- Agent Reach's CLI/runtime, authenticated X/Twitter backend and `yt-dlp` were
  not available here. The corresponding social/video connectors must be
  checked in the deployment environment; no X/Twitter or YouTube result is
  claimed by this build record.
- No custom Rust FFI module or Assembly code was added. The workspace had no
  `rustc`/`cargo` toolchain and no profiled hot path demonstrating a need for
  native code. RTK itself is the upstream compiled Rust CLI. Adding custom
  low-level code without a benchmark and target compiler would not meet the
  requested verification standard.

## Package hygiene

The ZIP excludes `.env`, local databases, `data/` runtime state, caches,
`node_modules`, compiled Python files and the Git working directory. First
startup recreates the runtime directories and `start.sh` respects
`KWEE_RTK=0` before attempting optional RTK installation/use.
