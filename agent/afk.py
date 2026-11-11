"""Persistent AFK sessions shared by WhatsApp commands and rich cards."""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class AfkStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS afk_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    ended_at REAL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_afk_active_sender
                    ON afk_sessions(chat_id, sender_id) WHERE ended_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_afk_chat_active
                    ON afk_sessions(chat_id, ended_at, started_at);
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _row(row):
        if not row:
            return None
        item = dict(row)
        now = time.time()
        item["elapsed_seconds"] = max(0, int(now - float(item["started_at"])))
        return item

    def start(self, chat_id: str, sender_id: str, sender_name: str, reason: str):
        chat_id, sender_id = str(chat_id).strip(), str(sender_id).strip()
        if not chat_id or not sender_id:
            raise ValueError("chat_id dan sender_id wajib diisi")
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM afk_sessions WHERE chat_id=? AND sender_id=? AND ended_at IS NULL",
                (chat_id, sender_id),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE afk_sessions SET sender_name=?, reason=? WHERE id=?",
                    (str(sender_name or "").strip(), str(reason or "AFK").strip(), row["id"]),
                )
                session_id = row["id"]
            else:
                cur = db.execute(
                    "INSERT INTO afk_sessions(chat_id,sender_id,sender_name,reason,started_at) VALUES(?,?,?,?,?)",
                    (chat_id, sender_id, str(sender_name or "").strip(), str(reason or "AFK").strip(), now),
                )
                session_id = cur.lastrowid
            saved = db.execute("SELECT * FROM afk_sessions WHERE id=?", (session_id,)).fetchone()
        return self._row(saved)

    def stop(self, chat_id: str, sender_id: str):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM afk_sessions WHERE chat_id=? AND sender_id=? AND ended_at IS NULL",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            if not row:
                return None
            now = time.time()
            db.execute("UPDATE afk_sessions SET ended_at=? WHERE id=?", (now, row["id"]))
            saved = db.execute("SELECT * FROM afk_sessions WHERE id=?", (row["id"],)).fetchone()
        return self._row(saved)

    def list_active(self, chat_id: str):
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM afk_sessions WHERE chat_id=? AND ended_at IS NULL ORDER BY started_at ASC",
                (str(chat_id),),
            ).fetchall()
        return [self._row(row) for row in rows]


def create_read_token(chat_id: str, secret: str, now: int | None = None, ttl: int = 43200) -> str:
    """Short-lived, read-only token scoped to one chat."""
    if not secret:
        return ""
    expires = int(now if now is not None else time.time()) + min(86400, max(1, int(ttl)))
    message = f"{chat_id}:{expires}".encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"{expires}.{signature}"


def verify_read_token(chat_id: str, token: str, secret: str, now: int | None = None) -> bool:
    try:
        expires_raw, signature = str(token).split(".", 1)
        expires = int(expires_raw)
    except (ValueError, TypeError):
        return False
    current = int(now if now is not None else time.time())
    if not secret or expires < current or expires > current + 86400:
        return False
    # Recreate with the exact encoded expiry (rather than trusting client data).
    message = f"{chat_id}:{expires}".encode()
    wanted = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, wanted)
