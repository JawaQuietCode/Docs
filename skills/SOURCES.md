# External system skill sources

The system skill installer is `scripts/install-system-skills.sh`.

- Agent-Reach: https://github.com/Panniantong/Agent-Reach
- Scientific Agent Skills: https://github.com/K-Dense-AI/scientific-agent-skills
- NVIDIA Skills: https://github.com/NVIDIA/skills (reference catalogue; install selectively when relevant)

Third-party skill instructions and scripts are untrusted supply-chain inputs. Review source changes before upgrading; the installer records resolved commit SHAs in `skills/external-sources.lock` and installs skills as on-demand external skills, not as privileged system instructions.

## Anti-slop design, accessibility and backend quality research (2026-11-01)

Seven-plus real Agent Skills sources were searched; the following entries have catalog records and source metadata. The always-on project skills are concise, locally authored syntheses rather than verbatim copies. NVIDIA's catalog was checked as requested; its own finder says not to invent or activate an NVIDIA-specific product skill for a generic frontend task, so no false NVIDIA design skill name is claimed.

1. NVIDIA Agent Skills catalogue — https://github.com/NVIDIA/skills (portable skill format and official catalog).
2. Agent Skills specification — https://agentskills.io/specification (portable `SKILL.md`, metadata, progressive disclosure).
3. Design Taste — https://github.com/h3nryprod01/design-taste (anti-generic visual direction, critique/refine loop, typography, layout, motion and accessibility; catalogued discovery-only, not installed).
4. Neej Frontend Craft — https://github.com/nchemb/neej-frontend-craft (production-grade visual frontend workflow; catalogued discovery-only, not installed).
5. Microsoft Frontend Design Review — https://github.com/microsoft/skills/tree/main/.github/skills/frontend-design-review (frontend craft, responsive design, theme and UI review; pinned catalog entry).
6. Addy Osmani Web Quality Accessibility — https://github.com/addyosmani/web-quality-skills/tree/main/skills/accessibility (WCAG 2.2, POUR, contrast, semantic HTML, keyboard, focus, reduced motion and manual verification; pinned catalog entry).
7. JPeetz Accessibility Compliance Audit — https://github.com/JPeetz/agent-skills/tree/main/skills/frontend/accessibility-compliance-audit (WCAG 2.2 AA, ARIA, focus, contrast and screen-reader workflow; pinned catalog entry).
8. MrGoonie Backend Development — https://github.com/mrgoonie/claudekit-skills/tree/main/.claude/skills/backend-development (FastAPI, databases, API security, testing and operations; pinned catalog entry).
9. Strix API Security Testing — https://github.com/usestrix/strix/tree/main/skills/api-security-testing (OWASP API risks, auth, object-level authorization and rate/resource abuse; pinned catalog entry).

The local `anti-slop-design` contract combines the design critique and WCAG requirements. `backend-reliability` applies the backend and API security principles to SQLite transactions, scoped read-only WebSocket tokens, media processing and tests. These source instructions remain untrusted upstream data; no upstream script or skill was silently executed.

Implementation note: WhatsApp AI Rich HTML is a constrained embedded WebView. Use self-contained semantic HTML and inline CSS; external font/CDN dependencies are not a reliable runtime contract. WebSocket synchronization requires a publicly reachable HTTPS/WSS reverse proxy and remains separate from the Baileys transport.

## Sumber sistem tambahan (2026-11-02)

- Claude Scientific Writer — https://github.com/K-Dense-AI/claude-scientific-writer (dipasang sebagai paket on-demand dengan prefix `writer-`).
- Andrej Karpathy Skills — https://github.com/multica-ai/andrej-karpathy-skills (dipasang sebagai paket on-demand dengan prefix `karpathy-`; TIDAK disuntik otomatis ke persona, rules, DB, atau memori. Tinjau isinya dulu, lalu tambahkan secara eksplisit).
