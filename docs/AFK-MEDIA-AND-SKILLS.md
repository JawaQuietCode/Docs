# AFK, media opt-in, and skills loading

## WhatsApp media policy

- Incoming media is downloaded/decrypted by the transport and staged locally.
- The AI is **not** sent the media metadata or bytes unless the user explicitly replies to that media with a prompt, or mentions Kwee on the media message with a caption.
- Owner media-only messages receive a deterministic acknowledgement. In groups, non-owners may trigger Vision only with an explicit media reply or a Kwee mention plus caption; other non-owner AI requests remain blocked. Captions without a Kwee mention are treated as text-only; the media is not inspected.
- If a requested download/decryption fails, Kwee reports that failure instead of asking the model to guess.
- Images and stickers use `/vision`; videos use `/video-vision`, which samples at most six frames with `ffmpeg`/`ffprobe`.
- Media download first uses `downloadMediaMessage`; a stream fallback uses `downloadContentFromMessage` when the installed Baileys fork exports it.

## AFK commands

- `!afk <reason>` starts or refreshes the sender's AFK session in `data/memory/kwee.db`.
- `!afk -ls` and `!afk list` return the active sessions in the current chat.
- `!afk off` closes the sender's active session. These three AFK commands are available to group participants (not a general owner-command bypass); each participant can only start/stop their own session.
- Each session stores sender ID/name, reason, start time, and end time. The HTML card calculates elapsed time from the persisted start timestamp.
- The card uses `sendRichHtml()` from `transport/api/rich.js`, which wraps the existing `GenAIaeacdsnwHtmlPrimitive` contract. HTML, CSS, and JavaScript are inline; no Google Fonts, Tailwind CDN, or remote stylesheet is required.

## WebSocket deployment

The embedded HTML can show a local ticking timer even without a public WebSocket URL. Cross-card list updates require a publicly reachable WSS endpoint. Set one of:

```env
KWEE_AFK_PUBLIC_URL=https://bot.example.com
# or set the exact path explicitly:
KWEE_AFK_WS_URL=wss://bot.example.com/ws/afk
```

Configure a reverse proxy to forward `/ws/afk` to the Python FastAPI agent service (normally port 8787) with WebSocket upgrade headers. Example Nginx location:

```nginx
location /ws/afk {
    proxy_pass http://127.0.0.1:8787/ws/afk;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

The service issues a short-lived, chat-scoped, read-only HMAC token; the write API token is never embedded in the card. The endpoint is not exposed to the public network by default.

The WhatsApp client must allow inline JavaScript and WebSocket connections in its embedded GenAI HTML WebView for cross-card live updates. That part requires a real WhatsApp client/device acceptance test; unit tests cannot prove WhatsApp's runtime policy.

## Skill loading

The Python and OpenHands/Node prompt builders now both scan flat skills and package skills with nested `SKILL.md` entrypoints. Internal skills are injected every prompt; external skills are selected by trigger/description matching. `!skills` reports the last prompt's injected names; `!skills reload` refreshes both loaders.

The always-on `anti-slop-design` and `backend-reliability` contracts are concise project-owned syntheses of the researched sources listed in `skills/SOURCES.md`. They are not copied wholesale upstream skill packages.
