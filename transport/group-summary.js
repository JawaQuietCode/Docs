import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

const ROOT = process.cwd()
const DATA_DIR = path.join(ROOT, 'data', 'group-summary')
const SETTINGS_FILE = path.join(DATA_DIR, 'settings.json')
const JOBS_FILE = path.join(ROOT, 'data', 'cron', 'jobs.json')
const RETENTION_DAYS = 7

function hash(value) {
  return createHash('sha256').update(String(value)).digest('hex')
}

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')) } catch { return fallback }
}

function readSettings() {
  const value = readJson(SETTINGS_FILE, {})
  return value && typeof value === 'object' && value.groups ? value : { groups: {} }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const temp = `${file}.${process.pid}.tmp`
  fs.writeFileSync(temp, JSON.stringify(value, null, 2))
  fs.renameSync(temp, file)
}

function readJobs() {
  const value = readJson(JOBS_FILE, [])
  return Array.isArray(value) ? value : []
}

function writeJobs(jobs) {
  writeJson(JOBS_FILE, jobs)
}

function primaryTenant() {
  const explicit = String(process.env.WA_PRIMARY_OWNER || '').replace(/\D/g, '')
  if (explicit) return explicit
  return String(process.env.WA_OWNER_NUMBERS || '').split(',').map((x) => x.replace(/\D/g, '')).find(Boolean) || ''
}

function nextRun(hour, minute) {
  const now = new Date()
  const next = new Date(now)
  next.setHours(hour, minute, 0, 0)
  if (next <= now) next.setDate(next.getDate() + 1)
  return Math.floor(next.getTime() / 1000)
}

function sanitize(text) {
  return String(text || '').slice(0, 1800)
    .replace(/\b(ptl[ac]_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{16,})\b/g, '[redacted]')
    .replace(/\bBearer\s+[A-Za-z0-9._-]{20,}/gi, 'Bearer [redacted]')
    .replace(/(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S+/gi, '$1=[redacted]')
}

function prune(groupHash) {
  const cutoff = Date.now() - RETENTION_DAYS * 86400 * 1000
  try {
    for (const name of fs.readdirSync(DATA_DIR)) {
      if (!name.startsWith(`${groupHash}_`) || !name.endsWith('.jsonl')) continue
      const file = path.join(DATA_DIR, name)
      if (fs.statSync(file).mtimeMs < cutoff) fs.rmSync(file, { force: true })
    }
  } catch {}
}

export function recordGroupMessage(chatId, senderId, text) {
  if (!String(chatId || '').endsWith('@g.us')) return false
  const groupHash = hash(chatId)
  prune(groupHash)
  if (!String(text || '').trim()) return false
  const entry = readSettings().groups[String(chatId)]
  if (!entry?.enabled || String(text).trim().toLowerCase().startsWith('!ringkasan')) return false
  fs.mkdirSync(DATA_DIR, { recursive: true })
  const day = new Date().toISOString().slice(0, 10)
  const file = path.join(DATA_DIR, `${groupHash}_${day}.jsonl`)
  try { if (fs.statSync(file).size > 5_000_000) return false } catch {}
  const sender = hash(senderId || 'unknown').slice(0, 8)
  fs.appendFileSync(file, JSON.stringify({ ts: Math.floor(Date.now() / 1000), sender, text: sanitize(text) }) + '\n')
  prune(groupHash)
  return true
}

export function getGroupSummaryStatus(chatId) {
  const entry = readSettings().groups[String(chatId)]
  return entry?.enabled ? { enabled: true, when: entry.when, jobId: entry.jobId } : { enabled: false }
}

export function enableGroupSummary(chatId, time) {
  if (!String(chatId || '').endsWith('@g.us')) throw new Error('Ringkasan hanya dapat diaktifkan di grup WhatsApp.')
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(time || ''))
  if (!match) throw new Error('Format waktu wajib HH:MM (waktu lokal server), misalnya 20:00.')
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (hour > 23 || minute > 59) throw new Error('Jam tidak valid.')
  const settings = readSettings()
  const old = settings.groups[String(chatId)]
  const jobs = readJobs().filter((job) => job.id !== old?.jobId)
  const groupHash = hash(chatId)
  const jobId = `group_summary_${groupHash.slice(0, 12)}`
  const when = `${minute} ${hour} * * *`
  jobs.push({
    id: jobId,
    when,
    kind: 'agent',
    args: {
      to: String(chatId),
      prompt: 'Ambil percakapan 24 jam terakhir memakai tool group_summary. Buat ringkasan Bahasa Indonesia yang ringkas dengan topik utama, keputusan, tugas dan tenggat yang disebutkan. Jangan menebak; bila tidak ada data, katakan demikian. Jangan menyebut ID internal atau identitas pseudonim. Kirim hanya ringkasan ke grup ini.'
    },
    tenant: primaryTenant(),
    enabled: true,
    createdAt: Math.floor(Date.now() / 1000),
    lastRun: null,
    nextRun: nextRun(hour, minute),
  })
  settings.groups[String(chatId)] = { enabled: true, when, jobId, updatedAt: Math.floor(Date.now() / 1000) }
  writeJson(SETTINGS_FILE, settings)
  writeJobs(jobs)
  return { when: `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`, jobId }
}

export function disableGroupSummary(chatId) {
  const settings = readSettings()
  const old = settings.groups[String(chatId)]
  if (!old) return { enabled: false }
  delete settings.groups[String(chatId)]
  writeJson(SETTINGS_FILE, settings)
  writeJobs(readJobs().filter((job) => job.id !== old.jobId))
  const groupHash = hash(chatId)
  let deletedLogs = 0
  try {
    for (const name of fs.readdirSync(DATA_DIR)) {
      if (name.startsWith(`${groupHash}_`) && name.endsWith('.jsonl')) {
        fs.rmSync(path.join(DATA_DIR, name), { force: true })
        deletedLogs += 1
      }
    }
  } catch {}
  return { enabled: false, deletedLogs }
}
