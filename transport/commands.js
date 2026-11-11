// commands.js — lapisan perintah cepat "!" (deterministik, TANPA AI).
//
// Instan, tanpa biaya, hasil selalu sama. Dipakai untuk kerja berulang yang
// sudah pasti. Pesan berawalan "!" tidak pernah menyentuh LLM.

import fs from 'node:fs/promises'
import { agentHeaders } from './agent-auth.js'
import { buildAfkHtml } from './api/afk-html.js'
import { buildPreamble, getPreambleStatus, reloadSkillCache } from './memori.js'
import path from 'node:path'
import { sendRichHtml } from './api/rich.js'
import { listPlugins } from './plugin-host.js'
import { runtimeBlocks } from '../plugins/_runtime-core.js'
import {
  searchBlocks, fanoutBlocks, faqBlocks, healthBlocks,
  menuBlocks, groupBlocks, statusBlocks, pluginsBlocks, skillsBlocks,
  skillHitsBlocks, skillDetailBlocks, diagBlocks, logBlocks, noticeBlocks, kunciGrup,
} from './api/ui.js'

const CMD_BUF_DIR = path.resolve(process.cwd(), 'data', 'cmdbuf')

// Simpan HTML terakhir per-chat (dipakai !run).
export async function bufferHtml(chatId, html) {
  await fs.mkdir(CMD_BUF_DIR, { recursive: true })
  const file = path.join(CMD_BUF_DIR, `${Buffer.from(chatId).toString('hex')}.html`)
  await fs.writeFile(file, html, 'utf8')
}

export async function readBufferedHtml(chatId) {
  const file = path.join(CMD_BUF_DIR, `${Buffer.from(chatId).toString('hex')}.html`)
  try {
    return await fs.readFile(file, 'utf8')
  } catch {
    return null
  }
}

// Buang pembungkus blok kode Markdown (```html … ``` atau ``` … ```) supaya HTML
// yang ditempel pemilik di dalam fence tetap terdeteksi & dieksekusi bersih.
export function stripCodeFence(text) {
  const s = String(text ?? '').trim()
  const m = s.match(/^```[a-zA-Z0-9_+.-]*[ \t]*\r?\n([\s\S]*?)\r?\n?```\s*$/)
  if (m) return m[1].trim()
  // fence tanpa bahasa / tanpa newline: ```<html>…```
  const m2 = s.match(/^```([\s\S]*?)```\s*$/)
  if (m2) return m2[1].trim()
  return s
}

// Nama tag HTML yang lazim. Dipakai untuk mengenali fragmen HTML, bukan hanya
// dokumen penuh (<!doctype>/<html>). Tanpa ini, `<div>…</div>` dari pemilik
// ditolak padahal itu HTML yang sah.
const HTML_TAG_NAMES = 'html|head|body|title|meta|link|style|script|div|p|span|h[1-6]|a|img|ul|ol|li|dl|dt|dd|table|thead|tbody|tfoot|tr|td|th|section|article|aside|header|footer|nav|main|form|input|button|select|option|textarea|label|fieldset|legend|svg|canvas|iframe|video|audio|source|picture|figure|figcaption|blockquote|pre|code|br|hr|em|strong|b|i|u|small|mark|details|summary|template|noscript|marquee|center|font'

// Deteksi teks yang berisi HTML. Longgar tapi aman: dipakai untuk memutuskan
// apakah sesuatu LAYAK dijalankan sebagai widget. False positive hanya membuat
// teks disimpan sebagai "HTML terakhir" (tidak berbahaya); `!run` tetap perlu
// perintah eksplisit.
export function looksLikeHtml(text) {
  // Buang mention di AWAL (mis. "@628123 <div>…" yang lazim di grup) SEBELUM
  // deteksi: tanpa ini, prefix mention membuat tag pertama tidak dikenali dan
  // HTML pendek (<div>x</div>) ditolak — `!run` lalu bilang "Belum ada HTML".
  const t = stripLeadingMentions(stripCodeFence(String(text ?? ''))).trim()
  if (!t) return false
  const low = t.toLowerCase()
  if (low.startsWith('<!doctype') || low.startsWith('<html')) return true
  if (new RegExp(`^<(${HTML_TAG_NAMES})\\b`, 'i').test(t)) return true
  // Deteksi umum: minimal 3 tag khas HTML (mis. teks dengan <b>…</b> <i>…</i>).
  const tags = t.match(new RegExp(`</?(?:${HTML_TAG_NAMES})\\b[^>]*>`, 'gi'))
  return Boolean(tags && tags.length >= 3)
}

// Buang sebutan (mention) di AWAL teks — mis. "@6281234567 !run" yang lazim di
// grup (WhatsApp menaruh mention sebagai "@<angka>" di dalam teks, bukan hanya
// di `mentionedJid`). Tanpa ini, perintah "!" TIDAK pernah terdeteksi karena
// teks tidak dimulai dengan "!" — pesan jatuh ke jalur AI dan `!run` gagal.
// Hanya mention di AWAL yang dibuang, jadi teks seperti "panggil @628…" utuh.
export function stripLeadingMentions(text) {
  const s = String(text ?? '')
  const invisible = '\\s\\u200b\\u200c\\u200d\\u200e\\u200f\\u2060\\u2066-\\u2069\\ufeff'
  const re = new RegExp(`^[${invisible}]*(?:@\\d+[${invisible}]*)+`, 'u')
  const stripped = s.replace(re, '').trim()
  // Kalau pesan HANYA berisi mention (tanpa teks lain), kembalikan aslinya
  // supaya jalur AI tetap menerima konteks "aku disebut" alih-alih teks kosong.
  return stripped || s.trim()
}

// Apakah pesan OWNER boleh diproses di grup TANPA mengetag bot? Ya untuk
// perintah "!" dan konten HTML (teks/lampiran .html/.txt). Dipakai wa.js
// supaya di grup cukup kirim HTML lalu ketik !run — tanpa '@bot'.
export function ownerBypassGroupMention({ text, isOwner, hasTextDoc } = {}) {
  if (!isOwner) return false
  const t = stripLeadingMentions(text)
  // '/' juga diterima untuk perintah yang dikenal (mis. /prompt, /skills).
  const slashCmd = /^\/[a-z0-9_-]+/i.test(t)
  return Boolean(t.startsWith('!') || slashCmd || extractHtml(t) || hasTextDoc)
}

// Kembalikan HTML bersih (fence dibuang, mention awal dibuang) bila teks memang
// HTML, atau null. Mention di buang supaya widget TIDAK merender "@62…" di
// dalam HTML saat owner menulis "@bot <div>…" di grup.
export function extractHtml(text) {
  const t = stripLeadingMentions(stripCodeFence(String(text ?? ''))).trim()
  return t && looksLikeHtml(t) ? t : null
}

// Ambil HTML dari ISI pesan yang DI-REPLY (`inner` = objek pesan hasil buka
// pembungkus viewOnce/ephemeral). Sumber berurutan:
//   1) teks kutipan (langsung atau di dalam ```fence```),
//   2) lampiran dokumen bertekstual (.html/.txt/.md/…) → diunduh via `download`.
// `download(doc)` harus mengembalikan Buffer/string isi dokumen. Mengembalikan
// string HTML bersih atau null. Dipisah dari transport supaya bisa diuji murni.
export async function htmlFromQuotedInner(inner, { download } = {}) {
  if (!inner) return null
  const text = (
    inner.conversation ||
    inner.extendedTextMessage?.text ||
    inner.imageMessage?.caption ||
    inner.videoMessage?.caption ||
    inner.documentMessage?.caption ||
    ''
  ).trim()
  const fromText = extractHtml(text)
  if (fromText) return fromText
  const doc = inner.documentMessage
  if (doc && isTextLikeDocument({ fileName: doc.fileName, mimetype: doc.mimetype }) && typeof download === 'function') {
    try {
      const buf = await download(doc)
      return extractHtml(Buffer.from(buf).toString('utf8'))
    } catch {
      return null
    }
  }
  return null
}

// Deteksi lampiran dokumen HTML dari METADATA saja (tanpa mengunduh isinya).
// Dipakai transport (wa.js) untuk memutuskan apakah sebuah dokumen perlu
// diunduh lalu ditampilkan sebagai preview WebView (web2ui).
export function isHtmlDocument({ fileName, mimetype } = {}) {
  const name = String(fileName || '').trim().toLowerCase()
  const mime = String(mimetype || '').trim().toLowerCase()
  if (/\.x?html?$/.test(name)) return true
  return mime === 'text/html' || mime === 'application/xhtml+xml'
}

// Dokumen bertekstual yang MASIH MUNGKIN berisi HTML (mis. file .txt / .md yang
// isinya HTML, atau lampiran tanpa nama). Untuk ini kita unduh isinya lalu
// sniff dengan looksLikeHtml(). Dipakai `!run` atas lampiran yang di-reply.
export function isTextLikeDocument({ fileName, mimetype } = {}) {
  if (isHtmlDocument({ fileName, mimetype })) return true
  const name = String(fileName || '').trim().toLowerCase()
  const mime = String(mimetype || '').trim().toLowerCase()
  if (/\.(txt|text|md|markdown|htm|xhtml|xml|json)$/.test(name)) return true
  if (mime.startsWith('text/')) return true
  if (mime === 'application/json' || mime === 'application/xml') return true
  // Tanpa nama & tanpa mime → coba unduh (best-effort), sniff setelahnya.
  if (!name && !mime) return true
  return false
}

// Registry perintah. Setiap perintah: { name, help, run({ctx,args,sock,config}) → replies[] }
export async function loadCommands() {
  const map = new Map()

  // !afk — status tersimpan di SQLite; rich HTML dikirim sebagai AI Rich WebView.
  map.set('afk', {
    name: 'afk',
    help: 'Set AFK: !afk <reason> · list: !afk -ls · return: !afk off',
    async run({ ctx, args, sock, config }) {
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      const base = `http://${host}:${port}`
      const first = String(args?.[0] || '').trim().toLowerCase()
      const request = async (method, body) => {
        const response = await fetch(`${base}/afk${method === 'GET' ? `?chat_id=${encodeURIComponent(ctx.chatId)}` : ''}`, {
          method, signal: AbortSignal.timeout(12000),
          headers: agentHeaders({ 'Content-Type': 'application/json' }),
          ...(body ? { body: JSON.stringify(body) } : {}),
        })
        const json = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(json.detail || json.error || `HTTP ${response.status}`)
        return json
      }
      if (['off', 'stop', 'back', 'kembali', 'selesai'].includes(first)) {
        const result = await request('POST', { action: 'stop', chat_id: ctx.chatId, sender_id: ctx.senderId })
        if (!result.ok) return [{ type: 'text', text: result.error || 'Kamu tidak sedang AFK.' }]
        return [{ type: 'text', text: 'Welcome back. Sesi AFK kamu sudah ditutup.' }]
      }
      const listing = ['-ls', 'ls', 'list', 'daftar'].includes(first)
      if (!listing && !args?.length) {
        return [{ type: 'text', text: 'Format: !afk <alasan> · !afk -ls · !afk off' }]
      }
      if (!listing) {
        const reason = (args || []).join(' ').trim().slice(0, 240)
        await request('POST', { action: 'start', chat_id: ctx.chatId, sender_id: ctx.senderId,
          sender_name: ctx.senderName || ctx.senderId, reason })
      }
      const snapshot = await request('GET')
      const session = !listing ? (snapshot.items || []).find((item) => item.sender_id === ctx.senderId) : null
      const html = buildAfkHtml({ mode: listing ? 'list' : 'single', items: snapshot.items || [], session,
        wsUrl: snapshot.ws_url || '', wsToken: snapshot.ws_token || '', chatId: snapshot.chat_id || ctx.chatId })
      let url = snapshot.public_url || (snapshot.ws_url ? snapshot.ws_url.replace(/^wss:/i, 'https:').replace(/\/ws\/afk$/i, '') : 'https://example.com')
      const sources = [...new Set([snapshot.public_url, snapshot.ws_url].filter(Boolean).flatMap((value) => {
        try { return [new URL(value).host] } catch { return [] }
      }))]
      await sendRichHtml(sock, ctx.chatId, html, { title: listing ? 'AFK sekarang' : 'AFK status',
        tabHeader: 'AFK', url, sources, includeCardLabels: false })
      return []
    },
  })

  // !ping — denyut runtime (RAM, node, uptime, load, modul).
  // Bentuk keluarannya IDENTIK dengan kata kunci `test` setelah mention/reply;
  // keduanya memakai modul yang sama (plugins/_runtime-core.js) supaya tidak
  // pernah menyimpang satu sama lain. Tanpa AI, tanpa biaya token.
  map.set('ping', {
    name: 'ping',
    help: 'Machine state: uptime, memory, load and add-ons',
    async run({ ctx }) {
      let modules = 0
      try {
        const list = await listPlugins()
        modules = list.filter((p) => p.ok).length
      } catch { /* biarkan 0 bila gagal dibaca */ }
      return runtimeBlocks({
        kind: 'ping',
        modules,
      })
    },
  })

  // !help — menu imersif (kartu GenAI), bahasa Indonesia, tanpa istilah asing.
  //
  // Kenapa kartu, bukan teks biasa: 15+ perintah dalam satu paragraf panjang
  // sulit dibaca di layar ponsel. Kartu memberi label kategori berwarna,
  // sehingga mata bisa melompat ke bagian yang dicari.
  map.set('help', {
    name: 'help',
    help: 'Show every command',
    async run({ ctx, args, config }) {
      const plugins = await listPlugins().catch(() => [])
      const sudahAda = new Set([...map.keys(), 'stop', 'lanjut'])

      const kelompok = [
        {
          judul: 'Everyday · 日常 · 每天都会用',
          warna: 'pink',
          items: [
            { cmd: 'ping', desc: 'check that I am awake and how fast' },
            { cmd: 'status', desc: 'see what I am working on' },
            { cmd: 'health', desc: 'run a quick self-check' },
            { cmd: 'diag', desc: 'show limits in force & what got blocked' },
            { cmd: 'help', desc: 'open this menu again' },
            { cmd: 'models', desc: 'list models configured by the server' },
            { cmd: 'model <name>', desc: 'choose a configured model for this sender' },
            { cmd: 'addown <62…>', desc: 'add another owner after explicit confirmation' },
          ],
        },
        {
          judul: 'Memory & Search · 记忆与搜索',
          warna: 'lilac',
          items: [
            { cmd: 'search <words>', desc: 'look in chats, files, memory and skills' },
            { cmd: 'prompt', desc: 'view or switch my tone' },
            { cmd: 'skills', desc: 'browse my skills' },
          ],
        },
        {
          judul: 'Teamwork · 团队 · 一起干活',
          warna: 'mint',
          items: [
            { cmd: 'team <task> | <task>', desc: 'several little me working at once' },
            { cmd: 'group ask <question>', desc: 'answer from the group books' },
            { cmd: 'group list', desc: 'list the books I can read' },
            { cmd: 'poll <question> | <options>', desc: 'start a poll in the group' },
          ],
        },
        {
          judul: 'Files & Terminal · 文件与终端',
          warna: 'amber',
          items: [
            { cmd: 'run', desc: 'render an HTML page full screen' },
            { cmd: 'code', desc: 'send a code snippet as a tidy card' },
            { cmd: 'log', desc: 'recent events' },
            { cmd: 'plugins', desc: 'installed add-ons' },
          ],
        },
        {
          judul: 'Task Control · 控制 · 开始与停止',
          warna: 'slate',
          items: [
            { cmd: 'stop', desc: 'stop the running task' },
            { cmd: 'resume', desc: 'continue from the last checkpoint' },
            { cmd: 'summary on', desc: 'daily group summary (on/off/status)' },
            { cmd: 'tenant', desc: 'rooms and their permissions' },
          ],
        },
      ]

      // Perintah plugin ikut masuk, tapi tidak mengulang yang sudah ada.
      const pluginItems = []
      for (const pl of plugins) {
        if (!pl.ok || pl.enabled === false) continue
        for (const c of pl.commands || []) {
          const nama = String(c).toLowerCase()
          if (sudahAda.has(nama)) continue
          pluginItems.push({ cmd: nama, desc: pl.description || pl.name || 'add-on' })
        }
      }
      if (pluginItems.length) {
        kelompok.push({ judul: 'Add-ons · 附加', warna: 'rose', items: pluginItems })
      }

      // Jaring pengaman: perintah baru yang lupa dimasukkan ke kelompok di atas
      // tetap muncul. Alias (mis. `halo` → `ping`) tidak didaftar dua kali.
      const terdaftar = new Set(kelompok.flatMap((g) => g.items.map((i) => i.cmd.split(' ')[0])))
      const punyaAlias = new Set(
        [...map.values()].filter((c) => c.aliasOf).map((c) => c.aliasOf),
      )
      const sisa = [...map.values()]
        .filter((c) => !c.aliasOf && !punyaAlias.has(c.name) && !terdaftar.has(c.name))
        .map((c) => ({ cmd: c.name, desc: c.help }))
      if (sisa.length) kelompok.push({ judul: 'More · 其他', warna: 'slate', items: sisa })

      const link = config?.ui?.helpUrl ? { label: 'Open guide', url: config.ui.helpUrl } : undefined

      // `!help <kelompok>` (nama atau nomor) → isi lengkap SATU kelompok.
      const minta = String(args?.[0] || '').trim().toLowerCase()
      if (minta) {
        const nomor = /^[1-9]$/.test(minta) ? Number(minta) - 1 : -1
        const g = kelompok[nomor] || kelompok.find((x) => kunciGrup(x.judul) === minta || kunciGrup(x.judul).startsWith(minta))
        if (g) return [{ type: 'card', blocks: groupBlocks(g, { chatId: ctx.chatId, link }) }]
        return [{
          type: 'card',
          blocks: noticeBlocks({
            title: 'No such group · 没有这个分组', color: 'amber', chatId: ctx.chatId,
            text: `I do not know “${minta.slice(0, 30)}”. Groups: ${kelompok.map((x) => kunciGrup(x.judul)).join(', ')}.`,
            actions: [{ label: 'Back to menu', cmd: '!help' }],
          }),
        }]
      }

      return [{
        type: 'card',
        blocks: menuBlocks(kelompok, {
          chatId: ctx.chatId,
          link,
          footer: 'Type without the "!" for a full task — I run it, you do not copy commands ♡',
        }),
      }]
    },
  })

  // !run — HTML terakhir → widget fullscreen di WhatsApp (tanpa AI).
  // Sumber HTML, berurutan: (1) argumen `!run <html>`, (2) pesan yang DI-REPLY
  // (teks HTML / HTML di dalam ```fence```, atau lampiran .html/.txt), (3) HTML
  // terakhir di buffer chat ini.
  map.set('run', {
    name: 'run',
    help: 'Render HTML full screen (send HTML, reply to it, or attach .html)',
    async run({ ctx, args, sock }) {
      let html = null
      // 1) HTML langsung sebagai argumen: !run <html>. Ambil RAW dari ctx.text
      //    (bukan hasil split) supaya spasi/newline di dalam HTML tidak rusak.
      //    Buang dulu mention di awal (mis. "@628123 !run <div>…") agar prefix
      //    grup tidak ikut terbawa ke HTML.
      const raw = stripLeadingMentions(ctx?.text || '').replace(/^\s*!run\b/i, '').trim()
      if (raw) html = extractHtml(raw)
      if (!html) {
        const inline = (args || []).join(' ').trim()
        if (inline) html = extractHtml(inline)
      }
      // 2) HTML dari pesan yang di-reply (teks, fence kode, atau lampiran).
      if (!html && ctx.quotedHtml) html = extractHtml(ctx.quotedHtml)
      // 3) HTML terakhir dari buffer chat ini (bersihkan fence bila ada)
      if (!html) html = extractHtml(await readBufferedHtml(ctx.chatId))
      if (!html) {
        return [{ type: 'text', text: 'No HTML yet. Send HTML (text or .html attachment), or reply to an HTML message with !run.' }]
      }
      try {
        // Simpan sebagai HTML terakhir supaya `!run` berikutnya (tanpa reply)
        // tetap bisa menampilkannya.
        await bufferHtml(ctx.chatId, html)
        await sendRichHtml(sock, ctx.chatId, html, { url: 'https://example.com', sources: ['example.com'] })
        return [] // widget sudah terkirim; tidak perlu teks tambahan
      } catch (e) {
        return [{ type: 'text', text: `Could not send the widget: ${e.message}` }]
      }
    },
  })

  // !code — kirim potongan kode sebagai kartu kode asli
  map.set('code', {
    name: 'code',
    help: 'Send a code snippet as a tidy card',
    async run({ args }) {
      const lang = (args[0] || 'javascript').toLowerCase()
      const code = args.slice(1).join(' ')
      if (!code) return [{ type: 'text', text: 'Format: !code <language> <code>' }]
      return [{ type: 'code', language: lang, code }]
    },
  })

  // !poll — buat jajak pendapat asli
  map.set('poll', {
    name: 'poll',
    help: 'Start a poll: !poll question | option one | option two',
    async run({ args }) {
      const parts = args.join(' ').split('|').map((s) => s.trim()).filter(Boolean)
      if (parts.length < 3) {
        return [{ type: 'text', text: 'Format: !poll Question | Option1 | Option2' }]
      }
      const [name, ...values] = parts
      return [{ type: 'poll', poll: { name, values, selectableCount: 1 } }]
    },
  })

  // !plugins — daftar plugin yang bisa dipakai (Kwee menulisnya sendiri)
  map.set('plugins', {
    name: 'plugins',
    help: 'List installed add-ons',
    async run({ ctx }) {
      const { listPlugins } = await import('./plugin-host.js')
      const plugins = await listPlugins().catch(() => [])
      return [{ type: 'card', blocks: pluginsBlocks(plugins, { chatId: ctx?.chatId }) }]
    },
  })

  // !status — agent sedang mengerjakan apa (mirip typing/terminal log)
  map.set('status', {
    name: 'status',
    help: 'See what I am working on',
    async run({ ctx, config }) {
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      try {
        const r = await fetch(`http://${host}:${port}/status?chat_id=${encodeURIComponent(ctx.chatId)}`, { signal: AbortSignal.timeout(5000), headers: agentHeaders() })
        const j = await r.json()
        const items = j.activities || []
        return [{ type: 'card', blocks: statusBlocks(items, { chatId: ctx.chatId }) }]
      } catch (e) {
        return [{
          type: 'card',
          blocks: noticeBlocks({
            title: 'Core unreachable · 无法连接', color: 'rose', chatId: ctx.chatId,
            text: `I could not reach the agent core: ${e.message}`,
            actions: [{ label: 'Retry', cmd: '!status' }, { label: 'Diagnostics', cmd: '!diag' }],
          }),
        }]
      }
    },
  })

  // !prompt — lihat/ganti PERSONA aktif (tanpa restart).
  // Logika prioritas (override tenant → persona aktif → persona.md) ada di
  // agent/promptlib.py; di sini hanya memformat balasan.
  map.set('prompt', {
    name: 'prompt',
    help: 'Tone: !prompt | !prompt set <name> | !prompt reset | !prompt reload',
    async run({ ctx, args, config }) {
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      const base = `http://${host}:${port}`
      const sub = String(args[0] || '').toLowerCase()
      const opts = (body) => ({
        method: 'POST',
        signal: AbortSignal.timeout(15000),
        headers: agentHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(body),
      })
      const sender = ctx.senderId || ''
      try {
        if (sub && ['set', 'ganti', 'pakai', 'use'].includes(sub)) {
          const name = String(args[1] || '').trim()
          if (!name) {
            const r = await fetch(`${base}/prompt`, { signal: AbortSignal.timeout(8000), headers: agentHeaders() })
            const j = await r.json()
            const daftar = (j.tersedia || []).map((x) => `- ${x.name} (${x.chars} char)`).join('\n')
            return [{ type: 'text', text: `Name the tone you want ♡\n\nAvailable:\n${daftar}` }]
          }
          const r = await fetch(`${base}/prompt`, opts({ action: 'set', name, sender_id: sender }))
          const j = await r.json()
          if (j.error) return [{ type: 'text', text: `Failed: ${j.error}` }]
          return [{ type: 'text', text: `♡ my tone is now: *${j.aktif}* (${j.file}). Applies from the next message.` }]
        }
        if (sub && ['reset', 'default', 'bawaan'].includes(sub)) {
          const r = await fetch(`${base}/prompt`, opts({ action: 'reset', sender_id: sender }))
          const j = await r.json()
          if (j.error) return [{ type: 'text', text: `Failed: ${j.error}` }]
          return [{ type: 'text', text: `♡ back to my default tone: *${j.aktif}*.` }]
        }
        if (sub && ['reload', 'muat'].includes(sub)) {
          const r = await fetch(`${base}/prompt`, opts({ action: 'reload', sender_id: sender }))
          const j = await r.json()
          if (j.error) return [{ type: 'text', text: `Failed: ${j.error}` }]
          const i = j.info || {}
          return [{ type: 'text', text: `♡ tone and skills reloaded from disk.\nActive: *${i.aktif}* · rules ${i.rules_chars} chars` }]
        }
        const r = await fetch(`${base}/prompt`, { signal: AbortSignal.timeout(8000), headers: agentHeaders() })
        const j = await r.json()
        const aktif = j.aktif || '?'
        const daftar = (j.tersedia || [])
          .map((x) => `${x.aktif ? '✅' : '•'} ${x.name} — ${x.chars} char`)
          .join('\n')
        const ov = j.tenant_override ? `\nOverride tenant: ${j.tenant_override.file} (${j.tenant_override.chars} char)` : ''
        const prev = (j.persona_preview || '').trim()
        return [{
          type: 'text',
          text: [
            `🧭 *Prompt aktif*: ${aktif}`,
            `Berkas: ${j.file} (${j.sumber})`,
            `Persona ${j.persona_chars} char · rules ${j.rules_chars} char${ov}`,
            '',
            'Persona tersedia:',
            daftar || '(tidak ada)',
            '',
            prev ? `Cuplikan: ${prev.slice(0, 200)}…` : '',
            '',
            'Ganti: *!prompt set <nama>* · *!prompt reset* · *!prompt reload*',
          ].filter(Boolean).join('\n'),
        }]
      } catch (e) {
        return [{ type: 'text', text: `I could not reach the agent core: ${e.message}` }]
      }
    },
  })

  // !skills — statistik, cari, dan muat SKILL (hidden tidak pernah dibuka).
  map.set('skills', {
    name: 'skills',
    help: 'Skills: !skills | !skills search <words> | !skills get <name>',
    async run({ ctx, args, config }) {
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      const base = `http://${host}:${port}`
      const kartuSkill = (blocks) => [{ type: 'card', blocks }]
      const sub = String(args[0] || '').toLowerCase()
      const getJson = async (path) => {
        const r = await fetch(`${base}${path}`, { signal: AbortSignal.timeout(12000), headers: agentHeaders() })
        return r.json()
      }
      try {
        if (sub === 'reload' || sub === 'muat') {
          reloadSkillCache(ctx.chatId)
          buildPreamble(config, 'reload system skills anti-slop accessibility backend', ctx.chatId)
          const j = await getJson('/skills?reload=1')
          if (String(config?.engine?.mode || 'openhands').toLowerCase() !== 'native') {
            const live = getPreambleStatus(ctx.chatId)
            j.internal = live.internal.length
            j.nama_internal = live.internal
            j.last_prompt_load = live
            j.load_visual = { status: live.status, loaded_count: live.loaded_count || 0,
              prepared_count: live.internal.length + live.external.length,
              external_count: live.external.length, deferred_count: live.deferred_count || 0, sequence: live.sequence }
          }
          return kartuSkill(noticeBlocks({
            title: 'Skills reloaded · 已重载', color: 'mint', chatId: ctx.chatId,
            text: `${j.internal} core and ${j.external} extra skills scanned; prompt injection will be verified on the next request.`,
            actions: [{ label: 'All skills', cmd: '!skills' }],
          }))
        }
        if (sub === 'get' || sub === 'lihat' || sub === 'muat') {
          const name = String(args[1] || '').trim()
          if (!name) return kartuSkill(noticeBlocks({ title: 'Which skill? · 哪个', color: 'amber', chatId: ctx.chatId, text: 'Name it: !skills get <name>', actions: [{ label: 'All skills', cmd: '!skills' }] }))
          const file = String(args[2] || '').trim()
          const q = `/skills?name=${encodeURIComponent(name)}${file ? `&file=${encodeURIComponent(file)}` : ''}`
          const j = await getJson(q)
          if (j.error) return kartuSkill(noticeBlocks({ title: 'Could not open · 失败', color: 'rose', chatId: ctx.chatId, text: String(j.error), actions: [{ label: 'All skills', cmd: '!skills' }] }))
          return kartuSkill(skillDetailBlocks(j, { chatId: ctx.chatId }))
        }
        if (sub && ['cari', 'search', 'match', 'temukan'].includes(sub)) {
          const query = args.slice(1).join(' ').trim()
          if (!query) return kartuSkill(noticeBlocks({ title: 'Search what? · 搜索', color: 'amber', chatId: ctx.chatId, text: 'Example: !skills search react component', actions: [{ label: 'All skills', cmd: '!skills' }] }))
          const j = await getJson(`/skills?query=${encodeURIComponent(query)}`)
          return kartuSkill(skillHitsBlocks(query, j.matches || [], { chatId: ctx.chatId }))
        }
        const j = await getJson('/skills')
        if (String(config?.engine?.mode || 'openhands').toLowerCase() !== 'native') {
          const live = getPreambleStatus(ctx.chatId)
          j.internal = live.internal.length
          j.nama_internal = live.internal
          j.last_prompt_load = live
          j.load_visual = { status: live.status, loaded_count: live.loaded_count || 0,
            prepared_count: live.internal.length + live.external.length,
            external_count: live.external.length, deferred_count: live.deferred_count || 0,
            sequence: live.sequence }
        }
        return kartuSkill(skillsBlocks(j, { chatId: ctx.chatId }))
      } catch (e) {
        return kartuSkill(noticeBlocks({
          title: 'Core unreachable · 无法连接', color: 'rose', chatId: ctx.chatId,
          text: `I could not reach the agent core: ${e.message}`,
          actions: [{ label: 'Retry', cmd: '!skills' }, { label: 'Diagnostics', cmd: '!diag' }],
        }))
      }
    },
  })

  // !tenant — ruangan & mekanisme owner ini (multi-tenant)
  // !cari — pencarian lintas sumber (fitur "otak kedua").
  map.set('cari', {
    name: 'cari',
    help: 'Search history, files, memory and skills',
    async run({ ctx, args, config }) {
      const q = String(args.join(' ')).trim()
      if (!q) return [{ type: 'text', text: 'What are we looking for? Type: !search <words> ♡' }]
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      try {
        const r = await fetch(`http://${host}:${port}/search`, {
          method: 'POST',
          signal: AbortSignal.timeout(30000),
          headers: agentHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ query: q, chat_id: ctx.chatId, sender_id: ctx.senderId, limit: 8 }),
        })
        const j = await r.json()
        if (j.error) return [{ type: 'text', text: `Search failed: ${j.error}` }]
        return [{ type: 'card', blocks: searchBlocks(j, { chatId: ctx.chatId }) }]
      } catch (e) {
        return [{ type: 'text', text: `I could not reach the agent core: ${e.message}` }]
      }
    },
  })

  // !tim — beberapa sub-agent PARALEL. Pemisah tugas: tanda pipa.
  map.set('tim', {
    name: 'tim',
    help: 'Several assistants working at once',
    async run({ ctx, args, config }) {
      const mentah = String(args.join(' ')).trim()
      const tugas = mentah.split('|').map((x) => x.trim()).filter(Boolean)
      if (tugas.length < 2) {
        return [{
          type: 'text',
          text: 'Separate at least two tasks with | ♡\n\nExample:\n!team compare laptop A | compare laptop B | find reviews for both',
        }]
      }
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      try {
        const r = await fetch(`http://${host}:${port}/fanout`, {
          method: 'POST',
          signal: AbortSignal.timeout(180000),
          headers: agentHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            chat_id: ctx.chatId,
            sender_id: ctx.senderId,
            tasks: tugas.slice(0, 5).map((t) => ({ judul: t.slice(0, 60), goal: t })),
          }),
        })
        const j = await r.json()
        if (!r.ok || j.error || j.ok === false) {
          return [{ type: 'text', text: `The team could not start: ${j.detail || j.error || `HTTP ${r.status}`}` }]
        }
        return [{ type: 'card', blocks: fanoutBlocks(j, { chatId: ctx.chatId }) }]
      } catch (e) {
        return [{ type: 'text', text: `I could not reach the agent core: ${e.message}` }]
      }
    },
  })

  // !grup — admin grup dari dokumen (FAQ, aturan, sambutan).
  map.set('grup', {
    name: 'grup',
    help: 'Answer questions from group documents',
    async run({ ctx, args, config }) {
      const sub = String(args[0] || '').toLowerCase()
      const sisa = args.slice(1).join(' ').trim()
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      const kirim = async (body) => {
        const r = await fetch(`http://${host}:${port}/group`, {
          method: 'POST',
          signal: AbortSignal.timeout(60000),
          headers: agentHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ ...body, chat_id: ctx.chatId, sender_id: ctx.senderId }),
        })
        return r.json()
      }

      if (!sub || ['bantu', 'help', 'menu'].includes(sub)) {
        return [{
          type: 'text',
          text: [
            'Cara pakai:',
            '• !group ask <question> — answer from group documents',
            '• !group list — documents already indexed',
            '• !group index — rebuild the document index',
            '• !group rules — show the group rules',
            '• !group welcome — sample welcome for new members',
            '',
            'Taruh berkas .md atau .txt di folder data/knowledge/<nama ruangan>/.',
          ].join('\n'),
        }]
      }
      try {
        if (['tanya', 'ask', 'jawab'].includes(sub)) {
          if (!sisa) return [{ type: 'text', text: 'Type the question: !group ask <question>' }]
          const j = await kirim({ action: 'ask', pertanyaan: sisa })
          if (j.error) return [{ type: 'text', text: `Failed: ${j.error}` }]
          return [{ type: 'card', blocks: faqBlocks(j, { chatId: ctx.chatId }) }]
        }
        if (['indeks', 'index', 'muat'].includes(sub)) {
          const j = await kirim({ action: 'index' })
          if (j.error) return [{ type: 'text', text: `Failed: ${j.error}` }]
          return [{
            type: 'text',
            text: `Index done: ${j.dokumen} documents, ${j.potongan} chunks.\n${(j.berkas || []).map((b) => `• ${b}`).join('\n')}`,
          }]
        }
        if (['daftar', 'list'].includes(sub)) {
          const j = await kirim({ action: 'list' })
          if (j.error) return [{ type: 'text', text: `Failed: ${j.error}` }]
          return [{
            type: 'text',
            text: j.jumlah
              ? `Group documents (${j.jumlah}):\n${(j.dokumen || []).map((b) => `• ${b}`).join('\n')}`
              : `No documents yet. Put .md/.txt files in:\n${j.folder}`,
          }]
        }
        if (['aturan', 'rules'].includes(sub)) {
          const j = await kirim({ action: 'rules' })
          return [{ type: 'text', text: j.teks || j.error || 'No rules available yet.' }]
        }
        if (['sapaan', 'welcome'].includes(sub)) {
          const j = await kirim({ action: 'welcome', nama: '', grup: '' })
          return [{ type: 'text', text: j.teks || j.error || 'No welcome text yet.' }]
        }
        return [{ type: 'text', text: 'Options: ask, list, index, rules, welcome.' }]
      } catch (e) {
        return [{ type: 'text', text: `I could not reach the agent core: ${e.message}` }]
      }
    },
  })

  // !sehat — pemeriksaan integritas diri (tanpa AI).
  map.set('sehat', {
    name: 'sehat',
    help: 'Run a system self-check',
    async run({ ctx, config }) {
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      try {
        const r = await fetch(`http://${host}:${port}/selfcheck`, {
          signal: AbortSignal.timeout(30000),
          headers: agentHeaders(),
        })
        const j = await r.json()
        return [{ type: 'card', blocks: healthBlocks(j, { chatId: ctx?.chatId }) }]
      } catch (e) {
        return [{
          type: 'card',
          blocks: noticeBlocks({
            title: 'Check failed · 检查失败', color: 'rose', chatId: ctx?.chatId,
            text: `Could not run the checks: ${e.message}`,
            actions: [{ label: 'Retry', cmd: '!health' }, { label: 'Diagnostics', cmd: '!diag' }],
          }),
        }]
      }
    },
  })

  // !diag — apa yang SEBENARNYA membatasi bot ini (tanpa AI).
  // Dibuat karena keluhan "masih ditolak / over limit" sering tidak jelas
  // bagian mana yang menolak. Perintah ini menyebut angka dan setelannya.
  map.set('diag', {
    name: 'diag',
    help: 'Show the limits actually in force, and what got blocked',
    async run({ ctx, config }) {
      const sec = config?.security || {}
      const llm = config?.llm || {}
      const ui = config?.ui || {}
      const mode = String(sec.accessMode || 'owner').toLowerCase()
      const kb = (n) => `${Math.round(Number(n || 0) / 1024)} KB`
      const baris = [
        ['Access mode', mode === 'owner' ? 'owner (full)' : 'sandbox (strict)'],
        ['Self-kill guard', sec.guardSelfKill === false ? 'off' : 'on'],
        ['Output limit', `${kb(sec.outputLimitBytes || 200000)} · tool ${kb(sec.toolResultLimitBytes || 65536)} · shell ${sec.execTimeoutSeconds || 1800}s`],
        ['Engine', `${String(config?.engine?.mode || 'openhands')}${config?.engine?.fallback === false ? ' (no fallback)' : ' (fallback: native)'}`],
        ['Model', `${llm.model || '-'} · ${llm.maxTokens || 2048} tok · ${llm.timeoutSeconds || 120}s`],
        ['Cards', `${ui.forceCard === false ? 'plain text allowed' : 'GenAI required'} · trace ${ui.showTrace === false ? 'off' : 'on'}`],
        ['Deny paths', `${(sec.denyPaths || []).length} patterns`],
      ]
      let ditolak = 0
      let contoh = []
      try {
        const fs = await import('node:fs/promises')
        const path = await import('node:path')
        const log = path.resolve(process.cwd(), 'data', 'audit.log')
        const isi = await fs.readFile(log, 'utf8').catch(() => '')
        const batas = Date.now() / 1000 - 86400
        for (const l of isi.split('\n')) {
          if (!l.trim()) continue
          let j = null
          try { j = JSON.parse(l) } catch { continue }
          if ((j.ts || 0) < batas) continue
          if (/blocked|denied|refused|failed/.test(String(j.event || ''))) {
            ditolak += 1
            if (contoh.length < 3) contoh.push(`${j.event}: ${j.reason || j.error || '-'}`)
          }
        }
      } catch { /* audit opsional */ }
      return [{ type: 'card', blocks: diagBlocks({ rows: baris, ditolak, contoh }, { chatId: ctx?.chatId }) }]
    },
  })

  map.set('tenant', {
    name: 'tenant',
    help: 'Rooms and the machine I run on',
    async run({ ctx, config }) {
      const host = config?.agent?.host || '127.0.0.1'
      const port = config?.agent?.port || 8787
      const ids = [ctx.senderIds, ctx.senderId, ctx.senderAlt]
        .flat().filter(Boolean).join(',')
      try {
        const url = `http://${host}:${port}/whoami?sender_id=${encodeURIComponent(ctx.senderId || '')}`
          + `&sender_ids=${encodeURIComponent(ids)}`
        const r = await fetch(url, { signal: AbortSignal.timeout(5000), headers: agentHeaders() })
        const j = await r.json()
        const mech = j.mechanism === j.mechanism_effective
          ? j.mechanism
          : `${j.mechanism} → ${j.mechanism_effective} (fallback)`
        const av = Object.entries(j.mechanisms_available || {})
          .map(([k, v]) => `${v ? '✅' : '❌'} ${k}`).join('  ')
        return [{
          type: 'text',
          text: [
            '🧩 *Your room*',
            `Tenant  : ${j.tenant}${j.primary ? ' (primary)' : ''}`,
            `Engine  : ${mech}`,
            `Room    : ${j.room}`,
            `DB      : ${j.db}`,
            '',
            `Available engines: ${av}`,
            'Change via .env: OWNER_MECHANISMS=<number>=<native|hermes|claude|codex>',
          ].join('\n'),
        }]
      } catch (e) {
        return [{ type: 'text', text: `I could not reach the agent core: ${e.message}` }]
      }
    },
  })

  // !log — aktivitas terakhir dari audit log (mirip terminal log)
  map.set('log', {
    name: 'log',
    help: 'Recent events',
    async run({ ctx, args }) {
      const n = Math.min(50, Math.max(1, parseInt(args[0] || '12', 10) || 12))
      try {
        const raw = await fs.readFile(path.resolve(process.cwd(), 'data', 'audit.log'), 'utf8')
        const lines = raw.trim().split('\n').filter(Boolean).slice(-n)
        const out = lines.map((l) => {
          try {
            const d = JSON.parse(l)
            const t = new Date((d.ts || 0) * 1000).toLocaleTimeString('id-ID')
            if (d.event === 'tool.call') return { time: t, text: `🔧 ${d.tool} ${d.ok ? '✓' : '✗'}` }
            if (d.event === 'shell.run') return { time: t, text: `▸ shell exit=${d.exit_code}` }
            if (d.event === 'shell.blocked') return { time: t, text: `⛔ shell ${d.reason || ''}` }
            if (d.event === 'message.handled') return { time: t, text: `💬 reply ${d.reply_chars} chars` }
            if (d.event === 'memory.compact') return { time: t, text: `🧠 compacted ${d.summarized} messages` }
            return { time: t, text: String(d.event || '?') }
          } catch { return { time: '', text: l.slice(0, 100) } }
        })
        return [{ type: 'card', blocks: logBlocks(out, { chatId: ctx?.chatId, total: lines.length }) }]
      } catch {
        return [{ type: 'card', blocks: logBlocks([], { chatId: ctx?.chatId }) }]
      }
    },
  })

  // ALIAS bahasa Indonesia. Perintah aslinya tetap bekerja (kompatibilitas),
  // tapi menu dan CTA memakai nama Indonesia supaya tidak ada kata asing.
  const alias = {
    // nama Inggris (yang ditampilkan di menu)
    search: 'cari',
    team: 'tim',
    group: 'grup',
    health: 'sehat',
    summary: 'ringkasan',
    resume: 'lanjut',
    // nama Indonesia (tetap jalan, tidak ditampilkan)
    bantuan: 'help',
    halo: 'ping',
    gaya: 'prompt',
    kemampuan: 'skills',
    kode: 'code',
    catatan: 'log',
    tambahan: 'plugins',
    ruangan: 'tenant',
    mulai: 'run',
    jajak: 'poll',
  }
  for (const [nama, target] of Object.entries(alias)) {
    const asal = map.get(target)
    if (!asal || map.has(nama)) continue
    map.set(nama, { ...asal, name: nama, aliasOf: target })
  }

  return map
}
