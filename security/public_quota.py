"""Persistent per-user token quota for non-owner WhatsApp chat.

The database stores only a keyed pseudonymous identity, never a raw JID or phone
number. Usage windows are rolling (36h by default) and count provider-reported
prompt + completion tokens.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()


def _db_path(config=None) -> Path:
    root = Path((config or {}).get("root") or ".").resolve()
    return root / "data" / "quotas" / "public.sqlite3"


def _user_key(identity: str, config=None) -> str:
    # HMAC prevents raw phone numbers from being recoverable by casually reading
    # the quota database. Secret falls back to a stable installation-local value.
    secret = str(os.environ.get("ACTION_TOKEN") or "kwee-public-quota-local").encode()
    return hmac.new(secret, str(identity or "").encode(), hashlib.sha256).hexdigest()


def _connect(config=None):
    path = _db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("CREATE TABLE IF NOT EXISTS usage (user_key TEXT NOT NULL, ts INTEGER NOT NULL, tokens INTEGER NOT NULL CHECK(tokens >= 0))")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_user_ts ON usage(user_key, ts)")
    return conn


def usage(identity: str, *, window_seconds=129600, config=None, now=None) -> int:
    """Return tokens consumed in the user's rolling window."""
    key = _user_key(identity, config)
    cutoff = int((time.time() if now is None else now) - max(1, int(window_seconds)))
    with _LOCK:
        conn = _connect(config)
        try:
            conn.execute("DELETE FROM usage WHERE ts < ?", (cutoff,))
            row = conn.execute("SELECT COALESCE(SUM(tokens), 0) FROM usage WHERE user_key=? AND ts>=?", (key, cutoff)).fetchone()
            return int((row or [0])[0] or 0)
        finally:
            conn.close()


def record(identity: str, tokens: int, *, config=None, now=None) -> int:
    """Add provider-reported tokens and return the new rolling total."""
    amount = max(0, int(tokens or 0))
    if not amount:
        return usage(identity, config=config, now=now)
    key = _user_key(identity, config)
    stamp = int(time.time() if now is None else now)
    with _LOCK:
        conn = _connect(config)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO usage(user_key, ts, tokens) VALUES(?,?,?)", (key, stamp, amount))
            cutoff = stamp - 129600
            conn.execute("DELETE FROM usage WHERE ts < ?", (cutoff,))
            row = conn.execute("SELECT COALESCE(SUM(tokens), 0) FROM usage WHERE user_key=? AND ts>=?", (key, cutoff)).fetchone()
            total = int((row or [0])[0] or 0)
            conn.execute("COMMIT")
            return total
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def status(identity: str, *, window_seconds=129600, config=None, now=None) -> dict:
    """Return usage and the next time the oldest charged tokens expire."""
    key = _user_key(identity, config)
    stamp = int(time.time() if now is None else now)
    cutoff = stamp - max(1, int(window_seconds))
    with _LOCK:
        conn = _connect(config)
        try:
            conn.execute("DELETE FROM usage WHERE ts < ?", (cutoff,))
            row = conn.execute("SELECT COALESCE(SUM(tokens),0), MIN(ts) FROM usage WHERE user_key=? AND ts>=?", (key, cutoff)).fetchone()
            return {"used": int((row or [0, None])[0] or 0),
                    "reset_at": (int(row[1]) + int(window_seconds)) if row and row[1] is not None else None}
        finally:
            conn.close()
