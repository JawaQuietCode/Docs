import test from 'node:test'
import assert from 'node:assert/strict'
import { buildAfkHtml } from '../transport/api/afk-html.js'

test('AFK card is self-contained and has a ticking elapsed-time display', () => {
  const html = buildAfkHtml({
    mode: 'single',
    session: { sender_id: 'user-1', sender_name: 'Alice', reason: 'tidur', started_at: 1000, elapsed_seconds: 20 },
    wsUrl: 'wss://bot.example/ws/afk', wsToken: '123.sig', chatId: 'group-1',
  })
  assert.match(html, /lang="id"/)
  assert.match(html, /aria-live="polite"/)
  assert.match(html, /setInterval\(tick,1000\)/)
  assert.match(html, /new WebSocket\(u\)/)
  assert.doesNotMatch(html, /fonts\.googleapis|cdn\.tailwindcss|unpkg\.com/)
})

test('AFK card escapes user-controlled names and reasons', () => {
  const html = buildAfkHtml({ items: [{ sender_name: '<img src=x onerror=alert(1)>', reason: '<script>alert(1)</script>', started_at: 1000 }] })
  assert.doesNotMatch(html, /<img src=x/)
  assert.doesNotMatch(html, /<script>alert\(1\)<\/script>/)
  assert.match(html, /&lt;script&gt;/)
})
