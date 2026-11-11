# Native Agent Learning & Optimization

This implementation is a local, auditable **online policy layer** for the native Python agent. It does not train or modify model weights, and it does not claim that a heuristic score is a factual accuracy judge. A hosted LLM's parameters remain unchanged.

## What adapts during real turns

- **Human feedback (RLHF-style preference signal):** the `agent_learning.feedback` tool records an explicit 1–5 rating against an allowed tool or explanation style. Ratings are mapped to [-1, 1] and stored per tenant in the existing SQLite memory database. Contextual tool preferences feed a conservative, shrinkage-bounded reward into the real `rank_tools()` ordering on subsequent turns; tool permissions are unchanged. Explanation preferences influence later response guidance only after at least two positive observations.
- **RLAIF-style measured reward:** after each native or external-engine reply, `agent.main` records a versioned, deterministic rubric score for non-empty output, query-term coverage, research URL evidence, and statistical caveats when applicable. This is a heuristic signal, not a truth score. Persistently weak signals for an intent add a verification reminder to later system prompts.
- **Self-optimization / learning-strategy optimization:** actual tool call success/latency metrics already feed the ranking; new preference rewards supplement them. Guidance is generated per turn from the query intent, task complexity, explicit style instructions, and accumulated evidence.
- **Prompt optimization:** `optimize_prompt` saves a candidate and rationale in tenant SQLite for inspection. The live prompt uses generated per-turn guidance; base internal prompt files are not silently overwritten.
- **Skill refinement:** `refine_skill` stores reviewable proposals and evidence in tenant SQLite. `approve_proposal` activates a supplemental runtime note only after the primary owner sends an explicit message containing the exact proposal ID (for example, “setuju proposal 12”). The note is injected only when that external skill is selected; the deployed `SKILL.md` file is never rewritten. Basic unsafe-instruction patterns are rejected at approval.
- **Tool discovery and selection:** `discover_tools` reuses the real built-in registry and filters results to the current tool allowlist. Per-turn selection ranks authorized tools by task intent, measured reliability/latency, and explicit preference reward. No dependency installation or permission escalation is performed by discovery.
- **Browser intelligence and composition:** intent classification distinguishes dynamic browser interactions from public research, then injects a bounded browser/search/fetch sequence. Claims about actions/screenshots must still be grounded in confirmed browser results.
- **Natural-language understanding / explanation adaptation:** lightweight bilingual keyword signals detect common research, coding, browser, statistical, memory, comparison, multi-step, and explanation-depth cues. The main LLM remains responsible for semantic understanding; these signals guide tool ordering and format rather than replacing it.
- **Statistical reasoning:** the native `statistics` action reports finite-value n, mean, median, sample standard deviation, min/max and a normal-approximation 95% interval when n>1, with a caveat for small/non-normal data. It does not imply causal inference.

## Native interface

The built-in tool is `agent_learning`, discovered by `tools.registry.discover()` and called through normal agent function-calling. Supported actions: `feedback`, `status`, `discover_tools`, `optimize_prompt`, `refine_skill`, `approve_proposal`, `compose`, `understand`, `explain`, `statistics`, `browser_plan`.

SQLite tables added by migration: `learning_events`, `skill_refinements`, and `prompt_candidates`. Existing `tool_metrics` remains the measured execution baseline. Data remains within the active tenant database.

## Validation

`tests/test_cognitive_learning.py` covers intent and guidance, preference-driven ranking, RLAIF rubric signals, statistical summary, and execution of `agent_learning` through the actual `run_turn` tool loop. Full Python and JavaScript suite results are recorded in the release notes.
