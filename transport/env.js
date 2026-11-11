// env.js — pemuat .env sederhana (tanpa dependensi).
//
// Kenapa ada: panel/egg server tidak menyuntik rahasia, jadi kunci dibaca dari
// file .env di root aplikasi. Variabel yang SUDAH ada di process.env TIDAK
// ditimpa (env asli menang).

import { readFileSync } from 'node:fs'
import path from 'node:path'

export function loadEnv(file = process.env.ENV_FILE || path.join(process.cwd(), '.env')) {
  const out = {}
  let raw
  try {
    raw = readFileSync(file, 'utf8')
  } catch {
    return out // .env tidak ada → pakai process.env
  }
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const eq = t.indexOf('=')
    if (eq < 0) continue
    const key = t.slice(0, eq).trim()
    if (!key) continue
    const val = t.slice(eq + 1).trim().replace(/^["']|["']$/g, '')
    if (!(key in process.env)) process.env[key] = val
    out[key] = val
  }
  return out
}
