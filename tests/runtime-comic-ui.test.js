import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildRuntimeHtml } from '../plugins/_runtime-core.js'

 test('comic runtime card uses live values and replaces every template token', () => {
  const html = buildRuntimeHtml({
    kind: 'ping', modules: 7, uptimeMs: 65000,
    proc: { rss: 1024 * 1024 * 46, heapUsed: 5 * 1024 * 1024, load1: 0.07,
      cpuCount: 6, sysFree: 13, sysTotal: 100 },
  })
  assert.match(html, /FIELD NOTE \/ uptime/)
  assert.match(html, /Proses bot/)
  assert.match(html, /memori bebas/)
  assert.match(html, />7</)
  assert.match(html, />6</)
  assert.doesNotMatch(html, /\{\{[A-Z_]+\}\}/)
  assert.match(html, /box-shadow:5px 5px 0/)
  assert.match(html, /radial-gradient/)
})

 test('runtime card escapes dynamic metric labels and values', () => {
  const html = buildRuntimeHtml({
    modules: '<img src=x onerror=alert(1)>', uptimeMs: 0,
    proc: { rss: null, heapUsed: null, load1: null, cpuCount: 2, sysFree: null, sysTotal: null },
  })
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/)
  assert.doesNotMatch(html, /<img src=x/)
})
