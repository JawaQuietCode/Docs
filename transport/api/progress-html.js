// progress-html.js — activity surface HTML mandiri di dalam kartu AI Rich.
// Tanpa indikator native, font eksternal, gambar, atau stylesheet jaringan.

const FASE_LABEL = {
  skills: 'Memilih konteks',
  think: 'Menyusun arah',
  tool: 'Menjalankan langkah',
  respond: 'Merangkai jawaban',
  asset: 'Menyiapkan hasil',
  done: 'Selesai',
}
const TOOL_SHELL = new Set(['bash', 'shell', 'terminal', 'exec', 'run_shell'])
const TOOL_RISET = new Set(['web_search', 'agent_reach', 'agent-reach', 'web_fetch', 'web_see'])

export const esc = (v) => String(v ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;')

const fmtDetik = (n) => `${(Number(n) || 0).toFixed(1)}s`

function barisHtml(l, i, selesai = true) {
  const gagal = l.ok === false
  const nama = String(l.nama || 'langkah')
  const detail = String(l.detail || '')
  const shell = TOOL_SHELL.has(nama.toLowerCase()) && detail
  const riset = TOOL_RISET.has(nama.toLowerCase())
  const judul = shell ? `$ ${detail}` : (riset ? `🔎 ${nama}` : nama)
  const rinci = shell ? '' : detail
  const waktu = Number.isFinite(Number(l.t)) && l.t !== null && l.t !== undefined && l.t !== ''
    ? `+${fmtDetik(l.t)}` : ''
  const status = gagal ? 'gagal' : (l.pending ? 'aktif' : 'beres')
  const ikon = gagal ? '×' : (l.pending && !selesai ? '↻' : '✓')
  return `<li class="${status}${riset ? ' riset' : ''}" style="--i:${i}">
    <span class="mark" aria-hidden="true">${ikon}</span>
    <span class="copy"><span class="name${shell ? ' mono' : ''}">${esc(judul)}</span>${rinci ? `<span class="detail">${esc(rinci)}</span>` : ''}</span>
    ${waktu ? `<time>${esc(waktu)}</time>` : ''}
  </li>`
}

function dokumen({ judul, fase, persen, detik, langkah, skillInfo, selesai, tick = 0, fasePernah = [] }) {
  const rows = (Array.isArray(langkah) ? langkah : []).slice(-8)
  const failed = rows.filter(x => x?.ok === false).length
  const pct = selesai ? 100 : Math.max(0, Math.min(98, Number(persen) || 0))
  const thinkVariants = ['Membaca konteks', 'Menyusun pendekatan', 'Merangkai petunjuk', 'Menimbang langkah']
  const activeTool = [...rows].reverse().find((item) => item?.pending)
  const phaseText = selesai
    ? 'Semua langkah sudah dirangkai'
    : (fase === 'think'
      ? thinkVariants[Math.abs(Math.trunc(Number(tick) || 0)) % thinkVariants.length]
      : (fase === 'tool' && activeTool?.nama
        ? `Menjalankan ${String(activeTool.nama)}`
        : (FASE_LABEL[fase] || String(fase || 'Sedang bekerja'))))
  const title = selesai ? 'Rangkaian selesai' : phaseText
  const liveClass = selesai ? 'done' : 'live'
  const phaseHistory = [...new Set((fasePernah || []).filter(Boolean))]
  const stage = phaseHistory.map((x) => FASE_LABEL[x]).filter(Boolean)
  const skill = String(skillInfo || '')
  const list = rows.length
    ? `<ol>${rows.map((l, i) => barisHtml(l, i, selesai)).join('')}</ol>`
    : `<p class="empty">${fase === 'skills' ? 'Memilah skills yang relevan…' : 'Menyiapkan langkah pertama…'}</p>`
  const skillLine = skill
    ? `<div class="skills"><span class="skill-mark" aria-hidden="true">✳</span><span class="skill-copy-wrap"><b>Skills / Context</b><span class="skill-copy">${esc(skill)}</span></span></div>`
    : (fase === 'skills' || phaseHistory.includes('skills')
      ? `<div class="skills pending"><span class="skill-mark" aria-hidden="true">✳</span><span class="skill-copy-wrap"><b>Skills / Context</b><span class="skill-copy">Memilih kemampuan yang relevan…</span></span></div>`
      : '')
  const phaseLine = stage.length
    ? `<div class="stages" aria-label="Tahap yang sudah dilewati">${stage.map(x => `<span>${esc(x)}</span>`).join('<i aria-hidden="true">→</i>')}</div>`
    : ''

  return `<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>${esc(judul || 'Kwee · progres')}</title>
<style>
  :root{color-scheme:light;--ink:#321d32;--paper:#fff0f3;--white:#fffaf8;--muted:#765d70;--yellow:#f1d4ad;--blue:#ddc9e5;--coral:#d85d83;--green:#c8dfcf;--line:#321d32}
  *{box-sizing:border-box}
  html,body{margin:0;padding:0;background:transparent!important;color:var(--ink);font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
  body{width:100%;overflow-wrap:anywhere}
  main{width:min(100%,560px);margin:0;padding:3px 2px 7px}
  .comic-frame{position:relative;isolation:isolate;overflow:hidden;padding:10px;border:2px solid var(--ink);border-radius:15px;background:var(--paper);box-shadow:4px 4px 0 var(--ink)}
  .comic-frame:before{content:"";position:absolute;z-index:-1;top:0;right:0;width:112px;height:80px;opacity:.12;background-image:radial-gradient(var(--ink) .8px,transparent .9px);background-size:7px 7px;pointer-events:none}
  .masthead{display:flex;align-items:center;justify-content:space-between;gap:9px;margin-bottom:10px}
  .identity{display:flex;align-items:center;gap:9px;min-width:0}
  .brand-stamp{display:grid;place-items:center;width:34px;height:34px;flex:0 0 34px;border:2px solid var(--ink);border-radius:3px;background:var(--coral);box-shadow:2px 2px 0 var(--ink);font-size:15px;font-weight:950;letter-spacing:-.08em;transform:rotate(-7deg)}
  .brand-copy{display:grid;gap:1px;min-width:0}
  .brand{font-size:10px;font-weight:950;letter-spacing:.15em;color:var(--ink)}
  .tagline{font-size:8px;font-weight:750;letter-spacing:.1em;color:var(--muted)}
  .state{display:inline-flex;align-items:center;gap:5px;padding:5px 7px;border:2px solid var(--ink);border-radius:6px;background:var(--ink);color:var(--yellow);font-size:9px;font-weight:900;font-variant-numeric:tabular-nums;white-space:nowrap;box-shadow:2px 2px 0 rgba(25,25,25,.2)}
  .state:before{content:"";width:6px;height:6px;border-radius:50%;background:var(--coral);box-shadow:0 0 0 2px rgba(255,120,109,.2)}
  .done .state{color:var(--green)}
  .done .state:before{background:var(--green);box-shadow:none}
  .hero-card{position:relative;padding:10px;border:2px solid var(--ink);border-radius:11px;background:#f4d9e2;box-shadow:3px 3px 0 var(--ink)}
  .hero-kicker{display:flex;align-items:center;gap:5px;margin-bottom:5px;font-size:8px;font-weight:950;letter-spacing:.14em}
  .burst{display:inline-grid;place-items:center;width:15px;height:15px;border:1.5px solid var(--ink);border-radius:50%;background:var(--coral);font-size:10px;line-height:1}
  h1{margin:0;font-size:clamp(16px,4vw,20px);line-height:1.12;font-weight:950;letter-spacing:-.045em}
  .meta{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:8px 0 6px;font-size:9px;font-weight:800}
  .meta span:first-child{min-width:0;overflow-wrap:anywhere}
  .step-count{padding:2px 5px;border:1.5px solid var(--ink);border-radius:4px;background:var(--white);white-space:nowrap}
  .track{height:12px;overflow:hidden;border:2px solid var(--ink);border-radius:5px;background:var(--white)}
  .fill{position:relative;height:100%;width:${pct}%;background:var(--coral);background-image:repeating-linear-gradient(135deg,rgba(25,25,25,.16) 0 4px,transparent 4px 8px);transition:width .55s cubic-bezier(.2,.8,.2,1)}
  .live .fill:after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.75),transparent);transform:translateX(-100%);animation:sweep 1.9s ease-in-out infinite}
  .hero-footer{display:flex;justify-content:space-between;align-items:center;margin-top:6px;font-size:8px;font-weight:900;letter-spacing:.12em}
  .percent{font-size:11px;letter-spacing:0;font-variant-numeric:tabular-nums}
  .stages{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin:10px 1px 0}
  .stages span{padding:3px 6px;border:1.5px solid var(--ink);border-radius:5px;background:var(--blue);font-size:8px;font-weight:850}
  .stages i{font-size:8px;font-style:normal;font-weight:900}
  .log-board{margin-top:10px;padding:9px;border:2px solid var(--ink);border-radius:11px;background:var(--white);box-shadow:3px 3px 0 var(--ink)}
  .board-head{display:flex;justify-content:space-between;align-items:center;gap:8px;padding-bottom:6px;border-bottom:2px solid var(--ink)}
  .board-title{font-size:9px;font-weight:950;letter-spacing:.13em}
  .board-count{padding:2px 5px;border:1.5px solid var(--ink);border-radius:4px;background:var(--blue);font-size:8px;font-weight:850;white-space:nowrap}
  .log{margin-top:5px}
  ol{display:grid;gap:0;margin:0;padding:0;list-style:none}
  li{display:grid;grid-template-columns:17px minmax(0,1fr) auto;align-items:start;gap:7px;padding:6px 0;border-bottom:1px dashed #c8c0b1;animation:rise .24s ease both;animation-delay:calc(var(--i)*35ms)}
  li:last-child{border-bottom:0;padding-bottom:1px}
  .mark{display:grid;place-items:center;width:16px;height:16px;border:1.5px solid var(--ink);border-radius:4px;background:var(--green);font-size:10px;font-weight:950;line-height:1;color:var(--ink);box-shadow:1px 1px 0 var(--ink);transform:rotate(-4deg)}
  .aktif .mark{background:var(--blue);animation:pulse 1.2s ease-in-out infinite}
  .gagal .mark{background:var(--coral)}
  .riset .name{color:#7c3458}
  .copy{min-width:0}
  .name{display:block;font-size:10px;font-weight:850;line-height:1.35}
  .mono{font:9px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
  .detail{display:block;margin-top:2px;color:var(--muted);font:9px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
  time{padding:2px 4px;border:1px solid var(--ink);border-radius:4px;background:var(--paper);color:var(--muted);font-size:8px;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
  .empty{margin:8px 0 2px;color:var(--muted);font-size:10px;font-weight:700}
  .skills{display:flex;align-items:flex-start;gap:8px;margin-top:10px;padding:8px;border:2px solid var(--ink);border-radius:10px;background:var(--blue);box-shadow:3px 3px 0 var(--ink);font-size:9px}
  .skill-mark{display:grid;place-items:center;width:20px;height:20px;flex:0 0 20px;border:1.5px solid var(--ink);border-radius:5px;background:var(--yellow);font-size:13px;font-weight:900}
  .skill-copy-wrap{min-width:0}
  .skills b{display:block;margin-bottom:2px;font-size:8px;font-weight:950;letter-spacing:.13em}
  .skill-copy{display:block;overflow-wrap:anywhere;font-weight:650}
  .pending .skill-copy{opacity:.78}
  .signature{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:10px 1px 0;padding-top:6px;border-top:2px solid var(--ink);font-size:7px;font-weight:950;letter-spacing:.16em}
  .signature-mark{display:grid;place-items:center;width:17px;height:17px;border:1.5px solid var(--ink);border-radius:50%;background:var(--coral);font-size:10px;letter-spacing:0}
  @keyframes pulse{50%{opacity:.45;transform:scale(.84) rotate(-4deg)}}
  @keyframes sweep{to{transform:translateX(110%)}}
  @keyframes rise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  @media(max-width:340px){main{padding-inline:1px}.comic-frame{padding:8px}.hero-card,.log-board{padding:8px}.state{font-size:8px}.tagline{font-size:7px}}
  @media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<main class="comic-frame ${liveClass}" data-tick="${Math.max(0, Math.trunc(Number(tick) || 0))}">
  <header class="masthead">
    <div class="identity"><span class="brand-stamp" aria-hidden="true">K!</span><span class="brand-copy"><span class="brand">KWEE / WORKROOM</span><span class="tagline">SMALL STEPS. REAL WORK.</span></span></div>
    <span class="state">${selesai ? '✓ SELESAI' : `${esc(fmtDetik(detik))} · ${pct}%`}</span>
  </header>
  <section class="hero-card">
    <div class="hero-kicker"><span class="burst" aria-hidden="true">✦</span> ${selesai ? 'COMIC WRAP / 100%' : 'LIVE BUILD / 01'}</div>
    <h1>${esc(title)}</h1>
    <div class="meta"><span>${esc(phaseText)}</span><span class="step-count">${rows.length} ${rows.length === 1 ? 'LANGKAH' : 'LANGKAH'}${failed ? ` · ${failed} GAGAL` : ''}</span></div>
    <div class="track" role="progressbar" aria-label="Progres pekerjaan" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><div class="fill"></div></div>
    <div class="hero-footer"><span>WORK IN MOTION</span><span class="percent">${pct}%</span></div>
  </section>
  ${phaseLine}
  <section class="log-board">
    <div class="board-head"><span class="board-title">FIELD NOTES / WORK LOG</span><span class="board-count">${rows.length} EVENT</span></div>
    <div class="log">${list}</div>
  </section>
  ${skillLine}
  <footer class="signature"><span>KWEE · MAKES THINGS MOVE</span><span class="signature-mark" aria-hidden="true">✳</span></footer>
</main>
</body>
</html>`
}

/** Render HTML mini untuk frame progres yang terus diedit di kartu yang sama. */
export function renderProgressLiveHtml(state = {}) {
  return dokumen({
    judul: state.judul || 'Kwee · activity',
    fase: state.fase || 'think',
    persen: state.persen,
    detik: state.detik,
    langkah: state.langkah,
    skillInfo: state.skillInfo,
    selesai: false,
    tick: state.tick,
    fasePernah: state.fasePernah,
  })
}

/**
 * Hasil akhir memakai visual berbeda dari live progress: lembar dossier statis,
 * tanpa progress bar/spinner. Ini menandai transisi dari "sedang bekerja" ke
 * "hasil kerja sudah ditutup" dan menjaga jawaban Markdown tetap jadi konten utama.
 */
function dokumenFinal({ judul = 'Kwee · field report', detik = 0, langkah = [], skillInfo = '', fasePernah = [] } = {}) {
  const rows = (Array.isArray(langkah) ? langkah : []).slice(-10)
  const failed = rows.filter((x) => x?.ok === false).length
  const elapsed = fmtDetik(detik)
  const phases = [...new Set((fasePernah || []).filter(Boolean))].map((x) => FASE_LABEL[x]).filter(Boolean)
  const timeline = rows.length
    ? `<ol class="dossier-log">${rows.map((l, i) => barisHtml(l, i, true)).join('')}</ol>`
    : '<p class="dossier-empty">Tidak ada langkah rinci yang direkam.</p>'
  const skills = skillInfo
    ? `<section class="dossier-skills"><b>Skills / Context</b><p>${esc(skillInfo)}</p></section>` : ''
  const phaseLine = phases.length
    ? `<div class="dossier-phases">${phases.map((x) => `<span>${esc(x)}</span>`).join(' <i>→</i> ')}</div>` : ''
  const status = failed ? 'CLOSED / WITH NOTES' : 'CLOSED / RUN COMPLETE'
  return `<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark">
<title>${esc(judul)}</title><style>
:root{color-scheme:dark;--ink:#fff5f1;--paper:#2b192d;--white:#fff8f5;--muted:#d7bfd0;--coral:#e7799b;--yellow:#efd4a9;--mint:#c9dfcf;--blue:#ddc9e5}
*{box-sizing:border-box}html,body{margin:0;padding:0;background:transparent!important;color:var(--ink);font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}body{width:100%;overflow-wrap:anywhere}main{width:min(100%,560px);margin:0;padding:3px 2px 7px}
.dossier{border:2px solid var(--yellow);border-radius:16px;background:var(--paper);box-shadow:5px 5px 0 #160d19;padding:14px;position:relative;overflow:hidden}
.dossier:after{content:"";position:absolute;right:0;top:0;width:86px;height:86px;opacity:.12;background-image:radial-gradient(var(--ink) .8px,transparent .9px);background-size:6px 6px;pointer-events:none}
.dossier-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;border-bottom:2px solid var(--yellow);padding-bottom:12px}
.dossier-brand{font-size:9px;font-weight:950;letter-spacing:.18em}.dossier-title{font-size:clamp(23px,5.5vw,32px);font-weight:1000;line-height:.98;letter-spacing:-.07em;text-transform:uppercase;margin:8px 0 5px;color:#fff8f5}.dossier-sub{font-size:9px;font-weight:750;color:var(--muted)}
.dossier-stamp{flex:none;transform:rotate(5deg);border:2px solid var(--yellow);border-radius:8px;background:var(--coral);color:#321d32;padding:8px 9px;box-shadow:3px 3px 0 #160d19;font-size:9px;font-weight:1000;letter-spacing:.08em;text-align:center;max-width:112px}
.dossier-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin:11px 0}
.dossier-stat{border:1.5px solid var(--yellow);border-radius:10px;background:#493149;color:#fff8f5;padding:8px;box-shadow:2px 2px 0 #160d19}.dossier-stat:nth-child(2){background:#efd4a9;color:#321d32}.dossier-stat:nth-child(3){background:#e9c5d5;color:#321d32}
.dossier-stat b{display:block;font-size:18px;font-weight:1000;letter-spacing:-.06em;line-height:1.05}.dossier-stat span{display:block;margin-top:3px;font-size:7px;font-weight:950;letter-spacing:.1em;text-transform:uppercase}
.dossier-section{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:14px 0 7px;padding-bottom:6px;border-bottom:1.5px solid var(--yellow)}.dossier-section b{font-size:9px;font-weight:1000;letter-spacing:.14em}.dossier-section span{font-size:8px;font-weight:800;color:var(--muted)}
.dossier-log{list-style:none;margin:0;padding:0 0 0 3px;border-left:2px solid var(--yellow)}.dossier-log li{display:grid;grid-template-columns:17px minmax(0,1fr) auto;gap:7px;padding:7px 0 7px 9px;border-bottom:1px dashed #684b65}.dossier-log li:last-child{border-bottom:0}.dossier-log .mark{display:grid;place-items:center;width:16px;height:16px;border:1.5px solid #321d32;border-radius:5px;background:var(--mint);color:#321d32;font-size:10px;font-weight:1000;line-height:1}.dossier-log .gagal .mark{background:var(--coral)}.dossier-log .name{display:block;font-size:10px;font-weight:900;color:#fff8f5}.dossier-log .detail{display:block;margin-top:2px;color:var(--muted);font:9px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.dossier-log time{font-size:8px;font-weight:850;white-space:nowrap}
.dossier-empty{font-size:10px;color:var(--muted);font-weight:700}.dossier-skills{margin-top:12px;padding:9px;border:1.5px solid var(--yellow);border-radius:10px;background:var(--blue);color:#321d32;box-shadow:2px 2px 0 #160d19}.dossier-skills b{font-size:8px;letter-spacing:.12em}.dossier-skills p{margin:4px 0 0;font-size:9px;font-weight:650;overflow-wrap:anywhere}
.dossier-phases{margin-top:10px;font-size:8px;font-weight:900}.dossier-phases span{display:inline-block;border:1.5px solid var(--yellow);border-radius:6px;background:#493149;color:#fff8f5;padding:4px 6px;margin:2px}.dossier-foot{display:flex;justify-content:space-between;gap:8px;margin-top:13px;padding-top:8px;border-top:1.5px solid var(--yellow);font-size:7px;font-weight:950;letter-spacing:.14em}
@media(max-width:360px){.dossier-stats{gap:5px}.dossier-stat{padding:5px}.dossier-stat b{font-size:16px}.dossier-stamp{font-size:8px;padding:5px}}
@media(prefers-reduced-motion:reduce){.dossier *{animation:none!important;transition:none!important}}
</style></head><body><main><article class="dossier" aria-label="Laporan akhir Kwee">
<header class="dossier-head"><div><div class="dossier-brand">KWEE / AFTER HOURS EDITION</div><h1 class="dossier-title">Catatan penutup</h1><div class="dossier-sub">Rangkaian selesai · jejak kerja, keputusan, dan hasil yang sudah dirangkai</div></div><div class="dossier-stamp">${esc(status)}</div></header>
<section class="dossier-stats" aria-label="Ringkasan hasil"><div class="dossier-stat"><b>${rows.length}</b><span>Langkah tercatat</span></div><div class="dossier-stat"><b>${esc(elapsed)}</b><span>Durasi</span></div><div class="dossier-stat"><b>${failed}</b><span>Langkah gagal</span></div></section>
<div class="dossier-section"><b>THE WORK / EXECUTION RECORD</b><span>${rows.length} entries</span></div>${timeline}${skills}${phaseLine}
<footer class="dossier-foot"><span>FINAL DOSSIER / NO. ${String(rows.length).padStart(2,'0')}</span><span>END OF RUN</span></footer>
</article></main></body></html>`
}

/** Render laporan akhir statis yang sengaja berbeda dari live progress. */
export function renderProgressHtml(state = {}) {
  return dokumenFinal({
    judul: state.judul || 'Kwee · field report',
    detik: state.detik,
    langkah: state.langkah,
    skillInfo: state.skillInfo,
    fasePernah: state.fasePernah,
  })
}

/** Susun state laporan dari catatan Kemajuan, lalu render. */
export function laporanDariKemajuan({ langkah = [], fasePernah = [], detik = 0, skillInfo = '', ringkasan = '' } = {}) {
  return renderProgressHtml({
    judul: 'Kwee · activity',
    langkah: langkah.map((l) => ({ nama: l.nama, detail: l.detail, ok: l.ok !== false, pending: false, t: l.t })),
    fasePernah,
    detik,
    skillInfo,
    ringkasan,
  })
}
