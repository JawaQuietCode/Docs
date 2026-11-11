// state.js — state bersama kecil antara transport dan router.
//
// Kenapa ada: di **self-chat** (chat ke diri sendiri), pesan USER dan pesan
// BALASAN bot sama-sama punya `key.fromMe = true`. Tanpa pelacakan, bot akan
// membalas balasannya sendiri tanpa henti. Di sini kita mencatat id pesan yang
// benar-benar dikirim bot, supaya bisa dibedakan dari pesan user.

const MAX = 500
const ownIds = new Set()
const order = []

/** Tandai sebuah pesan sebagai "dikirim oleh bot". */
export function markOwn(id) {
  if (!id) return
  if (!ownIds.has(id)) {
    ownIds.add(id)
    order.push(id)
    while (order.length > MAX) ownIds.delete(order.shift())
  }
}

/** True bila id ini adalah pesan yang dikirim bot sendiri. */
export function isOwn(id) {
  return Boolean(id) && ownIds.has(id)
}
