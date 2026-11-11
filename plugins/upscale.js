// plugins/upscale.js — !upscale (reply to an image or attach one with the command).
// Real-ESRGAN ncnn Vulkan invocation follows the upstream CLI. When the binary
// or bundled model weights are absent, use deterministic Lanczos 2x + unsharp.
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { randomUUID, createHash } from 'node:crypto'

function run(command, args, timeoutMs = 150000) {
  return new Promise((resolve) => {
    let child
    try { child = spawn(command, args, { stdio: ['ignore', 'ignore', 'pipe'] }) }
    catch (e) { resolve({ ok: false, error: e.message }); return }
    let err = ''
    const timer = setTimeout(() => { child.kill('SIGKILL') }, timeoutMs)
    timer.unref?.()
    child.stderr.on('data', (b) => { if (err.length < 4000) err += b.toString() })
    child.on('error', (e) => { clearTimeout(timer); resolve({ ok: false, error: e.message }) })
    child.on('close', (code) => { clearTimeout(timer); resolve({ ok: code === 0, code, error: err.trim() }) })
  })
}

function safeImagePath(value) {
  const root = path.resolve(process.cwd())
  const tmp = path.resolve(os.tmpdir())
  const abs = path.resolve(String(value || ''))
  return (abs === root || abs.startsWith(root + path.sep) || abs.startsWith(tmp + path.sep)) ? abs : ''
}

// ffprobe helper with captured output (stderr remains bounded).
function probeSize(file) {
  return new Promise((resolve) => {
    let child
    try { child = spawn('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', file], { stdio: ['ignore', 'pipe', 'ignore'] }) }
    catch { resolve(null); return }
    let out = ''
    const timer = setTimeout(() => child.kill('SIGKILL'), 20000)
    child.stdout.on('data', (b) => { if (out.length < 100) out += b.toString() })
    child.on('error', () => { clearTimeout(timer); resolve(null) })
    child.on('close', (code) => {
      clearTimeout(timer)
      const [width, height] = out.trim().split('x').map(Number)
      resolve(code === 0 && width > 0 && height > 0 ? { width, height } : null)
    })
  })
}

export default {
  name: 'upscale',
  description: 'Naikkan resolusi gambar 2× dengan Real-ESRGAN (jika tersedia), LANCZOS dan sharpen; fallback FFmpeg.',
  commands: ['upscale'],
  async run(ctx) {
    const kind = String(ctx.args?.mediaKind || '').toLowerCase()
    if (kind && !['image', 'sticker'].includes(kind)) return 'Upscale hanya menerima foto atau stiker gambar, bukan video/dokumen.'
    const input = safeImagePath(ctx.args?.media || ctx.args?.quotedMedia)
    if (!input || !fs.existsSync(input)) return 'Kirim foto dengan caption !upscale, atau reply foto lalu kirim !upscale.'
    const stat = fs.statSync(input)
    if (stat.size <= 0 || stat.size > 20 * 1024 * 1024) return 'Ukuran gambar harus lebih dari 0 dan maksimal 20 MB.'
    const dimensions = await probeSize(input)
    if (!dimensions) return 'File ini tidak terbaca sebagai gambar. Coba JPEG, PNG, atau WebP.'
    if (dimensions.width * dimensions.height > 20000000) return 'Gambar terlalu besar untuk diproses dengan aman (batas 20 megapiksel).'

    const isPublic = !ctx.msg?.isOwner
    if (isPublic) {
      const fingerprint = createHash('sha256').update(String(ctx.msg?.senderId || '')).digest('hex')
      const last = Number(ctx.state.get(`last-public-${fingerprint}`, 0)) || 0
      if (Date.now() - last < 60000) return 'Batas aman upscale: tunggu 60 detik sebelum permintaan berikutnya.'
      ctx.state.set(`last-public-${fingerprint}`, Date.now())
    }

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kwee-upscale-'))
    const esrgan = path.join(dir, 'esrgan.png')
    const sharpOut = path.join(dir, 'sharp.png')
    const fallback = path.join(dir, 'lanczos.png')
    try {
      // Upstream Real-ESRGAN ncnn Vulkan: -i input -o output -s scale -n model.
      // `-s 2` and the selected general-photo model are documented upstream.
      let neural = await run('realesrgan-ncnn-vulkan', [
        '-i', input, '-o', esrgan, '-s', '2', '-n', 'realesrgan-x4plus', '-f', 'png',
      ])
      let result = neural.ok && fs.existsSync(esrgan) ? esrgan : ''
      if (!result) {
        const resize = await run('ffmpeg', [
          '-y', '-v', 'error', '-i', input, '-vf', 'scale=iw*2:ih*2:flags=lanczos',
          '-frames:v', '1', '-f', 'image2', fallback,
        ])
        if (!resize.ok || !fs.existsSync(fallback)) {
          const detail = neural.error ? ` ESRGAN: ${neural.error.slice(0, 180)}.` : ''
          return `Upscale gagal. Pasang ffmpeg untuk fallback Lanczos.${detail}`
        }
        result = fallback
      }
      // Apply a restrained unsharp mask to either ESRGAN or Lanczos output.
      const sharpen = await run('ffmpeg', [
        '-y', '-v', 'error', '-i', result, '-vf', 'unsharp=5:5:0.65:3:3:0.25',
        '-frames:v', '1', '-f', 'image2', sharpOut,
      ])
      if (!sharpen.ok || !fs.existsSync(sharpOut) || fs.statSync(sharpOut).size === 0) {
        return 'Gambar berhasil diproses tetapi tahap sharpen gagal; coba lagi dengan gambar lain.'
      }
      const outputSize = fs.statSync(sharpOut).size
      if (outputSize > 60 * 1024 * 1024) return 'Hasil melebihi batas kirim WhatsApp 60 MB.'
      const sock = ctx.wa.raw()
      await sock.sendMessage(ctx.msg.chatId, {
        image: { url: sharpOut },
        caption: `${dimensions.width}×${dimensions.height} → ${dimensions.width * 2}×${dimensions.height * 2} · ${result === esrgan ? 'Real-ESRGAN + sharpen' : 'LANCZOS 2× + sharpen (ESRGAN tidak tersedia)'}`,
      })
      return null
    } finally {
      try { fs.rmSync(dir, { recursive: true, force: true }) } catch { /* best effort */ }
    }
  },
}
