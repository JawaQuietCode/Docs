// spdl — unduh audio penuh (track/album/playlist Spotify) atau cari lagu, lalu kirim.
//
// Perintah: !spdl <url|uri|id|judul> [--limit N]
//   !spdl https://open.spotify.com/track/5lK3Csu1KbhfC26aZoY9hB
//   !spdl https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M --limit 3
//   !spdl Scorpion --limit 3            (pencarian teks bebas)
//
// Sumber audio: Spotify hanya metadata (judul+artis), audio penuh diambil lewat
// yt-dlp "ytsearch1: artis judul". Preview 30 dtk Spotify hanya fallback bila
// yt-dlp gagal — jadi durasi yang dikirim = durasi berkas asli (ffprobe).
import fs from 'fs'
import path from 'path'
import { spawn } from 'node:child_process'
import { PY_BIN, hasModule, pipInstall } from '../transport/py.mjs'

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
const MAKS = 3              // ponytail: host mematikan plugin di 30 dtk; unduhan penuh ~5-10 dtk/lagu
const DEFAULT_LIMIT = 1
const SPOT_RE = /spotify:(track|album|playlist|artist):([A-Za-z0-9]{22})/
const URL_RE = /open\.spotify\.com\/(?:intl-[a-z-]+\/)?(track|album|playlist|artist)\/([A-Za-z0-9]{22})/
const ID_RE = /^[A-Za-z0-9]{22}$/

const TAK_TAMPAK = /[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g
const CADANGAN = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i
const safe = (s, n = 90) => {
  const bersih = String(s || '').normalize('NFC').replace(TAK_TAMPAK, '')
    .replace(/[\\/:*?"<>|\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').replace(/^[\s._-]+|[\s.]+$/g, '')
  const potong = [...bersih].slice(0, n).join('').replace(/[\s.-]+$/, '')
  if (!potong) return 'audio'
  return CADANGAN.test(potong) ? `${potong}_` : potong
}

function parseArgs(ctx) {
  const raw = String(ctx.args?.text ?? (ctx.args?.argv || []).join(' ')).trim()
  const argv = raw ? raw.split(/\s+/) : []
  const flags = { limit: DEFAULT_LIMIT, query: [] }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if ((a === '--limit' || a === '-n') && argv[i + 1]) flags.limit = Number(argv[++i]) || DEFAULT_LIMIT
    else if (/^--limit=/.test(a)) flags.limit = Number(a.split('=')[1]) || DEFAULT_LIMIT
    else flags.query.push(a)
  }
  flags.limit = Math.max(1, Math.min(MAKS, flags.limit))
  return { query: flags.query.join(' ').trim(), limit: flags.limit }
}

const get = async (url, as = 'text') => {
  const r = await fetch(url, { headers: { 'User-Agent': UA, Referer: 'https://open.spotify.com/' } })
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url.slice(0, 60)}`)
  return as === 'json' ? r.json() : as === 'buf' ? Buffer.from(await r.arrayBuffer()) : r.text()
}

const run = (cmd, argv, timeout = 25000) => new Promise((res) => {
  let p
  try { p = spawn(cmd, argv, { stdio: ['ignore', 'pipe', 'pipe'] }) } catch { return res({ code: -1, out: '', err: 'spawn gagal' }) }
  let out = '', err = ''
  p.stdout.on('data', (d) => { out += d })
  p.stderr.on('data', (d) => { err += d })
  const t = setTimeout(() => p.kill('SIGKILL'), timeout)
  p.on('error', (e) => { clearTimeout(t); res({ code: -1, out, err: String(e.message) }) })
  p.on('close', (code) => { clearTimeout(t); res({ code, out, err }) })
})

// Durasi ASLI berkas (detik) — wajib dari file, bukan metadata track.
async function probeSeconds(file) {
  const r = await run('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', file], 10000)
  return Math.round(Number((r.out || '').trim()) || 0)
}

// Jalankan yt-dlp dengan tiga percobaan, karena penyebab kegagalan yang paling
// sering adalah interpreter yang salah — bukan situsnya:
//   1. `$PY -m yt_dlp`         (interpreter proyek, modul terpasang)
//   2. `yt-dlp`                (binary di PATH, kalau ada)
//   3. pasang modul ke interpreter proyek, lalu ulangi langkah 1
export async function runYtDlp(args, timeout) {
  let lastErr = null
  // 1) Modul di interpreter proyek — jalur normal.
  if (hasModule('yt_dlp')) {
    const r = await run(PY_BIN, ['-m', 'yt_dlp', ...args], timeout)
    if (r.code === 0) return r
    lastErr = r
  }
  // 2) Binary `yt-dlp` di PATH — cadangan bila modulnya ada di interpreter lain.
  const bin = await run('yt-dlp', args, timeout)
  if (bin.code === 0) return bin
  if (bin.code !== -1) lastErr = bin
  // 3) Modul memang belum ada: pasang di LATAR, jangan menahan permintaan
  //    (host mematikan plugin setelah 30 detik; pip bisa lebih lama dari itu).
  if (!hasModule('yt_dlp')) {
    try {
      spawn(PY_BIN, ['-m', 'pip', 'install', '--user', '--break-system-packages',
        '--disable-pip-version-check', 'yt-dlp'], { stdio: 'ignore', detached: true }).unref()
    } catch { /* diabaikan: tetap kembalikan pesan yang jelas di bawah */ }
    return {
      code: 127, out: '',
      err: `yt_dlp belum terpasang di ${PY_BIN} — sedang kupasang di latar, coba lagi sebentar lagi`,
    }
  }
  return lastErr || bin
}

// Audio penuh lewat yt-dlp. Return path .mp3 atau null.
async function fullAudio(query, dir, base) {
  const stem = path.join(dir, base)
  const r = await runYtDlp([
    `ytsearch1:${query} audio`,
    '-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', '5',
    '--no-playlist', '--no-warnings', '--quiet', '--force-overwrites',
    '-o', `${stem}.%(ext)s`,
  ])
  for (const ext of ['mp3', 'm4a', 'webm', 'opus']) {
    const f = `${stem}.${ext}`
    if (fs.existsSync(f) && fs.statSync(f).size > 50_000) return f
  }
  if (r.code !== 0) throw new Error(`yt-dlp: ${(r.err || '').split('\n').filter(Boolean).pop() || 'gagal'}`)
  return null
}

function parseInput(raw) {
  const t = String(raw || '').trim()
  const m = t.match(SPOT_RE) || t.match(URL_RE)
  if (m) return { kind: m[1], id: m[2] }
  if (ID_RE.test(t)) return { kind: 'track', id: t }
  return null
}

async function spotifyItems(kind, id) {
  const html = await get(`https://open.spotify.com/embed/${kind}/${id}`)
  const m = html.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/)
  if (!m) throw new Error(`Spotify tidak mengembalikan data untuk ${kind}/${id}`)
  const pp = JSON.parse(m[1])?.props?.pageProps || {}
  if (!pp.state) throw new Error(`Spotify: ${pp.title || 'halaman tidak bisa dibaca'}`)
  const e = pp.state.data.entity
  const rows = e.type === 'track' ? [e] : (e.trackList || [])
  const items = rows.map((t) => ({
    title: t.title || t.name || '?',
    artist: (t.subtitle || (t.artists || []).map((x) => x.name).filter(Boolean).join(', ') || '').replace(/\s+/g, ' ').trim(),
    preview: (t.audioPreview || {}).url,
    source: 'spotify',
  }))
  return { kind: e.type || kind, title: e.title || e.name || 'Spotify', items }
}

async function deezerItems(query, limit) {
  const d = await get(`https://api.deezer.com/search?q=${encodeURIComponent(query)}&limit=${limit}`, 'json')
  const items = (d.data || []).map((t) => ({
    title: t.title || '?',
    artist: t.artist?.name || '',
    preview: t.preview,
    source: 'deezer',
  }))
  return { kind: 'search', title: query, items }
}

export default {
  name: 'spdl',
  description: 'Unduh audio penuh dari Spotify/album/playlist atau cari lagu, lalu kirim sebagai audio',
  commands: ['spdl', 'spotify'],
  async run(ctx) {
    const { query, limit } = parseArgs(ctx)
    const to = ctx.msg?.chatId || ctx.args?.to
    if (!query) return 'Pakai: !spdl <link/id/judul Spotify> [--limit N]'
    if (!to) return { ok: false, error: 'chat tujuan tidak diketahui' }

    const spot = parseInput(query)
    const found = spot ? await spotifyItems(spot.kind, spot.id) : await deezerItems(query, limit)
    // Deezer 0 hasil (kueri panjang/typo) → serahkan judul mentah ke yt-dlp.
    if (!found.items.length) {
      if (spot) return `Tidak ada audio yang bisa diunduh untuk: ${query}`
      found.items = [{ title: query, artist: '', preview: null, source: 'search' }]
    }

    const items = found.items.slice(0, found.kind === 'track' ? 1 : limit)
    const dir = path.resolve(process.cwd(), 'data', 'media', 'spdl', String(Date.now()))
    fs.mkdirSync(dir, { recursive: true })

    // Unduhan PARALEL: satu plugin dibatasi 30 dtk host, jadi N lagu tidak
    // boleh diunduh berurutan (N x ~10 dtk). Unduh semua dulu (paralel),
    // baru kirim berurutan.
    const jobs = items.map((it, i) => {
      const name = `${String(i + 1).padStart(2, '0')} ${safe(it.title)} - ${safe(it.artist, 40)}.mp3`
      const file = path.join(dir, name)
      return (async () => {
        try {
          let src = null
          try {
            src = await fullAudio(`${it.artist} ${it.title}`.trim(), dir, `dl${i}`)
          } catch (e) { ctx.log(`yt-dlp gagal ${it.title}: ${e.message}`) }
          if (!src) {
            if (!it.preview) throw new Error('audio penuh & preview tidak tersedia')
            fs.writeFileSync(file, await get(it.preview, 'buf'))
            return { file, preview: true, title: it.title }
          }
          if (src !== file) fs.renameSync(src, file)
          if (!fs.statSync(file).size) throw new Error('berkas kosong')
          return { file, preview: false, title: it.title }
        } catch (e) {
          ctx.log(`gagal ${it.title}: ${e.message}`)
          return null
        }
      })()
    })

    const done = (await Promise.all(jobs)).filter(Boolean)
    let terkirim = 0
    const durasi = []
    const preview = []
    for (const j of done) {
      try {
        const seconds = await probeSeconds(j.file)
        await ctx.wa.send(to, { audio: { stream: fs.createReadStream(j.file) }, mimetype: 'audio/mpeg', ptt: false, ...(seconds ? { seconds } : {}) })
        terkirim++
        durasi.push(seconds)
        if (j.preview) preview.push(j.title)
      } catch (e) {
        ctx.log(`gagal kirim ${j.title}: ${e.message}`)
      }
    }

    const label = found.kind === 'track' ? found.title : `${found.kind} “${found.title}”`
    const d = durasi.filter(Boolean)
    const note = [
      d.length ? `${d.join('/')} dtk` : '',
      preview.length ? `${preview.length} fallback preview 30 dtk` : '',
    ].filter(Boolean).join('; ')
    return `${terkirim}/${items.length} audio terkirim — ${label}${note ? ` (${note})` : ''}`
  },
}
