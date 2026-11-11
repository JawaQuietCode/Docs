---
name: backend-reliability
description: Always-on backend correctness, SQLite durability, WebSocket lifecycle, media ingestion, API boundaries and test discipline.
priority: 95
triggers: [backend, sqlite, database, websocket, media, api, endpoint, reliability, concurrency, persistence]
---
# Backend reliability contract

- Treat SQLite as the source of truth. Persist timestamps as UTC Unix seconds; compute elapsed durations from `started_at`, never from a stale cached counter.
- Use parameterized SQL, explicit transactions for read-modify-write, `busy_timeout`, WAL where concurrent readers/writers matter, and indexes for lookup paths.
- Scope records and read tokens by chat/tenant. WebSocket credentials must be short-lived, read-only, scoped, and never reuse the write API token in browser HTML.
- Broadcast after a successful commit. Remove disconnected WebSocket clients and tolerate reconnects, malformed frames, and absent public WSS configuration.
- Validate all inbound identifiers and action names. Keep user-provided HTML/text escaped when inserted into generated markup; serialize boot data safely.
- For media, distinguish download/decrypt from semantic analysis. Persist/download only as required; never invoke Vision without explicit user intent. Handle quoted messages and wrapped/view-once content, preserve MIME metadata, enforce size limits, and clean temporary files.
- Video understanding must sample bounded frames, enforce time/size limits, and return a clear ffmpeg dependency error rather than pretending the video was read.
- Keep API transport, business logic, storage, and HTML rendering in separate modules. Do not let WhatsApp Web access SQLite directly.
- Write a failing regression test first, then implement the smallest fix. Test state/results and failure paths; never claim a test or WhatsApp device integration passed unless actually run.
