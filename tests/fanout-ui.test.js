import test from 'node:test'
import assert from 'node:assert/strict'
import { fanoutBlocks } from '../transport/api/ui.js'

test('team card shows peer role and honest shared-memory status', () => {
  const blocks = fanoutBlocks({ tugas: [{
    judul: 'Check API design', agent: 'architect', ok: true,
    hasil: 'Keep the interface minimal.', remember: true,
    memory: { ok: true, scope: 'library' },
  }] })
  const text = JSON.stringify(blocks)
  assert.match(text, /architect/)
  assert.match(text, /shared note saved/)
  assert.match(text, /unverified/)
})
