"""store.py — sesi & profil (SQLite). Rahasia tidak pernah disimpan."""
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


TENANT_LIBRARY_CHAT = "__kwee_tenant_library__"


def _naive_summary(messages: list, max_chars: int = 24000) -> str:
    """Ringkasan tanpa LLM: ringkas tiap pesan jadi satu baris.

    Dipakai sebagai fallback bila tak ada summarizer LLM (mis. saat LLM down),
    supaya kompaksi tidak pernah gagal dan konteks tetap terkendali.
    """
    lines = []
    for m in messages:
        role = m.get("role", "?")
        text = " ".join(str(m.get("content") or "").split())
        if not text:
            continue
        if len(text) > 300:
            text = text[:300] + "…"
        lines.append(f"{role}: {text}")
    joined = "\n".join(lines)
    if len(joined) > max_chars:
        joined = joined[-max_chars:]
    return joined


class Store:
    def __init__(self, db_path: str = "data/memory/kwee.db"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: SATU Store dipakai bersama lintas-thread
        # (mis. turn background). SQLite tidak thread-safe untuk tulis paralel,
        # jadi setiap operasi tulis dikunci lewat `self._lock`.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        # SQLite tahan beban multi-thread untuk worker 24/7: WAL mengurangi
        # benturan pembaca/penulis; tiap pragma diisolasi agar satu yang gagal
        # tidak membuat setelan lainnya ikut terlewat.
        for pragma in ("PRAGMA busy_timeout=15000", "PRAGMA journal_mode=WAL",
                       "PRAGMA synchronous=NORMAL", "PRAGMA temp_store=MEMORY"):
            try:
                self.conn.execute(pragma)
            except Exception:
                continue
        self._migrate()

    def _migrate(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_calls TEXT,
            ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
        CREATE TABLE IF NOT EXISTS profile (
            key TEXT PRIMARY KEY,
            value TEXT,
            ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS permissions (
            scope TEXT PRIMARY KEY,
            decision TEXT,
            ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS research_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT '',
            snippet TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'web',
            accessed_at INTEGER NOT NULL,
            hits INTEGER NOT NULL DEFAULT 1,
            UNIQUE(chat_id, url)
        );
        CREATE INDEX IF NOT EXISTS idx_research_sources_chat ON research_sources(chat_id, accessed_at DESC);
        CREATE TABLE IF NOT EXISTS journal_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            source_url TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_journal_notes_chat ON journal_notes(chat_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS tool_metrics (
            tool_name TEXT PRIMARY KEY,
            calls INTEGER NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            total_ms INTEGER NOT NULL DEFAULT 0,
            last_ms INTEGER NOT NULL DEFAULT 0,
            last_used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS learning_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL DEFAULT '',
            signal TEXT NOT NULL, action TEXT NOT NULL, reward REAL NOT NULL,
            source TEXT NOT NULL, context TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '{}', ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_learning_signal ON learning_events(signal, ts DESC);
        CREATE TABLE IF NOT EXISTS skill_refinements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, skill_name TEXT NOT NULL,
            proposal TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'proposed',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prompt_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
            candidate TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'candidate',
            created_at INTEGER NOT NULL
        );
        """)
        # FTS5 mempercepat pencarian lintas riwayat. Jika SQLite yang terpasang
        # tidak membawa FTS5, pencarian tetap hidup melalui fallback LIKE.
        self._fts_available = False
        existed = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        try:
            self.conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content, content='messages', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.id, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END;
            """)
            if not existed:
                self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
            self._fts_available = True
        except sqlite3.OperationalError:
            self._fts_available = False
        self.conn.commit()

    # ---- riwayat sesi ----
    def append(self, chat_id: str, role: str, content=None, tool_calls=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO messages (chat_id, role, content, tool_calls, ts) VALUES (?,?,?,?,?)",
                (chat_id, role, content,
                 json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                 int(time.time())),
            )
            self.conn.commit()

    @staticmethod
    def _search_tokens(query: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"[0-9A-Za-zÀ-ÿ_]{3,}", str(query or "").lower())))[:12]

    def search_history(self, query: str, chat_id: str, limit: int = 5,
                       exclude_recent: int = 0) -> list:
        """Cari percakapan lama dengan scope chat yang eksplisit.

        Riwayat chat lain tidak pernah dicampurkan ke konteks aktif; pemilik
        dapat mempromosikan fakta lintas-sesi melalui tool `memory` secara sadar.
        """
        chat = str(chat_id or "").strip()
        tokens = self._search_tokens(query)
        if not chat or not tokens:
            return []
        limit = max(1, min(20, int(limit or 5)))
        params: list = []
        scope = "m.chat_id=?"
        params.append(chat)
        cutoff_sql = ""
        skip = max(0, min(200, int(exclude_recent or 0)))
        if skip:
            cutoff_sql = " AND m.id < (SELECT MIN(id) FROM (SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?))"
            params.extend([chat, skip])
        rows = []
        with self._lock:
            if self._fts_available:
                match = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
                try:
                    rows = self.conn.execute(
                        "SELECT m.id, m.chat_id, m.role, m.content, m.ts "
                        "FROM messages_fts JOIN messages m ON m.id=messages_fts.rowid "
                        "WHERE messages_fts MATCH ? AND " + scope + cutoff_sql +
                        " ORDER BY bm25(messages_fts), m.id DESC LIMIT ?",
                        [match, *params, limit],
                    ).fetchall()
                except sqlite3.OperationalError:
                    self._fts_available = False
            if not self._fts_available:
                conditions = " OR ".join("LOWER(COALESCE(m.content,'')) LIKE ?" for _ in tokens)
                likes = [f"%{t}%" for t in tokens]
                rows = self.conn.execute(
                    "SELECT m.id, m.chat_id, m.role, m.content, m.ts FROM messages m "
                    "WHERE " + scope + cutoff_sql + " AND (" + conditions + ") "
                    "ORDER BY m.id DESC LIMIT ?",
                    [*params, *likes, limit],
                ).fetchall()
        return [{"id": r["id"], "chat_id": r["chat_id"], "role": r["role"],
                 "content": str(r["content"] or "")[:1200], "ts": int(r["ts"] or 0)}
                for r in rows]

    def record_source(self, chat_id: str, item: dict, source_type: str = "web") -> dict:
        """Simpan jejak sumber yang valid; dedup berdasarkan URL per chat."""
        chat = str(chat_id or "").strip()
        item = item if isinstance(item, dict) else {}
        title = str(item.get("title") or item.get("name") or "Untitled source").strip()[:300]
        url = str(item.get("url") or "").strip()[:2048]
        try:
            parsed = urlsplit(url)
            if not chat or parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
                return {"ok": False, "error": "chat_id dan URL http(s) valid wajib"}
            if parsed.username or parsed.password:
                return {"ok": False, "error": "URL dengan kredensial tertanam ditolak"}
        except Exception:
            return {"ok": False, "error": "URL tidak valid"}
        domain = (parsed.hostname or "").lower()[:255]
        snippet = str(item.get("snippet") or item.get("content") or "").strip()[:2400]
        # Jangan menanamkan teks yang secara jelas tampak seperti secret.
        try:
            from memory.policy import secret_reason
            if secret_reason(snippet):
                snippet = "[snippet disensor oleh kebijakan keamanan]"
        except Exception:
            pass
        now = int(time.time())
        with self._lock:
            old = self.conn.execute(
                "SELECT id FROM research_sources WHERE chat_id=? AND url=?", (chat, url)
            ).fetchone()
            self.conn.execute(
                "INSERT INTO research_sources(chat_id,title,url,domain,snippet,source_type,accessed_at,hits) "
                "VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(chat_id,url) DO UPDATE SET "
                "title=excluded.title, domain=excluded.domain, snippet=excluded.snippet, "
                "source_type=excluded.source_type, accessed_at=excluded.accessed_at, hits=hits+1",
                (chat, title or "Untitled source", url, domain, snippet,
                 str(source_type or "web")[:40], now),
            )
            row = self.conn.execute(
                "SELECT id,hits FROM research_sources WHERE chat_id=? AND url=?", (chat, url)
            ).fetchone()
            self.conn.commit()
        return {"ok": True, "id": int(row["id"]), "duplicate": bool(old), "hits": int(row["hits"])}

    def search_sources(self, chat_id: str, query: str, limit: int = 8) -> list:
        chat = str(chat_id or "").strip()
        tokens = self._search_tokens(query)
        if not chat or not tokens:
            return []
        conditions = " OR ".join(
            "(LOWER(title) LIKE ? OR LOWER(domain) LIKE ? OR LOWER(snippet) LIKE ?)"
            for _ in tokens
        )
        params = []
        for token in tokens:
            params.extend([f"%{token}%"] * 3)
        with self._lock:
            rows = self.conn.execute(
                "SELECT id,title,url,domain,snippet,source_type,accessed_at,hits "
                "FROM research_sources WHERE chat_id=? AND (" + conditions + ") "
                "ORDER BY accessed_at DESC LIMIT ?",
                [chat, *params, max(1, min(20, int(limit or 8)))],
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_sources(self, chat_id: str, limit: int = 10) -> list:
        chat = str(chat_id or "").strip()
        if not chat:
            return []
        with self._lock:
            rows = self.conn.execute(
                "SELECT id,title,url,domain,snippet,source_type,accessed_at,hits "
                "FROM research_sources WHERE chat_id=? ORDER BY accessed_at DESC LIMIT ?",
                (chat, max(1, min(30, int(limit or 10)))),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_journal_note(self, chat_id: str, title: str, content: str,
                          tags=None, source_url: str = "", scope: str = "chat") -> dict:
        active_chat = str(chat_id or "").strip()
        scope = str(scope or "chat").strip().lower()
        if scope not in {"chat", "library"}:
            return {"ok": False, "error": "scope harus 'chat' atau 'library'"}
        chat = TENANT_LIBRARY_CHAT if scope == "library" else active_chat
        title = str(title or "").strip()[:240]
        content = str(content or "").strip()[:6000]
        if not active_chat or not title or len(content) < 5:
            return {"ok": False, "error": "active chat_id, title, dan content minimal 5 karakter wajib"}
        try:
            from memory.policy import secret_reason
            why = secret_reason(title + " " + content)
            if why:
                return {"ok": False, "error": f"catatan ditolak oleh kebijakan rahasia: {why}"}
        except Exception:
            pass
        tag_list = [str(x).strip()[:40] for x in (tags or []) if str(x).strip()][:12]
        source_url = str(source_url or "").strip()[:2048]
        if source_url:
            parsed = urlsplit(source_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                return {"ok": False, "error": "source_url harus URL http(s) valid"}
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO journal_notes(chat_id,title,content,tags,source_url,created_at) VALUES (?,?,?,?,?,?)",
                (chat, title, content, json.dumps(tag_list, ensure_ascii=False), source_url, int(time.time())),
            )
            self.conn.commit()
        return {"ok": True, "id": int(cur.lastrowid), "title": title, "scope": scope}

    def search_journal_notes(self, chat_id: str, query: str, limit: int = 10,
                             include_library: bool = False) -> list:
        chat = str(chat_id or "").strip()
        tokens = self._search_tokens(query)
        if not chat or not tokens:
            return []
        where = " OR ".join("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)" for _ in tokens)
        params = []
        for token in tokens:
            params.extend([f"%{token}%"] * 3)
        if include_library:
            chat_scope = "chat_id IN (?,?)"
            scope_params = [chat, TENANT_LIBRARY_CHAT]
        else:
            chat_scope = "chat_id=?"
            scope_params = [chat]
        with self._lock:
            rows = self.conn.execute(
                "SELECT id,title,content,tags,source_url,created_at, "
                "CASE WHEN chat_id=? THEN 'library' ELSE 'chat' END AS scope "
                "FROM journal_notes WHERE " + chat_scope + " AND (" + where + ") "
                "ORDER BY created_at DESC LIMIT ?",
                [TENANT_LIBRARY_CHAT, *scope_params, *params, max(1, min(30, int(limit or 10)))],
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except Exception:
                item["tags"] = []
            result.append(item)
        return result

    def record_tool_metric(self, tool_name: str, ok: bool, duration_ms: int = 0) -> None:
        name = str(tool_name or "").strip()[:120]
        if not name:
            return
        ms = max(0, min(600_000, int(duration_ms or 0)))
        with self._lock:
            self.conn.execute(
                "INSERT INTO tool_metrics(tool_name,calls,successes,failures,total_ms,last_ms,last_used) "
                "VALUES (?,1,?,?,?, ?,?) ON CONFLICT(tool_name) DO UPDATE SET "
                "calls=calls+1, successes=successes+excluded.successes, "
                "failures=failures+excluded.failures, total_ms=total_ms+excluded.total_ms, "
                "last_ms=excluded.last_ms, last_used=excluded.last_used",
                (name, 1 if ok else 0, 0 if ok else 1, ms, ms, int(time.time())),
            )
            self.conn.commit()

    def tool_stats(self, limit: int = 30) -> list:
        with self._lock:
            rows = self.conn.execute(
                "SELECT tool_name,calls,successes,failures,total_ms,last_ms,last_used "
                "FROM tool_metrics ORDER BY calls DESC, successes DESC LIMIT ?",
                (max(1, min(100, int(limit or 30))),),
            ).fetchall()
        return [{**dict(r), "avg_ms": round((int(r["total_ms"]) / max(1, int(r["calls"]))), 1),
                 "success_rate": round(int(r["successes"]) / max(1, int(r["calls"])), 3)}
                for r in rows]

    def record_learning_event(self, signal: str, action: str, reward: float,
                              source: str = "human", context: str = "",
                              evidence=None, chat_id: str = "") -> int:
        signal = re.sub(r"[^a-z0-9_.:-]", "", str(signal or "").lower())[:80]
        action = " ".join(str(action or "").split())[:180]
        if not signal or not action:
            raise ValueError("signal and action are required")
        reward = max(-1.0, min(1.0, float(reward)))
        source = str(source or "human").lower()
        if source not in {"human", "rlaif", "observed"}:
            raise ValueError("source must be human, rlaif, or observed")
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO learning_events(chat_id,signal,action,reward,source,context,evidence,ts) VALUES(?,?,?,?,?,?,?,?)",
                (str(chat_id or "")[:180], signal, action, reward, source,
                 " ".join(str(context or "").split())[:500],
                 json.dumps(evidence or {}, ensure_ascii=False, default=str)[:4000], int(time.time())))
            self.conn.commit()
            return int(cur.lastrowid)

    def learning_stats(self, signal: str = "", limit: int = 20) -> list:
        where = "WHERE signal=?" if signal else ""
        args = [str(signal)] if signal else []
        with self._lock:
            rows = self.conn.execute(
                "SELECT signal,action,COUNT(*) AS n,AVG(reward) AS mean_reward,"
                "SUM(CASE WHEN source='human' THEN 1 ELSE 0 END) AS human_n,"
                "SUM(CASE WHEN source='rlaif' THEN 1 ELSE 0 END) AS rlaif_n,MAX(ts) AS last_ts "
                f"FROM learning_events {where} GROUP BY signal,action ORDER BY n DESC,last_ts DESC LIMIT ?",
                [*args, max(1,min(100,int(limit or 20)))])
        return [{**dict(r), "mean_reward": round(float(r["mean_reward"] or 0),4)} for r in rows]

    def save_learning_proposal(self, kind: str, name: str, proposal: str,
                               rationale: str = "", evidence=None) -> int:
        name = " ".join(str(name or "").split())[:180]
        proposal = " ".join(str(proposal or "").split())[:4000]
        if not name or not proposal: raise ValueError("name and proposal are required")
        table, name_column, content_column = (("skill_refinements", "skill_name", "proposal") if kind == "skill" else
                          ("prompt_candidates", "scope", "candidate") if kind == "prompt" else (None,None,None))
        if not table: raise ValueError("kind must be skill or prompt")
        with self._lock:
            cur = self.conn.execute(
                f"INSERT INTO {table}({name_column},{content_column},rationale,evidence,created_at) VALUES(?,?,?,?,?)",
                (name, proposal, " ".join(str(rationale or "").split())[:1000],
                 json.dumps(evidence or {},ensure_ascii=False,default=str)[:4000],int(time.time())))
            self.conn.commit(); return int(cur.lastrowid)

    def learning_proposals(self, limit: int = 20) -> list:
        limit=max(1,min(100,int(limit or 20)))
        with self._lock:
            skills=self.conn.execute("SELECT id,skill_name AS name,proposal,rationale,status,created_at,'skill' AS kind FROM skill_refinements ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
            prompts=self.conn.execute("SELECT id,scope AS name,candidate AS proposal,rationale,status,created_at,'prompt' AS kind FROM prompt_candidates ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
        return sorted([dict(r) for r in [*skills,*prompts]],key=lambda x:x["created_at"],reverse=True)[:limit]

    def approve_skill_refinement(self, proposal_id: int) -> dict:
        with self._lock:
            row=self.conn.execute("SELECT id,skill_name,proposal,status FROM skill_refinements WHERE id=?",(int(proposal_id),)).fetchone()
            if not row:return {"ok":False,"error":"skill proposal not found"}
            if str(row["status"])=="approved":return {"ok":True,"id":int(row["id"]),"skill":row["skill_name"],"status":"approved"}
            text=str(row["proposal"] or "")
            unsafe=re.search(r"ignore\s+(all\s+)?previous instructions|bypass\s+(safety|security)|reveal\s+(the\s+)?system prompt|abaikan\s+instruksi|lewati\s+keamanan",text,re.I)
            if unsafe:return {"ok":False,"error":"proposal contains unsafe instruction pattern; edit/re-propose before approval"}
            self.conn.execute("UPDATE skill_refinements SET status='approved' WHERE id=?",(int(proposal_id),));self.conn.commit()
            return {"ok":True,"id":int(row["id"]),"skill":row["skill_name"],"status":"approved"}

    def approved_skill_refinements(self, names, limit: int = 20) -> list:
        names=[str(n) for n in (names or []) if str(n).strip()]
        if not names:return []
        with self._lock:
            rows=self.conn.execute("SELECT id,skill_name,proposal,rationale,created_at FROM skill_refinements WHERE status='approved' AND skill_name IN ("+",".join("?" for _ in names)+") ORDER BY created_at DESC LIMIT ?",[*names,max(1,min(100,int(limit or 20)))]).fetchall()
        return [dict(r) for r in rows]

    def history(self, chat_id: str, limit: int = 40) -> list:
        rows = self.conn.execute(
            "SELECT role, content, tool_calls FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        ).fetchall()
        out = []
        for r in reversed(rows):
            msg = {"role": r["role"], "content": r["content"]}
            if r["tool_calls"]:
                msg["tool_calls"] = json.loads(r["tool_calls"])
            out.append(msg)
        return out

    # ---- manajemen memori (kompaksi riwayat) ----
    def count(self, chat_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return int(row["n"]) if row else 0

    def compact(self, chat_id: str, protect_last_n: int = 20, summarize=None,
                max_chars: int = 24000) -> dict:
        """Rangkum pesan lama agar konteks tidak meledak (pola Hermes/Claude Code).

        Menyimpan pesan terakhir ``protect_last_n`` apa adanya, lalu merangkum
        sisanya menjadi SATU baris ringkasan (role system) yang disimpan di
        tabel ``profile`` (kunci ``summary:<chat_id>``). Pesan lama TIDAK
        dihapus — hanya tidak lagi dikirim ke LLM — sehingga riwayat mentah
        tetap utuh untuk audit.

        ``summarize`` = callable(list_of_messages) -> str. Bila None, dipakai
        ringkasan naif (potongan teks) supaya tetap aman tanpa LLM.

        Mengembalikan {"compacted": bool, "summarized": int, "kept": int}.
        """
        total = self.count(chat_id)
        if total <= protect_last_n:
            return {"compacted": False, "summarized": 0, "kept": total}

        with self._lock:
            # Ambil yang lama (di luar protect_last_n) berdasarkan id.
            cutoff = self.conn.execute(
                "SELECT id FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1 OFFSET ?",
                (chat_id, protect_last_n - 1),
            ).fetchone()
            if not cutoff:
                return {"compacted": False, "summarized": 0, "kept": total}
            old_rows = self.conn.execute(
                "SELECT role, content FROM messages WHERE chat_id=? AND id < ? ORDER BY id",
                (chat_id, cutoff["id"]),
            ).fetchall()
            if not old_rows:
                return {"compacted": False, "summarized": 0, "kept": total}

            msgs = [{"role": r["role"], "content": r["content"] or ""} for r in old_rows]
            try:
                summary = summarize(msgs) if callable(summarize) else _naive_summary(msgs, max_chars)
            except Exception:
                summary = _naive_summary(msgs, max_chars)
            if not summary:
                summary = _naive_summary(msgs, max_chars)

            # Gabungkan dengan ringkasan sebelumnya (kompaksi berulang).
            prev = self.get_profile().get(f"summary:{chat_id}", "")
            merged = (prev + "\n" + summary).strip() if prev else summary
            # Pertahankan bagian TERBARU ketika ringkasan berulang kali digabung.
            self.set_profile(f"summary:{chat_id}", merged[-max_chars:])
            self.conn.execute(
                "INSERT INTO profile (key, value, ts) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (f"summary_upto:{chat_id}", str(cutoff["id"]), int(time.time())),
            )
            self.conn.commit()
        return {"compacted": True, "summarized": len(msgs), "kept": protect_last_n}

    def compacted_history(self, chat_id: str, limit: int = 40, protect_last_n: int = 20) -> list:
        """Riwayat siap-kirim: [ringkasan] + pesan terakhir (mentah)."""
        summary = self.get_profile().get(f"summary:{chat_id}", "").strip()
        msgs = self.history(chat_id, limit=limit)
        out = []
        if summary:
            out.append({"role": "system", "content": f"Ringkasan percakapan sebelumnya:\n{summary}"})
        out.extend(msgs)
        return out

    def clear(self, chat_id: str):
        with self._lock:
            self.conn.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            self.conn.execute("DELETE FROM profile WHERE key IN (?,?)",
                              (f"summary:{chat_id}", f"summary_upto:{chat_id}"))
            self.conn.commit()

    # ---- profil ----
    def set_profile(self, key: str, value: str):
        with self._lock:
            self.conn.execute(
                "INSERT INTO profile (key, value, ts) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, value, int(time.time())),
            )
            self.conn.commit()

    def get_profile(self) -> dict:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM profile")}

    # ---- keputusan izin (persist, tak tanya ulang) ----
    def remember_permission(self, scope: str, decision: str):
        self.conn.execute(
            "INSERT INTO permissions (scope, decision, ts) VALUES (?,?,?) "
            "ON CONFLICT(scope) DO UPDATE SET decision=excluded.decision, ts=excluded.ts",
            (scope, decision, int(time.time())),
        )
        self.conn.commit()

    def permission(self, scope: str):
        row = self.conn.execute("SELECT decision FROM permissions WHERE scope=?", (scope,)).fetchone()
        return row["decision"] if row else None
