---
name: mem0-native
description: "Integrasi Mem0 OSS untuk semantic long-term memory di atas memori lokal Kwee, dengan provider eksplisit dan pemisahan chat."
triggers: [mem0, semantic memory, semantic recall, vector memory, qdrant, memori jangka panjang, long term memory, embedding]
priority: 70
---
# Mem0 OSS Memory Adapter

SQLite FTS5, compacted conversation history, and room memory remain Kwee's local source of truth. Mem0 is an optional semantic-recall layer.

## Enablement requirements
- `KWEE_MEM0_ENABLED=true` only after Qdrant is reachable.
- Configure `memory.mem0.oss_config` with explicit vector-store, LLM, and embedder providers.
- Keep provider credentials in environment secrets, never in YAML, source, prompts, or tool output.
- Use the Kwee adapter (`memory.mem0_backend`) so each tenant + chat has a deterministic opaque user identifier and collection isolation.
- Verify add/search behavior before relying on semantic results.

## Memory quality
Save durable, useful facts and curated learnings, not every raw message. Prefer short summaries with provenance and dates. Never ingest secrets, credentials, private identifiers, or data from another chat. Treat retrieved memory as fallible evidence and confirm current facts.

## Fallback
When Qdrant, the provider, or `mem0ai` is unavailable, the local SQLite/room-memory library still works. Report the degraded state honestly; do not block a WhatsApp reply because semantic memory failed.
