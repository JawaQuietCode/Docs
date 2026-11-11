const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]))

const isoDuration = (seconds) => {
  const n = Math.max(0, Math.floor(Number(seconds) || 0))
  const d = Math.floor(n / 86400), h = Math.floor(n % 86400 / 3600), m = Math.floor(n % 3600 / 60), sec = n % 60
  return `P${d ? `${d}D` : ''}T${h}H${m}M${sec}S`
}

const fmtTime = (seconds) => {
  const n = Math.max(0, Math.floor(Number(seconds) || 0))
  const d = Math.floor(n / 86400)
  const h = Math.floor((n % 86400) / 3600)
  const m = Math.floor((n % 3600) / 60)
  const s = n % 60
  return `${d ? `${d}d ` : ''}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export function buildAfkHtml({ mode = 'list', items = [], session = null, wsUrl = '', wsToken = '', chatId = '' } = {}) {
  const rows = mode === 'single' && session ? [session] : (Array.isArray(items) ? items : [])
  const title = mode === 'single' ? 'AFK status' : 'AFK sekarang'
  const intro = mode === 'single' && session
    ? `<p class="lede">${esc(session.sender_name || session.sender_id)} sedang AFK.</p>`
    : `<p class="lede">${rows.length ? `${rows.length} orang sedang tidak aktif.` : 'Belum ada yang AFK.'}</p>`
  const list = rows.map((item, index) => {
    const name = esc(item.sender_name || item.sender_id || 'Pengguna')
    const reason = esc(item.reason || 'AFK')
    const elapsed = Math.max(0, Number(item.elapsed_seconds) || 0)
    const startedLabel = new Date(Number(item.started_at || 0) * 1000).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Jakarta' })
    return `<li class="person" data-started="${Number(item.started_at) || 0}">
      <div class="person-main"><span class="ordinal">${String(index + 1).padStart(2, '0')}</span>
        <div class="identity"><strong>${name}</strong><span>${reason} · sejak ${startedLabel}</span></div>
      </div><div class="duration"><time datetime="${isoDuration(elapsed)}" data-duration data-elapsed="${elapsed}">${fmtTime(elapsed)}</time><small>berlangsung</small></div>
    </li>`
  }).join('')
  const safeWs = /^wss:\/\//i.test(String(wsUrl)) ? String(wsUrl) : ''
  const boot = JSON.stringify({ wsUrl: safeWs, wsToken, mode, chatId: String(chatId || '') }).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026')
  return `<!doctype html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title} · Kwee</title>
<style>
:root{color-scheme:light dark;--bg:#f3f1eb;--ink:#242720;--muted:#666b62;--line:#d8d8ce;--accent:#b34d31;--surface:#faf9f5}
*{box-sizing:border-box}body{margin:0;padding:18px;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:620px;margin:0 auto}.topline{display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--line);padding:0 0 13px}
.brand{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}.live{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--muted)}.dot{width:7px;height:7px;border-radius:50%;background:#8b9382}
h1{font-size:clamp(26px,7vw,38px);line-height:1.05;letter-spacing:-.045em;margin:22px 0 8px;font-weight:680}.lede{color:var(--muted);margin:0 0 18px}
ul{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}.person{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:15px 0;border-bottom:1px solid var(--line)}
.person-main{display:flex;align-items:flex-start;gap:12px;min-width:0}.ordinal{font-size:11px;color:var(--accent);font-variant-numeric:tabular-nums;padding-top:3px}.identity{display:grid;gap:3px;min-width:0}.identity strong{font-size:16px;font-weight:640;overflow-wrap:anywhere}.identity span{font-size:13px;color:var(--muted);overflow-wrap:anywhere}.duration{text-align:right;flex:0 0 auto;font-variant-numeric:tabular-nums}.duration time{display:block;font-size:16px;font-weight:650;white-space:nowrap}.duration small{display:block;font-size:11px;color:var(--muted)}
.empty{padding:22px 0;color:var(--muted);border-bottom:1px solid var(--line)}footer{padding-top:15px;color:var(--muted);font-size:11px;display:flex;justify-content:space-between;gap:12px}.status{min-height:1em}
@media(prefers-color-scheme:dark){:root{--bg:#1d201c;--ink:#f1f0e8;--muted:#a6aa9f;--line:#41463d;--accent:#e18a6b;--surface:#252923}}
@media(max-width:360px){body{padding:14px}.person{gap:8px}.duration time{font-size:14px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
</style></head><body><main>
<div class="topline"><span class="brand">Kwee · status</span><span class="live"><i class="dot" id="dot"></i><span id="connection">waktu berjalan</span></span></div>
<h1>${title}</h1>${intro}
<ul id="people">${list || '<li class="empty">Tidak ada sesi AFK aktif di chat ini.</li>'}</ul>
<footer><span class="status" id="status" role="status" aria-live="polite">Durasi dihitung dari waktu mulai tersimpan.</span><span>SQLite · local state</span></footer>
</main>
<script>
(()=>{const cfg=${boot};const list=document.getElementById('people');const status=document.getElementById('status');const conn=document.getElementById('connection');const dot=document.getElementById('dot');let clockOffset=0;const now=()=>Date.now()/1000+clockOffset;
const fmt=n=>{n=Math.max(0,Math.floor(n));const d=Math.floor(n/86400),h=Math.floor(n%86400/3600),m=Math.floor(n%3600/60),s=n%60;return (d?d+'d ':'')+[h,m,s].map(x=>String(x).padStart(2,'0')).join(':')};
const iso=n=>{n=Math.max(0,Math.floor(n));const d=Math.floor(n/86400),h=Math.floor(n%86400/3600),m=Math.floor(n%3600/60),s=n%60;return 'P'+(d?d+'D':'')+'T'+h+'H'+m+'M'+s+'S'};const tick=()=>{document.querySelectorAll('[data-started]').forEach(row=>{const t=row.querySelector('[data-duration]');if(!t)return;const start=Number(row.dataset.started||0),elapsed=Math.max(0,now()-start);t.textContent=fmt(elapsed);t.dateTime=iso(elapsed)})};tick();setInterval(tick,1000);
const render=items=>{
  if(!Array.isArray(items))return;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if(!items.length){list.innerHTML='<li class="empty">Tidak ada sesi AFK aktif di chat ini.</li>';return;}
  list.innerHTML=items.map((x,i)=>{
    const started=Number(x.started_at)||0;
    const elapsed=Math.max(0,Number(x.elapsed_seconds)||0);
    const startedLabel=new Date(started*1000).toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Jakarta'});
    return '<li class="person" data-started="'+started+'"><div class="person-main"><span class="ordinal">'+String(i+1).padStart(2,'0')+'</span><div class="identity"><strong>'+esc(x.sender_name||x.sender_id||'Pengguna')+'</strong><span>'+esc(x.reason||'AFK')+' · sejak '+startedLabel+'</span></div></div><div class="duration"><time datetime="'+iso(elapsed)+'" data-duration data-elapsed="'+elapsed+'">'+fmt(now()-started)+'</time><small>berlangsung</small></div></li>'
  }).join('');
  tick();
};
if(cfg.wsUrl&&cfg.wsToken){let retry=1000,ws;const connect=()=>{try{const u=new URL(cfg.wsUrl);u.searchParams.set('chat_id',cfg.chatId||'');u.searchParams.set('token',cfg.wsToken);ws=new WebSocket(u);ws.onopen=()=>{retry=1000;conn.textContent='live';dot.style.background='#4b9b67'};ws.onmessage=e=>{try{const msg=JSON.parse(e.data);if(msg.type==='snapshot'){if(Number(msg.server_time))clockOffset=Number(msg.server_time)-Date.now()/1000;render(msg.items)}if(msg.type==='ping')ws.send('pong')}catch{}};ws.onclose=e=>{if(e&&e.code===1008){conn.textContent='reopen card to refresh';status.textContent='Sesi live berakhir; buka ulang card untuk token baru.';return}conn.textContent='reconnecting';dot.style.background='#b58b46';setTimeout(connect,retry);retry=Math.min(30000,retry*2)};ws.onerror=()=>ws.close()}catch{conn.textContent='waktu lokal';}};connect();setInterval(()=>{if(ws&&ws.readyState===1)ws.send('ping')},25000)}else{conn.textContent='waktu lokal';status.textContent='Durasi real-time lokal; sinkronisasi daftar perlu URL WSS publik.'}
})();
</script></body></html>`
}
