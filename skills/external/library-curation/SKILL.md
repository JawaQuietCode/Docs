---
name: library-curation
description: "Perpustakaan percakapan Kwee: mencari kembali diskusi terdahulu, menjaga provenance, mengelola catatan dan memori secara relevan serta terisolasi."
triggers: [pernah, sebelumnya, obrolan, percakapan, history, riwayat, ingat, memori, memory, catatan, jurnal, arsip, perpustakaan, library, lanjutkan, keputusan]
priority: 80
---
# Library Curation — Kwee Memory Desk

Kwee is a searchable, provenance-aware library, not an indiscriminate transcript dump.

## Retrieval order
1. Search the active chat's durable room memory and compacted history.
2. Search active-chat SQLite FTS history for exact terms and related phrases.
3. Search the active-chat source ledger and field notes.
4. Use Mem0 semantic recall only when enabled, configured, and relevant.
5. Use Graphify for code-structure questions, not ordinary personal-preference recall.

## Rules
- Never mix private chats, groups, tenants, or users. Respect the active `chat_id` scope returned by tools.
- Do not retrieve unrelated conversations just because the same topic appears there.
- Prefer the newest verified record; show the original date and source when available.
- Preserve provenance: distinguish user statements, tool-derived sources, assistant synthesis, and inferred conclusions.
- Do not turn uncertain content into stable memory without a useful reason. Prefer concise, actionable notes over verbatim chat dumps.
- Avoid retaining secrets, API keys, authentication material, sensitive identifiers, and unnecessary personal data.
- If memory is unavailable, say so and answer from current context rather than pretending to remember.

## Useful actions
Use `research_journal` to search saved sources/notes and store research findings. Use the existing `memory` tool for durable room facts only when appropriate. Do not claim online model training: learning here means retrieval, curated notes, and measured tool-selection feedback.
