import { readdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { runtimeBlocks } from './_runtime-core.js'

const DIR = dirname(fileURLToPath(import.meta.url))

export default {
  name: 'ping',
  description: 'Denyut runtime: uptime, RAM, node, load, modul',
  commands: ['ping'],
  async run(ctx) {
    let modules = 0
    try {
      modules = (await readdir(DIR)).filter((f) => f.endsWith('.js') && !f.startsWith('_')).length
    } catch { /* biarkan 0 */ }
    const blocks = await runtimeBlocks({ kind: 'ping', modules })
    await ctx.wa.sendRich(ctx.msg.chatId, blocks)
    return 'ok'
  },
}
