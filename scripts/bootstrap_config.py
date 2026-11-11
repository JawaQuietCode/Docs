#!/usr/bin/env python3
"""First-run configuration for Kwee. Uses only the Python standard library."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Mapping

REQUIRED_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "WA_OWNER_NUMBERS")
_PLACEHOLDERS = {
    "LLM_BASE_URL": {"https://gateway.contoh.com/v1", "https://example.com/v1"},
    "LLM_API_KEY": {"isi-kunci-di-sini", "your-api-key", "changeme", "sk-your-key"},
    "WA_OWNER_NUMBERS": {"628111111111", "6281234567890", "owner-number"},
}


def parse_env_text(text: str) -> dict[str, str]:
    """Parse dotenv-style KEY=VALUE lines without shell evaluation."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def missing_required(values: Mapping[str, str]) -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_KEYS:
        value = str(values.get(key, "") or "").strip()
        if not value or value.lower() in _PLACEHOLDERS.get(key, set()):
            missing.append(key)
            continue
        if key == "LLM_BASE_URL" and not re.match(r"^https?://[^\s]+$", value, re.I):
            missing.append(key)
        elif key == "WA_OWNER_NUMBERS":
            numbers = [part.strip() for part in value.split(",") if part.strip()]
            if not numbers or any(not re.fullmatch(r"\+?\d{8,16}", number) for number in numbers):
                missing.append(key)
    return missing


def update_env_file(path: Path | str, updates: Mapping[str, str]) -> None:
    """Atomically update keys; preserve unrelated lines/comments and lock secrets."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    pending = {str(k): str(v) for k, v in updates.items() if v is not None}
    if any("\n" in value or "\r" in value for value in pending.values()):
        raise ValueError("Nilai .env tidak boleh mengandung baris baru")
    output: list[str] = []
    written: set[str] = set()
    for line in original.splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", stripped)
        if match and match.group(1) in pending:
            key = match.group(1)
            if key not in written:
                output.append(f"{key}={pending[key]}")
                written.add(key)
            continue
        output.append(line)
    for key, value in pending.items():
        if key not in written:
            output.append(f"{key}={value}")
    content = "\n".join(output).rstrip() + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def persist_action_token(path: Path | str, token: str) -> None:
    """Persist one generated transport token without accumulating duplicate dotenv keys."""
    value = str(token or "").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("ACTION_TOKEN tidak valid")
    update_env_file(path, {"ACTION_TOKEN": value})


def shell_exports(values: Mapping[str, str]) -> str:
    """Emit safe shell assignments, suitable only for eval of this helper's output."""
    return "".join(f"export {key}={shlex.quote(str(value))}\n" for key, value in values.items())


def _ask(label: str, current: str = "", *, secret: bool = False) -> str:
    shown_default = f" [{current}]" if current and not secret else ""
    prompt = f"  {label}{shown_default}: "
    if secret:
        answer = getpass.getpass(prompt, stream=sys.stderr)
    else:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        answer = sys.stdin.readline()
    return answer.strip() or current


def configure(path: Path, env: Mapping[str, str] | None = None, *, interactive: bool = True) -> dict[str, str]:
    file_values = parse_env_text(path.read_text(encoding="utf-8")) if path.exists() else {}
    # Process environment wins; blank process variables do not erase an existing file value.
    values = dict(file_values)
    for key, value in (env or os.environ).items():
        if value is not None and str(value).strip():
            values[key] = str(value)
    missing = missing_required(values)
    updates: dict[str, str] = {}
    if missing and not interactive:
        raise RuntimeError("Konfigurasi wajib belum lengkap: " + ", ".join(missing))
    if missing:
        print("\nKWEE · FIRST-RUN SETUP\nKonfigurasi disimpan lokal dengan permission 0600. API key tidak ditampilkan.", file=sys.stderr)
        prompts = {
            "LLM_BASE_URL": ("Base URL API OpenAI-compatible", "https://api.openai.com/v1", False),
            "LLM_API_KEY": ("API key provider", "", True),
            "LLM_MODEL": ("Model ID (contoh: provider/model)", "", False),
            "WA_OWNER_NUMBERS": ("Nomor owner WhatsApp, format internasional tanpa spasi; pisahkan koma", "", False),
        }
        for key in missing:
            label, default, secret = prompts[key]
            value = _ask(label, values.get(key) or default, secret=secret)
            while not value:
                print("  Nilai ini wajib diisi.", file=sys.stderr)
                value = _ask(label, default, secret=secret)
            values[key] = value
            updates[key] = value
        invalid = missing_required(values)
        if invalid:
            raise RuntimeError("Nilai konfigurasi tidak valid: " + ", ".join(invalid))
        # Optional integrations never block the core chat.
        if not values.get("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip():
            token = getpass.getpass("  GitHub fine-grained token baru (opsional, Enter untuk lewati): ").strip()
            if token:
                values["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
                updates["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
        if not values.get("OPENAI_API_KEY", "").strip():
            print("  Mem0 semantic memory bersifat opsional; FTS5 tetap aktif tanpa provider key.", file=sys.stderr)
    if updates:
        update_env_file(path, updates)
    elif path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    # Only export configuration keys that the runtime consumes; never print all of .env.
    export_keys = (*REQUIRED_KEYS, "GITHUB_PERSONAL_ACCESS_TOKEN", "OPENAI_API_KEY")
    return {key: values[key] for key in export_keys if values.get(key)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--exports", action="store_true", help="print shell-safe exports only")
    parser.add_argument("--persist-action-token", action="store_true", help="read a generated token from stdin and persist it atomically")
    args = parser.parse_args(argv)
    try:
        if args.persist_action_token:
            persist_action_token(args.env, sys.stdin.read())
            return 0
        values = configure(args.env, interactive=sys.stdin.isatty())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[bootstrap] {exc}", file=sys.stderr)
        return 2
    if args.exports:
        sys.stdout.write(shell_exports(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
