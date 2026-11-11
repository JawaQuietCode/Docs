import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  segmentMarkdown, polishMarkdown, headingStyle, countSections, codeCaption,
  tableToStackedMarkdown, plainHeading, readingMap,
} from '../transport/api/markdown-layout.js'

// airich.js needs @itsliaaa/baileys (installed by `npm install`); skip the
// integration case gracefully on machines where it is not available.
let buildCardParts = null
try { ({ buildCardParts } = await import('../transport/api/airich.js')) } catch { /* optional */ }


test('callouts are parsed with kind, custom title and body', () => {
  const parts = segmentMarkdown('Intro\n\n> [!WARNING] Data akan terhapus\n> Backup dulu.\n> Lalu lanjut.\n\nSesudah')
  assert.deepEqual(parts.map((p) => p.type), ['text', 'callout', 'text'])
  assert.equal(parts[1].kind, 'WARNING')
  assert.equal(parts[1].title, 'Data akan terhapus')
  assert.equal(parts[1].text, 'Backup dulu.\nLalu lanjut.')
})

test('plain blockquotes and headings inside them stay content', () => {
  const parts = segmentMarkdown('> kutipan biasa\n> # bukan heading')
  assert.deepEqual(parts.map((p) => p.type), ['text'])
})

test('H4-H6 are recognised (no raw #### leaking into the card)', () => {
  const parts = segmentMarkdown('#### Detail kecil\nisi')
  assert.equal(parts[0].type, 'heading')
  assert.equal(parts[0].level, 4)
})

test('polish converts arrows and <br> while preserving native task lists outside code', () => {
  const out = polishMarkdown('a -> b\n- [ ] todo\n- [x] done\nx<br>y\n`a -> b`\n```\nc -> d\n- [ ] raw\n```')
  assert.match(out, /a → b/)
  assert.ok(out.includes('- [ ] todo'))
  assert.ok(out.includes('- [x] done'))
  assert.match(out, /x\ny/)
  assert.match(out, /`a -> b`/)
  assert.match(out, /c -> d\n- \[ \] raw/)
})

test('polish never rewrites urls or horizontal rules', () => {
  const out = polishMarkdown('https://x.id/a->b\n* * *\nok...')
  assert.match(out, /https:\/\/x\.id\/a->b/)
  assert.match(out, /\* \* \*/)
  assert.match(out, /ok…/)
})

test('H2 ordinals appear only for long articles', () => {
  assert.equal(headingStyle(2, { index: 1, total: 2 }).detail, undefined)
  assert.equal(headingStyle(2, { index: 2, total: 4 }).detail, '02 / 04')
  assert.equal(countSections(segmentMarkdown('## A\n## B\n## C')), 3)
})

test('code caption only for multi-line code', () => {
  assert.equal(codeCaption('python', 'x=1'), '')
  assert.equal(codeCaption('python', 'a\nb\nc\nd'), '⌘ python · 4 baris')
})

test('stacked table is a bullet record list', () => {
  const md = tableToStackedMarkdown([['A', 'B'], ['1', '2']])
  assert.equal(md, '- **A**: 1\n- **B**: 2')
})

test('buildCardParts renders callout + sectioned article with labels and spacers', { skip: !buildCardParts && 'baileys belum terpasang' }, () => {
  const md = '# Brief\nLead.\n\n## Satu\nisi\n\n## Dua\nisi\n\n## Tiga\nisi\n\n> [!TIP] Coba ini\n> Pakai cache.'
  const { sections } = buildCardParts([{ type: 'text', text: md }])
  const json = JSON.stringify(sections)
  const labels = (json.match(/GenAILabelPrimitive/g) || []).length
  // H1 + 3×H2 + callout label
  assert.equal(labels, 5)
  assert.match(json, /01 \/ 03/)
  assert.match(json, /GenAISpacerPrimitive/)
  assert.match(json, /Coba ini/)
  assert.match(json, /Pakai cache/)
})

test('plainHeading strips inline markdown', () => {
  assert.equal(plainHeading('**Bold** `code` [link](http://x)'), 'Bold code link')
})

test('reading map: silent for short text, informative for long text, ignores code', () => {
  assert.equal(readingMap('pendek saja', 0), '')
  const long = 'kata '.repeat(400)
  assert.equal(readingMap(long, 4), '◷ 2 mnt baca · 4 bagian')
  assert.equal(readingMap(long, 1), '◷ 2 mnt baca')
  const codeHeavy = 'ringkas\n```js\n' + 'x '.repeat(900) + '\n```'
  assert.equal(readingMap(codeHeavy, 0), '')
})

test('H1 carries the reading map as its subtitle', () => {
  assert.equal(headingStyle(1, { meta: '◷ 2 mnt baca' }).detail, '◷ 2 mnt baca')
  assert.equal(headingStyle(1).detail, undefined)
})
