import test from 'node:test'
import assert from 'node:assert/strict'
import { inspectMediaOnly } from '../transport/engine.js'

test('vision-only inspection calls only image/video endpoints and returns descriptions', async () => {
  const originalFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), body: JSON.parse(options.body) })
    return { ok: true, json: async () => ({ text: 'objek terlihat jelas' }) }
  }
  try {
    const result = await inspectMediaOnly([
      { kind: 'sticker', path: '/tmp/sticker.webp' },
      { kind: 'video', path: '/tmp/clip.mp4' },
    ], { agent: { host: '127.0.0.1', port: 8787, authToken: 'test' } }, 'jelaskan konten ini')
    assert.match(result, /objek terlihat jelas/)
    assert.equal(calls.length, 2)
    assert.ok(calls.some((x) => x.url.endsWith('/vision')))
    assert.ok(calls.some((x) => x.url.endsWith('/video-vision')))
    assert.ok(calls.every((x) => x.body.prompt.includes('jelaskan konten ini')))
  } finally {
    globalThis.fetch = originalFetch
  }
})
