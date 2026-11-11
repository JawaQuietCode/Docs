// Model selection per sender. The file stores only a SHA-256 key, never a JID.
import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'

const FILE = path.resolve(process.cwd(), 'data', 'model-prefs.json')
const keyOf = (identity) => createHash('sha256').update(String(identity || '')).digest('hex')

async function readAll() {
  try {
    const value = JSON.parse(await fs.readFile(FILE, 'utf8'))
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { return {} }
}

export async function getModelPreference(identity) {
  if (!identity) return ''
  const values = await readAll()
  return String(values[keyOf(identity)] || '')
}

export async function setModelPreference(identity, model) {
  if (!identity) throw new Error('identitas pengguna tidak tersedia')
  await fs.mkdir(path.dirname(FILE), { recursive: true })
  const values = await readAll()
  const key = keyOf(identity)
  if (model) values[key] = String(model)
  else delete values[key]
  const tmp = `${FILE}.tmp`
  await fs.writeFile(tmp, JSON.stringify(values, null, 2), { mode: 0o600 })
  await fs.rename(tmp, FILE)
}
