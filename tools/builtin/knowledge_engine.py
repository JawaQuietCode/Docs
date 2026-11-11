"""Native, tenant-isolated knowledge and scientific-research workbench."""
from __future__ import annotations

from pathlib import Path
from memory.knowledge import KnowledgeWorkbench
from security.tenant_context import get_current
from tools.registry import register

_ACTIONS = [
    "status", "claim_add", "claim_search", "claim_reconcile", "claim_revise",
    "claim_link", "claim_provenance", "claim_reconstruct", "research_create",
    "hypothesis_add", "study_design", "observation_add", "research_report", "welch_test",
]
_PROPERTIES = {
    "action": {"type": "string", "enum": _ACTIONS},
    "subject": {"type": "string"}, "predicate": {"type": "string"},
    "object": {"type": "string"}, "claim_type": {"type": "string", "enum": ["observed", "reported", "hypothesis", "inference", "definition", "rule", "prediction", "mechanism"]},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "source_uri": {"type": "string"}, "source_label": {"type": "string"},
    "evidence": {"type": "string"}, "valid_from": {"type": "string"}, "valid_until": {"type": "string"},
    "metadata": {"type": "object"}, "query": {"type": "string"}, "as_of": {"type": "string"},
    "include_inactive": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    "claim_id": {"type": "integer"}, "new_object": {"type": "string"}, "reason": {"type": "string"},
    "source_id": {"type": "integer"}, "target_id": {"type": "integer"},
    "relation": {"type": "string", "enum": ["supports", "supported_by", "refutes", "contradicts", "depends_on", "derived_from", "analogous_to", "decomposed_into", "causal_hypothesis", "counterfactual_of", "supersedes", "same_as"]},
    "note": {"type": "string"}, "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
    "title": {"type": "string"}, "question": {"type": "string"}, "scope": {"type": "string"},
    "project_id": {"type": "integer"}, "hypothesis_id": {"type": "integer"}, "statement": {"type": "string"},
    "alternatives": {"type": "array", "items": {"type": "string"}},
    "predictions": {"type": "array", "items": {"type": "string"}},
    "hypothesis_type": {"type": "string", "enum": ["descriptive", "associational", "predictive", "causal", "mechanistic"]},
    "falsification_criteria": {"type": "string"},
    "independent_variables": {"type": "array", "items": {"type": "string"}},
    "dependent_variables": {"type": "array", "items": {"type": "string"}},
    "confounders": {"type": "array", "items": {"type": "string"}},
    "controls": {"type": "array", "items": {"type": "string"}},
    "design_type": {"type": "string"}, "sampling_plan": {"type": "string"},
    "analysis_plan": {"type": "string"}, "stopping_rule": {"type": "string"},
    "observation": {"type": "string"}, "source": {"type": "string"},
    "observed_at": {"type": "string"}, "data": {"type": "object"}, "deviations": {"type": "string"},
    "group_a": {"type": "array", "items": {"type": "number"}},
    "group_b": {"type": "array", "items": {"type": "number"}},
    "assumptions_confirmed": {"type": "boolean"},
    "comparisons_planned": {"type": "integer", "minimum": 1, "maximum": 1000},
    "alpha": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
}
SCHEMA = {
    "description": (
        "Native, tenant-local knowledge and scientific research workbench. Stores claims with evidence, source, "
        "epistemic type and time validity; detects exact-key temporal conflicts without choosing a winner; "
        "records explicit dependency/provenance links and revisions; persists research questions, rival hypotheses, "
        "predictions, variables, confounders, controls, study plans and observations. Supports a narrowly scoped "
        "Welch independent-samples t-test only with explicit caller attestation. This tool does not train model weights, "
        "prove causality, automatically decide truth, or replace scientific/ethical review."
    ),
    "parameters": {"type": "object", "properties": _PROPERTIES, "required": ["action"]},
}


def handler(args: dict | None, config: dict | None = None) -> dict:
    a = args if isinstance(args, dict) else {}
    ctx = get_current() or {}
    ctx = {**ctx, **((config or {}).get("context") or {})}
    root = ctx.get("tenant_root") or ctx.get("root")
    if not root:
        return {"ok": False, "error": "active tenant storage context is unavailable"}

    # Resolve under the tenant root; no caller-provided database path is accepted.
    db_path = Path(root).resolve() / "memory" / "knowledge.sqlite3"
    store = KnowledgeWorkbench(db_path)
    action = str(a.get("action") or "").strip().lower()
    try:
        if action == "status":
            return store.capability_status()
        if action == "claim_add":
            return store.add_claim(a.get("subject"), a.get("predicate"), a.get("object"),
                claim_type=a.get("claim_type", "reported"), confidence=a.get("confidence"),
                source_uri=a.get("source_uri", ""), source_label=a.get("source_label", ""),
                evidence=a.get("evidence", ""), valid_from=a.get("valid_from"),
                valid_until=a.get("valid_until"), metadata=a.get("metadata"))
        if action == "claim_search":
            rows = store.search_claims(a.get("query", ""), subject=a.get("subject", ""),
                predicate=a.get("predicate", ""), as_of=a.get("as_of"),
                include_inactive=bool(a.get("include_inactive", False)), limit=a.get("limit", 20))
            return {"ok": True, "count": len(rows), "claims": rows}
        if action == "claim_reconcile":
            return store.reconcile(a.get("subject", ""), a.get("predicate", ""))
        if action == "claim_revise":
            return store.revise_claim(a.get("claim_id"), new_object=a.get("new_object", ""),
                reason=a.get("reason", ""), source_uri=a.get("source_uri", ""),
                source_label=a.get("source_label", ""), evidence=a.get("evidence", ""),
                confidence=a.get("confidence"), valid_from=a.get("valid_from"), valid_until=a.get("valid_until"))
        if action == "claim_link":
            return store.link_claims(a.get("source_id"), a.get("target_id"), a.get("relation"), a.get("note", ""))
        if action == "claim_provenance":
            return store.claim_provenance(a.get("claim_id"))
        if action == "claim_reconstruct":
            return store.reconstruct(a.get("claim_id"), max_depth=a.get("max_depth", 5))
        if action == "research_create":
            return store.create_research(a.get("title", ""), a.get("question", ""), a.get("scope", ""))
        if action == "hypothesis_add":
            return store.add_hypothesis(a.get("project_id"), a.get("statement", ""),
                alternatives=a.get("alternatives"), predictions=a.get("predictions"),
                hypothesis_type=a.get("hypothesis_type", "associational"),
                falsification_criteria=a.get("falsification_criteria", ""))
        if action == "study_design":
            return store.create_study_design(a.get("project_id"), hypothesis_id=a.get("hypothesis_id"),
                independent_variables=a.get("independent_variables"), dependent_variables=a.get("dependent_variables"),
                confounders=a.get("confounders"), controls=a.get("controls"),
                design_type=a.get("design_type", ""), sampling_plan=a.get("sampling_plan", ""),
                analysis_plan=a.get("analysis_plan", ""), stopping_rule=a.get("stopping_rule", ""))
        if action == "observation_add":
            return store.add_observation(a.get("project_id"), a.get("observation", ""),
                source=a.get("source", ""), hypothesis_id=a.get("hypothesis_id"),
                observed_at=a.get("observed_at"), data=a.get("data"), deviations=a.get("deviations", ""))
        if action == "research_report":
            return store.research_report(a.get("project_id"))
        if action == "welch_test":
            return store.welch_test(a.get("group_a"), a.get("group_b"),
                assumptions_confirmed=bool(a.get("assumptions_confirmed", False)),
                comparisons_planned=a.get("comparisons_planned", 1), alpha=a.get("alpha", 0.05))
        return {"ok": False, "error": "unknown action"}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"invalid input: {exc}"}
    except Exception as exc:  # keep tool errors structured; details remain in local logs
        return {"ok": False, "error": f"knowledge operation failed: {type(exc).__name__}"}
    finally:
        store.close()


register("knowledge_engine", SCHEMA, handler, toolset="core", requires_owner=True, dangerous=False)
