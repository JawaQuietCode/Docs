import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

function log(...a) { try { process.stderr.write(`[browser ${new Date().toISOString()}] ${a.join(' ')}\n`) } catch {} }
function out(o) { try { process.stdout.write(JSON.stringify(o) + '\n') } catch {} }

function arg(name, def = '') {
  const prefix = `--${name}=`
  const hit = process.argv.find((a) => a.startsWith(prefix))
  return hit ? hit.slice(prefix.length) : def
}
function has(name) { return process.argv.includes(`--${name}`) }
function num(name, def) { const v = Number(arg(name, String(def))); return Number.isFinite(v) ? v : def }
function bool(name, def = false) {
  if (!has(name)) return def
  const v = arg(name, '').toLowerCase()
  if (v === 'false' || v === '0' || v === 'no' || v === '') return false
  return true
}
function argText(name) {
  const file = arg(`${name}File`)
  if (file && fs.existsSync(file)) {
    try { return fs.readFileSync(file, 'utf8') } catch (e) { log('readFile fail', file, e.message) }
  }
  return arg(name, '')
}
function readJSON(file, def) { try { return JSON.parse(fs.readFileSync(file, 'utf8')) } catch { return def } }
function writeJSON(file, obj) {
  try { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(obj)) }
  catch (e) { log('writeJSON fail', file, e.message) }
}
function isBlank(u) { return !u || u === 'about:blank' || u.startsWith('about:') }
function parseSelector(spec) {
  if (!spec) return []
  if (spec.startsWith('[')) {
    try { const arr = JSON.parse(spec); if (Array.isArray(arr)) return arr.map(String).filter(Boolean) } catch {}
  }
  return spec.split('|||').map((s) => s.trim()).filter(Boolean)
}
async function withRetry(fn, attempts, delayMs) {
  let last
  for (let i = 0; i < attempts; i++) {
    try { return await fn(i) }
    catch (e) {
      last = e
      log(`retry ${i + 1}/${attempts}:`, String(e?.message || e).slice(0, 200))
      if (i + 1 < attempts) await new Promise((r) => setTimeout(r, delayMs * (i + 1)))
    }
  }
  throw last
}
async function findFirst(page, selectors, timeoutMs) {
  if (!selectors.length) return null
  const perMs = Math.max(1000, Math.floor(timeoutMs / selectors.length))
  for (const sel of selectors) {
    try {
      const el = await page.waitForSelector(sel, { timeout: perMs, state: 'attached' })
      if (el) return { el, selector: sel }
    } catch {}
  }
  return null
}

async function main() {
  const action = arg('action', 'goto')
  const userDataDir = arg('profile') || path.join(process.cwd(), 'data', 'browser')
  const timeoutMs = num('timeout', 30000) || 30000
  const shotDir = arg('shotDir') || path.join(process.cwd(), 'data', 'media', 'files', 'browser')
  const width = num('width', 1366) || 1366
  const height = num('height', 900) || 900
  const durationSec = Math.min(600, Math.max(1, num('duration', 10) || 10))
  const stateFile = `${userDataDir.replace(/[\\/]+$/, '')}.state.json`
  const state = readJSON(stateFile, {})

  fs.mkdirSync(userDataDir, { recursive: true })
  fs.mkdirSync(shotDir, { recursive: true })

  const isRecord = action === 'record'
  const videoDir = path.join(shotDir, `rec-${Date.now()}`)
  if (isRecord) fs.mkdirSync(videoDir, { recursive: true })

  const launchOpts = {
    headless: !has('headed'),
    viewport: { width, height },
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    acceptDownloads: true,
    ignoreHTTPSErrors: bool('ignoreHTTPS', false),
    locale: arg('locale', 'en-US'),
    timezoneId: arg('timezone', 'UTC'),
  }
  if (isRecord) {
    launchOpts.recordVideo = {
      dir: videoDir,
      size: { width: width - (width % 2), height: height - (height % 2) },
    }
  }
  const proxy = arg('proxy')
  if (proxy) launchOpts.proxy = { server: proxy }
  const ua = arg('userAgent')
  if (ua) launchOpts.userAgent = ua

  const ctx = await chromium.launchPersistentContext(userDataDir, launchOpts)
  try { ctx.setDefaultTimeout(timeoutMs) } catch {}
  try { ctx.setDefaultNavigationTimeout(timeoutMs) } catch {}

  // Stealth ringan: banyak situs menolak Chromium headless karena penanda ini.
  // Cukup untuk halaman publik; bukan alat menembus captcha.
  if (!has('noStealth')) {
    try {
      await ctx.addInitScript(() => {
        try {
          Object.defineProperty(navigator, 'webdriver', { get: () => undefined })
          Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] })
          Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] })
          window.chrome = window.chrome || { runtime: {} }
        } catch {}
      })
    } catch (e) { log('stealth fail', e.message) }
  }
  // Mode hemat kuota: gambar/font/media tidak diunduh (teks & struktur tetap).
  if (has('blockImages')) {
    try {
      await ctx.route('**/*', (route) => {
        const t = route.request().resourceType()
        return (t === 'image' || t === 'media' || t === 'font') ? route.abort() : route.continue()
      })
    } catch (e) { log('blockImages fail', e.message) }
  }

  const extra = arg('extraHeaders')
  if (extra) {
    try { await ctx.setExtraHTTPHeaders(JSON.parse(extra)) } catch (e) { log('extraHeaders fail', e.message) }
  }

  const pages = ctx.pages()
  let page = pages.length ? pages[0] : await ctx.newPage()
  try { page.setDefaultTimeout(timeoutMs) } catch {}
  try { page.setDefaultNavigationTimeout(timeoutMs) } catch {}

  const consoleLogs = []
  const networkLogs = []
  const captureConsole = bool('captureConsole', false)
  const captureNetwork = bool('captureNetwork', false)
  if (captureConsole) {
    page.on('console', (m) => consoleLogs.push({ type: m.type(), text: m.text(), ts: Date.now() }))
    page.on('pageerror', (e) => consoleLogs.push({ type: 'pageerror', text: String(e?.message || e), ts: Date.now() }))
  }
  if (captureNetwork) {
    page.on('request', (r) => networkLogs.push({ phase: 'req', url: r.url(), method: r.method(), resource: r.resourceType(), ts: Date.now() }))
    page.on('response', (r) => networkLogs.push({ phase: 'res', url: r.url(), status: r.status(), ts: Date.now() }))
    page.on('requestfailed', (r) => networkLogs.push({ phase: 'fail', url: r.url(), error: r.failure()?.errorText || '', ts: Date.now() }))
  }

  const autoAccept = bool('autoAcceptDialogs', true)
  const autoDismiss = bool('autoDismissDialogs', false)
  const promptText = arg('dialogText', '')
  page.on('dialog', async (d) => {
    try {
      if (autoDismiss) await d.dismiss()
      else if (autoAccept) { if (d.type() === 'prompt') await d.accept(promptText); else await d.accept() }
    } catch (e) { log('dialog fail', e.message) }
  })
  page.on('popup', async (p) => { try { await p.waitForLoadState('domcontentloaded', { timeout: 5000 }) } catch {} })

  const lastUrl = state.url
  if (isBlank(page.url()) && lastUrl && !arg('url')) {
    try {
      await page.goto(lastUrl, { waitUntil: 'domcontentloaded', timeout: timeoutMs })
      await page.waitForTimeout(300)
      log('restored', lastUrl)
    } catch (e) { log('restore fail', e.message) }
  }

  const result = { ok: true, action, startedAt: new Date().toISOString() }
  let videoHandle = null

  try {
    switch (action) {
      case 'goto': {
        const url = arg('url')
        if (!url) throw new Error('url kosong')
        const attempts = Math.max(1, num('retries', 2) || 2)
        const resp = await withRetry(
          () => page.goto(url, { waitUntil: arg('waitUntil', 'domcontentloaded'), timeout: timeoutMs }),
          attempts, 500)
        await page.waitForTimeout(num('settleMs', 600) || 600)
        result.url = page.url()
        result.title = await page.title()
        result.status = resp ? resp.status() : null
        result.requestedUrl = url
        break
      }
      case 'screenshot': {
        if (arg('url')) {
          await page.goto(arg('url'), { waitUntil: 'domcontentloaded', timeout: timeoutMs })
          await page.waitForTimeout(num('settleMs', 800) || 800)
        }
        if (bool('waitStable', false)) { try { await page.waitForLoadState('networkidle', { timeout: 5000 }) } catch {} }
        const type = arg('type', 'png')
        const ext = type === 'jpeg' ? 'jpg' : type
        const file = path.join(shotDir, `shot-${Date.now()}.${ext}`)
        const opts = { path: file, type, fullPage: has('full') }
        const quality = num('quality', 0)
        if (type === 'jpeg' && quality > 0) opts.quality = Math.min(100, Math.max(0, quality))
        const sel = arg('selector')
        if (sel) {
          const found = await findFirst(page, parseSelector(sel), timeoutMs)
          if (!found) throw new Error(`selector not found: ${sel}`)
          await found.el.screenshot(opts)
          result.selector = found.selector
        } else {
          const clip = arg('clip')
          if (clip) { try { const c = JSON.parse(clip); if (c && c.width && c.height) opts.clip = c } catch {} }
          await page.screenshot(opts)
        }
        result.path = file
        result.url = page.url()
        result.title = await page.title()
        try { result.sizeBytes = fs.statSync(file).size } catch {}
        break
      }
      case 'text': {
        const limit = num('limit', 20000) || 20000
        const found = await findFirst(page, parseSelector(arg('selector', 'body')), timeoutMs)
        result.text = found ? (await found.el.innerText()).slice(0, limit) : ''
        result.url = page.url()
        break
      }
      case 'html': {
        const limit = num('limit', 40000) || 40000
        const found = await findFirst(page, parseSelector(arg('selector', 'html')), timeoutMs)
        result.html = found ? (await found.el.innerHTML()).slice(0, limit) : ''
        break
      }
      case 'attribute': {
        const sel = arg('selector')
        const name = arg('name')
        if (!sel || !name) throw new Error('selector/name kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        result.value = found ? await found.el.getAttribute(name) : null
        break
      }
      case 'click': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const force = bool('force', false)
        const attempts = Math.max(1, num('retries', 2) || 2)
        await withRetry(async () => {
          const found = await findFirst(page, parseSelector(sel), timeoutMs)
          if (!found) throw new Error(`selector not found: ${sel}`)
          if (bool('highlight', false)) {
            try { await found.el.evaluate((e) => { e.style.outline = '3px solid red' }) ; await page.waitForTimeout(150) } catch {}
          }
          await found.el.click({ force, timeout: timeoutMs })
        }, attempts, 500)
        await page.waitForTimeout(num('settleMs', 600) || 600)
        if (bool('waitStable', false)) { try { await page.waitForLoadState('networkidle', { timeout: 5000 }) } catch {} }
        result.url = page.url()
        result.title = await page.title()
        break
      }
      case 'dblclick': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        await found.el.dblclick({ timeout: timeoutMs })
        await page.waitForTimeout(num('settleMs', 400) || 400)
        result.url = page.url()
        break
      }
      case 'hover': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        await found.el.hover({ timeout: timeoutMs })
        await page.waitForTimeout(num('settleMs', 300) || 300)
        result.ok = true
        break
      }
      case 'fill': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        await found.el.fill(argText('value'))
        result.ok = true
        break
      }
      case 'type': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        await found.el.type(argText('value'), { delay: num('delay', 30) || 30 })
        result.ok = true
        break
      }
      case 'clear': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        await found.el.fill('')
        result.ok = true
        break
      }
      case 'press': { await page.keyboard.press(arg('key', 'Enter')); result.ok = true; break }
      case 'keydown': { await page.keyboard.down(arg('key')); result.ok = true; break }
      case 'keyup': { await page.keyboard.up(arg('key')); result.ok = true; break }
      case 'select': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const value = argText('value')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        const valList = value.includes('|||') ? value.split('|||').map((s) => s.trim()) : [value]
        await found.el.selectOption(valList.length === 1 ? valList[0] : valList)
        result.ok = true
        break
      }
      case 'check':
      case 'uncheck': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        if (action === 'check') await found.el.check({ timeout: timeoutMs })
        else await found.el.uncheck({ timeout: timeoutMs })
        result.ok = true
        break
      }
      case 'scroll': {
        const sel = arg('selector')
        if (sel) {
          const found = await findFirst(page, parseSelector(sel), timeoutMs)
          if (!found) throw new Error(`selector not found: ${sel}`)
          await found.el.scrollIntoViewIfNeeded()
        } else {
          await page.evaluate(([x, y]) => window.scrollBy(x, y), [num('x', 0) || 0, num('y', 0) || 0])
        }
        await page.waitForTimeout(num('settleMs', 200) || 200)
        result.scrollY = await page.evaluate(() => window.scrollY)
        break
      }
      case 'scrollTo': {
        await page.evaluate(([x, y]) => window.scrollTo(x, y), [num('x', 0) || 0, num('y', 0) || 0])
        await page.waitForTimeout(200)
        result.scrollY = await page.evaluate(() => window.scrollY)
        break
      }
      case 'eval': {
        const code = argText('script') || arg('script')
        if (!code) throw new Error('script kosong')
        result.value = await page.evaluate(code)
        break
      }
      case 'links': {
        const limit = num('limit', 200) || 200
        result.links = await page.$$eval('a[href]',
          (as, lim) => as.slice(0, lim).map((a) => ({
            text: (a.innerText || '').trim().slice(0, 120),
            href: a.href,
            title: a.title || '',
          })), limit)
        break
      }
      case 'elements': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const limit = num('limit', 100) || 100
        result.elements = await page.$$eval(sel,
          (els, lim) => els.slice(0, lim).map((e) => ({
            tag: e.tagName.toLowerCase(),
            text: (e.innerText || '').trim().slice(0, 200),
            id: e.id || '',
            class: typeof e.className === 'string' ? e.className : '',
            href: e.href || '',
            visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length),
          })), limit)
        break
      }
      case 'pdf': {
        const file = path.join(shotDir, `page-${Date.now()}.pdf`)
        const pdfOpts = {
          path: file,
          format: arg('format', 'A4'),
          printBackground: bool('printBackground', true),
          landscape: bool('landscape', false),
          scale: Math.min(2, Math.max(0.1, num('scale', 1) || 1)),
        }
        const margins = arg('margins')
        if (margins) { try { pdfOpts.margin = JSON.parse(margins) } catch {} }
        const ranges = arg('pageRanges')
        if (ranges) pdfOpts.pageRanges = ranges
        await page.pdf(pdfOpts)
        result.path = file
        try { result.sizeBytes = fs.statSync(file).size } catch {}
        break
      }
      case 'wait': {
        const sel = arg('selector')
        const ms = num('ms', 1000) || 1000
        const st = arg('state', 'visible')
        const loadState = arg('loadState', '')
        const waitFn = arg('fn', '')
        if (sel) await page.waitForSelector(sel, { timeout: num('timeout', timeoutMs) || timeoutMs, state: st })
        else if (loadState) await page.waitForLoadState(loadState, { timeout: timeoutMs })
        else if (waitFn) await page.waitForFunction(waitFn, null, { timeout: timeoutMs })
        else await page.waitForTimeout(ms)
        result.ok = true
        break
      }
      case 'back': {
        const r = await page.goBack({ waitUntil: arg('waitUntil', 'domcontentloaded'), timeout: timeoutMs })
        result.url = page.url()
        result.title = await page.title()
        result.status = r ? r.status() : null
        break
      }
      case 'forward': {
        const r = await page.goForward({ waitUntil: arg('waitUntil', 'domcontentloaded'), timeout: timeoutMs })
        result.url = page.url()
        result.title = await page.title()
        result.status = r ? r.status() : null
        break
      }
      case 'reload': {
        const r = await page.reload({ waitUntil: arg('waitUntil', 'domcontentloaded'), timeout: timeoutMs })
        result.url = page.url()
        result.title = await page.title()
        result.status = r ? r.status() : null
        break
      }
      case 'cookies': {
        const sub = arg('sub', 'get')
        if (sub === 'set') { await ctx.addCookies(JSON.parse(argText('cookies'))); result.ok = true }
        else if (sub === 'clear') { await ctx.clearCookies(); result.ok = true }
        else result.cookies = await ctx.cookies()
        break
      }
      case 'storage': {
        const sub = arg('sub', 'get')
        if (sub === 'set') {
          const kv = JSON.parse(argText('value'))
          await page.evaluate((obj) => { for (const k of Object.keys(obj)) localStorage.setItem(k, String(obj[k])) }, kv)
          result.ok = true
        } else if (sub === 'clear') { await page.evaluate(() => localStorage.clear()); result.ok = true }
        else result.storage = await page.evaluate(() => ({ ...localStorage }))
        break
      }
      case 'upload': {
        const sel = arg('selector')
        if (!sel) throw new Error('selector kosong')
        const files = (arg('files') || '').split('|||').map((s) => s.trim()).filter(Boolean)
        if (!files.length) throw new Error('files kosong')
        const found = await findFirst(page, parseSelector(sel), timeoutMs)
        if (!found) throw new Error(`selector not found: ${sel}`)
        await found.el.setInputFiles(files)
        result.count = files.length
        result.ok = true
        break
      }
      case 'download': {
        const sel = arg('selector')
        const urlArg = arg('url')
        if (!sel && !urlArg) throw new Error('selector/url kosong')
        const [dl] = await Promise.all([
          page.waitForEvent('download', { timeout: timeoutMs }),
          (async () => {
            if (sel) {
              const found = await findFirst(page, parseSelector(sel), timeoutMs)
              if (!found) throw new Error(`selector not found: ${sel}`)
              await found.el.click()
            } else {
              await page.goto(urlArg, { waitUntil: 'domcontentloaded', timeout: timeoutMs })
            }
          })(),
        ])
        const dlPath = path.join(shotDir, `download-${Date.now()}-${dl.suggestedFilename()}`)
        await dl.saveAs(dlPath)
        result.path = dlPath
        result.suggestedFilename = dl.suggestedFilename()
        try { result.sizeBytes = fs.statSync(dlPath).size } catch {}
        break
      }
      case 'a11y': {
        result.snapshot = await page.accessibility.snapshot({ interestingOnly: bool('interestingOnly', true) })
        break
      }
      case 'metrics': {
        result.metrics = await page.evaluate(() => {
          const nav = performance.getEntriesByType('navigation')[0] || {}
          return {
            url: location.href,
            title: document.title,
            readyState: document.readyState,
            timing: {
              dns: nav.domainLookupEnd - nav.domainLookupStart,
              connect: nav.connectEnd - nav.connectStart,
              ttfb: nav.responseStart - nav.requestStart,
              domReady: nav.domContentLoadedEventEnd - nav.startTime,
              load: nav.loadEventEnd - nav.startTime,
            },
            resources: performance.getEntriesByType('resource').length,
            memory: performance.memory ? {
              used: performance.memory.usedJSHeapSize,
              total: performance.memory.totalJSHeapSize,
            } : null,
          }
        })
        break
      }
      case 'state': {
        result.url = page.url()
        result.title = await page.title()
        result.pages = ctx.pages().map((p, i) => ({ index: i, url: p.url() }))
        break
      }
      case 'tabs': {
        result.pages = ctx.pages().map((p, i) => ({ index: i, url: p.url() }))
        break
      }
      case 'newtab': {
        const p = await ctx.newPage()
        try { p.setDefaultTimeout(timeoutMs); p.setDefaultNavigationTimeout(timeoutMs) } catch {}
        const url = arg('url')
        if (url) await p.goto(url, { waitUntil: 'domcontentloaded', timeout: timeoutMs })
        result.url = p.url()
        result.index = ctx.pages().indexOf(p)
        break
      }
      case 'closetab': {
        const all = ctx.pages()
        const idx = num('index', -1)
        if (idx >= 0 && idx < all.length) { await all[idx].close(); result.closedIndex = idx }
        else if (all.length > 1) { await all[all.length - 1].close(); result.closedIndex = all.length - 1 }
        else throw new Error('tidak bisa menutup tab terakhir')
        result.ok = true
        break
      }
      case 'switchtab': {
        const all = ctx.pages()
        const idx = num('index', -1)
        if (idx < 0 || idx >= all.length) throw new Error('index tidak valid')
        page = all[idx]
        try { page.setDefaultTimeout(timeoutMs); page.setDefaultNavigationTimeout(timeoutMs) } catch {}
        await page.bringToFront()
        result.url = page.url()
        result.index = idx
        break
      }
      case 'headers': {
        const raw = argText('headers') || arg('headers')
        if (!raw) throw new Error('headers kosong')
        await ctx.setExtraHTTPHeaders(JSON.parse(raw))
        result.ok = true
        break
      }
      case 'viewport': {
        const w = num('width', width) || width
        const h = num('height', height) || height
        await page.setViewportSize({ width: w, height: h })
        result.width = w
        result.height = h
        break
      }
      case 'geolocation': {
        await ctx.setGeolocation({ latitude: num('latitude', 0) || 0, longitude: num('longitude', 0) || 0 })
        try { await ctx.grantPermissions(['geolocation']) } catch {}
        result.ok = true
        break
      }
      case 'permissions': {
        const list = (arg('permissions') || '').split('|||').map((s) => s.trim()).filter(Boolean)
        if (!list.length) throw new Error('permissions kosong')
        await ctx.grantPermissions(list)
        result.granted = list
        break
      }
      case 'trace': {
        const sub = arg('sub', 'start')
        if (sub === 'start') {
          await ctx.tracing.start({ screenshots: true, snapshots: true, sources: bool('sources', true) })
          result.ok = true
        } else if (sub === 'stop') {
          const file = path.join(shotDir, `trace-${Date.now()}.zip`)
          await ctx.tracing.stop({ path: file })
          result.path = file
        } else throw new Error(`sub trace tidak dikenal: ${sub}`)
        break
      }
      case 'record': {
        const url = arg('url')
        if (url) {
          await page.goto(url, { waitUntil: arg('waitUntil', 'domcontentloaded'), timeout: timeoutMs })
          await page.waitForTimeout(num('settleMs', 800) || 800)
        }
        videoHandle = page.video()
        result.durationSec = durationSec
        result.url = page.url()
        result.title = await page.title()
        await page.waitForTimeout(durationSec * 1000)
        break
      }
      default:
        throw new Error(`aksi tidak dikenal: ${action}`)
    }
  } catch (e) {
    result.ok = false
    result.error = String(e?.message || e)
    if (bool('stack', false) && e?.stack) result.stack = String(e.stack)
    log('ERROR', result.error)
  } finally {
    try {
      const u = page.url()
      if (!isBlank(u)) {
        let title = ''
        try { title = await page.title() } catch {}
        writeJSON(stateFile, { ...state, url: u, title, updatedAt: new Date().toISOString() })
      }
    } catch (e) { log('state save fail', e.message) }

    if (captureConsole && consoleLogs.length) {
      try {
        const f = path.join(shotDir, `console-${Date.now()}.json`)
        fs.writeFileSync(f, JSON.stringify(consoleLogs, null, 2))
        result.consolePath = f
      } catch (e) { log('console save fail', e.message) }
    }
    if (captureNetwork && networkLogs.length) {
      try {
        const f = path.join(shotDir, `network-${Date.now()}.json`)
        fs.writeFileSync(f, JSON.stringify(networkLogs, null, 2))
        result.networkPath = f
      } catch (e) { log('network save fail', e.message) }
    }

    let videoPath = null
    if (videoHandle) { try { videoPath = await videoHandle.path() } catch {} }
    try { await ctx.close() } catch (e) { log('close fail', e.message) }

    if (isRecord) {
      try {
        if (!videoPath || !fs.existsSync(videoPath)) {
          const files = fs.existsSync(videoDir) ? fs.readdirSync(videoDir).filter((f) => f.endsWith('.webm')) : []
          if (files.length) videoPath = path.join(videoDir, files[0])
        }
        if (videoPath && fs.existsSync(videoPath)) {
          result.path = videoPath
          result.mime = 'video/webm'
          try { result.sizeBytes = fs.statSync(videoPath).size } catch {}
        } else if (result.ok) {
          result.ok = false
          result.error = 'rekaman tidak menghasilkan berkas video'
        }
      } catch (e) {
        result.ok = false
        result.error = `gagal mengambil rekaman: ${e?.message || e}`
      }
    }
    result.finishedAt = new Date().toISOString()
  }
  out(result)
}

main().catch((e) => {
  out({ ok: false, error: String(e?.message || e) })
  process.exit(0)
})