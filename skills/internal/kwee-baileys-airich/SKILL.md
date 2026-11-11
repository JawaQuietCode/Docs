---
name: kwee-baileys-airich
description: Project-specific guidance for maintaining Kwee’s WhatsApp Baileys transport, LID/PN/JID routing, plugins, and Meta AI Rich/GenAI response payloads. Use when changing WhatsApp events, mentions, media, command/plugin routing, or rich response cards.
---

# Kwee WhatsApp, Baileys, and AI Rich

Use this guide whenever modifying WhatsApp transport, address resolution, plugins, media delivery, or Meta AI Rich responses in this repository. Treat the installed package and this repository’s implementation as the source of truth; Baileys forks and WhatsApp protobufs are not interchangeable.

## Start with the current implementation

1. Read `package.json` and the installed `node_modules/@itsliaaa/baileys` package metadata/types before using or changing Baileys APIs. This project has used the `@itsliaaa/baileys` fork; verify its exact version every time. Do not copy examples from upstream Baileys without checking the installed fork.
2. Trace incoming messages through `transport/wa.js` → `transport/router.js`; inspect `transport/commands.js` for deterministic command aliases, `transport/plugin-host.js` for plugin invocation, and `transport/lid.js` for address resolution.
3. For AI turns, inspect `agent/main.py`, `agent/loop.py`, `agent/llm/client.py`, and `security/roles.py`. Keep role checks in both transport and agent layers; never let a public DM or group command jump directly to a plugin or owner engine.
4. For rich replies, read `transport/api/rich.js` and `transport/api/airich.js` together with relevant tests. Prefer existing builders (`sendRich`, `sendRichHtml`, `buildRichCard`) over hand-constructing protobufs.

## WhatsApp identity and mentions

- Preserve address type and full JID while routing: phone-number JIDs (`@s.whatsapp.net`), LID JIDs (`@lid`), and group JIDs (`@g.us`) have different semantics. Do not treat a LID’s numeric-looking local part as a phone number.
- Use the repository’s LID mapping helpers and tests for PN↔LID resolution. Carry all trusted sender identities through authorization, deduplicate them without changing their JID type, and resolve a verified PN when a feature truly needs a phone-number identity (for example, per-account quota keys).
- Construct group mentions using the member JID actually present in the group participant list. Validate group membership and preserve mention metadata; never fabricate a mention JID by string replacement.
- Keep user-facing commands on the existing `!` route and explicit slash aliases. Unknown commands must not fall through into privileged plugin dispatch.

## Plugin and media contract

- Follow the discovered plugin contract in `transport/plugin-host.js` and existing plugins. Use only media paths supplied by the transport; validate path, media type, size, and output before processing.
- Keep plugins least-privileged. A public-safe plugin must be explicitly allowlisted at the router, limited to DM unless approved otherwise, rate-limited, and unable to call arbitrary owner-only plugins/tools.
- Use argument-array process spawning (never interpolate untrusted text into a shell command), timeouts, bounded output, and cleanup in `finally`. Keep optional heavy/native dependencies optional and provide a deterministic fallback when practical.
- Preserve LID/PN and group context in plugin messages; redact secrets and raw session/auth data from logs.

## Meta AI Rich / GenAI responses

- Use existing rich helpers and serializers. In `rich.js`, the repository currently builds `AIRichResponseMessage`, puts `submessages` plus `unifiedResponse.data` in the expected structure, and serializes unified data as a `Buffer` (`Buffer.from(JSON.stringify(...))`). It also adds bot-forwarding context, bot metadata, device-list metadata, and the extra interactive/native-flow node used by this fork.
- Keep the current response primitives/section schema and `__typename` conventions aligned with `airich.js`, the installed fork, and fixtures/tests. Do not invent a new field or assume that a successful local serialization means WhatsApp will render it.
- When changing protobuf/message shape, add or update a focused test and inspect the result using the repository’s send/preview flow. Do not remove proofs, bot metadata, or forwarding context casually; they can make an otherwise valid card be rejected or rendered as an ordinary message.
- For code, HTML, tables, sources, images, progress indicators, and interactive elements, reuse the matching project builder. Keep normal text fallback available when a rich card cannot be built.

## Security and validation checklist

- Confirm owner/public/group authorization on the transport route and the Python agent. `.env` owner numbers remain unlimited; non-owner identities must use separate tenant storage and per-account credit records.
- Treat `data/sessions`, auth tokens, `.env`, and owner lists as secrets. Never include them in prompts, skill references, test fixtures, or logs.
- Run `node --check` for edited JavaScript and the focused `node --test` / Python test files for affected routing, LID, media, quota, or rich payload behavior. If a test runner is unavailable, say so and run syntax plus targeted smoke checks; do not claim a full suite passed.
- Real-ESRGAN CLI used by the optional upscale plugin is documented upstream at https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan. Recheck that documentation before changing model names or flags; fall back safely if the executable or weights are unavailable.
