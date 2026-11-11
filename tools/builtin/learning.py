"""Native, tenant-local agent learning controls; no plugin or remote trainer."""
from agent import cognitive
import re
from pathlib import Path
from security.tenant_context import get_current
from tools.registry import register

SCHEMA={"description":"Native agent learning and optimization. Actions: feedback (store explicit human preference score 1-5 for a tool or explanation style; score changes future contextual tool ordering/prompts), status (measured rewards/tool metrics and pending proposals), discover_tools (list already installed native tools respecting role allowlist), optimize_prompt (create a reviewable prompt candidate; runtime adaptation is also driven by intent and evidence), refine_skill (store a versioned skill-improvement proposal), approve_proposal (owner-confirmed supplemental runtime guidance for a named skill), compose (recommend a bounded tool sequence), understand (classify task intent), explain (adapt explanation), statistics (compute audited numeric summary), browser_plan (choose real browser vs public search/fetch). No model-weight training is claimed; online policy only.","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["feedback","status","discover_tools","optimize_prompt","refine_skill","approve_proposal","compose","understand","explain","statistics","browser_plan"]},"query":{"type":"string"},"target":{"type":"string","description":"Tool name or explanation style: brief, deep, example, steps"},"rating":{"type":"number","minimum":1,"maximum":5},"intent":{"type":"string"},"skill_name":{"type":"string"},"proposal_id":{"type":"integer"},"proposal":{"type":"string"},"rationale":{"type":"string"},"values":{"type":"array","items":{"type":"number"}},"limit":{"type":"integer","minimum":1,"maximum":100}},"required":["action"]}}

def _store(room):
    from memory.store import Store
    path=room.get("memory_db_path") or room.get("db_path") or (Path(room["tenant_root"])/"data"/"memory"/"kwee.db")
    return Store(str(path))

def handler(args,config=None):
    a=args or {}; action=str(a.get("action") or "").lower(); room=get_current() or {}
    ctx=(config or {}).get("context") or {}
    room={**room,**ctx}
    if not (room.get("memory_db_path") or room.get("tenant_root") or room.get("root")):return {"ok":False,"error":"no active tenant memory context"}
    query=str(a.get("query") or ""); store=_store(room)
    try:
        if action=="understand":return {"ok":True,**cognitive.understand(query)}
        if action=="explain":return {"ok":True,**cognitive.explanation_profile(query,store,room.get("chat_id",""))}
        if action=="compose":
            parsed=cognitive.understand(query);return {"ok":True,"intent":parsed,"sequence":cognitive.composition_plan(parsed)}
        if action=="browser_plan":
            parsed=cognitive.understand(query)
            browser="browser" in parsed["intents"]
            return {"ok":True,"use_browser":browser,"sequence":cognitive.composition_plan(parsed),"available_tools":[n for n in ("browser","web_search","web_fetch") if n in (room.get("tool_allow") or [])]}
        if action=="statistics":return cognitive.statistics_summary(a.get("values") or [])
        if action=="discover_tools":
            from tools import registry
            tools=registry.discover(); allowed=room.get("tool_allow")
            items=[{"name":t["name"],"description":(t.get("schema") or {}).get("description","")[:240],"toolset":t.get("toolset"),"dangerous":bool(t.get("dangerous"))} for t in tools.values() if allowed is None or t["name"] in allowed]
            return {"ok":True,"count":len(items),"tools":items}
        if action=="feedback":
            rating=float(a.get("rating") or 0)
            if not 1<=rating<=5:return {"ok":False,"error":"rating must be 1..5"}
            target=str(a.get("target") or "").strip()
            if not target:return {"ok":False,"error":"target is required"}
            intent=str(a.get("intent") or cognitive.understand(query)["intents"][0])
            if target.startswith("skill:"):
                skill_name=target.split(":",1)[1]
                from skills import loader
                if skill_name not in {s["name"] for s in loader.all_skills()["external"]}:
                    return {"ok":False,"error":"only a listed external skill can receive feedback"}
                signal=f"skill:{intent}";action_name=skill_name
            elif target in {t["name"] for t in __import__("tools.registry",fromlist=["all_tools"]).all_tools()}:
                signal=f"tool:{intent}";action_name=target
            else:
                signal="response:explanation";action_name=target if target.startswith("style:") else "style:"+target
            reward=(rating-3.0)/2.0
            event=store.record_learning_event(signal,action_name,reward,source="human",context=intent,chat_id=str(room.get("chat_id") or ""),evidence={"rating_1_to_5":rating})
            return {"ok":True,"event_id":event,"signal":signal,"action":action_name,"reward":reward,"message":"Preference recorded; only authorized tool order/explanation guidance can adapt. It does not train external model weights."}
        if action=="status":
            return {"ok":True,"learning":"local contextual policy (not model-weight training)","feedback":store.learning_stats(limit=a.get("limit",20)),"tools":store.tool_stats(limit=a.get("limit",20)),"proposals":store.learning_proposals(limit=a.get("limit",20)),"rlaif":"heuristic-v1 observable response rubric"}
        if action=="refine_skill":
            name=str(a.get("skill_name") or "").strip(); proposal=str(a.get("proposal") or "").strip()
            if not name or not proposal:return {"ok":False,"error":"skill_name and proposal required"}
            evidence={"source":"owner_or_agent_review","approval_required_before_skill_file_change":True,"tenant":room.get("tenant")}
            proposal_id=store.save_learning_proposal("skill",name,proposal,a.get("rationale", ""),evidence)
            return {"ok":True,"proposal_id":proposal_id,"status":"proposed_not_applied","skill":name,"note":"Proposal persisted for review; skill files remain unchanged."}
        if action=="approve_proposal":
            if not bool(room.get("is_primary")) or not bool(room.get("owner_confirmed")):
                return {"ok":False,"error":"only the primary owner can activate a skill refinement, and only after explicit confirmation in the current turn"}
            proposal_id=int(a.get("proposal_id") or 0)
            request=str(room.get("owner_request_text") or "")
            if proposal_id<=0 or not re.search(r"\b(approve|setuju|setujui|terapkan)\b",request,re.I) or str(proposal_id) not in request:
                return {"ok":False,"error":"for safety, send an explicit owner message such as 'setuju proposal 12' with the exact proposal ID"}
            result=store.approve_skill_refinement(proposal_id)
            if result.get("ok"):result["note"]="Approved runtime guidance will be appended only when this skill is selected; source skill file is unchanged."
            return result
        if action=="optimize_prompt":
            scope=str(a.get("target") or "task").strip(); candidate=cognitive.adaptive_guidance(query,store,room.get("chat_id",""))
            rationale=str(a.get("rationale") or "Contextual instruction candidate based on current intent and recorded feedback")
            pid=store.save_learning_proposal("prompt",scope,candidate,rationale,{"intent":cognitive.understand(query),"policy":"candidate-only; active runtime guidance is generated per turn"})
            return {"ok":True,"proposal_id":pid,"scope":scope,"status":"candidate_saved","candidate":candidate,"note":"Runtime prompt adaptation is intent/feedback driven; this candidate is stored for review and does not overwrite internal prompt files."}
        return {"ok":False,"error":"unknown action"}
    finally:store.conn.close()

register("agent_learning",SCHEMA,handler,toolset="core")
