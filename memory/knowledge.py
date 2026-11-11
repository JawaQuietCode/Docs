"""Tenant-local, provenance-first knowledge and research workbench.

This module stores *claims about information*, not truth. It records the source,
epistemic label, confidence (when supplied), time interval, and explicit links.
Conflict detection is deterministic for exact subject/predicate triples; it does
not choose a winner or infer that a causal relation is true.

The scientific workbench stores candidate hypotheses, rivals, predictions,
variables, controls, plans and observations. Its inferential statistics support
is deliberately narrow: a two-sided Welch independent-samples t-test with an
explicit caller attestation for design/distribution assumptions and Bonferroni
adjustment for a declared comparison family. It is not a substitute for a
statistician, study preregistration, causal identification, or domain review.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

CLAIM_TYPES = {
    "observed", "reported", "hypothesis", "inference", "definition",
    "rule", "prediction", "mechanism",
}
CLAIM_STATUSES = {"active", "disputed", "superseded", "retracted"}
RELATIONS = {
    "supports", "supported_by", "refutes", "contradicts", "depends_on",
    "derived_from", "analogous_to", "decomposed_into", "causal_hypothesis",
    "counterfactual_of", "supersedes", "same_as",
}
_DEPENDENCY_RELATIONS = {"supported_by", "depends_on", "derived_from"}
_PROJECT_STATUSES = {"active", "paused", "completed", "archived"}
_HYPOTHESIS_TYPES = {"descriptive", "associational", "predictive", "causal", "mechanistic"}
_WORDS = re.compile(r"[\wÀ-ÿ]{2,}", re.UNICODE)
_MAX_TEXT = 8000


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, fallback):
    try:
        return json.loads(value or "null")
    except (TypeError, ValueError):
        return fallback


def _clean(value: Any, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_time(value: Any) -> tuple[str | None, int | None]:
    if value in (None, ""):
        return None, None
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed_date = date.fromisoformat(raw)
        parsed = datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(), int(parsed.timestamp())


def _intervals_overlap(left: dict, right: dict) -> bool:
    left_start = left.get("valid_from_ts")
    right_start = right.get("valid_from_ts")
    left_end = left.get("valid_until_ts")
    right_end = right.get("valid_until_ts")
    start = max(left_start if left_start is not None else -math.inf,
                right_start if right_start is not None else -math.inf)
    end = min(left_end if left_end is not None else math.inf,
              right_end if right_end is not None else math.inf)
    return start < end


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta function."""
    max_iter, eps, fpmin = 300, 3.0e-14, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_bt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
              + a * math.log(x) + b * math.log1p(-x))
    bt = math.exp(log_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return max(0.0, min(1.0, bt * _betacf(a, b, x) / a))
    return max(0.0, min(1.0, 1.0 - bt * _betacf(b, a, 1.0 - x) / b))


def _student_t_cdf(value: float, degrees_freedom: float) -> float:
    if degrees_freedom <= 0 or not math.isfinite(degrees_freedom):
        raise ValueError("degrees of freedom must be finite and positive")
    if value == 0:
        return 0.5
    x = degrees_freedom / (degrees_freedom + value * value)
    tail = 0.5 * _regularized_beta(x, degrees_freedom / 2.0, 0.5)
    return 1.0 - tail if value > 0 else tail


def _two_sided_t_p_value(value: float, degrees_freedom: float) -> float:
    if math.isinf(value):
        return 0.0
    if degrees_freedom <= 0 or not math.isfinite(degrees_freedom):
        raise ValueError("degrees of freedom must be finite and positive")
    # Direct beta-tail evaluation avoids cancellation in 1 - CDF for small p.
    x = degrees_freedom / (degrees_freedom + value * value)
    return max(0.0, min(1.0, _regularized_beta(x, degrees_freedom / 2.0, 0.5)))


def _t_critical(degrees_freedom: float, confidence: float) -> float:
    target = (1.0 + confidence) / 2.0
    low, high = 0.0, 1.0
    while _student_t_cdf(high, degrees_freedom) < target and high < 1e7:
        high *= 2.0
    for _ in range(100):
        mid = (low + high) / 2.0
        if _student_t_cdf(mid, degrees_freedom) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


class KnowledgeWorkbench:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), timeout=15)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_claims (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
          claim_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
          confidence REAL, source_uri TEXT NOT NULL DEFAULT '', source_label TEXT NOT NULL DEFAULT '',
          evidence TEXT NOT NULL DEFAULT '', valid_from TEXT, valid_until TEXT,
          valid_from_ts INTEGER, valid_until_ts INTEGER,
          supersedes_id INTEGER REFERENCES knowledge_claims(id),
          metadata TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_knowledge_claim_key
          ON knowledge_claims(subject, predicate, status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_knowledge_claim_validity
          ON knowledge_claims(valid_from_ts, valid_until_ts, status);
        CREATE TABLE IF NOT EXISTS knowledge_relations (
          source_id INTEGER NOT NULL REFERENCES knowledge_claims(id),
          target_id INTEGER NOT NULL REFERENCES knowledge_claims(id),
          relation TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
          PRIMARY KEY(source_id, target_id, relation));
        CREATE INDEX IF NOT EXISTS idx_knowledge_relations_target
          ON knowledge_relations(target_id, relation);
        CREATE TABLE IF NOT EXISTS research_projects (
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          question TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active', created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS research_hypotheses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
          statement TEXT NOT NULL, hypothesis_type TEXT NOT NULL,
          alternatives TEXT NOT NULL DEFAULT '[]', predictions TEXT NOT NULL DEFAULT '[]',
          falsification_criteria TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'candidate',
          created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_research_hypotheses_project
          ON research_hypotheses(project_id, created_at);
        CREATE TABLE IF NOT EXISTS research_designs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
          hypothesis_id INTEGER REFERENCES research_hypotheses(id),
          design_type TEXT NOT NULL, independent_variables TEXT NOT NULL,
          dependent_variables TEXT NOT NULL, confounders TEXT NOT NULL DEFAULT '[]',
          controls TEXT NOT NULL DEFAULT '[]', sampling_plan TEXT NOT NULL DEFAULT '',
          analysis_plan TEXT NOT NULL DEFAULT '', stopping_rule TEXT NOT NULL DEFAULT '',
          created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS research_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          project_id INTEGER NOT NULL REFERENCES research_projects(id) ON DELETE CASCADE,
          hypothesis_id INTEGER REFERENCES research_hypotheses(id),
          observation TEXT NOT NULL, source TEXT NOT NULL,
          observed_at TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
          deviations TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_research_observations_project
          ON research_observations(project_id, created_at);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _claim_dict(row: sqlite3.Row | dict | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["metadata"] = _json_load(item.get("metadata"), {})
        return item

    @staticmethod
    def _safe_source_uri(value: Any) -> str:
        uri = _clean(value, 2000)
        if not uri:
            return ""
        parsed = urlparse(uri)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_uri must be an absolute http(s) URL")
        return uri

    @staticmethod
    def _string_list(values: Any, field: str, *, required: bool = False, maximum: int = 20) -> list[str]:
        if values is None:
            values = []
        if not isinstance(values, list):
            raise ValueError(f"{field} must be an array of strings")
        result = []
        for value in values[:maximum]:
            text = _clean(value, 500)
            if text and text not in result:
                result.append(text)
        if required and not result:
            raise ValueError(f"{field} must contain at least one item")
        return result

    def add_claim(self, subject: Any, predicate: Any, object_value: Any, *,
                  claim_type: str = "reported", confidence: float | None = None,
                  source_uri: str = "", source_label: str = "", evidence: str = "",
                  valid_from: Any = None, valid_until: Any = None,
                  metadata: dict | None = None, supersedes_id: int | None = None) -> dict:
        subject, predicate, object_value = _clean(subject, 500), _clean(predicate, 300), _clean(object_value, 2000)
        if not subject or not predicate or not object_value:
            return {"ok": False, "error": "subject, predicate and object are required"}
        claim_type = str(claim_type or "reported").strip().lower()
        if claim_type not in CLAIM_TYPES:
            return {"ok": False, "error": f"claim_type must be one of {sorted(CLAIM_TYPES)}"}
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                return {"ok": False, "error": "confidence must be a number between 0 and 1"}
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                return {"ok": False, "error": "confidence must be between 0 and 1"}
        if metadata is not None and not isinstance(metadata, dict):
            return {"ok": False, "error": "metadata must be an object"}
        try:
            source_uri = self._safe_source_uri(source_uri)
            valid_from_text, valid_from_ts = _parse_time(valid_from)
            valid_until_text, valid_until_ts = _parse_time(valid_until)
            if valid_from_ts is not None and valid_until_ts is not None and valid_until_ts <= valid_from_ts:
                return {"ok": False, "error": "valid_until must be later than valid_from"}
            now = int(time.time())
            cur = self.db.execute("""INSERT INTO knowledge_claims
              (subject,predicate,object,claim_type,confidence,source_uri,source_label,evidence,
               valid_from,valid_until,valid_from_ts,valid_until_ts,supersedes_id,metadata,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (subject, predicate, object_value, claim_type, confidence, source_uri,
               _clean(source_label, 500), _clean(evidence, 4000), valid_from_text,
               valid_until_text, valid_from_ts, valid_until_ts, supersedes_id,
               _json_dump(metadata or {}), now, now))
            self.db.commit()
            return {"ok": True, "id": cur.lastrowid, "status": "active", "claim_type": claim_type}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        except (sqlite3.Error, TypeError) as exc:
            self.db.rollback()
            return {"ok": False, "error": f"claim storage failed: {type(exc).__name__}"}

    def get_claim(self, claim_id: Any, *, include_inactive: bool = True) -> dict | None:
        try:
            sql = "SELECT * FROM knowledge_claims WHERE id=?"
            row = self.db.execute(sql, (int(claim_id),)).fetchone()
        except (TypeError, ValueError, sqlite3.Error):
            return None
        item = self._claim_dict(row)
        if item and not include_inactive and item["status"] not in {"active", "disputed"}:
            return None
        return item

    def search_claims(self, query: str = "", *, subject: str = "", predicate: str = "",
                      as_of: Any = None, include_inactive: bool = False, limit: int = 20) -> list[dict]:
        terms = {token.casefold() for token in _WORDS.findall(_clean(query, 1000))}
        subject_key, predicate_key = _clean(subject, 500).casefold(), _clean(predicate, 300).casefold()
        try:
            _, as_of_ts = _parse_time(as_of) if as_of not in (None, "") else (None, int(time.time()))
        except ValueError:
            return []
        sql = "SELECT * FROM knowledge_claims WHERE 1=1"
        args: list[Any] = []
        if not include_inactive:
            sql += " AND status IN ('active','disputed')"
        if as_of_ts is not None:
            sql += " AND (valid_from_ts IS NULL OR valid_from_ts<=?) AND (valid_until_ts IS NULL OR valid_until_ts>?)"
            args.extend([as_of_ts, as_of_ts])
        rows = [self._claim_dict(r) for r in self.db.execute(sql + " ORDER BY updated_at DESC LIMIT 1000", args).fetchall()]
        hits = []
        for item in rows:
            if subject_key and item["subject"].casefold() != subject_key:
                continue
            if predicate_key and item["predicate"].casefold() != predicate_key:
                continue
            haystack = " ".join(str(item.get(k) or "") for k in ("subject", "predicate", "object", "claim_type", "evidence", "source_label", "source_uri")).casefold()
            overlap = len(terms & {x.casefold() for x in _WORDS.findall(haystack)})
            if terms and overlap == 0:
                continue
            item["match_score"] = round(overlap / max(1, len(terms)), 4) if terms else 1.0
            hits.append(item)
        return sorted(hits, key=lambda x: (-x["match_score"], -x["updated_at"]))[:max(1, min(100, int(limit)))]

    def reconcile(self, subject: str, predicate: str) -> dict:
        subject, predicate = _clean(subject, 500), _clean(predicate, 300)
        if not subject or not predicate:
            return {"ok": False, "error": "subject and predicate are required", "conflicts": []}
        rows = self.db.execute("""SELECT * FROM knowledge_claims
          WHERE LOWER(subject)=LOWER(?) AND LOWER(predicate)=LOWER(?)
          AND status IN ('active','disputed') ORDER BY created_at""", (subject, predicate)).fetchall()
        claims = [self._claim_dict(row) for row in rows]
        conflicts, disputed_ids = [], set()
        for index, left in enumerate(claims):
            for right in claims[index + 1:]:
                if left["object"].casefold() == right["object"].casefold():
                    continue
                if _intervals_overlap(left, right):
                    disputed_ids.update((left["id"], right["id"]))
                    conflicts.append({"claims": [left, right], "reason": "different objects for the same subject/predicate over overlapping validity intervals"})
        if disputed_ids:
            now = int(time.time())
            self.db.executemany("UPDATE knowledge_claims SET status='disputed',updated_at=? WHERE id=? AND status='active'",
                                [(now, claim_id) for claim_id in sorted(disputed_ids)])
            self.db.commit()
            for conflict in conflicts:
                for item in conflict["claims"]:
                    item["status"] = "disputed"
        return {
            "ok": True, "subject": subject, "predicate": predicate,
            "conflicts": conflicts, "disputed_claim_ids": sorted(disputed_ids),
            "policy": "Conflicts are flagged, not automatically resolved; inspect provenance and temporal scope before revising.",
        }

    def link_claims(self, source_id: Any, target_id: Any, relation: str, note: str = "") -> dict:
        try:
            source_id, target_id = int(source_id), int(target_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "source_id and target_id must be integers"}
        relation = str(relation or "").strip().lower()
        if relation not in RELATIONS:
            return {"ok": False, "error": f"relation must be one of {sorted(RELATIONS)}"}
        if source_id == target_id or not self.get_claim(source_id) or not self.get_claim(target_id):
            return {"ok": False, "error": "two existing, distinct claim IDs are required"}
        self.db.execute("""INSERT OR REPLACE INTO knowledge_relations
          (source_id,target_id,relation,note,created_at) VALUES(?,?,?,?,?)""",
          (source_id, target_id, relation, _clean(note, 1000), int(time.time())))
        self.db.commit()
        return {"ok": True, "source_id": source_id, "target_id": target_id, "relation": relation}

    def claim_provenance(self, claim_id: Any) -> dict:
        item = self.get_claim(claim_id)
        if not item:
            return {"ok": False, "error": "claim not found"}
        rows = self.db.execute("""SELECT relation,note,created_at,source_id,target_id
          FROM knowledge_relations WHERE source_id=? OR target_id=?
          ORDER BY created_at,relation""", (item["id"], item["id"])).fetchall()
        relations = []
        for row in rows:
            edge = dict(row)
            other_id = edge["target_id"] if edge["source_id"] == item["id"] else edge["source_id"]
            edge["other_claim"] = self.get_claim(other_id)
            relations.append(edge)
        return {"ok": True, "claim": item, "source": {"uri": item["source_uri"], "label": item["source_label"], "evidence": item["evidence"]}, "relations": relations}

    def revise_claim(self, claim_id: Any, *, new_object: str, reason: str,
                     source_uri: str = "", source_label: str = "", evidence: str = "",
                     confidence: float | None = None, valid_from: Any = None,
                     valid_until: Any = None) -> dict:
        old = self.get_claim(claim_id)
        if not old or old["status"] not in {"active", "disputed"}:
            return {"ok": False, "error": "claim must exist and be active/disputed to be revised"}
        reason = _clean(reason, 2000)
        if not reason:
            return {"ok": False, "error": "revision reason is required"}
        start = valid_from or datetime.now(timezone.utc).isoformat()
        try:
            start_text, start_ts = _parse_time(start)
            old_end_text, old_end_ts = old.get("valid_until"), old.get("valid_until_ts")
            # Keep the old end when it is still in the future; an already-expired
            # end must not make the replacement invalid or inherit stale validity.
            end = valid_until if valid_until is not None else (
                old_end_text if old_end_ts is not None and old_end_ts > start_ts else None
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": f"invalid revision time: {exc}"}
        added = self.add_claim(
            old["subject"], old["predicate"], new_object,
            claim_type=old["claim_type"],
            confidence=confidence if confidence is not None else old.get("confidence"),
            source_uri=source_uri or old.get("source_uri", ""),
            source_label=source_label or old.get("source_label", ""),
            evidence=_clean(f"Revision reason: {reason}. {evidence or ''}", 4000),
            valid_from=start_text, valid_until=end,
            metadata={"revision_reason": reason, "supersedes_id": old["id"]},
            supersedes_id=old["id"],
        )
        if not added.get("ok"):
            return added
        now = int(time.time())
        try:
            if old_end_ts is None or old_end_ts > start_ts:
                self.db.execute("""UPDATE knowledge_claims
                    SET status='superseded',valid_until=?,valid_until_ts=?,updated_at=? WHERE id=?""",
                    (start_text, start_ts, now, old["id"]))
            else:
                self.db.execute("UPDATE knowledge_claims SET status='superseded',updated_at=? WHERE id=?",
                                (now, old["id"]))
            self.db.execute("INSERT INTO knowledge_relations(source_id,target_id,relation,note,created_at) VALUES(?,?,?,?,?)",
                            (added["id"], old["id"], "supersedes", reason, now))
            self.db.commit()
        except sqlite3.Error as exc:
            self.db.rollback()
            # `add_claim` commits independently; remove the staged revision if
            # its supersession edge could not be persisted.
            self.db.execute("DELETE FROM knowledge_claims WHERE id=?", (added["id"],))
            self.db.commit()
            return {"ok": False, "error": f"revision link failed: {type(exc).__name__}"}
        return {"ok": True, "id": added["id"], "supersedes_id": old["id"], "status": "active", "reason": reason}

    def reconstruct(self, claim_id: Any, *, max_depth: int = 5) -> dict:
        root = self.get_claim(claim_id)
        if not root:
            return {"ok": False, "error": "claim not found"}
        depth_limit = max(1, min(8, int(max_depth or 5)))
        visited = {root["id"]}
        queue = [(root["id"], 0)]
        dependencies = []
        edges = []
        while queue:
            current, depth = queue.pop(0)
            if depth >= depth_limit:
                continue
            rows = self.db.execute("""SELECT target_id,relation,note FROM knowledge_relations
              WHERE source_id=? AND relation IN ('supported_by','depends_on','derived_from')
              ORDER BY relation,target_id""", (current,)).fetchall()
            for row in rows:
                target_id = int(row["target_id"])
                edges.append({"source_id": current, "target_id": target_id,
                              "relation": row["relation"], "note": row["note"]})
                if target_id in visited:
                    continue
                visited.add(target_id)
                item = self.get_claim(target_id)
                if item:
                    item["depth"] = depth + 1
                    dependencies.append(item)
                    queue.append((target_id, depth + 1))
        return {"ok": True, "root": root, "dependencies": dependencies,
                "edges": edges, "depth_limit": depth_limit,
                "note": "Structural reconstruction follows only explicit dependency/support links; it does not infer missing facts."}

    def create_research(self, title: str, question: str, scope: str = "") -> dict:
        title, question = _clean(title, 300), _clean(question, 2000)
        if not title or not question or "?" not in question:
            return {"ok": False, "error": "title and an answerable question ending with '?' are required"}
        now = int(time.time())
        cur = self.db.execute("INSERT INTO research_projects(title,question,scope,created_at,updated_at) VALUES(?,?,?,?,?)",
                              (title, question, _clean(scope, 2000), now, now))
        self.db.commit()
        return {"ok": True, "id": cur.lastrowid, "status": "active"}

    def add_hypothesis(self, project_id: Any, statement: str, *, alternatives: Any = None,
                       predictions: Any = None, hypothesis_type: str = "associational",
                       falsification_criteria: str = "") -> dict:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "project_id must be an integer"}
        if not self.db.execute("SELECT 1 FROM research_projects WHERE id=?", (project_id,)).fetchone():
            return {"ok": False, "error": "research project not found"}
        statement = _clean(statement, 2000)
        hypothesis_type = str(hypothesis_type or "associational").strip().lower()
        if not statement or hypothesis_type not in _HYPOTHESIS_TYPES:
            return {"ok": False, "error": f"statement is required; hypothesis_type must be one of {sorted(_HYPOTHESIS_TYPES)}"}
        try:
            alternatives = self._string_list(alternatives, "alternatives")
            predictions = self._string_list(predictions, "predictions")
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        now = int(time.time())
        cur = self.db.execute("""INSERT INTO research_hypotheses
          (project_id,statement,hypothesis_type,alternatives,predictions,falsification_criteria,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?)""",
          (project_id, statement, hypothesis_type, _json_dump(alternatives), _json_dump(predictions),
           _clean(falsification_criteria, 2000), now, now))
        self.db.commit()
        warnings = []
        if not alternatives:
            warnings.append("No rival/alternative explanations recorded yet.")
        if not predictions:
            warnings.append("No discriminating observable predictions recorded yet.")
        return {"ok": True, "id": cur.lastrowid, "status": "candidate", "warnings": warnings,
                "note": "A stored hypothesis remains a candidate, not an established result."}

    def create_study_design(self, project_id: Any, *, hypothesis_id: Any = None,
                            independent_variables: Any = None, dependent_variables: Any = None,
                            confounders: Any = None, controls: Any = None,
                            design_type: str = "", sampling_plan: str = "",
                            analysis_plan: str = "", stopping_rule: str = "") -> dict:
        try:
            project_id = int(project_id)
            independent = self._string_list(independent_variables, "independent_variables", required=True)
            dependent = self._string_list(dependent_variables, "dependent_variables", required=True)
            confounders = self._string_list(confounders, "confounders")
            controls = self._string_list(controls, "controls")
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        project = self.db.execute("SELECT 1 FROM research_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            return {"ok": False, "error": "research project not found"}
        if hypothesis_id not in (None, ""):
            try:
                hypothesis_id = int(hypothesis_id)
            except (TypeError, ValueError):
                return {"ok": False, "error": "hypothesis_id must be an integer"}
            if not self.db.execute("SELECT 1 FROM research_hypotheses WHERE id=? AND project_id=?", (hypothesis_id, project_id)).fetchone():
                return {"ok": False, "error": "hypothesis does not belong to this research project"}
        else:
            hypothesis_id = None
        design_type = _clean(design_type, 300)
        if not design_type:
            return {"ok": False, "error": "design_type is required"}
        now = int(time.time())
        cur = self.db.execute("""INSERT INTO research_designs
          (project_id,hypothesis_id,design_type,independent_variables,dependent_variables,
           confounders,controls,sampling_plan,analysis_plan,stopping_rule,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (project_id, hypothesis_id, design_type, _json_dump(independent), _json_dump(dependent),
           _json_dump(confounders), _json_dump(controls), _clean(sampling_plan, 2000),
           _clean(analysis_plan, 4000), _clean(stopping_rule, 1000), now))
        self.db.commit()
        warnings = []
        if not confounders:
            warnings.append("Potential confounders have not been enumerated.")
        if not controls:
            warnings.append("Controls/negative controls have not been specified.")
        if not sampling_plan:
            warnings.append("Sampling plan is missing.")
        if not analysis_plan:
            warnings.append("Analysis plan is missing.")
        return {"ok": True, "id": cur.lastrowid, "status": "draft", "warnings": warnings,
                "note": "This is a stored draft, not ethics approval, preregistration, or validation of design quality."}

    def add_observation(self, project_id: Any, observation: str, *, source: str,
                        hypothesis_id: Any = None, observed_at: Any = None,
                        data: dict | None = None, deviations: str = "") -> dict:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "project_id must be an integer"}
        if not self.db.execute("SELECT 1 FROM research_projects WHERE id=?", (project_id,)).fetchone():
            return {"ok": False, "error": "research project not found"}
        observation, source = _clean(observation, 4000), _clean(source, 1000)
        if not observation or not source:
            return {"ok": False, "error": "observation and source are required"}
        if data is not None and not isinstance(data, dict):
            return {"ok": False, "error": "data must be an object"}
        if hypothesis_id not in (None, ""):
            try:
                hypothesis_id = int(hypothesis_id)
            except (TypeError, ValueError):
                return {"ok": False, "error": "hypothesis_id must be an integer"}
            if not self.db.execute("SELECT 1 FROM research_hypotheses WHERE id=? AND project_id=?", (hypothesis_id, project_id)).fetchone():
                return {"ok": False, "error": "hypothesis does not belong to this research project"}
        else:
            hypothesis_id = None
        try:
            observed_text, _ = _parse_time(observed_at) if observed_at not in (None, "") else (datetime.now(timezone.utc).isoformat(), None)
        except ValueError as exc:
            return {"ok": False, "error": f"invalid observed_at: {exc}"}
        cur = self.db.execute("""INSERT INTO research_observations
          (project_id,hypothesis_id,observation,source,observed_at,data,deviations,created_at)
          VALUES(?,?,?,?,?,?,?,?)""",
          (project_id, hypothesis_id, observation, source, observed_text,
           _json_dump(data or {}), _clean(deviations, 2000), int(time.time())))
        self.db.commit()
        return {"ok": True, "id": cur.lastrowid, "recorded_as": "observation", "source": source}

    def research_report(self, project_id: Any) -> dict:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "project_id must be an integer"}
        row = self.db.execute("SELECT * FROM research_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "research project not found"}
        hypotheses = []
        for item in self.db.execute("SELECT * FROM research_hypotheses WHERE project_id=? ORDER BY created_at,id", (project_id,)).fetchall():
            value = dict(item)
            value["alternatives"] = _json_load(value.pop("alternatives"), [])
            value["predictions"] = _json_load(value.pop("predictions"), [])
            hypotheses.append(value)
        designs = []
        for item in self.db.execute("SELECT * FROM research_designs WHERE project_id=? ORDER BY created_at,id", (project_id,)).fetchall():
            value = dict(item)
            for key in ("independent_variables", "dependent_variables", "confounders", "controls"):
                value[key] = _json_load(value[key], [])
            designs.append(value)
        observations = []
        for item in self.db.execute("SELECT * FROM research_observations WHERE project_id=? ORDER BY observed_at,id", (project_id,)).fetchall():
            value = dict(item)
            value["data"] = _json_load(value["data"], {})
            observations.append(value)
        return {"ok": True, "project": dict(row), "hypotheses": hypotheses,
                "designs": designs, "observations": observations}

    @staticmethod
    def _numeric_values(values: Any) -> tuple[list[float], int]:
        if not isinstance(values, list):
            return [], 0
        result = []
        for value in values:
            try:
                number = float(value)
                if math.isfinite(number):
                    result.append(number)
            except (TypeError, ValueError):
                continue
        return result, len(values) - len(result)

    @staticmethod
    def _two_sided_t_p_value(value: float, degrees_freedom: float) -> float:
        return _two_sided_t_p_value(value, degrees_freedom)

    def welch_test(self, group_a: Any, group_b: Any, *, assumptions_confirmed: bool = False,
                   comparisons_planned: int = 1, alpha: float = 0.05) -> dict:
        a, dropped_a = self._numeric_values(group_a)
        b, dropped_b = self._numeric_values(group_b)
        if len(a) < 2 or len(b) < 2:
            return {"ok": False, "error": "each group must contain at least two finite numeric observations"}
        try:
            comparisons = max(1, min(1000, int(comparisons_planned)))
            alpha = float(alpha)
        except (TypeError, ValueError):
            return {"ok": False, "error": "comparisons_planned and alpha must be valid numbers"}
        if not math.isfinite(alpha) or not 0 < alpha < 1:
            return {"ok": False, "error": "alpha must be between 0 and 1"}

        mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
        variance_a, variance_b = statistics.variance(a), statistics.variance(b)
        difference = mean_a - mean_b
        base = {
            "ok": True, "test": "Welch two-sample t-test (two-sided)",
            "n": {"group_a": len(a), "group_b": len(b)},
            "dropped_nonfinite_or_nonnumeric": {"group_a": dropped_a, "group_b": dropped_b},
            "means": {"group_a": mean_a, "group_b": mean_b},
            "mean_difference_a_minus_b": difference,
            "sample_sd": {"group_a": math.sqrt(variance_a), "group_b": math.sqrt(variance_b)},
            "inferential_executed": False,
        }
        if not assumptions_confirmed:
            base["warning"] = (
                "No inferential test was run. Review whether observations are independent, "
                "the outcome is continuous, sampling/allocation supports the intended contrast, "
                "and neither group has severe skew/outliers; then explicitly attest the assumptions."
            )
            return base

        term_a, term_b = variance_a / len(a), variance_b / len(b)
        standard_error = math.sqrt(term_a + term_b)
        if standard_error <= 0 or not math.isfinite(standard_error):
            return {"ok": False, "error": "Welch test is undefined when both groups have zero within-group variance"}
        denominator = (term_a * term_a / (len(a) - 1)) + (term_b * term_b / (len(b) - 1))
        if denominator <= 0:
            return {"ok": False, "error": "Welch degrees of freedom could not be estimated"}
        degrees_freedom = (term_a + term_b) ** 2 / denominator
        statistic = difference / standard_error
        p_value = _two_sided_t_p_value(statistic, degrees_freedom)
        p_adjusted = min(1.0, p_value * comparisons)
        family_confidence = 1.0 - alpha / comparisons
        critical = _t_critical(degrees_freedom, family_confidence)
        margin = critical * standard_error
        pooled_variance = ((len(a) - 1) * variance_a + (len(b) - 1) * variance_b) / (len(a) + len(b) - 2)
        effect_size = difference / math.sqrt(pooled_variance) if pooled_variance > 0 else None
        base.update({
            "inferential_executed": True,
            "t_statistic": statistic,
            "degrees_freedom": degrees_freedom,
            "p_value": p_value,
            "p_value_adjusted": p_adjusted,
            "alpha": alpha,
            "confidence_level": family_confidence,
            "confidence_interval": [difference - margin, difference + margin],
            "cohens_d_pooled_sd": effect_size,
            "significant_after_adjustment": p_adjusted <= alpha,
            "multiplicity": {"method": "Bonferroni", "comparisons_planned": comparisons},
            "assumption_status": "caller-attested; independence, sampling design and distribution shape are not verified by this function",
            "diagnostics": {
                "variance": "Welch procedure does not assume equal population variances.",
                "independence": "Must be established by study design; not testable from the value arrays.",
                "distribution": "Inspect raw values/plots for outliers and severe skew; this function does not run a normality diagnostic.",
                "multiple_testing": "Bonferroni adjustment uses the declared comparisons_planned value; an incomplete family declaration understates multiplicity.",
            },
            "interpretation_note": "A p-value is not the probability that the hypothesis is true. Report the mean difference, effect size and interval; association alone does not establish causation.",
        })
        return base

    def capability_status(self) -> dict:
        return {
            "ok": True,
            "native_deterministic": [
                "claim provenance and source URI", "claim time-validity filtering",
                "exact-key conflict detection without automatic winner selection",
                "explicit claim revision with supersession history", "typed claim dependency links",
                "bounded dependency reconstruction", "research question/hypothesis/design/observation persistence",
                "Welch independent-samples t-test with caller-attested assumptions and Bonferroni adjustment",
            ],
            "model_guided_not_algorithmically_proven": [
                "abstract pattern recognition", "analogical transfer", "causal and counterfactual reasoning",
                "multi-perspective and adversarial analysis", "hypothesis generation and research-gap discovery",
            ],
            "not_provided_by_this_module": [
                "model-weight training", "self-supervised representation learning", "automatic causal identification",
                "spatial reasoning", "automatic literature completeness guarantees", "paired/repeated/categorical/survival tests",
            ],
        }


__all__ = ["KnowledgeWorkbench", "CLAIM_TYPES", "CLAIM_STATUSES", "RELATIONS"]
