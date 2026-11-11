// plugin-host.js — pemuat & penjalan plugin (sisi Node).
//
// Plugin = file `.js` di folder `plugins/`. Karena butuh socket WhatsApp,
// plugin berjalan di proses Node (transport), bukan di inti Python. Setiap
// plugin menerima `ctx` berisi API siap pakai:
//
//   ctx.wa        — API WhatsApp: send, sendRich, forward, react, group, dll.
//   ctx.airich    — pembangun kartu kaya (image/sources/suggest/footer).
//   ctx.msg       — info pesan pemicu (chatId, senderId, text, isGroup, args).
//   ctx.args      — argumen (dari tool plugin_run atau job cron).
//   ctx.log(...)  — catat ke audit/log.
//   ctx.state     — penyimpanan key-value persisten per-plugin (data/plugins/).
//   ctx.schedule  — helper cron (add/list/remove) — lihat scheduler.js.
//
// Kontrak plugin (default export):
//
//   export default {
//     name: 'salam',
//     description: 'Balas salam',
//     commands: ['halo'],                 // opsional: dipicu pesan diawali '!halo'
//     async run(ctx) { ... }              // wajib
//   }
//
// Keamanan: hanya owner yang bisa memicu (dijaga di sisi Python & router).
// Setiap plugin dibungkus try/catch + timeout agar bot tidak ikut mati.
import fs from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const PLUGINS_DIR = path.resolve(process.cwd(), 'plugins')
const STATE_DIR = path.resolve(process.cwd(), 'data', 'plugins')

function ensureDirs() {
  fs.mkdirSync(PLUGINS_DIR, { recursive: true })
  fs.mkdirSync(STATE_DIR, { recursive: true })
}

function enabledMap() {
  ensureDirs()
  try {
    const value = JSON.parse(fs.readFileSync(path.join(STATE_DIR, '.system', 'enabled.json'), 'utf8'))
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { return {} }
}

function isPluginEnabled(name) {
  return enabledMap()[name] !== false
}

/** Daftar file plugin (nama modul -> path absolut). */
export function listPluginFiles() {
  ensureDirs()
  const out = {}
  for (const f of fs.readdirSync(PLUGINS_DIR)) {
    if (!f.endsWith('.js') || f.startsWith('_')) continue
    out[f.replace(/\.js$/, '')] = path.join(PLUGINS_DIR, f)
  }
  return out
}

/** Muat satu plugin (cache-bust agar perubahan langsung aktif). */
export async function loadPlugin(name, { allowDisabled = false } = {}) {
  const files = listPluginFiles()
  const file = files[name]
  if (!file) throw new Error(`plugin '${name}' tidak ada di plugins/`)
  if (!allowDisabled && !isPluginEnabled(name)) throw new Error(`plugin '${name}' dinonaktifkan`)
  const url = pathToFileURL(file).href + `?t=${Date.now()}`
  const mod = await import(url)
  const def = mod.default ?? mod.plugin ?? mod
  if (!def || typeof def.run !== 'function') {
    throw new Error(`plugin '${name}' harus mengekspor default { name, run }`)
  }
  return def
}

/** Daftar plugin beserta metadata (nama, deskripsi, perintah). */
export async function listPlugins() {
  const files = listPluginFiles()
  const out = []
  for (const [name, file] of Object.entries(files)) {
    const rec = { name, file, ok: true, enabled: isPluginEnabled(name) }
    try {
      const def = await loadPlugin(name, { allowDisabled: true })
      rec.name = def.name || name
      rec.description = def.description || ''
      rec.commands = Array.isArray(def.commands) ? def.commands : []
    } catch (e) {
      rec.ok = false
      rec.error = e.message
    }
    out.push(rec)
  }
  return out
}

/** Penyimpanan key-value per-plugin (JSON, atomik). */
export function pluginState(name) {
  ensureDirs()
  const file = path.join(STATE_DIR, `${name}.json`)
  const read = () => {
    try { return JSON.parse(fs.readFileSync(file, 'utf8') || '{}') } catch { return {} }
  }
  const write = (obj) => {
    const tmp = `${file}.tmp`
    fs.writeFileSync(tmp, JSON.stringify(obj, null, 2))
    fs.renameSync(tmp, file)
  }
  return {
    get: (k, dflt = null) => { const o = read(); return k in o ? o[k] : dflt },
    set: (k, v) => { const o = read(); o[k] = v; write(o) },
    all: () => read(),
    clear: () => write({}),
  }
}

/**
 * Jalankan plugin dengan `ctx` lengkap.
 * `makeCtx` disuntik oleh pemanggil (wa.js/scheduler.js) supaya API WA tersedia.
 */
export async function runPlugin(name, { args = {}, msg = {}, makeCtx, timeoutMs = 30000 } = {}) {
  const def = await loadPlugin(name)
  const logs = []
  const ctx = makeCtx ? makeCtx({ plugin: def, logs }) : {}
  ctx.args = args
  ctx.msg = msg
  ctx.log = (...a) => { logs.push(a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' ')) }
  ctx.state = pluginState(def.name || name)
  ctx.plugin = { name: def.name || name, description: def.description || '' }

  const run = Promise.resolve().then(() => def.run(ctx))
  let timer
  const timeout = new Promise((_, rej) => {
    timer = setTimeout(() => rej(new Error(`plugin '${name}' timeout ${timeoutMs}ms`)), timeoutMs)
    if (timer.unref) timer.unref()
  })
  try {
    const result = await Promise.race([run, timeout])
    return { ok: true, result: result ?? null, logs }
  } finally {
    clearTimeout(timer)
  }
}
