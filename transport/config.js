// config.js — baca config/default.yaml + .env → objek config.
//
// Parser YAML minimal (tanpa dependensi): cukup untuk bentuk yang kita pakai —
// nested key, string, angka, boolean, dan list "- item". Bukan YAML penuh.

import { readFileSync } from 'node:fs'
import path from 'node:path'
import { loadEnv } from './env.js'

export function toBool(v) {
  if (typeof v === 'boolean') return v
  const s = String(v ?? '').trim().toLowerCase()
  return s === '1' || s === 'true' || s === 'yes' || s === 'on'
}

// Buang komentar sebaris ("nilai   # catatan") TANPA merusak "#" di dalam
// tanda kutip atau warna seperti #FF9CB5 (ada "#" tanpa spasi di depannya).
// Tanpa ini "port: 8000   # ingress" terbaca sebagai teks → Number() = NaN.
export function stripInlineComment(raw) {
  const s = String(raw ?? '')
  let quote = ''
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (quote) {
      if (ch === '\\' && quote === '"') { i++; continue }
      if (ch === quote) quote = ''
      continue
    }
    if (ch === '"' || ch === "'") { quote = ch; continue }
    if (ch === '#' && (i === 0 || /\s/.test(s[i - 1]))) return s.slice(0, i).trimEnd()
  }
  return s.trimEnd()
}

function coerce(raw) {
  const v = stripInlineComment(raw).trim()
  if (v === '') return ''
  if (/^-?\d+$/.test(v)) return parseInt(v, 10)
  if (/^-?\d*\.\d+$/.test(v)) return parseFloat(v)
  if (/^(true|false)$/i.test(v)) return v.toLowerCase() === 'true'
  if (/^(null|~)$/i.test(v)) return null
  const q = v.match(/^(["'])([\s\S]*)\1$/)
  return q ? q[2] : v
}

export function parseSimpleYaml(text) {
  const root = {}
  // stack of {indent, obj}
  const stack = [{ indent: -1, obj: root }]
  const lines = String(text).split(/\r?\n/)

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]
    if (!raw.trim() || raw.trim().startsWith('#')) continue
    const indent = raw.length - raw.trimStart().length
    const line = raw.trim()

    // list item: "- value"
    if (line.startsWith('- ')) {
      const parent = stack[stack.length - 1]
      const val = coerce(line.slice(2))
      // cari array pada objek teratas yang indentasinya lebih kecil
      let holder = parent
      while (holder && !Array.isArray(holder.arr)) {
        holder = holder.parent
      }
      if (holder && Array.isArray(holder.arr)) holder.arr.push(val)
      continue
    }

    const colon = line.indexOf(':')
    if (colon < 0) continue
    const key = line.slice(0, colon).trim()
    const rest = line.slice(colon + 1).trim()

    // pop stack sampai ketemu induk yang indentasinya lebih kecil
    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) stack.pop()
    const parent = stack[stack.length - 1].obj

    if (rest === '') {
      // bisa objek atau array; tentukan dari baris berikutnya
      const next = lines.slice(i + 1).find((l) => l.trim() && !l.trim().startsWith('#'))
      const nextTrim = next ? next.trim() : ''
      if (nextTrim.startsWith('- ')) {
        const arr = []
        parent[key] = arr
        const entry = { indent, obj: parent, arr, parent: null }
        // tautkan parent agar list item tahu ke array mana
        entry.parent = stack[stack.length - 1]
        entry.arr = arr
        stack.push(entry)
      } else {
        const child = {}
        parent[key] = child
        stack.push({ indent, obj: child })
      }
    } else {
      parent[key] = coerce(rest)
    }
  }
  return root
}

export function loadConfig(root = process.cwd()) {
  loadEnv(path.join(root, '.env'))
  let cfg = {}
  try {
    cfg = parseSimpleYaml(readFileSync(path.join(root, 'config', 'default.yaml'), 'utf8'))
  } catch {
    cfg = {}
  }

  cfg.identity = cfg.identity || { name: 'kweewind blows', version: '0.1.0' }
  cfg.whatsapp = cfg.whatsapp || {}
  cfg.agent = cfg.agent || {}
  cfg.security = cfg.security || {}
  cfg.llm = cfg.llm || {}

  // .env menimpa
  cfg.whatsapp.authDir = cfg.whatsapp.authDir || './data/sessions'
  cfg.whatsapp.pairingCode = (process.env.WA_PAIRING_CODE || cfg.whatsapp.pairingCode || '').toUpperCase()
  cfg.whatsapp.pairingNumber = process.env.WA_PAIRING_NUMBER || ''
  cfg.whatsapp.requireMentionInGroup = cfg.whatsapp.requireMentionInGroup !== false
  // Proteksi sesi (transport/sessionguard.js). Deteksi selalu aktif; BLOKIR
  // enumerasi perangkat diri hanya bila diminta eksplisit lewat config/env.
  cfg.whatsapp.blockSelfDeviceEnumeration =
    (process.env.WA_BLOCK_SELF_DEVICE_ENUM || '').toLowerCase() === 'true'
    || cfg.whatsapp.blockSelfDeviceEnumeration === true

  cfg.owner = String(process.env.WA_OWNER_NUMBERS || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  try {
    const runtimeOwners = JSON.parse(readFileSync(path.join(root, 'data', 'owners.json'), 'utf8'))
    if (Array.isArray(runtimeOwners)) {
      cfg.owner = [...new Set([...cfg.owner, ...runtimeOwners.map((x) => String(x).trim()).filter(Boolean)])]
    }
  } catch { /* no runtime additions yet */ }

  cfg.agent.port = Number(process.env.AGENT_PORT || 8787)
  cfg.agent.host = process.env.AGENT_HOST || '127.0.0.1'
  cfg.agent.maxIterations = cfg.agent.maxIterations || 25
  cfg.agent.timeoutSeconds = cfg.agent.timeoutSeconds || 1800
  cfg.agent.progressAfterMs = cfg.agent.progressAfterMs || 8000
  cfg.agent.jobFirstPollMs = cfg.agent.jobFirstPollMs || 1200
  cfg.agent.jobPollMs = cfg.agent.jobPollMs || 4000
  cfg.agent.maxTaskSeconds = cfg.agent.maxTaskSeconds || 21600

  cfg.llm.baseUrl = process.env.LLM_BASE_URL || ''
  cfg.llm.apiKey = process.env.LLM_API_KEY || ''
  cfg.llm.model = process.env.LLM_MODEL || ''
  cfg.llm.fallbackModels = String(process.env.LLM_FALLBACK_MODELS || '')
    .split(',').map((s) => s.trim()).filter(Boolean)

  return cfg
}
