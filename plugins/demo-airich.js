// plugins/demo-airich.js — demo SEMUA primitif AI Rich (GenAI WhatsApp).
// Dipicu: "!demorich" atau tool plugin_run {name:'demo-airich'}.
export default {
  name: 'demo-airich',
  description: 'Demo semua primitif AI Rich (GenAI WhatsApp) satu per satu',
  commands: ['demorich', 'demoairich'],

  async run(ctx) {
    const to = ctx.msg.chatId
    const img = 'https://picsum.photos/seed/kwee/600/400'
    const img2 = 'https://picsum.photos/seed/kwee2/400/300'
    const hasil = []

    const kirim = async (label, blocks) => {
      try {
        const r = await ctx.wa.sendRich(to, blocks)
        hasil.push(`${label}:${r ? 'ok' : 'kosong'}`)
      } catch (e) {
        ctx.log(`[${label}] gagal:`, e.message)
        hasil.push(`${label}:gagal(${e.message})`)
      }
    }

    // ---- KARTU 1: teks, kode, tabel, sumber, saran, tombol, catatan ----
    await kirim('teks', [
      { type: 'text', text:
        '# Contoh primitif *teks* (Markdown)\n' +
        'Ini **tebal**, *miring*, ~~coret~~, dan `mono`.\n' +
        '- poin satu\n- poin dua\n\n' +
        'Tautan bisa diklik: [Wikipedia AI](https://id.wikipedia.org/wiki/Kecerdasan_buatan)\n' +
        'Sitasi bernomor: klaim ini punya sumber [](https://id.wikipedia.org/wiki/Generative_AI)' },
      { type: 'code', language: 'js',
        code: 'const a = 2n + 1;\n// blok kode + syntax highlight\nconsole.log(`hasil = ${a}`);' },
      { type: 'table', title: 'Perbandingan', rows: [
        ['Istilah', 'Contoh'],
        ['AI', 'spam filter, rekomendasi'],
        ['GenAI', 'ChatGPT, DALL·E'],
        ['RAG', 'chatbot dari dokumen'],
      ] },
      { type: 'divider', style: 'DOT' },
      { type: 'metadata', text: 'Sumber: arsip demo bot (metadata text)' },
      { type: 'sources', sources: [
        { url: 'https://id.wikipedia.org/wiki/Kecerdasan_buatan', title: 'Kecerdasan buatan — Wikipedia' },
        { url: 'https://id.wikipedia.org/wiki/Generative_AI', title: 'Generative AI — Wikipedia' },
      ] },
      { type: 'footer', actions: [
        { label: 'Buka Wikipedia', url: 'https://id.wikipedia.org/wiki/Kecerdasan_buatan' },
      ] },
      { type: 'suggest', prompts: ['Ringkas lagi', 'Beri contoh lain', 'Jelaskan RAG'] },
      { type: 'spacer', height: 12 },
    ])

    // ---- KARTU 2: status, entitas, commerce/sosial ----
    await kirim('status', [
      { type: 'thinking', title: 'Berpikir…', thought_duration_sec: 2 },
      { type: 'progress', title: 'Memproses…', is_in_progress: true },
      { type: 'task', data: {
        task_id: 'demo-1', title: 'Riset pasar', subtitle: 'contoh kartu tugas', status: 'IN_PROGRESS',
        steps: [{ title: 'Kumpulkan data', status: 'DONE' }, { title: 'Analisis', status: 'PENDING' }],
      } },
      { type: 'divider' },
      { type: 'compact', data: {
        title: 'Kwee Bot', subtitle: 'Asisten AI WhatsApp', secondary_subtitle: 'contoh compact entity',
        image: img2, entity_type: 'PAGE', action_type: 'FOLLOW', is_verified: true,
      } },
      { type: 'upsell', data: { title: 'Paket Pro', subtitle: 'Buka fitur lanjutan', button: { label: 'Langganan' } } },
      { type: 'igsuggest', items: [
        { title: '@kwee', subtitle: 'Ikuti', username: 'kwee' },
        { title: '@genai', subtitle: 'Ikuti', username: 'genai' },
      ] },
      { type: 'product', items: [
        { title: 'Kopi Susu', brand: 'Kwee', price: 'Rp18.000', sale_price: 'Rp15.000', image_url: img2, product_url: 'https://example.com/kopi' },
      ] },
      { type: 'post', items: [
        { username: '@kwee', post_caption: 'Contoh kartu post sosial', thumbnail_url: img2, likes_count: 128, comments_count: 12, source_app: 'INSTAGRAM' },
      ] },
      { type: 'reel', items: [
        { username: '@kwee', title: 'Demo reel', thumbnail_url: img2, likes_count: 340, source_app: 'INSTAGRAM' },
      ] },
      { type: 'widget3p', data: { title: 'Widget 3P', actions: [{ label: 'Konfirmasi', kind: 'OTHER', state: 'PENDING' }] } },
      { type: 'foatext', text: 'Teks jalur FOA (FOATextPrimitive).' },
      { type: 'bloks', data: { payload: '{"surface":"contoh","v":1}' } },
    ])

    // ---- KARTU 3: media, rumus, HTML ----
    await kirim('media', [
      { type: 'text', text: '## Contoh primitif *media* & *rumus*' },
      { type: 'image', url: img, width: 600, height: 400 },
      { type: 'fullimage', url: img, preview: img2, width: 600, height: 400, alt: 'gambar penuh' },
      { type: 'latex', expr: '\\int_0^1 x^2\\,dx = \\tfrac13' },
      { type: 'text', text: 'Rumus inline di dalam teks: $E = mc^2$ dan $a^2+b^2=c^2$.' },
      { type: 'divider', style: 'DOT' },
      { type: 'html', data: { html: '<h1>Halo</h1><p>Kartu HTML (WebView Android).</p>', url: 'https://example.com', title: 'Web', trusted_sources: ['example.com'] } },
      { type: 'suggest', prompts: ['Kirim lagi'] },
    ])

    return `demo selesai → ${hasil.join(' | ')}`
  },
}
