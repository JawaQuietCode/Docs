// agent-auth.js — header autentikasi untuk inti agent Python.
//
// Inti agent (agent/main.py) sekarang menolak request tanpa header
// `X-Agent-Token` bila ACTION_TOKEN diisi di .env. Transport membaca .env lewat
// transport/env.js (dipanggil config.js), jadi token yang sama tersedia di sini.
//
// Kenapa perlu: sebelumnya /message, /cancel, /status terbuka di 127.0.0.1 —
// proses lain di container (atau agent itu sendiri lewat `curl localhost:8787/
// message`) bisa menyuruh bot sebagai owner tanpa lewat WhatsApp.

export function agentToken() {
  return process.env.ACTION_TOKEN || process.env.AGENT_TOKEN || ''
}

export function agentHeaders(extra = {}) {
  const token = agentToken()
  if (!token) return { ...extra }
  return { ...extra, 'X-Agent-Token': token }
}
