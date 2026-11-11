---
name: plugin-kustom
description: Membuat plugin Node baru untuk menambah kemampuan bot
triggers: [plugin, tambah fitur, buat bot, kemampuan baru, ekstensi, command baru, perintah baru, kirim media, send-media, custom]
priority: 50
---

# Membuat plugin sendiri

Kamu bisa **menulis bot kecil** lewat `plugin_write`, jalankan dengan
`plugin_run`, atau picu lewat perintah cepat `!<nama>`.

## Bentuk plugin
```js
export default {
  name: 'cuaca',
  description: 'Info cuaca ringkas',
  commands: ['cuaca'],           // opsional → dipicu "!cuaca Jakarta"
  async run(ctx) {
    await ctx.wa.send(ctx.msg.chatId, { text: 'Cerah, 32°C' })
    // ctx.wa.sendRich(to, blocks) → kartu AI Rich
    // ctx.args, ctx.log(), ctx.state (key-value persisten)
    return 'selesai'
  },
}
```

## Aturan
- **Tulis plugin hanya bila diminta** (plugin = menjalankan kode → butuh izin).
- Jangan pakai `process.exit`, `child_process`, atau menghapus berkas.
- Setelah menulis, **uji** dengan `plugin_run` dan laporkan hasilnya nyata.
- Beri `description` yang jelas supaya bisa ditemukan lewat `plugin_list`.

## Contoh nyata
Plugin `send-media` (mengirim media sebagai media, bukan dokumen) dibuat lewat
alur ini. Kalau ada kebutuhan berulang, pertimbangkan jadikan plugin.
