import test from 'node:test'
import assert from 'node:assert/strict'
import plugin from '../plugins/hidetag.js'

function context({ text, participants, senderId, senderIds = [] }) {
  const sent = []
  const sock = { groupMetadata: async () => ({ participants }) }
  return {
    sent,
    ctx: {
      args: { argv: ['Pemberitahuan'] },
      msg: { chatId: '120363000@g.us', isGroup: true, senderId, senderIds, text },
      wa: { raw: () => sock, send: async (...args) => sent.push(args) },
    },
  }
}

test('mentionjid lists full PN and LID JIDs without stripping address types', async () => {
  const { ctx, sent } = context({
    text: '!mentionjid',
    senderId: '6281000000000@s.whatsapp.net',
    participants: [{
      id: '12345@lid', lid: '12345@lid', phoneNumber: '6281111111111@s.whatsapp.net',
      admin: 'admin',
    }],
  })
  const result = await plugin.run(ctx)
  assert.match(result, /12345@lid/)
  assert.match(result, /6281111111111@s\.whatsapp\.net/)
  assert.deepEqual(sent, [])
})

test('mass mention is blocked unless resolved sender identity is a group admin', async () => {
  const { ctx, sent } = context({
    text: '!hidetag',
    senderId: '6281000000000@s.whatsapp.net',
    senderIds: ['6281000000000@s.whatsapp.net'],
    participants: [{ id: '6281000000000@s.whatsapp.net' }, { id: '6282000000000@lid' }],
  })
  const result = await plugin.run(ctx)
  assert.match(result.error, /admin grup/)
  assert.deepEqual(sent, [])
})

test('mass mention uses the exact active group participant JID after PN/LID resolution', async () => {
  const { ctx, sent } = context({
    text: '!tagall berita',
    senderId: '6281000000000@s.whatsapp.net',
    senderIds: ['6281000000000@s.whatsapp.net', '99999@lid'],
    participants: [
      { id: '99999@lid', lid: '99999@lid', phoneNumber: '6281000000000@s.whatsapp.net', admin: 'superadmin' },
      { id: '6282000000000@s.whatsapp.net' },
    ],
  })
  const result = await plugin.run(ctx)
  assert.equal(result, null)
  assert.deepEqual(sent[0][1].mentions, ['99999@lid', '6282000000000@s.whatsapp.net'])
})
