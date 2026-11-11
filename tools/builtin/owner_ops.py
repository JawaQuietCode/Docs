import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from security.audit import audit
from security.tenant_context import current_audit_path, current_tenant, get_current
from tools.registry import register

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "data" / "backups"
# Backup = isi /data + .env + plugins/ (supaya restore tidak menghasilkan "plugin not found").
_BACKUP_DIRS = ("data", "plugins")
_BACKUP_FILES = (".env",)
# Skip: isi backup sendiri, artefak cache, workspace. Sesi/media/inbox IKUT (diminta owner).
_EXCLUDED_DIRS = {"backups", "__pycache__", "node_modules", ".git", "workspace", "group-summary"}
_SAFE_RESTORE_ROOTS = {"data", "plugins"}

SCHEMA = {
    "description": "Operasi owner: health check, ringkasan audit, backup/restore /data + plugins + .env, serta pencarian FAQ dari docs/faq.md. Restore menimpa berkas dan perlu persetujuan owner.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["health", "activity", "backup", "restore", "faq"]},
            "name": {"type": "string", "description": "Nama backup .zip untuk restore"},
            "query": {"type": "string", "description": "Pertanyaan untuk pencarian FAQ"},
        },
        "required": ["action"],
    },
}


def _relative(path):
    try:
        return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:
        return None


def _skipped(rel):
    parts = PurePosixPath(rel).parts
    return any(part in _EXCLUDED_DIRS for part in parts)


def _backup_files():
    files = set()
    for item in _BACKUP_DIRS:
        p = ROOT / item
        if not p.is_dir() or p.is_symlink():
            continue
        for child in p.rglob("*"):
            if not child.is_file() or child.is_symlink():
                continue
            rel = _relative(child)
            if rel and not _skipped(rel):
                files.add(child)
    for item in _BACKUP_FILES:
        p = ROOT / item
        if p.is_file() and not p.is_symlink():
            files.add(p)
    return sorted(files)


def _make_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    tenant = current_tenant() or "primary"
    stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    name = f"kwee-{tenant}-{stamp}.zip"
    target = BACKUP_DIR / name
    records = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for src in _backup_files():
            rel = _relative(src)
            if not rel:
                continue
            data = src.read_bytes()
            zf.writestr(rel, data)
            records.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        zf.writestr("_kwee_manifest.json", json.dumps(
            {"version": 2, "tenant": tenant, "kind": "data+plugins+env", "created_at": int(time.time()), "files": records},
            ensure_ascii=False,
        ))
    ok, detail = _verify(target)
    audit("backup.create", name=name, files=len(records), bytes=target.stat().st_size, verified=ok)
    return target, len(records), ok, detail


def _verify(archive):
    """Baca ulang zip: CRC + hash tiap berkas vs manifest."""
    try:
        with zipfile.ZipFile(archive) as zf:
            bad = zf.testzip()
            if bad:
                return False, f"CRC gagal: {bad}"
            manifest = json.loads(zf.read("_kwee_manifest.json"))
            names = set(zf.namelist())
            for item in manifest.get("files", []):
                rel, expected = item.get("path"), item.get("sha256")
                if not rel or rel not in names:
                    return False, f"hilang dari arsip: {rel}"
                if hashlib.sha256(zf.read(rel)).hexdigest() != expected:
                    return False, f"hash mismatch: {rel}"
            return True, f"{len(manifest.get('files', []))} berkas terverifikasi"
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def _plugin_report():
    files = sorted(p.name for p in (ROOT / "plugins").glob("*.js") if not p.name.startswith("_"))
    missing = [n for n in files if not (ROOT / "plugins" / n).is_file()]
    return {"count": len(files), "ok": bool(files) and not missing}


def _inside_safe_path(rel):
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        return False
    if _skipped(rel):
        return False
    return any(p.as_posix() == r or p.as_posix().startswith(r + "/") for r in _SAFE_RESTORE_ROOTS) or \
        p.as_posix() == ".env"


def _restore(name, config):
    ctx = (config or {}).get("context") or {}
    if not ctx.get("owner_confirmed"):
        return {"error": "restore ditahan. Jelaskan berkas yang akan ditimpa dan minta persetujuan eksplisit owner."}
    if not re.fullmatch(r"kwee-[A-Za-z0-9_-]+-\d{8}T\d{6}Z\.zip", str(name or "")):
        return {"error": "nama backup tidak valid"}
    archive = (BACKUP_DIR / name).resolve()
    try:
        archive.relative_to(BACKUP_DIR.resolve())
    except ValueError:
        return {"error": "path backup di luar folder backup"}
    if not archive.is_file():
        return {"error": "backup tidak ditemukan"}
    tenant = current_tenant() or "primary"
    ok, detail = _verify(archive)
    if not ok:
        return {"error": f"backup rusak: {detail}"}
    try:
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if len(names) > 20000 or sum(i.file_size for i in zf.infolist()) > 2_000_000_000:
                return {"error": "backup melewati batas ukuran aman"}
            manifest = json.loads(zf.read("_kwee_manifest.json"))
            if manifest.get("tenant") != tenant:
                return {"error": "backup berasal dari tenant berbeda; restore ditolak"}
            pending = []
            for item in manifest.get("files", []):
                rel = item.get("path")
                if not rel or rel not in names or not _inside_safe_path(rel):
                    continue
                data = zf.read(rel)
                if hashlib.sha256(data).hexdigest() != item.get("sha256"):
                    return {"error": f"hash berkas backup tidak cocok: {rel}"}
                pending.append((rel, data))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        return {"error": f"backup tidak dapat dibaca: {type(exc).__name__}"}
    if not pending:
        return {"error": "tidak ada berkas aman untuk dipulihkan"}
    rollback, _, _, _ = _make_backup()
    restored = 0
    for rel, data in pending:
        dest = (ROOT / rel).resolve()
        try:
            dest.relative_to(ROOT.resolve())
        except ValueError:
            return {"error": "jalur pemulihan keluar dari project; proses dihentikan"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_name(dest.name + ".restore.tmp")
        temp.write_bytes(data)
        os.replace(temp, dest)
        restored += 1
    audit("backup.restore", name=name, files=restored, rollback=rollback.name)
    return {"ok": True, "restored_files": restored, "rollback_backup": rollback.name, "plugins": _plugin_report(), "restart_required": True}


def _health(config):
    checks = {}
    for module in ("fastapi", "uvicorn", "yaml", "httpx"):
        checks[f"python:{module}"] = importlib.util.find_spec(module) is not None
    port = int(os.environ.get("AGENT_PORT", ((config or {}).get("agent") or {}).get("port", 8787)))
    host = os.environ.get("AGENT_HOST", "127.0.0.1")
    try:
        req = urllib.request.Request(f"http://{host}:{port}/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
        checks["agent_http"] = {"ok": bool(body.get("ok")), "model": body.get("model", ""), "tool_count": len(body.get("tools") or []), "mechanisms": body.get("mechanisms_available", [])}
    except Exception as exc:
        checks["agent_http"] = {"ok": False, "error": type(exc).__name__}
    try:
        node_check = subprocess.run(
            ["node", "--input-type=module", "-e", "await import('@itsliaaa/baileys'); await import('@hapi/boom'); await import('pino')"],
            cwd=ROOT, capture_output=True, timeout=8, check=False,
        )
        checks["node_dependencies"] = {"ok": node_check.returncode == 0}
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks["node_dependencies"] = {"ok": False, "error": type(exc).__name__}
    security = (config or {}).get("security") or {}
    action_host = str(security.get("actionHost") or "127.0.0.1")
    action_port = int(security.get("actionPort") or 8788)
    try:
        with socket.create_connection((action_host, action_port), timeout=1):
            checks["transport_action_port"] = {"ok": True}
    except OSError:
        checks["transport_action_port"] = {"ok": False}
    try:
        usage = shutil.disk_usage(ROOT)
        checks["disk"] = {"free_gib": round(usage.free / (1024 ** 3), 2), "total_gib": round(usage.total / (1024 ** 3), 2)}
    except OSError:
        checks["disk"] = {"ok": False}
    checks["scheduler_source"] = (ROOT / "scheduler" / "cron.py").is_file()
    checks["cron_data"] = Path((get_current() or {}).get("cron") or ROOT / "data" / "cron" / "jobs.json").exists()
    checks["plugins"] = _plugin_report()
    healthy = (
        all(value for key, value in checks.items() if key.startswith("python:"))
        and bool(checks["agent_http"].get("ok"))
        and bool(checks["node_dependencies"].get("ok"))
        and bool(checks["transport_action_port"].get("ok"))
        and bool(checks["scheduler_source"])
    )
    return {"ok": healthy, "checks": checks}


def _activity():
    path = Path(current_audit_path() or ROOT / "data" / "audit.log")
    if not path.is_file():
        return {"ok": True, "count_24h": 0, "events": {}, "recent": []}
    cutoff = int(time.time()) - 86400
    events = {}
    recent = []
    count = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
        for line in lines:
            try:
                row = json.loads(line)
                if int(row.get("ts", 0)) < cutoff:
                    continue
                event = str(row.get("event", "unknown"))[:80]
                events[event] = events.get(event, 0) + 1
                count += 1
                recent.append({"time": datetime.fromtimestamp(int(row["ts"])).isoformat(timespec="minutes"), "event": event})
            except (ValueError, TypeError, KeyError):
                continue
    except OSError as exc:
        return {"error": f"audit log tidak bisa dibaca: {type(exc).__name__}"}
    return {"ok": True, "count_24h": count, "events": events, "recent": recent[-10:]}


def _faq(query):
    file = ROOT / "docs" / "faq.md"
    if not file.is_file():
        return {"error": "docs/faq.md belum tersedia. Isi dokumen itu dengan FAQ resmi sebelum mode FAQ dipakai."}
    text = file.read_text(encoding="utf-8", errors="replace")
    terms = {x.lower() for x in re.findall(r"[\w-]{2,}", str(query or ""))}
    sections = re.split(r"(?m)(?=^#{1,3} )", text)
    scored = []
    for section in sections:
        words = {x.lower() for x in re.findall(r"[\w-]{2,}", section)}
        score = len(terms & words)
        if score:
            scored.append((score, section.strip()))
    scored.sort(key=lambda x: -x[0])
    hits = [body[:2500] for _, body in scored[:5]]
    if not hits:
        return {"ok": True, "count": 0, "answer_source": "docs/faq.md", "message": "Tidak ada jawaban cocok; jangan mengarang."}
    return {"ok": True, "count": len(hits), "answer_source": "docs/faq.md", "matches": hits}


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action", "")).strip().lower()
    if action == "health":
        return _health(config)
    if action == "activity":
        return _activity()
    if action == "faq":
        query = str(args.get("query", "")).strip()
        if not query:
            return {"error": "query wajib untuk FAQ"}
        return _faq(query)
    if action == "backup":
        try:
            target, count, ok, detail = _make_backup()
            return {
                "ok": ok,
                "name": target.name,
                "path": str(target),
                "files": count,
                "bytes": target.stat().st_size,
                "verify": detail,
                "plugins": _plugin_report(),
                "includes": [".env", "data/ (penuh: memory, sessions, media, inbox, tasks, cron)", "plugins/"],
                "excludes": ["data/backups", "workspace", "cache", "__pycache__"],
            }
        except Exception as exc:
            return {"error": f"backup gagal: {type(exc).__name__}: {str(exc)[:180]}"}
    if action == "restore":
        return _restore(str(args.get("name", "")).strip(), config)
    return {"error": "action harus health, activity, backup, restore, atau faq"}


register("owner_ops", SCHEMA, handler, toolset="core", dangerous=False)
