# Kwee Agentic — autonomous VPS build

> **v0.4.0** — native AIFS tutor skills, Kwee-native peer learning, and best-effort FCC CLI bootstrap at startup. FCC provider setup is separate; Kwee peers continue to use the existing Kwee LLM configuration. Detail: [docs/AGENT-LEARNING.md](docs/AGENT-LEARNING.md).

This is the complete project-root source bundle. The ZIP is intentionally laid out with project files at its root; it does not add a wrapper directory.

## Deploy — one command

1. Extract the ZIP into the server's working directory.
2. Run **`bash start.sh`**.
3. On first launch, Kwee creates a private `.env` and opens a guided setup for the OpenAI-compatible API URL, hidden API key, model ID, and owner WhatsApp number. A fresh GitHub read-only token is optional. Values are saved locally with permission `0600`; existing `.env` files are never overwritten by the template.
4. The first launch installs missing Node/Python project dependencies and the optional intelligence adapters once, then builds a local Graphify code graph when its CLI is available. Later starts reuse the installed packages and graph. Scan the WhatsApp QR code once if the session is not paired yet; the session is kept in the local data directory.

No separate `setup.sh`, `setup-intelligence.sh`, or graph-build command is required for the normal path. `bash start.sh` is both the bootstrap and runtime entrypoint. The base server image must provide Python 3, Node.js 20+ and npm; an internet connection is needed on first install. Mem0/Qdrant remains opt-in (`KWEE_MEM0_ENABLED=true` plus a configured provider), and GitHub MCP is skipped when Docker or a fresh token is unavailable; neither blocks native chat.

For external Agent-Reach and Scientific Agent Skills, the optional `bash scripts/install-system-skills.sh` fetches their repositories into `skills/external/` and records resolved commit SHAs in `skills/external-sources.lock`. For live AFK list updates, configure a public WSS endpoint and reverse-proxy `/ws/afk`; without WSS, the card still updates elapsed time locally.

Set `INSTALL_AGENT_REACH_CLI=1` when running the skill installer only if you also want its Python CLI installed into the local user environment. Skill instructions are installed without silently mutating system Python.

## Runtime behavior

- Routine owner tasks are autonomous; high-impact/destructive operations retain tool-level safeguards.
- `llm.requestIntervalSeconds` defaults to 10 seconds. Override with `LLM_REQUEST_INTERVAL_SECONDS`; use 0 only if your provider quota allows it.
- `!skills` shows the last prompt's actually injected skills. The snapshot is process-local and updates whenever a new system prompt is built.
- AFK sessions use `data/memory/kwee.db`; media metadata uses the active tenant SQLite database (or the default Kwee database when no tenant context exists). Both are created/migrated at runtime and deliberately not bundled. Media files are stored under the active tenant media directory (default `data/media/assets/`).
- Visual media is opt-in: the AI only receives image/video/sticker content after a user replies with a prompt or mentions Kwee with a caption. Video Vision needs `ffmpeg` and `ffprobe`; `setup.sh` reports whether they are available.
- AFK: `!afk <reason>`, `!afk -ls` / `!afk list`, and `!afk off`. Rich cards use the existing AI Rich HTML primitive with inline CSS/JS and no external font or stylesheet.
- Intro media is disabled because the supplied `assets/kwee-intro.mp4` was an empty file. Add a valid intro video before enabling it in `config/default.yaml`.
- Public AI is enabled for direct messages only (`publicChat` in `config/default.yaml`). `.env` owners have unlimited credit; each other WhatsApp identity gets a separate tenant and a rolling 1,000,000-token / 36-hour ledger. The Python agent requires the transport `ACTION_TOKEN`; `setup.sh`/`start.sh` create one if missing. Public users can use `.menu`, `/credit`, `/models`, `/model <configured-name>`, and `!upscale`; group access and owner-only plugins/tools remain closed.
- Prompt history for public users is trimmed conservatively to a 128K target (UTF-8 byte cap with output reserve); this is a safety ceiling, not a guarantee that every selected provider/model supports a 128K context window. Provider usage is charged when reported; if a gateway omits usage, the fallback byte estimate intentionally errs high.
- `/addown 62…` is a private-chat, two-step owner-only operation. Confirm with `/addown confirm 62…` within five minutes. It grants full owner privileges and stores the extra number in the private runtime file `data/owners.json`, not `.env`; protect that file like an owner allowlist.
- `!upscale` accepts an attached/replied image up to 20 MB and 20 megapixels. It uses `realesrgan-ncnn-vulkan` with the `realesrgan-x4plus` model when binary and model weights are installed; otherwise it falls back to FFmpeg Lanczos 2× plus an unsharp mask. The plugin does not install the model/binary automatically; keep `ffmpeg` and `ffprobe` available for fallback and validation.
- The internal `kwee-baileys-airich` skill documents this repository’s Baileys fork, LID/PN handling, plugin boundaries, and rich GenAI response builders. Verify the installed `@itsliaaa/baileys` version before changing message/protobuf fields.
- Group `!hidetag` / `!tagall` now require the sender to be a listed group admin and use the participant JID exactly as returned by group metadata. Owner-only `!mentionjid` displays full typed PN/LID identities instead of ambiguous bare digits; matching never collapses PN and LID numeric spaces.

## Verify

```bash
python3 -m unittest discover -s tests -v
bash tests/test_rtk_setup.sh
python3 -m compileall -q agent skills memory media tools security scheduler
node --test tests/*.test.js
bash -n setup.sh start.sh scripts/setup_rtk.sh tests/test_rtk_setup.sh
node --check transport/api/ui.js
node --check transport/commands.js
node --check transport/router.js
node --check transport/engine.js
node --check transport/wa.js
```

See `docs/AUTONOMOUS-24-7.md`, `docs/AFK-MEDIA-AND-SKILLS.md`, and `docs/BUILD-NOTES.md` for operational details.
See `docs/BUILD-VERIFICATION.md` for the test record and external-runtime checks that were not possible in this build environment.


## Native peer team and skill loading

- `subagent` now exposes peer, researcher, architect, critic, mentor, and builder roles. Parallel `tasks` form a small team; every worker uses Kwee's existing provider URL, model, fallback list, retry policy, and runtime credential. No Claude API key or additional environment variable is introduced.
- Successful peer outputs are stored as `peer-learning` journal notes in the current tenant's shared library, tagged `unverified` with the peer role. Later peers can read relevant notes as untrusted context; they are not silently promoted into trusted facts or system instructions.
- Python skill discovery now finds nested `SKILL.md` packages at arbitrary depth while refusing symlink escapes. A skill is counted as injected only after its selected heading is found in the final system prompt. OpenHands follows the same prepared-then-injected status transition.
- The `ai-engineering-from-scratch` curriculum is used as the design reference for native agent loops, role-based teams, memory, and eval-driven work. `free-claude-code` is not vendored: it is a separate local protocol proxy with optional chat bridges, not a peer-community runtime. See [docs/NATIVE-AGENT-TEAM.md](docs/NATIVE-AGENT-TEAM.md).

## Agent progress, HTML preview, and RTK

- Live progress is one AI Rich message containing a single HTML activity surface; the message is edited in place on each refresh. It deliberately does not emit WhatsApp's native `progress`/thinking indicator, whose rendering is inconsistent across clients. The self-contained HTML uses a feminine Velvet Noir editorial identity (plum ink, blush paper, rose and champagne accents), with a moving progress sheen, elapsed time, and a chronological tool timeline. Real `tool_start` events are shown once and updated when the tool finishes; phase markers such as thinking are not faked as tool steps. Skills are displayed as their own stage. The estimated percentage is monotonic and stays below 100% until the turn closes. If an HTML-card edit fails, the existing delivery fallback keeps the final answer from being lost.
- On completion, a deep-plum closing dossier replaces the live workroom panel and is placed **before** the AI response in the same card. The HTML report is built by the transport (`transport/api/progress-html.js`) from recorded tool events, so it works with OpenHands and the Python core without relying on `activity_html`. Small HTML results (up to 160 KiB) are embedded as a second HTML preview in that same final card, followed by the plain AI response. Larger HTML files and non-HTML assets remain separate attachments so the rich card does not become oversized. Runs shorter than `agent.progressAfterMs` stay on the normal reply path. `agent.progressHtmlMode: file` remains available as an explicit standalone-report mode; disable the progress report with `agent.progressHtml: false`.
- HTML inside a model answer (a ```html fence, or a complete HTML document) is extracted by `transport/api/htmlfile.js`. When a progress card is active and the HTML is small enough, it becomes a tappable `GenAIaeacdsnwHtmlPrimitive` preview before the response text; otherwise it remains a `.html` document. Code in other languages and prose that merely mentions a tag are left alone.
- `agent.progressTickMs` (default 2500, minimum 1500) sets the gap between card edits. Lower it for a livelier bar if your account tolerates it.
- `/job/{id}` now returns the final `activity` snapshot, so fast tool calls that finish between polls are kept in the card and the report.
- `setup.sh` installs the upstream RTK Rust Token Killer Apache-2.0 (`RTK_EDITION=oss`) CLI/filter edition into the project-local home by default and verifies its identity with `rtk gain` (there is another unrelated program named `rtk`). Kwee's native `shell` runner calls `rtk rewrite` before command execution; `terminal` inherits the same path. Every rewritten command is checked again by Kwee's dangerous-command, allowlist, inline-code and `denyPaths` rules. Missing, unsupported or failing RTK falls back to the original command, still under Kwee's policy. Set `KWEE_RTK=0` to skip installation/use. Kwee never runs `rtk init -g` or mutates another agent's global configuration. RTK can reduce supported shell-output bytes in model context; it does not promise fewer LLM API requests or a fixed percentage saving on the total bill. See `docs/RTK-INTEGRATION.md`.
- Caveman response skill is available on explicit request (`caveman mode`, `be brief`, `/caveman`). It preserves technical facts and returns to normal mode when asked. Caveman Cloud telemetry is a separate integration and is not enabled by this local skill.


### Export progres sebagai HTML mandiri

Untuk penggunaan CLI/Python, snapshot dari `Progres.ke_dict()` dapat diubah menjadi dokumen HTML biasa (bukan WebView, sheet, atau widget). Laporan di chat dibuat oleh transport (lihat di atas); renderer Python ini tetap dipakai CLI:

```bash
kwee progress-html progress.json -o progress-kwee.html
cat progress.json | kwee progress-html - -o progress-kwee.html
```

HTML bersifat mandiri, responsif, mendukung `prefers-reduced-motion`, dan mengekspos `window.kweeProgress.update(state)` bila aplikasi pemanggil ingin memperbarui tampilannya. Konten teks dirender sebagai teks, bukan HTML mentah.


## Kwee Field Journal — Library, Research & Adaptive Tools

Kwee now defaults to its native agent loop so local skills, the field journal, Graphify MCP, and GitHub MCP run behind Kwee's own tool registry, role checks, tenant boundaries, and audit flow. Hermes/OpenClaw are design references for the skill and learning patterns; external engines remain opt-in per owner.

### What gets better as conversations accumulate

- **Conversation library:** SQLite FTS5 retrieves relevant older messages from the active chat. A separate curated tenant library can share concise notes between that owner's chats only when the owner explicitly chooses `scope=library`; raw transcripts and source ledgers remain chat-isolated. A `LIKE` fallback is used if FTS5 is unavailable. Retrieved history, notes, and source snippets are explicitly labelled untrusted evidence.
- **Field journal:** `research_journal` stores source URLs and editorial notes, deduplicates sources, and keeps them scoped to the active chat. The built-in `web_search` and successful `web_fetch` flows automatically add provenance records.
- **Measured tool routing:** Kwee records tool success and latency in the tenant's local SQLite store. It ranks already-authorized tools based on task intent and measured outcomes. It never expands a role allowlist, removes a permitted tool, or treats latency as proof of quality.
- **Native agent learning:** `agent_learning` classifies task intent, recommends browser/research/tool compositions, adapts explanation depth, computes statistical summaries, stores explicit 1–5 human preference rewards, and records a versioned heuristic RLAIF signal after responses. Feedback changes future authorized tool and relevant skill ordering; weak observed response checks add a bounded verification reminder.
- **Prompt and skill refinement:** reviewable prompt candidates and skill proposals are persisted in the active tenant database. A primary owner can activate a skill proposal by explicitly approving its exact ID; it becomes supplemental guidance only when that skill is selected. Base prompt/skill files are never silently overwritten. Tool discovery reads only the built-in registry and obeys the active tool allowlist.
- **Native skills:** investigative journalism, library curation, adaptive tool routing, LangChain adapter, Mem0 memory, GitHub MCP, and Graphify are installed as local `SKILL.md` playbooks. Skills are loaded on demand by the existing skill loader.
- **Editorial GenAI/AIRich:** Markdown headings become high-contrast native GenAI labels, thematic breaks become dividers, and the body remains interactive Markdown. Wide or verbose tables become stacked field/value rows for mobile; compact tables stay native. The `KWEE / FIELD INTELLIGENCE` signature uses ink, paper, signal teal, and restrained copper; source provenance appears in the footer only when citations exist.

### Native knowledge workbench and CLI

`knowledge_engine` is a native owner-only tool for tenant-local claims, source provenance, time validity, explicit dependency links, conflict review, revisions and a persisted research workflow. Use `kwee knowledge status` to inspect its actual capability boundary, or `kwee knowledge claim_add --json '{"subject":"...","predicate":"...","object":"..."}'` from the CLI. CLI data is kept separately in `data/cli/memory/knowledge.sqlite3`; it does not share a WhatsApp tenant database. Statistical support is limited to a two-sided independent-samples Welch test and requires the caller to acknowledge design assumptions. See [the full 55-item capability matrix](docs/COGNITIVE-CAPABILITY-MATRIX.md) and [RTK integration notes](docs/RTK-INTEGRATION.md).

This is **retrieval- and feedback-based online policy improvement**, not model-weight training. The RLAIF signal uses an explicit heuristic rubric (not an accuracy oracle); Kwee does not rewrite external model weights from chat history. Conversation recall stays chat-isolated; learning aggregates in the active tenant database and never expands permissions or mixes tenants.

### Optional native integrations

| Integration | Role in Kwee | Default |
|---|---|---|
| LangChain | Convert already-authorized Kwee registry tools to `StructuredTool` for interoperability; not a replacement agent loop | Optional adapter |
| Graphify-Labs | Local AST knowledge graph for repository structure, exposed through MCP stdio | Enabled in config; waits for graph file/package |
| Mem0 OSS | Semantic memory on top of local SQLite/room memory; explicit LLM/embedder/Qdrant configuration | Disabled |
| GitHub MCP | Repository/code/commit/issue/PR reads, with read-only mode and exact tool allowlist | Configured; waits for Docker and a fresh token |

The one-command `start.sh` bootstrap installs missing optional Python integrations into the project-local Python user site. Manual installation remains available for controlled deployments:

```bash
bash setup-intelligence.sh
bash scripts/build-project-graph.sh
```

The Graphify extraction command defaults to `--code-only --no-viz`, so code is parsed locally without a semantic LLM pass. To expose docs/PDFs/images to Graphify, invoke its skill explicitly and understand that semantic extraction may route document content to the configured model backend. Graphify's MCP stdio server starts only after `graphify-out/graph.json` exists.

Start a local Qdrant for Mem0 only when semantic memory is needed:

```bash
docker compose -f docker-compose.intelligence.yml up -d
```

Then configure `.env` (never commit it):

```dotenv
KWEE_MEM0_ENABLED=true
MEM0_QDRANT_HOST=127.0.0.1
MEM0_QDRANT_PORT=6333
OPENAI_API_KEY=your-dedicated-provider-key
GITHUB_PERSONAL_ACCESS_TOKEN=your-new-fine-grained-read-token
```

Mem0 only starts when Qdrant and the explicit provider config in `config/default.yaml` are ready. Its collection is tenant-isolated and its opaque `user_id` is tenant + active-chat scoped. Kwee refuses semantic writes/search without an active chat context; the raw chat ID is not copied into Mem0 metadata. Local SQLite and room memory remain usable if Mem0 fails.

GitHub MCP requires Docker and the official server image. The configured server receives the token only through the process environment (`pass_env`), sets `GITHUB_READ_ONLY=1`, and only exposes tools in `mcp.servers.github.allow_tools`. Keep the token fine-grained and read-only. Do not store credentials in YAML or source. The token previously pasted into the conversation must be revoked; generate a new least-privilege token and place it only in the server's secret environment or `.env`.

The default mechanism is now `native`; an explicit `ownerMechanisms` override can still select Hermes, Claude, Codex, or Canvas for a particular owner. After restart, inspect the layer with the owner-only tools `intelligence_status` and `mcp_list_tools`. Use `research_journal` to search saved sources/notes, add field notes, and inspect tool efficiency. No remote Graphify workspace or provider credential is created automatically. Qdrant is started only when Mem0 is explicitly enabled; GitHub MCP requires Docker plus a fresh read-only token.

## Security and visual hardening (2026-11-10)

- External, hidden, and internal skill sources are scanned before prompt/catalog loading. Only `LOW_RISK` content is auto-loaded; `REVIEW` and `BLOCKED` are quarantined. Use native `skill` tool `action=audit` with an exact skill name to inspect evidence.
- `.zip`/`.skill` archives are inspected without executing payloads. The scanner reports SHA-256 file hashes, checks archive path traversal/symlinks/size limits, and applies static pattern + Python AST checks.
- Third-party skill text is explicitly wrapped as untrusted reference content. This scanner is not antivirus and does not guarantee malware detection; see `docs/SECURITY-SKILLS.md`.
- UI identity is now Velvet Noir: blush/plum live progress and a separate deep-plum final dossier. Native Markdown formatting remains intact, including task lists, nested lists, links, tables, code, citations, and math. See `docs/UI-DESIGN-SYSTEM.md`.
- Build/test and browser-render verification: `docs/BUILD-VERIFICATION-V0.3.0.md (baseline)`; skill scan baseline: `docs/SECURITY-SKILLS-BASELINE-V0.3.0.md`; rendered progress/final/runtime previews: `docs/ui-previews/`.


- AIFS tutor skills are bundled under `skills/external/ai-engineering-from-scratch/` and loaded on demand; lessons remain upstream. FCC CLI is bootstrapped by `start.sh` where compatible tools/runtime are available. FCC is a coding-agent launcher/proxy, not Kwee’s LLM peer API; use Kwee’s native `subagent` for same-environment peer discussion and memory.
