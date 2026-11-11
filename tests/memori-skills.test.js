import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { daftarSkill, buildPreamble, confirmPreambleIncluded, getPreambleStatus, reloadSkillCache } from '../transport/memori.js'

test('OpenHands skill scanner loads nested Agent Skills SKILL.md packages', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'kwee-skills-'))
  const previous = process.env.KWEE_ROOT
  try {
    process.env.KWEE_ROOT = root
    await fs.mkdir(path.join(root, 'skills', 'internal'), { recursive: true })
    await fs.mkdir(path.join(root, 'skills', 'external', 'pkg', 'references'), { recursive: true })
    await fs.writeFile(path.join(root, 'skills', 'internal', 'core.md'), '---\nname: core\ndescription: always\n---\nCore system rules')
    await fs.writeFile(path.join(root, 'skills', 'external', 'pkg', 'SKILL.md'), '---\nname: nested-package\ndescription: >-\n  anti-slop frontend design\n  and accessible UI quality\ntriggers:\n  - frontend\n  - design\n---\nUse the package skill.')
    await fs.writeFile(path.join(root, 'skills', 'external', 'pkg', 'references', 'a11y.md'), '# A11y')
    const found = daftarSkill()
    assert.ok(found.some((x) => x.nama === 'nested-package'))
    reloadSkillCache()
    const preamble = buildPreamble({}, 'please design frontend html')
    assert.match(preamble, /nested-package/)
    const prepared = getPreambleStatus()
    assert.equal(prepared.status, 'prepared')
    assert.equal(prepared.loaded_count, 0)
    assert.deepEqual(prepared.internal, ['core'])
    assert.deepEqual(prepared.external, ['nested-package'])
    assert.equal(confirmPreambleIncluded(`${preamble}Tugas pengguna`), true)
    const status = getPreambleStatus()
    assert.equal(status.status, 'injected')
    assert.equal(status.loaded_count, 2)
    buildPreamble({}, 'please design frontend html')
    assert.equal(confirmPreambleIncluded('payload without preamble'), false)
    assert.equal(getPreambleStatus().status, 'not_injected')
  } finally {
    if (previous === undefined) delete process.env.KWEE_ROOT
    else process.env.KWEE_ROOT = previous
    await fs.rm(root, { recursive: true, force: true })
  }
})


test('OpenHands skill telemetry is isolated by chat id', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'kwee-skills-chat-'))
  const previous = process.env.KWEE_ROOT
  try {
    process.env.KWEE_ROOT = root
    await fs.mkdir(path.join(root, 'skills', 'internal'), { recursive: true })
    await fs.mkdir(path.join(root, 'skills', 'external', 'pkg'), { recursive: true })
    await fs.writeFile(path.join(root, 'skills', 'internal', 'core.md'), '---\nname: core\ndescription: always\n---\nCore system rules')
    await fs.writeFile(path.join(root, 'skills', 'external', 'pkg', 'SKILL.md'), '---\nname: nested-package\ndescription: frontend design\ntriggers: [frontend, design]\n---\nUse the package skill.')
    reloadSkillCache('chat-alpha')
    reloadSkillCache('chat-beta')
    const alpha = buildPreamble({}, 'frontend', 'chat-alpha')
    const beta = buildPreamble({}, 'unrelated-unique-query', 'chat-beta')
    assert.equal(getPreambleStatus('chat-alpha').status, 'prepared')
    assert.equal(getPreambleStatus('chat-beta').status, 'prepared')
    assert.equal(confirmPreambleIncluded(`${alpha}task`, 'chat-alpha'), true)
    assert.equal(confirmPreambleIncluded(`${beta}task`, 'chat-beta'), true)
    assert.deepEqual(getPreambleStatus('chat-alpha').external, ['nested-package'])
    assert.deepEqual(getPreambleStatus('chat-beta').external, [])
    assert.equal(getPreambleStatus('unknown-chat').status, 'not-yet-loaded')
    assert.equal(confirmPreambleIncluded('no prompt', 'unknown-chat'), false)
    assert.equal(getPreambleStatus('unknown-chat').status, 'not-yet-loaded')
  } finally {
    if (previous === undefined) delete process.env.KWEE_ROOT
    else process.env.KWEE_ROOT = previous
    await fs.rm(root, { recursive: true, force: true })
  }
})
