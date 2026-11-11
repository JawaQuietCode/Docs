// plugins/send-media.js — kirim media sebagai MEDIA (bukan cuma dokumen) + kualitas HD.
//
// MASALAH YANG DIPECAHKAN
//   Aksi `send_file` bawaan SELALU mengirim sebagai dokumen, jadi video/gambar/
//   audio muncul sebagai lampiran abu-abu, bukan bisa diputar/dilihat langsung.
//   Plugin ini mengirim sesuai JENISNYA, dan memprosesnya agar hasilnya HD:
//     image    -> foto HD (auto-orient EXIF, resize pintar, mozjpeg q92)
//     video    -> video HD inline (remux lossless bila bisa; transcode H.264 High)
//     audio    -> audio; ptt:true -> voice note (ogg/opus)
//     sticker  -> stiker (webp, 512px)
//     document -> dokumen (perilaku lama, kalau memang mau file mentah)
//
// PIPELINE HD
//   * Gambar  -> sharp: .rotate() (hormati orientasi EXIF), resize "inside" tanpa
//               memperbesar, jaga alpha (png) atau mozjpeg q92 4:4:4 untuk foto.
//   * Video   -> ffmpeg: kalau sudah H.264/AAC cukup REMUX (-c copy, 100% kualitas
//               asli, super cepat). Kalau HEVC/VP9/AV1 atau perlu dikecilkan,
//               transcode H.264 High profile + CRF (kualitas HD) dengan -maxrate
//               otomatis supaya ukuran tetap muat diputar inline di WhatsApp.
//   * Audio   -> ffmpeg: format tak didukung (wav/flac/amr) -> m4a AAC 192k.
//
// PEMAKAIAN (ctx.args)
//   { path: 'workspace/clip.mp4' }                          // otomatis deteksi jenis
//   { url: 'https://…/x.jpg', caption: 'halo' }             // unduh lalu kirim
//   { path: 'workspace/clip.mkv', type: 'video' }           // remux/transcode HD
//   { path: 'workspace/lagu.mp3', type: 'voice' }           // jadi voice note
//   { path: 'workspace/big.mp4', quality: 'hd' }            // paksa HD (720p)
//   { path: 'workspace/foto.heic', quality: 'fhd' }         // HEIC -> JPEG HD
//   { path: 'workspace/foto.png', type: 'sticker' }
//   { path: 'workspace/a.zip', type: 'document', filename: 'arsip.zip' }
//
// KNOB KUALITAS
//   quality : 'sd' | 'hd' (720p) | 'fhd' (1080p, default) | '2k' | '4k' | <angka CRF>
//   maxDim  : batas sisi terpanjang (px). Default 1920 video / 2560 gambar.
//   crf     : override CRF x264 (angka kecil = kualitas lebih tinggi).
//   fps     : batas fps (default 60).
//   preset  : preset x264 (default 'medium').
//   maxMb   : batas ukuran agar tetap inline (default 15 MB).
//   denoise / sharpen : aktifkan filter tambahan (default false).
//
// Keamanan: path lokal dibatasi ke dalam folder proyek + folder tmp.

import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'

const ROOT = path.resolve(process.cwd())

// Ekstensi per jenis media.
const EXT = {
  image: ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'heic', 'heif', 'avif', 'tif', 'tiff'],
  video: ['mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', '3gp', 'ts', 'flv', 'wmv', 'mpg', 'mpeg'],
  audio: ['mp3', 'm4a', 'aac', 'ogg', 'oga', 'opus', 'wav', 'flac', 'amr', 'wma'],
}

const MIME = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp',
  gif: 'image/gif', bmp: 'image/bmp', heic: 'image/heic', heif: 'image/heif', avif: 'image/avif',
  tif: 'image/tiff', tiff: 'image/tiff',
  mp4: 'video/mp4', m4v: 'video/x-m4v', mov: 'video/quicktime', mkv: 'video/x-matroska',
  webm: 'video/webm', avi: 'video/x-msvideo', '3gp': 'video/3gpp', ts: 'video/mp2t',
  flv: 'video/x-flv', wmv: 'video/x-ms-wmv', mpg: 'video/mpeg', mpeg: 'video/mpeg',
  mp3: 'audio/mpeg', m4a: 'audio/mp4', aac: 'audio/aac', ogg: 'audio/ogg',
  oga: 'audio/ogg', opus: 'audio/ogg', wav: 'audio/wav', flac: 'audio/flac',
  amr: 'audio/amr', wma: 'audio/x-ms-wma',
}

// Preset kualitas -> { maxDim, crf }.
const QUALITY = {
  sd: { maxDim: 854, crf: 25 },
  hd: { maxDim: 1280, crf: 22 },
  fhd: { maxDim: 1920, crf: 20 },
  '2k': { maxDim: 2560, crf: 20 },
  '4k': { maxDim: 3840, crf: 21 },
  high: { maxDim: 1920, crf: 19 },
}

const AUDIO_INLINE = ['mp3', 'm4a', 'aac', 'ogg', 'oga', 'opus']

const extOf = (p) => String(p || '').toLowerCase().split('.').pop()
const mimeOf = (p) => MIME[extOf(p)] || 'application/octet-stream'

function detectType(fileOrUrl, forced) {
  const f = String(forced || '').trim().toLowerCase()
  if (f) {
    if (f === 'voice' || f === 'ptt' || f === 'vn') return 'audio'
    if (f === 'photo' || f === 'picture' || f === 'img') return 'image'
    if (f === 'sticker' || f === 'stiker') return 'sticker'
    if (f === 'vid' || f === 'movie') return 'video'
    return f
  }
  const e = extOf(fileOrUrl)
  if (EXT.image.includes(e)) return 'image'
  if (EXT.video.includes(e)) return 'video'
  if (EXT.audio.includes(e)) return 'audio'
  return 'document'
}

// ---- jalankan proses (ffmpeg/ffprobe) ----
function run(cmd, args, { captureStdout = false, timeoutMs = 180000 } = {}) {
  return new Promise((resolve) => {
    let child
    try {
      child = spawn(cmd, args, { stdio: ['ignore', captureStdout ? 'pipe' : 'ignore', 'pipe'] })
    } catch (e) {
      return resolve({ ok: false, error: e.message, stdout: Buffer.alloc(0), stderr: '' })
    }
    const out = []
    const err = []
    if (child.stdout) child.stdout.on('data', (c) => out.push(c))
    child.stderr.on('data', (c) => err.push(c))
    const timer = setTimeout(() => { try { child.kill('SIGKILL') } catch { /* abaikan */ } }, timeoutMs)
    if (timer.unref) timer.unref()
    child.on('error', (e) => { clearTimeout(timer); resolve({ ok: false, error: e.message, stdout: Buffer.concat(out), stderr: Buffer.concat(err).toString() }) })
    child.on('close', (code) => { clearTimeout(timer); resolve({ ok: code === 0, code, stdout: Buffer.concat(out), stderr: Buffer.concat(err).toString() }) })
  })
}

// sharp dimuat opsional (kalau tidak ada, fallback ke ffmpeg apa adanya).
let _sharp
async function getSharp() {
  if (_sharp !== undefined) return _sharp
  try { _sharp = (await import('sharp')).default } catch { _sharp = null }
  return _sharp
}

// ---- metadata via ffprobe ----
async function probe(file) {
  const r = await run('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file], { captureStdout: true })
  let j = {}
  try { j = JSON.parse(r.stdout.toString() || '{}') } catch { j = {} }
  const v = (j.streams || []).find((s) => s.codec_type === 'video')
  const a = (j.streams || []).find((s) => s.codec_type === 'audio')
  let fps = 0
  try {
    const fr = String(v?.r_frame_rate || v?.avg_frame_rate || '0/1')
    const [n, d] = fr.split('/').map(Number)
    if (d) fps = n / d
  } catch { fps = 0 }
  return {
    duration: Number(j.format?.duration || 0),
    size: Number(j.format?.size || 0),
    width: Number(v?.width || 0),
    height: Number(v?.height || 0),
    fps: Number.isFinite(fps) ? fps : 0,
    vcodec: String(v?.codec_name || ''),
    acodec: String(a?.codec_name || ''),
    hasVideo: Boolean(v),
    hasAudio: Boolean(a),
    formatName: String(j.format?.format_name || ''),
  }
}

// ---- gambar: proses HD pakai sharp ----
async function processImage(src, tmpDir, { maxDim = 2560, quality = 92, format = null } = {}) {
  const s = await getSharp()
  if (!s) return null
  let img
  try { img = s(src, { failOn: 'none', animated: false }) } catch { return null }
  let meta
  try { meta = await img.metadata() } catch { return null }
  if (!meta.width || !meta.height) return null

  const longest = Math.max(meta.width, meta.height)
  // rotate() = terapkan orientasi EXIF lalu buang tag-nya.
  let pipe = img.rotate()
  if (longest > maxDim) {
    // "inside" + tanpa memperbesar -> hanya mengecilkan, jaga rasio.
    pipe = pipe.resize({ width: maxDim, height: maxDim, fit: 'inside', withoutEnlargement: true })
  }

  let outExt = String(format || (meta.hasAlpha ? 'png' : 'jpg')).toLowerCase()
  if (outExt === 'jpeg') outExt = 'jpg'
  if (outExt === 'jpg' && meta.hasAlpha) pipe = pipe.flatten({ background: '#ffffff' })

  const out = path.join(tmpDir, `image.${outExt}`)
  if (outExt === 'png') pipe = pipe.png({ compressionLevel: 9, effort: 7 })
  else if (outExt === 'webp') pipe = pipe.webp({ quality, effort: 4 })
  else if (outExt === 'avif') pipe = pipe.avif({ quality, effort: 4 })
  else pipe = pipe.jpeg({ quality, mozjpeg: true, chromaSubsampling: '4:4:4' })

  try { await pipe.toFile(out) } catch { return null }
  return fs.existsSync(out) && fs.statSync(out).size > 0 ? out : null
}

// ---- video: remux lossless (kualitas asli 100%) ----
async function remuxMp4(src, out) {
  const r = await run('ffmpeg', ['-y', '-loglevel', 'error', '-i', src, '-c', 'copy', '-movflags', '+faststart', out], { timeoutMs: 300000 })
  return r.ok && fs.existsSync(out) && fs.statSync(out).size > 0
}

// ---- video: transcode H.264 High profile, HD, ukuran terkendali ----
async function transcodeVideo(src, out, meta, opts) {
  const { maxDim = 1920, crf = 20, preset = 'medium', maxMb = 15, fpsCap = 60, denoise = false, sharpen = false, audioKbps = 128 } = opts
  const filters = []
  const W = meta.width || 0, H = meta.height || 0
  const longest = Math.max(W, H)
  if (W && H && longest > maxDim) {
    const sc = maxDim / longest
    const nw = Math.max(2, Math.round((W * sc) / 2) * 2)
    const nh = Math.max(2, Math.round((H * sc) / 2) * 2)
    filters.push(`scale=${nw}:${nh}`)
  } else {
    // Pastikan dimensi genap (wajib untuk yuv420p).
    filters.push('scale=trunc(iw/2)*2:trunc(ih/2)*2')
  }
  if (fpsCap && meta.fps && meta.fps > fpsCap + 0.5) filters.push(`fps=${fpsCap}`)
  if (denoise) filters.push('hqdn3d=2:1:2:3')
  if (sharpen) filters.push('unsharp=5:5:0.8:3:3:0.4')

  const args = ['-y', '-loglevel', 'error', '-i', src,
    '-c:v', 'libx264', '-preset', preset, '-crf', String(crf),
    '-profile:v', 'high', '-pix_fmt', 'yuv420p',
  ]

  // Kendalikan ukuran agar tetap muat diputar inline. CRF tetap penentu kualitas;
  // -maxrate/-bufsize hanya "plafon" saat adegan berat. Bitrate audio diturunkan
  // otomatis kalau budget ketat.
  const dur = meta.duration || 0
  let abKbps = audioKbps
  if (dur > 0) {
    const budget = maxMb * 1024 * 1024 * 8 * 0.90
    const vBudget = budget - abKbps * 1000 * dur
    if (vBudget < 300 * 1000 * dur) {
      // Budget video terlalu sempit -> turunkan audio dulu (min 48k) lalu batasi video.
      abKbps = Math.max(48, Math.min(audioKbps, Math.floor((budget * 0.15) / dur / 1000)))
    }
    const vbits = budget - (meta.hasAudio ? abKbps * 1000 * dur : 0)
    const vk = Math.floor(vbits / dur / 1000)
    if (vk >= 200) args.push('-maxrate', `${vk}k`, '-bufsize', `${vk * 2}k`)
  }

  args.push('-vf', filters.join(','))
  if (meta.hasAudio) args.push('-c:a', 'aac', '-b:a', `${abKbps}k`, '-ac', '2', '-ar', '48000')
  else args.push('-an')
  args.push('-movflags', '+faststart', '-max_muxing_queue_size', '2048', out)

  const r = await run('ffmpeg', args, { timeoutMs: 900000 })
  return r.ok && fs.existsSync(out) && fs.statSync(out).size > 0
}

// ---- gif animasi -> mp4 (biar WhatsApp memutar inline & loop) ----
async function gifToMp4(src, out) {
  const r = await run('ffmpeg', ['-y', '-loglevel', 'error', '-i', src,
    '-movflags', '+faststart', '-pix_fmt', 'yuv420p',
    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-an', out], { timeoutMs: 300000 })
  return r.ok && fs.existsSync(out) && fs.statSync(out).size > 0
}

// ---- audio -> ogg/opus (voice note) ----
async function toOggOpus(inPath, outPath) {
  const r = await run('ffmpeg', ['-y', '-loglevel', 'error', '-i', inPath, '-vn', '-c:a', 'libopus', '-b:a', '64k', '-ar', '48000', '-ac', '1', outPath])
  return r.ok && fs.existsSync(outPath) && fs.statSync(outPath).size > 0
}

// ---- audio tak didukung -> m4a AAC 192k ----
async function toM4a(inPath, outPath) {
  const r = await run('ffmpeg', ['-y', '-loglevel', 'error', '-i', inPath, '-vn', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-movflags', '+faststart', outPath])
  return r.ok && fs.existsSync(outPath) && fs.statSync(outPath).size > 0
}

// Gambar/video -> stiker webp 512px.
async function toWebp(inPath, outPath, isVideo) {
  const vf = isVideo
    ? 'scale=512:512:force_original_aspect_ratio=decrease,fps=15'
    : 'scale=512:512:force_original_aspect_ratio=decrease'
  const args = ['-y', '-loglevel', 'error', '-i', inPath]
  if (isVideo) args.push('-t', '6', '-an')
  args.push('-vf', vf, '-c:v', 'libwebp', '-lossless', '0', '-q:v', '80', '-loop', '0', '-preset', 'default', outPath)
  const r = await run('ffmpeg', args)
  return r.ok && fs.existsSync(outPath) && fs.statSync(outPath).size > 0
}

// Thumbnail JPEG (untuk preview video di WhatsApp) — pakai sharp bila ada (lebih HD).
async function makeThumbnail(file, time = 0) {
  const s = await getSharp()
  if (s) {
    try {
      const raw = await run('ffmpeg', [
        '-loglevel', 'error', '-ss', String(time), '-i', file,
        '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1',
      ], { captureStdout: true, timeoutMs: 30000 })
      if (raw.ok && raw.stdout.length) {
        const buf = await s(raw.stdout).resize(320, 320, { fit: 'inside', withoutEnlargement: true }).jpeg({ quality: 80, mozjpeg: true }).toBuffer()
        if (buf.length) return buf
      }
    } catch { /* jatuh ke cara ffmpeg */ }
  }
  const r = await run('ffmpeg', [
    '-loglevel', 'error', '-ss', String(time), '-i', file,
    '-frames:v', '1', '-vf', 'scale=320:-2', '-f', 'mjpeg', 'pipe:1',
  ], { captureStdout: true, timeoutMs: 30000 })
  return r.ok && r.stdout.length ? r.stdout : undefined
}

async function download(url, dir) {
  const res = await fetch(url, { redirect: 'follow' })
  if (!res.ok) throw new Error(`unduh gagal HTTP ${res.status}`)
  const name = (() => {
    try { return path.basename(new URL(url).pathname) || 'media' } catch { return 'media' }
  })()
  const dest = path.join(dir, `${randomUUID()}-${name}`)
  const buf = Buffer.from(await res.arrayBuffer())
  fs.writeFileSync(dest, buf)
  return dest
}

// Batasi path lokal ke dalam proyek atau folder tmp.
function resolveLocal(p) {
  const abs = path.resolve(ROOT, String(p || ''))
  const inRoot = abs === ROOT || abs.startsWith(ROOT + path.sep)
  const inTmp = abs.startsWith(path.resolve(os.tmpdir()) + path.sep)
  return inRoot || inTmp ? abs : null
}

export default {
  name: 'send-media',
  description: 'Kirim media sebagai MEDIA (video/foto/audio/stiker), bukan dokumen — HD via ffmpeg/sharp: remux lossless, H.264 High CRF, auto-orient EXIF, thumbnail, kompresi otomatis.',
  commands: ['kirimmedia', 'sendmedia', 'media'],

  async run(ctx) {
    const a = ctx.args || {}
    const to = a.to || a.chat_id || ctx.msg?.chatId
    if (!to) return { ok: false, error: 'tujuan (to) kosong' }

    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'send-media-'))
    const cleanup = () => { try { fs.rmSync(tmpDir, { recursive: true, force: true }) } catch { /* abaikan */ } }

    try {
      // 1) Siapkan file sumber (dari path lokal atau unduh URL).
      let src = null
      if (a.path || a.file) {
        src = resolveLocal(a.path || a.file)
        if (!src) return { ok: false, error: 'path di luar folder proyek' }
        if (!fs.existsSync(src) || !fs.statSync(src).isFile()) return { ok: false, error: `file tidak ditemukan: ${a.path || a.file}` }
      } else if (a.url || a.media) {
        src = await download(a.url || a.media, tmpDir)
      } else {
        return { ok: false, error: 'butuh `path` (lokal) atau `url`' }
      }

      // 2) Tentukan jenis + knob kualitas.
      const type = detectType(a.filename || src, a.type)
      const caption = a.caption != null ? String(a.caption) : ''
      const wantPtt = type === 'audio' && (a.ptt === true || String(a.type || '').toLowerCase() === 'voice')
      const maxMb = Number(a.maxMb || a.max_mb || 15)

      // quality: nama preset atau angka CRF langsung.
      const qName = String(a.quality || a.q || 'fhd').toLowerCase()
      const qPreset = QUALITY[qName] || QUALITY.fhd
      const qCrf = Number.isFinite(Number(a.crf)) ? Number(a.crf) : (!QUALITY[qName] && Number(qName) ? Number(qName) : qPreset.crf)
      const vMaxDim = Number(a.maxDim || a.maxdim || qPreset.maxDim)
      const imgMaxDim = Number(a.imgMaxDim || a.maxDim || 2560)
      const fpsCap = Number(a.fps || 60)
      const preset = String(a.preset || 'medium')

      let sendPath = src
      let meta = {}
      const notes = []

      if (type === 'video' || type === 'image' || type === 'audio') meta = await probe(src).catch(() => ({}))

      // 3) Normalisasi / kompres / HD-kan sesuai jenis.
      if (type === 'video') {
        const sizeMb = fs.statSync(src).size / (1024 * 1024)
        const ext = extOf(src)
        const tooBig = sizeMb > maxMb
        const forceQuality = Boolean(a.quality || a.q || a.crf)
        const isH264 = meta.vcodec === 'h264'
        const isAac = !meta.hasAudio || meta.acodec === 'aac'
        const containerOk = ['mp4', 'm4v', 'mov'].includes(ext)

        if (!tooBig && !forceQuality && isH264 && isAac && containerOk && a.compress !== true) {
          // Sudah optimal & muat -> REMUX lossless (kualitas asli 100%).
          const out = path.join(tmpDir, 'video.mp4')
          if (ext !== 'mp4' && await remuxMp4(src, out)) {
            sendPath = out
            notes.push('remux lossless (kualitas asli)')
            meta = await probe(sendPath).catch(() => meta)
          } else if (ext === 'mp4') {
            notes.push('sudah MP4/H.264, kirim apa adanya (lossless)')
          }
        } else {
          // Perlu transcode: HEVC/VP9/AV1, wadah aneh, terlalu besar, atau diminta HD.
          const out = path.join(tmpDir, 'video.mp4')
          const ok = await transcodeVideo(src, out, meta, {
            maxDim: vMaxDim, crf: qCrf, preset, maxMb, fpsCap,
            denoise: a.denoise === true, sharpen: a.sharpen === true,
          })
          if (ok) {
            sendPath = out
            notes.push(`transcode H.264 High CRF${qCrf} @${vMaxDim}px`)
            if (tooBig || a.compress === true) notes.push(`batas ${maxMb}MB (inline)`)
            meta = await probe(sendPath).catch(() => meta)
          } else {
            notes.push('transcode gagal, kirim apa adanya')
          }
        }
      } else if (type === 'image') {
        // GIF animasi -> mp4 (WhatsApp memutar inline & loop).
        if (extOf(src) === 'gif' && a.sticker !== true) {
          const out = path.join(tmpDir, 'anim.mp4')
          if (await gifToMp4(src, out)) {
            sendPath = out
            notes.push('GIF → MP4 (loop)')
            meta = await probe(sendPath).catch(() => meta)
            const thumb = await makeThumbnail(sendPath).catch(() => undefined)
            const content = {
              video: { stream: fs.createReadStream(sendPath) },
              caption,
              mimetype: 'video/mp4',
              gifPlayback: true,
              ...(thumb ? { jpegThumbnail: thumb } : {}),
              ...(meta.width ? { width: meta.width, height: meta.height } : {}),
              ...(meta.duration ? { seconds: Math.round(meta.duration) } : {}),
            }
            const sent = await ctx.wa.send(to, content)
            const stat = fs.statSync(sendPath)
            ctx.log(`kirim gif→mp4 ${(stat.size / 1048576).toFixed(2)}MB -> ${to}`)
            return { ok: true, type: 'video', to, bytes: stat.size, mb: Number((stat.size / 1048576).toFixed(2)), gifPlayback: true, notes, id: sent?.key?.id }
          }
        }
        const out = await processImage(src, tmpDir, { maxDim: imgMaxDim, quality: Number(a.quality ? 92 : (a.imgQuality || 92)), format: a.format })
        if (out) {
          sendPath = out
          const before = meta.width && meta.height ? `${meta.width}x${meta.height}` : ''
          meta = await probe(sendPath).catch(() => ({}))
          notes.push(`sharp HD${before && meta.width ? ` (${before}→${meta.width}x${meta.height})` : ''}`)
        }
      } else if (type === 'audio') {
        if (wantPtt) {
          const out = path.join(tmpDir, 'voice.ogg')
          if (await toOggOpus(src, out)) { sendPath = out; meta = await probe(sendPath).catch(() => meta); notes.push('opus 48k mono (voice note)') }
        } else if (!AUDIO_INLINE.includes(extOf(src))) {
          const out = path.join(tmpDir, 'audio.m4a')
          if (await toM4a(src, out)) { sendPath = out; meta = await probe(sendPath).catch(() => meta); notes.push('AAC 192k (m4a)') }
        }
      } else if (type === 'sticker') {
        const isVideo = ['video', 'gif'].includes(detectType(src))
        const out = path.join(tmpDir, 'sticker.webp')
        if (!(await toWebp(src, out, isVideo))) return { ok: false, error: 'gagal membuat stiker webp' }
        sendPath = out
      }

      // 4) Susun konten WhatsApp sesuai jenis.
      const stream = () => fs.createReadStream(sendPath)
      let content
      if (type === 'image') {
        content = {
          image: { stream: stream() },
          caption,
          mimetype: mimeOf(sendPath) === 'application/octet-stream' ? 'image/jpeg' : mimeOf(sendPath),
          ...(meta.width ? { width: meta.width, height: meta.height } : {}),
          ...(a.viewOnce ? { viewOnce: true } : {}),
        }
      } else if (type === 'video') {
        const thumb = await makeThumbnail(sendPath, Number(a.thumbAt || 0)).catch(() => undefined)
        content = {
          video: { stream: stream() },
          caption,
          mimetype: 'video/mp4',
          ...(thumb ? { jpegThumbnail: thumb } : {}),
          ...(meta.width ? { width: meta.width, height: meta.height } : {}),
          ...(meta.duration ? { seconds: Math.round(meta.duration) } : {}),
          ...(a.gif === true || a.gifPlayback === true ? { gifPlayback: true } : {}),
          ...(a.viewOnce ? { viewOnce: true } : {}),
        }
      } else if (type === 'audio') {
        content = {
          audio: { stream: stream() },
          mimetype: wantPtt ? 'audio/ogg; codecs=opus' : (mimeOf(sendPath) === 'application/octet-stream' ? 'audio/mpeg' : mimeOf(sendPath)),
          ptt: wantPtt,
          ...(meta.duration ? { seconds: Math.round(meta.duration) } : {}),
        }
      } else if (type === 'sticker') {
        content = { sticker: { stream: stream() } }
      } else {
        content = {
          document: { stream: stream() },
          fileName: String(a.filename || path.basename(sendPath)),
          mimetype: String(a.mimetype || mimeOf(sendPath)),
          ...(caption ? { caption } : {}),
        }
      }

      // 4b) Tag/mention bila diminta (args.mentions = daftar JID). Caption tetap
      // harus memuat "@<nomor>" agar tampil biru/tertag di WhatsApp.
      if (Array.isArray(a.mentions) && a.mentions.length) content.mentions = a.mentions

      // 5) Kirim.
      const sent = await ctx.wa.send(to, content)
      const stat = fs.statSync(sendPath)
      const out = {
        ok: true,
        type,
        to,
        bytes: stat.size,
        mb: Number((stat.size / (1024 * 1024)).toFixed(2)),
        ...(meta.duration ? { seconds: Number(meta.duration.toFixed(1)) } : {}),
        ...(meta.width ? { resolution: `${meta.width}x${meta.height}` } : {}),
        ...(meta.vcodec ? { vcodec: meta.vcodec } : {}),
        ...(wantPtt ? { ptt: true } : {}),
        ...(notes.length ? { notes } : {}),
        id: sent?.key?.id,
      }
      ctx.log(`kirim ${type} ${out.mb}MB -> ${to}${notes.length ? ` (${notes.join('; ')})` : ''}`)
      return out
    } catch (e) {
      return { ok: false, error: e.message }
    } finally {
      cleanup()
    }
  },
}
