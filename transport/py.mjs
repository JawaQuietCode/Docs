// py.mjs — satu tempat untuk mencari interpreter Python yang BENAR.
//
// Kenapa perlu: transport Node memanggil Python untuk beberapa hal (yt-dlp di
// plugins/spdl.js, helper upload di plugins/tourl.js, backup_full.py). Dulu
// semuanya menulis `python3` mentah, sehingga memakai /usr/bin/python3 milik
// sistem — padahal dependensi proyek (yt-dlp, httpx, dst) dipasang ke user site
// milik interpreter lain. Gejalanya: "yt-dlp: /usr/bin/python3: No module named
// yt_dlp" walau paketnya "sudah dipasang".
//
// Urutan pilihan: KWEE_PY (di-export start.sh) → python3 → python.
import { spawnSync } from 'node:child_process'

function usable(bin) {
  try {
    const r = spawnSync(bin, ['-c', 'import sys; print(sys.version_info[0])'], {
      timeout: 8000, encoding: 'utf8',
    })
    return r.status === 0 && String(r.stdout || '').trim() === '3'
  } catch {
    return false
  }
}

const candidates = [process.env.KWEE_PY, 'python3', 'python'].filter(Boolean)
export const PY_BIN = candidates.find(usable) || 'python3'

/** True bila modul bisa diimpor interpreter ini. */
export function hasModule(name, bin = PY_BIN) {
  try {
    const r = spawnSync(bin, ['-c', `import ${name}`], { timeout: 15000 })
    return r.status === 0
  } catch {
    return false
  }
}

/** Pasang paket Python ke interpreter ini (best-effort). */
export function pipInstall(pkg, bin = PY_BIN) {
  try {
    const r = spawnSync(bin, ['-m', 'pip', 'install', '--user',
      '--break-system-packages', '--disable-pip-version-check', pkg],
      { timeout: 180000, encoding: 'utf8' })
    return r.status === 0
  } catch {
    return false
  }
}
