"""Tenant-scoped memory intelligence primitives backed by SQLite.

The store is intentionally deterministic: no external vector service is required.
Semantic retrieval uses normalized token overlap with recency weighting; optional
providers may be layered on later without becoming the source of truth.
"""
from __future__ import annotations
import json, re, sqlite3, time
from pathlib import Path

KINDS = {"episodic", "semantic", "procedural", "working", "long_term", "associative", "temporal", "hierarchical"}
_WORDS = re.compile(r"[\wÀ-ÿ]{2,}", re.UNICODE)

class IntelligentMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, content TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT 'unknown',
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, expires_at INTEGER NOT NULL DEFAULT 0,
            parent_id INTEGER, active INTEGER NOT NULL DEFAULT 1);
          CREATE INDEX IF NOT EXISTS idx_items_kind_time ON items(kind, active, created_at DESC);
          CREATE TABLE IF NOT EXISTS links (src INTEGER NOT NULL, dst INTEGER NOT NULL, label TEXT NOT NULL DEFAULT 'related', created_at INTEGER NOT NULL, UNIQUE(src,dst,label));
          CREATE TABLE IF NOT EXISTS missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active', owner TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
          CREATE INDEX IF NOT EXISTS idx_missions_status ON missions(status, updated_at DESC);
        ''')
        self.db.commit()

    @staticmethod
    def _clean(value, limit=8000):
        return " ".join(str(value or "").split())[:limit]

    def add(self, kind, content, *, source="unknown", metadata=None, ttl_seconds=0, parent_id=None):
        kind = str(kind or "").strip().lower().replace("-", "_")
        content = self._clean(content)
        if kind not in KINDS: return {"ok": False, "error": f"kind must be one of {sorted(KINDS)}"}
        if len(content) < 3: return {"ok": False, "error": "content must contain at least 3 characters"}
        now = int(time.time()); expires = now + max(0, int(ttl_seconds or 0)) if ttl_seconds else 0
        cur = self.db.execute("INSERT INTO items(kind,content,metadata,source,created_at,updated_at,expires_at,parent_id) VALUES(?,?,?,?,?,?,?,?)",
          (kind, content, json.dumps(metadata or {}, ensure_ascii=False), self._clean(source,160), now, now, expires, parent_id))
        self.db.commit()
        return {"ok": True, "id": cur.lastrowid, "kind": kind, "source": source, "created_at": now}

    def get(self, item_id):
        row = self.db.execute("SELECT * FROM items WHERE id=? AND active=1", (int(item_id),)).fetchone()
        if not row: return None
        out = dict(row); out["metadata"] = json.loads(out["metadata"] or "{}")
        return out

    def search(self, query, *, kinds=None, limit=10):
        terms = set(w.lower() for w in _WORDS.findall(self._clean(query,1000)))
        if not terms: return []
        now = int(time.time()); allowed = [k for k in (kinds or []) if k in KINDS]
        sql = "SELECT * FROM items WHERE active=1 AND (expires_at=0 OR expires_at>?)"
        args = [now]
        if allowed: sql += " AND kind IN (" + ",".join("?" for _ in allowed) + ")"; args.extend(allowed)
        rows = self.db.execute(sql + " ORDER BY updated_at DESC LIMIT 500", args).fetchall()
        scored = []
        for row in rows:
            body = (row["content"] + " " + row["kind"] + " " + row["metadata"]).lower()
            words = set(_WORDS.findall(body)); overlap = len(terms & words)
            if overlap:
                age_days = max(0, now - int(row["updated_at"])) / 86400
                score = overlap / max(1, len(terms)) + 0.08 / (1 + age_days / 30)
                item = self.get(row["id"]); item["score"] = round(score, 4); scored.append(item)
        return sorted(scored, key=lambda x:(-x["score"],-x["updated_at"]))[:max(1,min(50,int(limit)))]

    def retrieve(self, query, limit=8):
        return self.search(query, limit=limit)

    def link(self, src, dst, label="related"):
        if int(src) == int(dst) or not self.get(src) or not self.get(dst): return {"ok":False,"error":"two active distinct memory IDs required"}
        self.db.execute("INSERT OR IGNORE INTO links(src,dst,label,created_at) VALUES(?,?,?,?)", (int(src),int(dst),self._clean(label,80) or "related",int(time.time())))
        self.db.commit(); return {"ok":True}

    def relations(self, item_id):
        rows = self.db.execute("SELECT l.label,i.* FROM links l JOIN items i ON i.id=CASE WHEN l.src=? THEN l.dst ELSE l.src END WHERE (l.src=? OR l.dst=?) AND i.active=1",(int(item_id),int(item_id),int(item_id))).fetchall()
        return [{"label":r["label"],"item":self.get(r["id"])} for r in rows]

    def compress(self, *, kind=None, limit=100):
        sql="SELECT * FROM items WHERE active=1"; args=[]
        if kind in KINDS: sql += " AND kind=?"; args.append(kind)
        rows=self.db.execute(sql+" ORDER BY created_at DESC LIMIT ?",(*args,max(2,min(500,int(limit))))).fetchall()
        if len(rows)<2: return {"ok":False,"error":"at least two entries required"}
        grouped={}
        for r in rows:
            words=set(_WORDS.findall(r["content"].lower()))
            key=next((k for k,v in grouped.items() if len(words & v[0]) >= max(2,min(4,len(words)//3))),None)
            if key: grouped[key][1].append(r)
            else: grouped[str(r["id"])]=[words,[r]]
        merged=0; summaries=[]
        for group in grouped.values():
            if len(group[1])<2: continue
            entries=group[1]; text="; ".join(dict.fromkeys(x["content"] for x in entries))[:6000]
            res=self.add("semantic",text,source="memory_compression",metadata={"compressed_ids":[x["id"] for x in entries],"provenance":[x["source"] for x in entries]})
            if res.get("ok"):
                ids=[x["id"] for x in entries]
                self.db.executemany("UPDATE items SET active=0 WHERE id=?",[(i,) for i in ids]); merged+=len(ids); summaries.append(res["id"])
        self.db.commit()
        return {"ok":True,"compressed":merged,"summary_ids":summaries}

    def reflect(self, limit=20):
        rows=self.db.execute("SELECT id,kind,content,source,created_at FROM items WHERE active=1 ORDER BY created_at DESC LIMIT ?",(max(1,min(100,int(limit))),)).fetchall()
        if not rows: return {"count":0,"themes":[],"reflection":"Belum ada memori untuk dievaluasi."}
        counts={}; sources={}
        for r in rows:
            counts[r["kind"]]=counts.get(r["kind"],0)+1; sources[r["source"]]=sources.get(r["source"],0)+1
        themes=sorted(counts.items(),key=lambda x:(-x[1],x[0]))
        return {"count":len(rows),"themes":[{"kind":k,"count":n} for k,n in themes],"sources":sources,"reflection":f"{len(rows)} memori aktif; jenis paling sering: {themes[0][0]}. Asal tercatat untuk {len(sources)} kategori sumber."}

    def provenance(self, item_id):
        item=self.get(item_id)
        if not item:return {"ok":False,"error":"memory not found"}
        return {"ok":True,"id":item["id"],"kind":item["kind"],"source":item["source"],"created_at":item["created_at"],"updated_at":item["updated_at"],"metadata":item["metadata"],"relations":self.relations(item_id)}

    def mission(self, action, *, mission_id=None, title="", goal="", status="active", owner="", metadata=None, limit=20):
        now = int(time.time())
        if action == "create":
            title = self._clean(title, 240); goal = self._clean(goal, 4000)
            if not title: return {"ok": False, "error": "title is required"}
            cur = self.db.execute("INSERT INTO missions(title,goal,status,owner,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (title, goal, "active", self._clean(owner,160), json.dumps(metadata or {},ensure_ascii=False), now, now))
            self.db.commit(); return {"ok": True, "id": cur.lastrowid, "title": title, "status": "active"}
        if action == "list":
            rows = self.db.execute("SELECT * FROM missions ORDER BY updated_at DESC LIMIT ?", (max(1,min(100,int(limit))),)).fetchall()
            return {"ok": True, "missions": [{**dict(r), "metadata":json.loads(r["metadata"] or "{}")} for r in rows]}
        if action == "update":
            allowed = {"active", "paused", "completed", "cancelled"}
            if not mission_id or status not in allowed: return {"ok":False,"error":"mission_id and valid status required"}
            cur = self.db.execute("UPDATE missions SET status=?, updated_at=? WHERE id=?",(status,now,int(mission_id)))
            self.db.commit(); return {"ok":bool(cur.rowcount),"id":int(mission_id),"status":status}
        return {"ok":False,"error":"action must be create, list, or update"}

    def close(self): self.db.close()
