"""Local, deduplicated media asset store backed by the Kwee SQLite database."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _root() -> Path:
    return Path(os.environ.get("KWEE_ROOT") or _PROJECT_ROOT).resolve()


def _tenant_paths() -> dict:
    try:
        from security import tenant_context
        return tenant_context.get_current() or {}
    except Exception:
        return {}


def _db_path() -> Path:
    tenant_db = _tenant_paths().get("db")
    return Path(tenant_db) if tenant_db else _root() / "data" / "memory" / "kwee.db"


def _assets_dir() -> Path:
    tenant_media = _tenant_paths().get("media")
    base = Path(tenant_media) if tenant_media else _root() / "data" / "media"
    return base / "assets"


@contextmanager
def _connect():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("""CREATE TABLE IF NOT EXISTS media_assets (
            id TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            mime TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            bytes INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'local',
            path TEXT NOT NULL,
            created_at REAL NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_media_kind_created ON media_assets(kind, created_at DESC)")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _kind_for(mime: str, filename: str) -> str:
    m = str(mime or "").lower()
    ext = Path(filename).suffix.lower()
    if m.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}:
        return "image"
    if m.startswith("video/") or ext in {".mp4", ".mov", ".webm", ".mkv", ".3gp"}:
        return "video"
    if m.startswith("audio/") or ext in {".mp3", ".ogg", ".opus", ".wav", ".m4a", ".aac"}:
        return "audio"
    return "document"


def _safe_filename(name: str) -> str:
    base = Path(str(name or "media")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (base or "media")[:140]


def _as_dict(row):
    return dict(row) if row else None


def save_file(path: str | Path, kind: str = "", mime: str = "", note: str = "", source: str = "local") -> dict:
    src = Path(path).expanduser()
    if not src.is_file():
        return {"error": "berkas media tidak ditemukan", "path": str(src)}
    size = src.stat().st_size
    if size <= 0:
        return {"error": "berkas media kosong", "path": str(src)}
    if size > 100 * 1024 * 1024:
        return {"error": f"berkas media terlalu besar ({size} bytes)"}
    filename = _safe_filename(src.name)
    content_type = str(mime or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    asset_kind = str(kind or _kind_for(content_type, filename)).lower()
    digest = hashlib.sha256()
    with src.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    sha = digest.hexdigest()

    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM media_assets WHERE sha256=?", (sha,)).fetchone()
        if existing and Path(existing["path"]).is_file():
            return _as_dict(existing)
        # Repair a stale metadata row if the asset file was manually removed.
        asset_id = existing["id"] if existing else "m_" + uuid.uuid4().hex[:16]
        ext = Path(filename).suffix.lower()
        if not ext:
            ext = mimetypes.guess_extension(content_type) or ".bin"
        target_dir = _assets_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{asset_id}{ext}"
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            shutil.copyfile(src, temporary)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except Exception:
                pass
        values = (asset_id, sha, asset_kind, content_type, filename, size,
                  str(note or "")[:500], str(source or "local")[:80], str(target), time.time())
        if existing:
            db.execute("""UPDATE media_assets SET kind=?,mime=?,filename=?,bytes=?,note=?,source=?,path=?,created_at=?
                WHERE id=?""", (asset_kind, content_type, filename, size, str(note or "")[:500],
                                  str(source or "local")[:80], str(target), time.time(), asset_id))
        else:
            db.execute("""INSERT INTO media_assets
                (id,sha256,kind,mime,filename,bytes,note,source,path,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", values)
        row = db.execute("SELECT * FROM media_assets WHERE id=?", (asset_id,)).fetchone()
    return _as_dict(row)


def get_asset(asset_id: str) -> dict | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM media_assets WHERE id=?", (str(asset_id or ""),)).fetchone()
    return _as_dict(row)


def resolve_path(ref: str) -> str:
    value = str(ref or "").strip()
    if not value:
        return ""
    direct = Path(value).expanduser()
    if direct.is_file():
        return str(direct.resolve())
    with _connect() as db:
        row = db.execute("SELECT path FROM media_assets WHERE id=? OR filename=? ORDER BY created_at DESC LIMIT 1",
                         (value, value)).fetchone()
    if row and Path(row["path"]).is_file():
        return str(Path(row["path"]).resolve())
    return ""


def list_assets(kind: str = "", limit: int = 30, query: str = "") -> list[dict]:
    clauses, params = [], []
    if kind:
        clauses.append("kind=?")
        params.append(str(kind).lower())
    if query:
        clauses.append("(id LIKE ? OR filename LIKE ? OR note LIKE ?)")
        pattern = f"%{str(query)[:120]}%"
        params.extend([pattern, pattern, pattern])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, min(int(limit or 30), 100)))
    with _connect() as db:
        rows = db.execute(f"SELECT * FROM media_assets{where} ORDER BY created_at DESC LIMIT ?", params).fetchall()
    return [_as_dict(row) for row in rows]
