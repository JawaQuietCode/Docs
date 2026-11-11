#!/usr/bin/env python3
"""Backup /data folder + .env only, with integrity verification."""
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP_DIR = ROOT / "data" / "backups"
DATA_DIR = ROOT / "data"
ENV_FILE = ROOT / ".env"

BACKUP_DIR.mkdir(parents=True, exist_ok=True)

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def collect_files():
    files = []
    # .env at root
    if ENV_FILE.is_file():
        files.append((".env", ENV_FILE))
    # Everything under /data
    if DATA_DIR.is_dir():
        for child in DATA_DIR.rglob("*"):
            if child.is_file() and not child.is_symlink():
                rel = child.relative_to(ROOT).as_posix()
                files.append((rel, child))
    return files

def make_backup():
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    name = f"kwee-data-{stamp}.zip"
    target = BACKUP_DIR / name
    records = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, src in collect_files():
            data = src.read_bytes()
            zf.writestr(rel, data)
            records.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        zf.writestr("_kwee_manifest.json", json.dumps({
            "version": 1,
            "type": "data-only",
            "created_at": int(datetime.utcnow().timestamp()),
            "files": records
        }, ensure_ascii=False))
    return target, records

def verify_backup(zip_path: Path):
    with zipfile.ZipFile(zip_path) as zf:
        # Check manifest
        try:
            manifest = json.loads(zf.read("_kwee_manifest.json"))
        except KeyError:
            return False, "manifest missing"
        except json.JSONDecodeError:
            return False, "manifest corrupt"
        # Verify each file
        for item in manifest.get("files", []):
            rel = item["path"]
            expected = item["sha256"]
            if rel not in zf.namelist():
                return False, f"missing in archive: {rel}"
            data = zf.read(rel)
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                return False, f"hash mismatch: {rel}"
    return True, "ok"

def check_plugins_exist():
    """Verify plugins/ folder has the expected plugin files."""
    plugins_dir = ROOT / "plugins"
    if not plugins_dir.is_dir():
        return False, "plugins/ folder missing"
    plugin_files = list(plugins_dir.glob("*.js"))
    return True, f"{len(plugin_files)} plugin files found"

if __name__ == "__main__":
    print("Creating data-only backup (.env + /data)...")
    target, records = make_backup()
    print(f"Created: {target.name} ({len(records)} files, {target.stat().st_size} bytes)")
    
    print("Verifying integrity...")
    ok, msg = verify_backup(target)
    if not ok:
        print(f"VERIFICATION FAILED: {msg}")
        sys.exit(1)
    print(f"Integrity: {msg}")
    
    print("Checking plugins folder...")
    ok, msg = check_plugins_exist()
    print(f"Plugins: {msg}")
    
    print("Done.")