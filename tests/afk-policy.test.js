import test from 'node:test'
import assert from 'node:assert/strict'
import { isAfkGroupCommand } from '../transport/afk-policy.js'

test('AFK is available to group participants but not a general owner bypass', () => {
  assert.equal(isAfkGroupCommand('!afk tidur', true), true)
  assert.equal(isAfkGroupCommand('!afk -ls', true), true)
  assert.equal(isAfkGroupCommand('!help', true), false)
  assert.equal(isAfkGroupCommand('!afk list', false), false)
})
