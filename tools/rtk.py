"""Optional RTK command rewriting for Kwee's native shell runner.

The binary is verified with the upstream ``rtk gain`` identity check before it
is trusted as Rust Token Killer. The subprocess receives only Kwee's already-
sanitized child environment. Missing/wrong binaries, unsupported commands,
timeouts and RTK errors preserve the original command. Kwee's own command
policy is checked before and after rewriting; RTK is never a security boundary.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from typing import Any

_RTK_ENV_ALLOWLIST = {
    "PATH", "HOME", "XDG_CONFIG_HOME", "TMPDIR", "TMP", "TEMP", "LANG",
    "LC_ALL", "LC_CTYPE", "TERM", "SYSTEMROOT", "WINDIR",
}


def _result(command: str, *, applied: bool = False, reason: str = "unchanged",
            detail: str = "") -> dict[str, Any]:
    return {
        "command": command,
        "engine": "rtk",
        "available": reason not in {"disabled", "empty_command", "not_installed", "not_token_killer"},
        "applied": applied,
        "blocked": False,
        "reason": reason,
        "detail": str(detail or "")[:300],
    }


@lru_cache(maxsize=32)
def _probe_token_killer(binary: str, mtime_ns: int, size: int,
                        env_items: tuple[tuple[str, str], ...]) -> tuple[bool, str]:
    """Validate binary identity once per binary fingerprint and safe env."""
    del mtime_ns, size  # part of cache key; values need not affect the subprocess
    try:
        completed = subprocess.run(
            [binary, "gain"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=1.5, env=dict(env_items), check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        return False, type(exc).__name__
    if completed.returncode != 0:
        return False, (str(completed.stderr or "").strip() or f"rtk gain exit={completed.returncode}")
    return True, ""


def rewrite_command(command: str, env: dict[str, str] | None, *,
                    enabled: bool = True, timeout: float = 2.0) -> dict[str, Any]:
    """Return a candidate rewrite for caller-side security revalidation.

    Upstream currently documents exit 0 for a rewrite and exit 1 when no
    equivalent RTK command exists. Other non-zero statuses are treated as
    RTK errors (not as undocumented security-deny signals) and fail open to
    Kwee's already-validated original command.
    """
    original = str(command or "").strip()
    if not original:
        return _result(original, reason="empty_command")
    if not enabled:
        return _result(original, reason="disabled")

    source_env = env if isinstance(env, dict) else os.environ
    search_path = str(source_env.get("PATH") or os.defpath)
    rtk = shutil.which("rtk", path=search_path)
    if not rtk:
        return _result(original, reason="not_installed")

    # The optimizer receives a least-privilege environment. It only needs the
    # executable/config/temp/locale context, never provider or WhatsApp secrets.
    child_env = {
        key: str(source_env[key])
        for key in _RTK_ENV_ALLOWLIST
        if source_env.get(key) not in (None, "")
    }
    child_env["PATH"] = search_path
    child_env["RTK_TELEMETRY_DISABLED"] = "1"
    try:
        stat = os.stat(rtk)
        identity_key = (rtk, stat.st_mtime_ns, stat.st_size, tuple(sorted(child_env.items())))
        verified, probe_detail = _probe_token_killer(*identity_key)
        if not verified:
            return _result(original, reason="not_token_killer", detail=probe_detail)
        completed = subprocess.run(
            [rtk, "rewrite", original],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, min(float(timeout or 2.0), 5.0)),
            env=child_env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _result(original, reason="timeout")
    except (OSError, ValueError, TypeError) as exc:
        return _result(original, reason="runtime_error", detail=type(exc).__name__)

    stderr = str(completed.stderr or "").strip()
    if completed.returncode == 1:
        return _result(original, reason="unsupported_or_no_rewrite", detail=stderr)
    if completed.returncode != 0:
        return _result(original, reason="rtk_error", detail=f"exit={completed.returncode}; {stderr}")

    rewritten = str(completed.stdout or "").strip()
    if not rewritten:
        return _result(original, reason="empty_rewrite_output", detail=stderr)
    if rewritten == original:
        return _result(original, reason="unchanged", detail=stderr)
    return _result(rewritten, applied=True, reason="rewritten", detail=stderr)


__all__ = ["rewrite_command"]
