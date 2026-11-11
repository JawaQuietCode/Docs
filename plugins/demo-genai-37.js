// plugins/demo-genai-37.js — uji EMPIRIS 37 `__typename` GenAI di klien WhatsApp.
//
// Latar: klien WhatsApp menggagalkan `__typename` tak dikenal DIAM-DIAM, dan
// beberapa nama tidak ada di Baileys/whatsmeow ("external"). Untuk tahu mana
// yang benar-benar dirender, kita harus MELIHATNYA di WhatsApp.
//
// Pelajaran penting (dari percobaan pertama, 2026-10-12):
//   * Mengirim 37 section dalam SATU kartu GAGAL — kartu hanya menampilkan
//     `GenAIBotProgressStatusPrimitive`. Sebabnya: primitif STATUS dengan
//     `is_in_progress: true` (GenAIBotProgress/ThinkingStatus) MEMBAJAK render
//     seluruh kartu (mode "thinking") dan menyembunyikan section lain.
//   * Karena itu: (a) status dikirim NON-aktif, (b) 37 nama dipecah jadi
//     BEBERAPA kartu kecil — bila satu primitif bermasalah, ia hanya merusak
//     kartunya sendiri, bukan semuanya.
//
// Bentuk section mengikuti payload PRODUKSI Meta AI:
//   { view_model: { __typename, primitive|primitives } }
// (produksi TIDAK menaruh `__typename` di level section)
//
// Dipicu: "!demo37" atau plugin_run {name:'demo-genai-37'}.

const IMG = 'https://picsum.photos/seed/kwee37/600/400'
const IMG2 = 'https://picsum.photos/seed/kwee37b/400/300'

// Bungkus primitive ke section ala PRODUKSI (TANPA __typename level-section).
const sec = (layout, primitive) => ({
  view_model: { __typename: `GenAI${layout}LayoutViewModel`, primitive },
})

// Kelompokkan 37 nama. Setiap grup → SATU kartu kecil (±2-4 primitif) supaya
// bila ada primitif yang membajak/gagal, sisanya tetap terlihat di kartu lain.
export function buildGroups() {
  const g = (label, items) => ({ label, items })
  return [
    g('teks & entitas', [
      ['GenAIMarkdownTextUXPrimitive', 'Single', { text: '**GenAIMarkdownTextUXPrimitive** — teks Markdown di dalam kartu.' }],
      ['GenAISearchCitationItem', 'Single', { text: 'Sitasi [](https://id.wikipedia.org/wiki/Generative_AI) di sini.', inline_entities: [{ __typename: 'GenAISearchCitationItem', key: '0', length: 0, offset: 6, metadata: { __typename: 'GenAISearchCitationItem', sources: [{ url: 'https://id.wikipedia.org/wiki/Generative_AI', title: 'Generative AI — Wikipedia' }] } }] }],
      ['GenAIInlineLinkItem', 'Single', { text: 'Tautan [Wikipedia](https://id.wikipedia.org) di sini.', inline_entities: [{ __typename: 'GenAIInlineLinkItem', key: '1', length: 9, offset: 7, metadata: { __typename: 'GenAIInlineLinkItem', display_name: 'Wikipedia', url: 'https://id.wikipedia.org', is_trusted: true } }] }],
      ['GenAIMetadataTextPrimitive', 'Single', { text: 'GenAIMetadataTextPrimitive — catatan kecil.' }],
    ]),
    g('sumber', [
      ['GenAISearchResultPrimitive', 'Single', { sources: [{ url: 'https://id.wikipedia.org/wiki/AI', title: 'AI — Wikipedia' }] }],
      ['GenAISearchResultV2Primitive', 'Single', { sources: [{ url: 'https://id.wikipedia.org/wiki/Kecerdasan_buatan', title: 'Kecerdasan buatan — Wikipedia' }] }],
    ]),
    g('kode', [
      ['GenAICodeUXPrimitive', 'Single', { language: 'javascript', code_blocks: [{ content: '// GenAICodeUXPrimitive (kanonik)\nconst a = 2n + 1', type: 'KEYWORD' }] }],
      ['GenAICodeBlockPrimitive', 'Single', { language: 'javascript', code_blocks: [{ content: '// GenAICodeBlockPrimitive (external?)\nconsole.log(1)', type: 'DEFAULT' }] }],
    ]),
    g('tabel', [
      ['GenATableUXPrimitive', 'Single', { title: 'GenATableUXPrimitive', rows: [{ is_header: true, cells: ['X', 'Y'] }, { is_header: false, cells: ['3', '4'] }] }],
      ['GenAITablePrimitive', 'Single', { title: 'GenAITablePrimitive', rows: [{ is_header: true, cells: ['A', 'B'] }, { is_header: false, cells: ['1', '2'] }] }],
    ]),
    g('rumus', [
      ['GenAILatexUXPrimitive', 'Single', { latex_expression: '\\int_0^1 x^2\\,dx = \\tfrac13', item: { __typename: 'GenAILatexItem', latex_expression: '\\int_0^1 x^2\\,dx = \\tfrac13', font_height: 24, padding: 15 } }],
      ['GenAILatexItem', 'Single', { text: 'Rumus inline: $E = mc^2$ (lihat entitas).' }],
    ]),
    g('media gambar', [
      ['GenAIImagePrimitive', 'Single', { preview_image: { __typename: 'GenAIMediaItem', url: IMG }, full_image: { __typename: 'GenAIMediaItem', url: IMG } }],
      ['GenAIImaginePrimitive', 'Single', { imagine_type: 'IMAGE', status: { status: 'COMPLETED' }, media: { __typename: 'GenAIMediaItem', url: IMG }, update_text: 'GenAIImaginePrimitive' }],
    ]),
    g('audio & video', [
      ['GenAIAudioPrimitive', 'Single', { audio_url: 'https://example.com/a.mp3', title: 'GenAIAudioPrimitive' }],
      ['GenAIVideoPrimitive', 'Single', { reels_url: 'https://example.com/v.mp4', thumbnail_url: IMG, creator: 'kwee', video_delivery_response: { progressive_urls: [{ progressive_url: 'https://example.com/v.mp4' }], dash_manifests: [] } }],
    ]),
    g('peta', [
      ['GenAIMapPrimitive', 'Single', { latitude: -6.2, longitude: 106.8, title: 'GenAIMapPrimitive' }],
    ]),
    g('sosial & commerce', [
      ['GenAIReelPrimitive', 'Single', { reels_url: 'https://example.com/r', thumbnail_url: IMG2, avatar_url: IMG2, creator: '@kwee', reels_title: 'GenAIReelPrimitive' }],
      ['GenAIPostPrimitive', 'Single', { username: '@kwee', post_caption: 'GenAIPostPrimitive', thumbnail_url: IMG2, likes_count: 12, comments_count: 3, source_app: 'INSTAGRAM' }],
      ['GenAIProductItemCardPrimitive', 'Single', { title: 'GenAIProductItemCardPrimitive', brand: 'Kwee', price: 'Rp18.000', sale_price: 'Rp15.000', image_url: IMG2, product_url: 'https://example.com/p' }],
    ]),
    g('tata letak & saran', [
      ['GenAIFollowUpSuggestionPillPrimitive', 'HScroll', { prompt_text: 'GenAIFollowUpSuggestionPillPrimitive', prompt_type: 'SUGGESTED_PROMPT' }],
      ['GenAIIGSuggestedBloomCardLayoutViewModel', 'HScroll', { cards: [{ title: '@kwee', subtitle: 'Ikuti', username: 'kwee' }] }],
      ['GenAIMultipleResponseLayoutViewModel', 'HScroll', [{ __typename: 'GenAIMarkdownTextUXPrimitive', text: 'GenAIMultipleResponse #1' }, { __typename: 'GenAIMarkdownTextUXPrimitive', text: '#2' }]],
      ['GenAICarouselViewModel', 'HScroll', { cards: [{ title: 'GenAICarouselViewModel' }] }],
    ]),
    g('aksi & lain-lain', [
      ['GenAIFooterActionPrimitive', 'Single', { cta_text: 'Buka Wikipedia', cta_type: 'OPEN_URL', cta_url: 'https://id.wikipedia.org', footer_action_type: 'OPEN_URL' }],
      ['GenAIQuickReplyPrimitive', 'Single', { label: 'GenAIQuickReplyPrimitive', reply_text: 'ok', quick_reply_type: 'TEXT' }],
      ['GenAIMetaSubsQuotaUpsellPrimitive', 'Single', { title: 'GenAIMetaSubsQuotaUpsellPrimitive', subtitle: 'Buka fitur lanjutan' }],
      ['GenAIMetaSubsQuotaUpsellButton', 'Single', { label: 'GenAIMetaSubsQuotaUpsellButton', button_text: 'Langganan' }],
    ]),
    g('tugas & HTML', [
      ['GenAITaskPrimitive', 'Single', { task_id: 'demo-37', title: 'GenAITaskPrimitive', subtitle: 'contoh tugas', status: 'COMPLETED' }],
      ['GenAIaeacdsnwHtmlPrimitive', 'Single', { payload: '<h1>GenAIaeacdsnwHtmlPrimitive</h1><p>HTML kartu.</p>', url: 'https://example.com', trusted_sources: ['example.com'] }],
    ]),
    g('pemisah', [
      ['GenAIDividerPrimitive', 'Single', { type: 'HORIZONTAL_LINE' }],
      ['GenAISpacerPrimitive', 'Single', { spacing: 12 }],
    ]),
    // STATUS — SENGAJA SENDIRI & NON-aktif: `is_in_progress: true` MEMBAJAK
    // seluruh kartu (mode "thinking") → jangan dicampur primitif lain.
    g('status thinking (non-aktif)', [
      ['GenAIBotThinkingStatusPrimitive', 'Single', { title: 'GenAIBotThinkingStatusPrimitive', is_in_progress: false }],
    ]),
    g('status progress (non-aktif)', [
      ['GenAIBotProgressStatusPrimitive', 'Single', { title: 'GenAIBotProgressStatusPrimitive', is_in_progress: false }],
    ]),
  ]
}

// Section untuk SATU grup: judul + primitifnya.
export function groupSections(grp) {
  const out = [sec('Single', { __typename: 'GenAIMarkdownTextUXPrimitive', text: `### ${grp.label}` })]
  for (const [name, layout, fields] of grp.items) out.push(sec(layout, { __typename: name, ...fields }))
  return out
}

// Daftar datar semua nama (verifikasi/tes).
export function allNames() {
  return buildGroups().flatMap((grp) => grp.items.map((it) => it[0]))
}

// Semua section sekaligus (dipakai tes; di produksi demo mengirim per-grup).
export function buildSections() {
  const S = []
  for (const grp of buildGroups()) S.push(...groupSections(grp))
  // Layout `GenAIActionRowLayoutViewModel` — wakili sebagai section kecil.
  S.push(sec('ActionRow', { __typename: 'GenAIMetadataTextPrimitive', text: 'ActionRow layout: GenAIActionRowLayoutViewModel' }))
  return S
}

export default {
  name: 'demo-genai-37',
  description: 'Uji 37 __typename GenAI — dipecah jadi kartu kecil agar terlihat mana yang render',
  commands: ['demo37', 'genai37'],

  async run(ctx) {
    const to = ctx.msg.chatId
    const groups = buildGroups()
    const total = groups.reduce((n, g) => n + g.items.length, 0)
    ctx.log(`[demo37] ${groups.length} kartu, ${total} nama (nama asli, tanpa alias)`)
    let ok = 0
    const gagal = []
    for (const grp of groups) {
      try {
        const r = await ctx.wa.sendRich(to, [{ type: 'rawsections', sections: groupSections(grp) }])
        if (r) ok += 1; else gagal.push(grp.label)
      } catch (e) {
        gagal.push(`${grp.label}(${e.message})`)
      }
      await new Promise((res) => setTimeout(res, 1200))  // jeda agar tidak membanjiri
    }
    const pesanGagal = gagal.length ? ` | gagal: ${gagal.join(', ')}` : ''
    return `demo37: ${ok}/${groups.length} kartu terkirim, ${total} nama${pesanGagal}`
  },
}
