// plugins/tourl.js — "!tourl": dari pesan yang di-reply (media + caption)
// ambil URL-nya. Caption → ekstrak URL. Media → upload ke file host → link.
//
//|-- tourl            → reply pesan media/caption, kirim URL-nya
//   |-- tourl <teks>  → teks yang diberikan diproses juga
//
// Mesin: plugins/_tourl.py (Python, stdlib saja) — ekstraksi URL + upload.

import { spawn } from 'node:child_process'
import { PY_BIN } from '../transport/py.mjs'
import path from 'node:path'

const PY = path.resolve(process.cwd(), 'plugins', '_tourl.py')

export default {
  name: 'tourl',
  description: 'Reply media/caption → kirim URL-nya (caption jadi tautan, media di-upload jadi link)',
  commands: ['tourl', 'url'],

  async run(ctx) {
    const { msg, args, wa, log } = ctx
    const to = msg.chatId

    // Sumber teks: caption pesan yang di-reply, atau teks pada perintah.
    const quotedText = msg.quoted?.text || msg.quoted?.caption || ''
    const argText = args?.text || ''
    const text = [quotedText, argText].filter(Boolean).join('\n')

    // Sumber berkas: lampiran yang di-reply dulu, lalu lampiran pesan ini.
    const file = args?.quotedMedia || args?.media || ''

    if (!text && !file) {
      await wa.send(to, { text: '❌ Reply pesan berisi caption/media, lalu ketik !tourl' })
      return 'no-input'
    }

    const res = await runPy(text, file)
    if (res.error && !res.urls?.length && !res.upload) {
      await wa.send(to, { text: `❌ Gagal: ${res.error}` })
      return 'error'
    }

    const lines = [...(res.urls || [])]
    if (res.upload) lines.push(res.upload)
    if (!lines.length) {
      await wa.send(to, { text: '❌ Tidak ada URL di caption, dan upload media gagal.' })
      return 'empty'
    }

    await wa.send(to, { text: lines.join('\n') })
    log(`url=${(res.urls || []).length} upload=${res.upload ? 'ya' : 'tidak'}`)
    return `ok (${lines.length} baris)`
  },
}

/** Panggil helper Python; hasil JSON. Timeout 120s (upload berkas besar). */
function runPy(text, file) {
  return new Promise((resolve) => {
    const argv = ['-u', PY, '--text', String(text || '')]
    if (file) argv.push('--file', String(file))
    let out = ''
    let err = ''
    const child = spawn(PY_BIN, argv, { stdio: ['ignore', 'pipe', 'pipe'] })
    const timer = setTimeout(() => child.kill('SIGKILL'), 120000)
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { err += d })
    child.on('error', (e) => { clearTimeout(timer); resolve({ urls: [], upload: null, error: e.message }) })
    child.on('close', () => {
      clearTimeout(timer)
      try {
        resolve(JSON.parse(out || '{}'))
      } catch {
        resolve({ urls: [], upload: null, error: (err || out || 'output bukan JSON').slice(0, 200) })
      }
    })
  })
}
