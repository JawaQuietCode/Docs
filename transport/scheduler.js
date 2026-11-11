// scheduler.js — penjalan job terjadwal (sisi Node).
//
// Job disimpan oleh inti Python di `data/cron/jobs.json` (lihat
// scheduler/cron.py). Node membacanya, menghitung waktu berikutnya, dan
// menjalankannya karena hanya di sini socket WhatsApp tersedia.
//
// Tiga jenis job:
//   send   — kirim teks ke chat:   {to, text}
//   agent  — minta inti agent Python mengerjakan prompt lalu kirim hasilnya: {to, prompt}
//   plugin — jalankan plugin JS:   {name, args}
//
// Polling tiap 20 detik; job yang jatuh tempo (nextRun <= now) dijalankan sekali,
// lalu nextRun dihitung ulang. Job yang dinonaktifkan dilewati.
import fs from 'node:fs'
import path from 'node:path'
import { runPlugin } from './plugin-host.js'
import { sendRich, groupReplies, bizNode, buildRichCard } from './api/rich.js'
import { buildProgressIndicatorMetadata, progressStep, PlanningStepStatus } from './api/progress.js'
import { callAgent } from './router.js'

const ROOT = process.cwd()
const AGENT_HOST = process.env.AGENT_HOST || '127.0.0.1'
const AGENT_PORT = Number(process.env.AGENT_PORT || 8787)
const TICK_MS = 20000

// ---- multi-tenant: satu file job per owner ----
// Owner utama memakai path LAMA (data/cron/jobs.json); owner lain memakai
// data/tenants/<t>/cron/jobs.json — sama seperti sisi Python.
function ownerNumbers() {
  return String(process.env.WA_OWNER_NUMBERS || '')
    .split(',').map((s) => s.replace(/\D/g, '')).filter(Boolean)
}

function primaryOwner() {
  const explicit = String(process.env.WA_PRIMARY_OWNER || '').replace(/\D/g, '')
  if (explicit) return explicit
  return ownerNumbers()[0] || ''
}

function tenantNumbers() {
  const set = new Set(ownerNumbers())
  try {
    const base = path.join(ROOT, 'data', 'tenants')
    for (const d of fs.readdirSync(base)) {
      if (/^\d+$/.test(d)) set.add(d)
    }
  } catch { /* folder belum ada */ }
  return [...set]
}

/** Semua file job (tenant utama + tenant lain) beserta tenant-nya. */
export function jobFiles() {
  const out = []
  const primary = primaryOwner()
  for (const t of tenantNumbers()) {
    const file = t === primary
      ? path.join(ROOT, 'data', 'cron', 'jobs.json')
      : path.join(ROOT, 'data', 'tenants', t, 'cron', 'jobs.json')
    out.push({ tenant: t, primary: t === primary, file })
  }
  // Job lama (tanpa tenant) tetap dibaca dari file utama.
  if (!out.some((f) => f.primary)) {
    out.push({ tenant: '', primary: true, file: path.join(ROOT, 'data', 'cron', 'jobs.json') })
  }
  return out
}

// ---- cron minimal (sama semantik dengan scheduler/cron.py) ----

function parseField(spec, lo, hi) {
  const out = new Set()
  for (let part of String(spec).split(',')) {
    part = part.trim()
    if (!part) throw new Error('field kosong')
    let step = 1
    if (part.includes('/')) {
      const [base, s] = part.split('/')
      part = base.trim()
      step = parseInt(s, 10)
      if (!(step >= 1)) throw new Error('langkah tidak sah')
    }
    let start
    let end
    if (part === '*' || part === '') { start = lo; end = hi } else if (part.includes('-')) {
      const [a, b] = part.split('-')
      start = parseInt(a, 10); end = parseInt(b, 10)
    } else { start = end = parseInt(part, 10) }
    if (Number.isNaN(start) || Number.isNaN(end) || start > end) throw new Error(`field tidak sah: ${spec}`)
    for (let v = start; v <= end; v += step) {
      if (v < lo || v > hi) throw new Error(`nilai ${v} di luar ${lo}-${hi}`)
      out.add(v)
    }
  }
  if (!out.size) throw new Error('tidak ada nilai')
  return out
}

function parseCron(expr) {
  const p = String(expr || '').split(/\s+/)
  if (p.length !== 5) throw new Error('cron harus 5 field')
  return [parseField(p[0], 0, 59), parseField(p[1], 0, 23), parseField(p[2], 1, 31), parseField(p[3], 1, 12), parseField(p[4], 0, 6)]
}

/** Waktu jalan berikutnya (epoch detik) setelah `after` (ms). Waktu LOKAL. */
export function nextRunTs(expr, afterMs = Date.now()) {
  const [minute, hour, dom, month, dow] = parseCron(expr)
  let t = new Date(afterMs)
  t.setSeconds(0, 0)
  t = new Date(t.getTime() + 60000)
  const limit = new Date(t.getTime() + 366 * 24 * 3600 * 1000)
  while (t <= limit) {
    if (
      minute.has(t.getMinutes())
      && hour.has(t.getHours())
      && month.has(t.getMonth() + 1)
      && dow.has(t.getDay())
      && dom.has(t.getDate())
    ) {
      return Math.floor(t.getTime() / 1000)
    }
    t = new Date(t.getTime() + 60000)
  }
  throw new Error('tidak ada jadwal dalam 1 tahun')
}

function readJobs(file) {
  try {
    const raw = fs.readFileSync(file, 'utf8')
    const data = JSON.parse(raw || '[]')
    return Array.isArray(data) ? data : []
  } catch { return [] }
}

function writeJobs(file, jobs) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const tmp = `${file}.tmp`
  fs.writeFileSync(tmp, JSON.stringify(jobs, null, 2))
  fs.renameSync(tmp, file)
}

// ---- eksekusi job ----

export async function runJob(sock, job, logger = console) {
  const kind = job.kind
  const a = job.args || {}
  if (kind === 'send') {
    const to = a.to
    if (!to) throw new Error('job send tanpa tujuan')
    await sock.sendMessage(to, { text: String(a.text ?? '') })
    return { sent: 'text' }
  }
  if (kind === 'agent') {
    const to = a.to
    const prompt = String(a.prompt ?? '')
    if (!to || !prompt) throw new Error('job agent butuh to & prompt')
    // MULTI-TENANT: kirim tenant job supaya inti agent memakai DB/sesi/tugas
    // milik owner yang membuat job ini (bukan selalu owner utama).
    // MODE BACKGROUND: job terjadwal juga bisa panjang — pakai jalur yang sama
    // dengan pesan biasa supaya tidak dipotong plafon tunggu.
    const cfg = { agent: { host: AGENT_HOST, port: AGENT_PORT } }
    const data = await callAgent(cfg, {
      chat_id: to,
      sender_id: to,
      sender_ids: [to],
      text: prompt,
      tenant: job.tenant || '',
      scheduled: true,
    }, { background: true })
    const replies = Array.isArray(data.replies) ? data.replies : []
    for (const g of groupReplies(replies)) {
      try { await sendRich(sock, to, g) } catch (e) { logger.warn?.(`[cron] kirim hasil gagal: ${e.message}`) }
    }
    return { replies: replies.length }
  }
  if (kind === 'plugin') {
    const name = a.name
    if (!name) throw new Error('job plugin tanpa nama')
    const out = await runPlugin(name, {
      args: a.args || {},
      msg: { chatId: a.to || '', scheduled: true },
      makeCtx: ({ logs }) => ({
        wa: {
          send: (to, content) => sock.sendMessage(to || a.to || '', typeof content === 'string' ? { text: content } : content),
          sendRich: (to, blocks) => sendRich(sock, to || a.to || '', { type: 'richResponse', blocks }),
          raw: () => sock,
        },
        airich: { sendRich, groupReplies, buildRichCard, bizNode, buildProgressIndicatorMetadata, progressStep, PlanningStepStatus },
        log: (...x) => logs.push(x.join(' ')),
      }),
    })
    return { plugin: name, result: out.result }
  }
  throw new Error(`kind tidak dikenal: ${kind}`)
}

/** Mulai loop penjadwal. `getSock` mengembalikan socket WA aktif (atau null). */
export function startScheduler({ getSock, logger = console } = {}) {
  let running = false
  const tick = async () => {
    if (running) return
    running = true
    try {
      const sock = getSock?.()
      if (!sock) return
      const now = Math.floor(Date.now() / 1000)
      // MULTI-TENANT: telusuri tiap file job (satu per owner/tenant).
      for (const { tenant, file } of jobFiles()) {
        const jobs = readJobs(file)
        if (!jobs.length) continue
        let changed = false
        for (const job of jobs) {
          if (job.tenant == null) job.tenant = tenant
          if (!job.enabled) continue
          if (!job.nextRun) { job.nextRun = nextRunTs(job.when); changed = true; continue }
          if (job.nextRun > now) continue
          try {
            const res = await runJob(sock, job, logger)
            job.lastRun = now
            job.lastResult = res
            logger.log?.(`[cron] job ${job.id} (${job.kind}) dijalankan [tenant ${tenant || '-'}]`)
          } catch (e) {
            job.lastResult = { error: e.message }
            logger.warn?.(`[cron] job ${job.id} gagal: ${e.message}`)
          }
          try { job.nextRun = nextRunTs(job.when) } catch { job.nextRun = now + 3600 }
          changed = true
        }
        if (changed) writeJobs(file, jobs)
      }
    } finally {
      running = false
    }
  }
  const timer = setInterval(() => { tick().catch(() => {}) }, TICK_MS)
  tick().catch(() => {})
  logger.log?.(`[cron] penjadwal aktif (tick ${TICK_MS / 1000}s)`)
  return { stop: () => clearInterval(timer), tick }
}
