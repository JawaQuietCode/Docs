# Full rebuild notes

- Archive root contents are packaged directly at ZIP root (no extra wrapper directory).
- `.env`, runtime sessions, logs, SQLite data, caches, `node_modules`, and Python bytecode are excluded.
- Copy `.env.example` to `.env` on the target VPS and insert secrets there; never upload `.env` into this source ZIP.
- Third-party skill repositories are installed by `scripts/install-system-skills.sh` on a network-enabled host.
- Start with the existing Pterodactyl/start.sh procedure after reviewing `.env.example` and config.

- `!afk` persists sessions in the local SQLite database and sends self-contained GenAI rich HTML cards; optional WSS provides cross-card live list updates.
- Image/video/sticker Vision is explicit opt-in (reply with a prompt or mention Kwee with a caption); unsolicited media is not sent to the AI.
- Added the missing `media.store` implementation, deduplicated asset files, video frame sampling through ffmpeg, and nested `SKILL.md` discovery for the OpenHands prompt builder.
- Always-on anti-slop/accessibility and backend-reliability skills are project-authored summaries; upstream sources and review scope are recorded in `skills/SOURCES.md`.

## Progress card + HTML report fix (3 Nov 2026)

Root causes found:
- `pollJob()` (transport/router.js) returned only `{replies, raw, trace}` and the Python `/job/{id}` endpoint dropped `activity` / `activity_html`. In the default background mode the HTML report and the final activity snapshot therefore never reached the transport, so `kwee-progress.html` was never sent.
- The default engine is OpenHands, which returns raw text without `activity` or `activity_html`, and its text never went through `agent/format.py`, so ```html fences were rendered as a "Lihat kode" card.
- The previous live card was a `code` block (`$ kwee --jalan`, bar, steps) rendered by WhatsApp as "Bash / Lihat kode"; its text-swapping layout also gave no sense of motion.

Changes: `progress-card.js` (no code blocks, smooth bar, phase checklist, creeping percent), `progress-html.js` (transport-side animated report), `htmlfile.js` (HTML in answers -> attachment), `kemajuan.js` (phase tracking, resilient to engines without telemetry, builds the report on close), `router.js` + `agent/main.py` (pass `activity` through `/job`), config keys `agent.progressHtml` and `agent.progressTickMs`.

Follow-up: the report is now embedded in the final card as an `html` block (same shape as `!ping`) instead of a separate document message. `agent.progressHtmlMode: file` restores the document; the document is also the fallback when the card edit fails.


## Live progress HTML-only update (9 Nov 2026)

- Removed the native WhatsApp `progress` block from `blokBerjalan()`. The live status payload is now exactly one HTML block, avoiding a second client-specific indicator in the edited message.
- Preserved the existing one-message edit lifecycle in `Kemajuan` / `editRichCard`, including monotonic percentage updates, chronological tool events, and answer delivery when an edit fails.
- Reworked `transport/api/progress-html.js` into a self-contained comic-neobrutalist KWEE surface: paper panel, heavy ink outlines and offset shadows, halftone texture, yellow/blue/coral accents, clear work log, skills panel, and signature footer. No external fonts or assets are loaded; reduced-motion is respected.
- Regression coverage now asserts HTML-only payloads, the visual identity hooks, and HTML-only status updates while attachments are sent.
