import { test } from 'node:test'
import assert from 'node:assert/strict'
import { segmentMarkdown, tableNeedsStacking, tableToStackedMarkdown, polishMarkdown } from '../transport/api/markdown-layout.js'

test('Markdown headings become semantic editorial sections without losing body text', () => {
  const parts = segmentMarkdown('# Executive brief\nLead paragraph.\n\n## Evidence\nTwo sources support this.\n### Limitation\nOne caveat.')
  assert.deepEqual(parts.map((part) => part.type), ['heading', 'text', 'heading', 'text', 'heading', 'text'])
  assert.deepEqual(parts.filter((part) => part.type === 'heading').map((part) => part.level), [1, 2, 3])
  assert.equal(parts.filter((part) => part.type === 'text').map((part) => part.text).join('\n'), 'Lead paragraph.\nTwo sources support this.\nOne caveat.')
})

test('horizontal rules are semantic dividers and empty blocks are discarded', () => {
  const parts = segmentMarkdown('Before\n\n---\n\n## Next\nAfter')
  assert.deepEqual(parts.map((part) => part.type), ['text', 'divider', 'heading', 'text'])
})

test('headings inside blockquotes and indented code are kept as content', () => {
  const parts = segmentMarkdown('> # not a heading\n\n    # also not a heading\n\nReal text')
  assert.equal(parts.filter((part) => part.type === 'heading').length, 0)
  assert.ok(parts.some((part) => part.type === 'text' && part.text.includes('> # not a heading')))
})

test('wide or verbose tables switch to stacked mobile reading layout', () => {
  const rows = [
    ['Model', 'Speed', 'Context', 'Price'],
    ['Kwee A', 'Fast', '128k tokens', '$0.20 per million input tokens'],
  ]
  assert.equal(tableNeedsStacking(rows), true)
  const stacked = tableToStackedMarkdown(rows)
  assert.match(stacked, /\*\*Model\*\*: Kwee A/)
  assert.match(stacked, /\*\*Price\*\*: \$0\.20 per million input tokens/)
  assert.ok(!stacked.includes('|---|'))
})

test('compact tables remain native tables', () => {
  assert.equal(tableNeedsStacking([['Feature', 'Status'], ['Journal', 'Ready']]), false)
})

test('native GFM task lists, strikethrough, nested lists and links are preserved', () => {
  const source = '- [ ] pending\n- [x] complete\n  - nested **bold**\n\n~~removed~~ and [docs](https://example.test)'
  const polished = polishMarkdown(source)
  assert.match(polished, /- \[ \] pending/)
  assert.match(polished, /- \[x\] complete/)
  assert.match(polished, /  - nested \*\*bold\*\*/)
  assert.match(polished, /~~removed~~/)
  assert.match(polished, /\[docs\]\(https:\/\/example\.test\)/)
})
