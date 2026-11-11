# Kwee v2.3 — Native Agent Learning

This release adds `agent.cognitive` plus the tenant-local learning/event store and the built-in `agent_learning` function tool. The production turn path now classifies task signals before tool ordering, injects bounded intent/explanation/composition guidance into the system prompt, captures a heuristic response-quality signal after each answer, and uses explicit human feedback to adjust subsequent authorized tool and relevant skill rankings. User prompt content is not copied into learning-event telemetry; the event stores a chat-scoped identifier and intent label.

`agent_learning` exposes status, explicit feedback, native registry discovery, intent understanding, browser/tool composition guidance, explain-profile adaptation, statistical summary, reviewable prompt candidates, and skill-refinement proposals. The primary owner can explicitly approve a proposal by naming its ID; approved text is injected as supplemental runtime guidance only when the named skill is selected. Prompt and skill source files are never silently mutated. No external model weights are trained. The RLAIF-labelled signal is a transparent heuristic rubric for observable response properties, not an accuracy oracle or a learned reward model.

## Double-check results

- Python: `python3 -m unittest discover -s tests -v` — **112 passed, 0 failed**.
- JavaScript: `node --test tests/*.test.js` — **66 passed, 0 failed, 1 skipped**.
- `bash -n start.sh`, Python compilation of changed modules, and `node --check transport/api/airich.js` — passed.
- Registry discovery confirmed `agent_learning` is available as a native tool.
- E2E test invokes `agent_learning` through the production `run_turn` tool loop and checks its result returns to the model.
