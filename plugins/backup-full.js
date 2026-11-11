// plugins/backup-full.js — backup otomatis: /data penuh + .env + plugins/
// Dipicu cron: kind=plugin args={name:'backup-full', args:{to:'...'}}
// Exclude: .mjs, workspace, node_modules, cache, backups lama.
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { readdir, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { PY_BIN } from '../transport/py.mjs'

const run = promisify(execFile)
const ROOT = process.cwd()

export default {
  name: 'backup-full',
  description: 'Backup /data penuh + .env + plugins/ dengan verifikasi SHA256',
  commands: ['backup'],

  async run(ctx) {
    const t0 = Date.now()
    let stdout = ''
    let stderr = ''
    try {
      const res = await run(PY_BIN, [join(ROOT, 'backup_full.py')], {
        cwd: ROOT,
        timeout: 600000,
        maxBuffer: 8 * 1024 * 1024,
      })
      stdout = res.stdout || ''
      stderr = res.stderr || ''
    } catch (e) {
      stdout = e.stdout || ''
      stderr = e.stderr || e.message
      ctx.log(`backup gagal: ${e.message}`)
    }

    // Ambil ringkasan dari output script.
    const grab = (re) => (stdout.match(re) || [])[1] || ''
    const fileName = grab(/Created: (\S+)/)
    const verified = /Integrity: (.+)/.exec(stdout)?.[1] || ''
    const pluginsLine = /Plugins: (.+)/.exec(stdout)?.[1] || ''
    const failed = /VERIFICATION FAILED: (.+)/.exec(stdout)?.[1] || (stderr ? stderr.split('\n')[0] : '')

    const to = ctx.args?.to || ctx.msg?.chatId
    try {
      if (ctx.wa?.sendRich && to) {
        await ctx.wa.sendRich(to, [
          { type: 'text', text: failed ? `**Backup gagal**\n\`${failed}\`` : `**Backup harian selesai**` },
          {
            type: 'table',
            title: 'Hasil',
            rows: [
              ['File', fileName || '(tidak ada)'],
              ['Integritas', verified || '(tidak dicek)'],
              ['Plugin', pluginsLine || '(tidak ada)'],
              ['Durasi', `${((Date.now() - t0) / 1000).toFixed(1)}s`],
            ],
          },
        ])
      } else if (ctx.wa?.send && to) {
        await ctx.wa.send(to, { text: `Backup: ${fileName || 'gagal'} | ${verified || failed}` })
      }
    } catch (e) {
      ctx.log(`kirim ringkasan gagal: ${e.message}`)
    }

    // Simpan jejak terakhir.
    try {
      ctx.state.set('last', { at: new Date().toISOString(), file: fileName, verified, failed })
    } catch { /* state opsional */ }

    if (failed) throw new Error(failed)
    return { file: fileName, verified }
  },
}