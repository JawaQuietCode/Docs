#!/usr/bin/env python3
"""Backup source + .env + plugins. /data TIDAK ikut.
Exclude: data/, workspace/, node_modules/, .cache/, .tmp/, .git/, .npm/, .pyuser/, .local/, __pycache__/, *.mjs, *.zip, *.gz
"""
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
BACKUP_DIR = ROOT / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

EXCLUDED_DIRS = {
    "backups", "__pycache__", "node_modules", ".git", "workspace", "data",
    ".cache", ".tmp", ".npm", ".pyuser", ".local", "group-summary",
}
EXCLUDED_EXTS = {".mjs", ".zip", ".gz", ".log"}


def collect_files():
    out = []
    for child in ROOT.rglob("*"):
        if not child.is_file() or child.is_symlink():
            continue
        try:
            rel = child.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if set(PurePosixPath(rel).parts) & EXCLUDED_DIRS:
            continue
        if child.suffix in EXCLUDED_EXTS:
            continue
        out.append((rel, child))
    return sorted(out, key=lambda x: x[0])


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_backup():
    now = datetime.now(timezone.utc)
    target = BACKUP_DIR / f"kwee-{now:%Y-%m-%d}.zip"
    if target.exists():  # backup kedua di hari sama
        target = BACKUP_DIR / f"kwee-{now:%Y-%m-%d}-{now:%H%M%S}.zip"
    records = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, src in collect_files():
            data = src.read_bytes()
            zf.writestr(rel, data)
            records.append({"path": rel, "sha256": sha256_bytes(data), "size": len(data)})
        zf.writestr("_kwee_manifest.json", json.dumps({
            "version": 4,
            "kind": "source+env+plugins (no data/)",
            "created_at": int(datetime.now(timezone.utc).timestamp()),
            "files": records,
        }, ensure_ascii=False))
    return target, records


def verify_backup(zip_path: Path):
    try:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            if bad:
                return False, f"CRC gagal: {bad}"
            manifest = json.loads(zf.read("_kwee_manifest.json"))
            names = set(zf.namelist())
            for item in manifest.get("files", []):
                rel, expected = item.get("path"), item.get("sha256")
                if rel not in names:
                    return False, f"hilang dari arsip: {rel}"
                if sha256_bytes(zf.read(rel)) != expected:
                    return False, f"hash mismatch: {rel}"
            return True, f"{len(manifest.get('files', []))} berkas terverifikasi"
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def check_plugins():
    d = ROOT / "plugins"
    if not d.is_dir():
        return False, "plugins/ folder missing"
    js = sorted(p.name for p in d.glob("*.js") if not p.name.startswith("_"))
    return True, f"{len(js)} plugin: {', '.join(js)}"


if __name__ == "__main__":
    print("Membuat backup (source + .env + plugins, tanpa data/)...")
    target, records = make_backup()
    print(f"Created: {target.name} ({len(records)} files, {target.stat().st_size:,} bytes)")
    ok, msg = verify_backup(target)
    if not ok:
        print(f"VERIFICATION FAILED: {msg}")
        sys.exit(1)
    print(f"Integrity: {msg}")
    ok_p, msg_p = check_plugins()
    print(f"Plugins: {msg_p}")
    if not ok_p:
        sys.exit(1)
    print("Done.")
