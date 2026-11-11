// plugins/_probe-store.js — probe: apakah store.messages bisa dipakai.
export default {
  name: '_probe-store',
  description: 'probe',
  commands: ['probestore'],
  async run(ctx) {
    const sock = ctx.wa.raw()
    const store = sock?.store
    const out = {
      hasStore: Boolean(store),
      storeKeys: store ? Object.keys(store) : [],
      chatIds: store?.messages ? Object.keys(store.messages).slice(0, 3) : [],
      sample: null,
    }
    try {
      const chats = Object.keys(store?.messages || {})
      for (const cid of chats) {
        const msgs = store.messages[cid]
        const ids = Object.keys(msgs)
        out.sample = { chat: cid, count: ids.length, last: ids[ids.length - 1] }
        break
      }
    } catch (e) {
      out.err = e.message
    }
    await ctx.wa.send(ctx.msg.chatId, { text: JSON.stringify(out) })
    return 'probe'
  },
}
