"""Practical, auditable cognitive adaptations for the native Kwee agent.

This is online policy learning, not model-weight training. Human preference
signals and conservative RLAIF rubric rewards tune tool ordering and response
instructions using tenant-local evidence. No prompt or skill file is silently
rewritten.
"""
from __future__ import annotations
import math, re, statistics

INTENTS = {
    "research": re.compile(r"\b(research|latest|today|news|source|citation|report|riset|terbaru|hari ini|berita|sumber|referensi|laporan)\b",re.I),
    "coding": re.compile(r"\b(code|coding|repo|github|bug|test|debug|implement|architecture|kode|program|debug|fitur|aplikasi)\b",re.I),
    "browser": re.compile(r"\b(open|click|browse|browser|website|web page|screenshot|login|dashboard|buka|klik|peramban|situs|halaman|screenshot|masuk)\b",re.I),
    "statistics": re.compile(r"\b(statistic|statistics|probability|average|median|variance|dataset|trend|correlation|statistik|peluang|rata-rata|median|varians|data|tren|korelasi|uji statistik)\b",re.I),
    "explain": re.compile(r"\b(explain|why|how does|teach|eli5|jelaskan|mengapa|bagaimana|ajari|terangkan)\b",re.I),
    "memory": re.compile(r"\b(remember|recall|memory|history|previous|decision|preference|ingat|memori|riwayat|sebelumnya|keputusan)\b",re.I),
}

# These are routing signals for careful model-guided work, not claims that an
# LLM's internal reasoning has been objectively measured or proven.
REASONING_MODES = {
    "abstract_pattern": re.compile(r"\b(abstract pattern|abstract structure|universal structure|pola abstrak|struktur universal|struktur umum)\b", re.I),
    "knowledge_decomposition": re.compile(r"\b(decompose|decomposition|break down|pecah(?:kan)?|uraikan|dekomposisi)\b", re.I),
    "generalization": re.compile(r"\b(generaliz(?:e|ation)|generalise|spesialisasi|specializ(?:e|ation)|specialise|generalisasi)\b", re.I),
    "causal": re.compile(r"\b(causal|causality|cause and effect|sebab-akibat|hubungan sebab|kausal)\b", re.I),
    "counterfactual": re.compile(r"\b(counterfactual|what if|suppose that|seandainya|andaikan|bagaimana jika)\b", re.I),
    "temporal": re.compile(r"\b(temporal|timeline|chronology|sequence of events|time series|kronologi|linimasa|urutan waktu|perubahan waktu)\b", re.I),
    "spatial": re.compile(r"\b(spatial|spatial reasoning|spatial relation|hubungan ruang|penalaran ruang|tata ruang)\b", re.I),
    "adversarial": re.compile(r"\b(adversarial|devil['’]s advocate|counterargument|counter-argument|kontra[- ]argumen|rebuttal|bantahan|sanggahan|argumen tandingan)\b", re.I),
    "multi_perspective": re.compile(r"\b(multi[- ]perspective|multiple perspectives|different viewpoints|pro.?kontra|beberapa perspektif|berbagai sudut pandang|sudut pandang)\b", re.I),
    "analogy": re.compile(r"\b(analogy|analogical|analogi|perbandingan lintas domain)\b", re.I),
    "knowledge_reconciliation": re.compile(r"\b(reconcil(?:e|iation)|contradiction|conflicting claims|knowledge revision|rekonsiliasi|kontradiksi|bertentangan|informasi berlawanan)\b", re.I),
    "associative_retrieval": re.compile(r"\b(associative retrieval|related concepts|concept association|keterkaitan konsep|asosiasi konsep)\b", re.I),
    "scientific": re.compile(r"\b(scientific|research question|hypothesis|experiment(?:al)?|variable|replication|research gap|ilmiah|pertanyaan penelitian|hipotesis|eksperimen|variabel|replikasi|celah penelitian)\b", re.I),
    "capability_audit": re.compile(r"\b(capability assessment|self-assessment|confidence calibration|what can you verify|batas kemampuan|ukur kemampuan|kalibrasi keyakinan)\b", re.I),
}

def understand(query: str) -> dict:
    text=str(query or "").strip(); labels=[k for k,p in INTENTS.items() if p.search(text)]
    if not labels: labels=["conversation"]
    compare=bool(re.search(r"\b(compare|versus|vs\.?|bandingkan|dibanding)\b",text,re.I))
    multi=bool(re.search(r"\b(step[- ]by[- ]step|multi[- ]step|workflow|langkah|tahapan|rencana)\b",text,re.I))
    detail="brief" if re.search(r"\b(short|brief|concise|singkat|ringkas|tl;dr)\b",text,re.I) else "deep" if re.search(r"\b(detailed|comprehensive|deep|detail|lengkap|mendalam)\b",text,re.I) else "adaptive"
    language="id" if re.search(r"\b(apa|bagaimana|kenapa|tolong|yang|untuk|dengan|saya|kamu|ini|itu|hitung|buka|lihat|buat|analisis|bandingkan|ringkas|jelaskan|carikan|terangkan)\b",text,re.I) else "en"
    reasoning_modes=[name for name, pattern in REASONING_MODES.items() if pattern.search(text)]
    return {"intents":labels,"reasoning_modes":reasoning_modes,"compare":compare,"multi_step":multi,"explanation_depth":detail,"language_hint":language}

def explanation_profile(query: str, store=None, chat_id: str="") -> dict:
    parsed=understand(query); preference=parsed["explanation_depth"]
    if preference=="adaptive" and store:
        try:
            rows=store.learning_stats("response:explanation",limit=20)
            human=[r for r in rows if r["human_n"]]
            supported=[r for r in human if int(r["n"])>=2 and float(r["mean_reward"])>=.2]
            if supported:
                preference=max(supported,key=lambda r:r["mean_reward"])["action"].replace("style:","")
        except Exception: pass
    return {"depth":preference,"language":parsed["language_hint"],"format":"comparison_table" if parsed["compare"] else "steps" if parsed["multi_step"] else "prose"}

def composition_plan(parsed: dict) -> list[str]:
    intents=set(parsed.get("intents") or [])
    if "browser" in intents:return ["browser: inspect the target page and capture evidence", "web_fetch: read public page content if accessible", "summarize verified observations and limitations"]
    if "research" in intents:return ["web_search: discover primary sources", "web_fetch: open and verify the strongest sources", "research_journal: preserve citations/provenance where appropriate"]
    if "coding" in intents:return ["files/search: inspect relevant implementation and tests", "compose tools only from the active role allowlist", "run targeted tests, then regression tests"]
    if "statistics" in intents:return ["validate units, sample size, missing values, and assumptions", "compute descriptive/inferential statistics with explicit method", "report uncertainty and practical meaning"]
    if parsed.get("reasoning_modes") and any(m in parsed["reasoning_modes"] for m in ("scientific", "knowledge_reconciliation", "causal", "counterfactual")):
        return ["knowledge_engine: inspect provenance, temporal validity, rivals and explicit dependencies", "knowledge_engine: preserve questions, candidate hypotheses, predictions and study variables", "verify observations and method assumptions before any inferential claim"]
    if parsed.get("multi_step"):return ["decompose into bounded steps", "use the narrowest authorized tools", "verify each material result before summarizing"]
    return ["answer directly; use tools only when evidence or an action is needed"]

def adaptive_guidance(query: str, store=None, chat_id: str="") -> str:
    parsed=understand(query); profile=explanation_profile(query,store,chat_id)
    lines=["NATIVE ADAPTATION (evidence-based policy; never override safety/role permissions):",
           f"Intent signals: {', '.join(parsed['intents'])}; explanation={profile['depth']}; format={profile['format']}.",
           "Tool composition: " + " → ".join(composition_plan(parsed))]
    if "statistics" in parsed["intents"]:
        lines.append("Statistical reasoning: show n, units, missing-data handling, method, uncertainty/interval when justified; distinguish association from causation and avoid false precision.")
    mode_guidance = {
        "abstract_pattern": "Abstract-pattern work: identify the shared structure, map where the analogy stops, and label a proposed abstraction as a candidate—not proof.",
        "knowledge_decomposition": "Decomposition: separate entities, claims, assumptions, dependencies and unresolved questions; preserve source links when recombining them.",
        "generalization": "Generalization: state the observed scope, boundary conditions, counterexamples and what evidence would be needed before extending the rule.",
        "causal": "Causal analysis: correlation does not establish causation. Define the estimand, time order, intervention/comparator, confounders, selection and measurement assumptions; call it causal only when the design supports that claim.",
        "counterfactual": "Counterfactual analysis: state the factual baseline, intervention/change, structural assumptions and unobserved potential outcome; label the result as model-dependent, not observed fact.",
        "temporal": "Temporal reasoning: preserve event time separately from record time where possible; check validity intervals, ordering, duration and whether facts overlap in time.",
        "spatial": "Spatial reasoning: declare coordinate/reference frame, scale, units, topology and missing geometry; do not infer exact location or metric from a text label alone.",
        "adversarial": "Adversarial review: construct the strongest plausible rival and counterargument, identify falsifying evidence and distinguish a rebuttal from disproof.",
        "multi_perspective": "Multi-perspective review: state whose perspective is represented, its evidence and its blind spots; do not manufacture symmetry where evidence is asymmetric.",
        "analogy": "Analogical transfer: map source and target concepts one-to-one, state the invariant structure and explicitly list disanalogies; mapping is not proof.",
        "knowledge_reconciliation": "Knowledge reconciliation: compare provenance, epistemic type, confidence and validity intervals; flag conflicts without silently selecting a winner; preserve superseded claims.",
        "associative_retrieval": "Associative retrieval: follow explicit concept/entity links and show why each retrieved item is related; similarity is not entailment.",
        "scientific": "Scientific workflow: separate observation, research question, candidate hypothesis, rival explanation, prediction, operational variable, analysis plan and actual evidence. Identify a research gap only within a documented search boundary.",
        "capability_audit": "Capability self-audit: distinguish implemented functions, model-guided tasks and unsupported claims; report test evidence, sample limits and uncertainty rather than self-certifying ability.",
    }
    for mode in parsed.get("reasoning_modes", []):
        if mode in mode_guidance:
            lines.append(mode_guidance[mode])
    if "browser" in parsed["intents"]:
        lines.append("Browser intelligence: use the real browser for dynamic UI interactions; use web search/fetch for public evidence. Never claim a screenshot/action succeeded unless the browser tool confirms it.")
    if profile["depth"]=="brief": lines.append("Adapt the explanation to the user's explicit concise preference; retain essential caveats.")
    elif profile["depth"]=="deep": lines.append("Adapt the explanation to the requested depth; make reasoning steps and assumptions clear without exposing hidden chain-of-thought.")
    else: lines.append("Adapt length to task complexity; lead with the result, define unfamiliar terms, and use a short example when it improves comprehension.")
    try:
        if store:
            prefs=store.learning_stats("response:explanation",limit=20)
            if prefs: lines.append("Measured explanation preference evidence: " + "; ".join(f"{r['action']} reward={r['mean_reward']:.2f} n={r['n']} human={r['human_n']}" for r in prefs[:2]))
            quality=store.learning_stats(f"response_quality:{parsed['intents'][0]}",limit=10)
            quality_n=sum(int(r.get("n") or 0) for r in quality)
            if quality_n>=3 and quality and quality[0]["mean_reward"]<0:
                lines.append("Recent observable quality checks for this task class were weak; verify completeness, requested format, and evidence before answering. This heuristic is not a truth/accuracy guarantee.")
    except Exception: pass
    return "\n".join(lines)

def rlaif_score(query: str, answer: str) -> tuple[float,dict]:
    """Conservative observable rubric; not a claim of model-judge quality."""
    q=str(query or ""); a=str(answer or "").strip(); reasons={}
    if not a:return -1.0,{"empty":True,"rubric":"heuristic-v1"}
    qterms=set(re.findall(r"[\wÀ-ÿ]{3,}",q.lower())); aterms=set(re.findall(r"[\wÀ-ÿ]{3,}",a.lower()))
    overlap=len(qterms & aterms)/max(1,len(qterms)); reasons["query_term_coverage"]=round(overlap,3)
    score=0.0
    score += 0.25 if len(a)>=30 else -0.15
    score += min(.30,overlap*.45)
    has_sources=bool(re.search(r"https?://|\[[^\]]+\]\(https?://",a))
    research=bool(INTENTS["research"].search(q))
    if research: score += .15 if has_sources else -.15
    has_qualifier=bool(re.search(r"\b(estimate|approximately|uncertain|assumption|sample|CI|confidence|perkiraan|asumsi|sampel|interval|ketidakpastian|tidak pasti)\b",a,re.I))
    statistical=bool(INTENTS["statistics"].search(q))
    if statistical: score += .12 if has_qualifier else -.10
    reasons.update({"has_citations_or_urls":has_sources,"research_query":research,"statistical_caveat":has_qualifier if statistical else None,"rubric":"heuristic-v1"})
    return max(-1,min(1,score)),reasons

def statistics_summary(values) -> dict:
    nums=[]
    for value in (values or []):
        try:
            x=float(value)
            if math.isfinite(x):nums.append(x)
        except (TypeError,ValueError):continue
    n=len(nums)
    if not n:return {"ok":False,"error":"no finite numeric observations"}
    mean=statistics.fmean(nums); sd=statistics.stdev(nums) if n>1 else 0.0
    margin=1.96*sd/math.sqrt(n) if n>1 else None
    ordered=sorted(nums)
    return {"ok":True,"n":n,"mean":mean,"median":statistics.median(nums),"sample_sd":sd,
            "min":ordered[0],"max":ordered[-1],"ci95_normal":[mean-margin,mean+margin] if margin is not None else None,
            "ci_note":"normal approximation; small samples/non-normal data require a suitable method" if n>1 else "interval unavailable for n=1",
            "dropped_nonfinite_or_nonnumeric":len(list(values or []))-n}

def tool_reward_bonus(store, intent: str, tool_name: str) -> float:
    """Small bounded contextual-bandit signal, backed only by explicit feedback."""
    if not store:return 0.0
    try:
        rows=store.learning_stats(f"tool:{intent}",limit=100)
        row=next((r for r in rows if r["action"]==tool_name),None)
        if not row:return 0.0
        # Conservative shrinkage toward zero prevents one vote dominating.
        return max(-1.0,min(1.0,float(row["mean_reward"])*int(row["n"])/(int(row["n"])+4))) * .8
    except Exception:return 0.0

__all__=["REASONING_MODES","understand","explanation_profile","composition_plan","adaptive_guidance","rlaif_score","statistics_summary","tool_reward_bonus"]
