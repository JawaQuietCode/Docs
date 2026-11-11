import fs from 'node:fs'

export default {
  name: 'kirim-pdf-soal',
  description: 'Kirim PDF soal matematika dari workspace',
  async run(ctx) {
    const path = '/home/container/workspace/soal-mtk.pdf'
    if (!fs.existsSync(path)) {
      ctx.log('file tidak ditemukan')
      return { ok: false, error: 'file tidak ditemukan' }
    }
    const stream = fs.createReadStream(path)
    const to = ctx.msg?.chatId || ctx.args?.to
    const sent = await ctx.wa.send(to, {
      document: { stream },
      fileName: 'soal-mtk.pdf',
      mimetype: 'application/pdf',
      caption: '📄 Kumpulan Soal Matematika (PDF) — LaTeX sudah terender sempurna',
    })
    return { ok: true, id: sent?.key?.id, to }
  },
}
