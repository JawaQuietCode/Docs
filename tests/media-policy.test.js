import test from 'node:test'
import assert from 'node:assert/strict'
import { shouldInspectMedia } from '../transport/media-policy.js'

test('does not inspect unsolicited image, video, or sticker messages', () => {
  for (const kind of ['image', 'video', 'sticker']) {
    assert.equal(shouldInspectMedia({ text: '', attachment: { kind, path: '/tmp/media' } }), false)
    assert.equal(shouldInspectMedia({ text: 'caption biasa', attachment: { kind, path: '/tmp/media' } }), false)
  }
})

test('inspects media when user replies with a request and the asset is available', () => {
  assert.equal(shouldInspectMedia({ text: 'ini apa?', quotedAttachment: { kind: 'image', path: '/tmp/image.jpg' } }), true)
})

test('does not inspect quoted media metadata when the asset is unavailable', () => {
  assert.equal(shouldInspectMedia({ text: 'ini apa?', quotedAttachment: { kind: 'image' } }), false)
})

test('inspects attached media when bot is mentioned with a caption', () => {
  assert.equal(shouldInspectMedia({ text: '@Kwee jelaskan ini', isMentionBot: true, attachment: { kind: 'video', path: '/tmp/video.mp4' } }), true)
})

test('mention alone or a command is not a Vision request', () => {
  assert.equal(shouldInspectMedia({ text: '@628123456789', isMentionBot: true, attachment: { kind: 'image', path: '/tmp/image.jpg' } }), false)
  assert.equal(shouldInspectMedia({ text: '@628123456789 !afk tidur', isMentionBot: true, attachment: { kind: 'image', path: '/tmp/image.jpg' } }), false)
  assert.equal(shouldInspectMedia({ text: '@628123456789 jelaskan ini', isMentionBot: true, attachment: { kind: 'image', path: '/tmp/image.jpg' } }), true)
})
