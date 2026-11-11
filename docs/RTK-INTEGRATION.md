# Native RTK integration

Kwee integrates the upstream **Rust Token Killer** CLI inside
`tools/builtin/shell.py`, not through a global shell hook. The `terminal` tool
delegates to the same shell handler, so both paths share the rewrite, policy
checks and execution result.

## Runtime contract

1. Kwee evaluates the original command with its existing policy first.
2. If `security.rtk.enabled` is true (default), Kwee searches `PATH` for `rtk`
   and verifies that it is the Rust Token Killer using the upstream
   `rtk gain` identity command. `--version` alone is not an identity check:
   another unrelated project also publishes a binary named `rtk`.
3. Kwee calls `rtk rewrite <original-command>` using a least-privilege child
   environment (path, home/config, temporary directory and locale only), not
   provider/WhatsApp secrets, and sets `RTK_TELEMETRY_DISABLED=1` for that
   subprocess.
4. Upstream documents exit status `0` for a rewrite and `1` when there is no
   equivalent rewrite. Other non-zero statuses are treated as RTK errors. They
   are **not** interpreted as an undocumented security-deny signal.
5. A non-empty rewritten command is checked again by Kwee's dangerous-command,
   inline-code, allowlist and `denyPaths` policy before it can execute.
6. Missing/wrong binaries, unsupported commands, timeouts, malformed/empty
   output and RTK errors fall back to the original command, which remains
   subject to Kwee's own policy.

RTK is an optimization, not a sandbox or security boundary. In sandbox access
mode it is skipped unless `rtk` is explicitly allowed in
`security.allowedExecutables`. Set `security.rtk.enabled: false` or
`KWEE_RTK=0` to disable the integration.

## Installation and licensing

`setup.sh` downloads the upstream install script from `rtk-ai/rtk`, selects
`RTK_EDITION=oss`, and sets `RTK_INSTALL_DIR` to the project-local
`~/.local/bin`. The official licensing guide describes this edition as Apache
2.0 only: it retains the hook/rewrite mechanism and command filters, while
leaving out the separate BUSL-1.1 compression engine. The full RTK download
contains both Apache-2.0 CLI/filter code and the BUSL-1.1 engine; `rtk-proxy`
is a separately downloaded BUSL-1.1 daemon. Kwee uses command rewriting and
filters, so it does not need to add the separate engine or proxy. Setup probes
`rtk gain` before using an existing binary; a name collision is not accepted
as RTK. Kwee does not run `rtk init` (including `rtk init -g`) and does not
change another agent's global configuration.

RTK's output reduction is a reduction in selected shell-output bytes, not a
measured reduction of the same percentage in total provider billing or API
requests. Prompt history, system instructions, user input, model output and
unsupported command output still contribute. Check provider-side usage before
claiming an amount of cost saved.

## Verification

`tests/test_rtk_integration.py` uses controlled subprocess doubles to test
identity verification, command rewriting, the documented `0`/`1` statuses,
unknown non-zero statuses, missing binaries, timeouts and subprocess errors.
`tests/test_shell_rtk.py` verifies that shell rewrites are rechecked against
Kwee's own security policy and that sandbox executable allowlisting still
applies. These tests do not install RTK or prove behavior of a live upstream
binary; `setup.sh` performs the real identity and rewrite smoke checks on the
target host.
