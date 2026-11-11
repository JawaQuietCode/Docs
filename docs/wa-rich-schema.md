# Skema AI Rich Response WhatsApp — rujukan otoritatif

## DUA sumber, keduanya sebagian — jangan pakai satu saja

| Sumber | Apa itu | Kekuatan | Batas |
|---|---|---|---|
| **`whatsmeow`** `richresponse` (tulir Asokan) | Pustaka resmi WhatsApp Web (Go), *pembaca* | Field & enum PERSIS untuk 25 tipe yang dimodelkannya | Hanya subset — tak memodelkan Table/Code/Label/QuickReply/ActionRow/Video-grid |
| **`plogme`** (`crysnovax/bailey`, npm) | Fork Baileys PRODUKSI, *pengirim* | Memancarkan lebih banyak tipe + field persis (botJid sama: `867051314767696@bot`) | Fork pihak ketiga, bukan vendor resmi |

**Kesimpulan penting:** `whatsmeow` TIDAK lengkap. `GenATableUXPrimitive`,
`GenAICodeUXPrimitive`, `GenAIActionRowLayoutViewModel`, `GenAIVideoPrimitive`,
`GenAIDividerPrimitive` semua NYATA (dipakai plogme) walau whatsmeow tak
memodelkannya. Karena itu **absen di whatsmeow ≠ karangan**.

## Field terverifikasi (dua sumber)

| `__typename` | Field | Sumber bukti |
|---|---|---|
| `GenAIDividerPrimitive` | **`type`** = `DOT`\|`HORIZONTAL_LINE` | whatsmeow + plogme (`{type:'HORIZONTAL_LINE'}`) |
| `GenAISpacerPrimitive` | **`spacing`** (int, opsional) | whatsmeow (`spacing`); plogme memakai `{}` kosong |
| `GenAITableUXPrimitive` | `title`, `rows[{is_header, cells[], markdown_cells[{text}]}]` | plogme:156-166 |
| `GenAICodeUXPrimitive` | `language`, `code_blocks[{content, type}]` | whatsmeow + plogme:92 |
| `GenAIVideoPrimitive` | `reels_url`, `thumbnail_url`, `creator`, `video_delivery_response{progressive_urls[{progressive_url}], dash_manifests[{manifest_xml}]}` | plogme:986 — **TERBUKTI TERPUTAR di klien hidup (2026-10-12)**: `reels_url` diisi URL `mmg` hasil `prepareWAMessageMedia` → video diputar. `reels_` cuma nama; ia BUKAN reels sosial (bedanya: `GenAIReelPrimitive` tak punya `video_delivery_response`) |
| `GenAIActionRowLayoutViewModel` | `{ __typename, primitives:[...] }` | plogme:433,467,544 |
| `GenAIMarkdownTextUXPrimitive` | `text`, `inline_entities[]` | whatsmeow |
| `GenAILatexUXPrimitive` | `latex_expression` (langsung ATAU `item.latex_expression`) | whatsmeow |
| `GenAIBotThinkingStatusPrimitive` | `title`, `is_in_progress` | whatsmeow |
| `GenAIBotProgressStatusPrimitive` | = alias tipe dari ThinkingStatus | whatsmeow:161 |
| `GenAIImagePrimitive` | `full_image{url,url_fallback,width,height,mime_type}`, `preview_image` | whatsmeow + plogme |
| `GenAIImaginePrimitive` | `imagine_type` (IMAGINE/ANIMATE/MEMU), `media`, `status{status,update_text}` | whatsmeow |
| `GenAIProductItemCardPrimitive` | `title` | whatsmeow |
| `GenAIReelPrimitive` | `reels_url`, `thumbnail_url`, `avatar_url`, `creator`, `reels_title` | whatsmeow |
| `GenAIPostPrimitive` | `post_url`, `post_caption`, `username`, `likes_count`, … | whatsmeow |
| `GenAISearchResultPrimitive` | `sources`, `search_engine` | whatsmeow |
| `GenAITaskPrimitive` | `task_id`, `title`, `subtitle`, `status` | whatsmeow |
| `GenAIMetadataTextPrimitive` | `text` | whatsmeow |
| `FOATextPrimitive` | `text` (Markdown) | whatsmeow |
| `FOABloksPrimitive` | `type`, `data`, `uuid` | whatsmeow |

### Enum persis
- **CodeBlockType**: `COMMENT`, `DEFAULT`, `KEYWORD`, `METHOD`, `NUMBER`, `STR`
  — perhatikan **`STR`** (skema) vs `STRING` (enum pengirim `@itsliaaa/baileys`).
  Kita memakai jalur pengirim (`STRING`) yang sudah terbukti jalan di produksi.
- **ImagineType**: `IMAGINE`, `ANIMATE`, `MEMU`.
- **DividerType**: `DOT`, `HORIZONTAL_LINE`.

### Inline entity
`GenAISearchCitationItem`, `GenAIInlineLinkItem`, `GenAILatexItem`,
`GenAIDeepLinkItem`.

## Kategorisasi nama (jujur, per tingkat bukti)

Setelah membandingkan `SUPPORTED_TYPENAMES` kita vs kedua pustaka + bukti
produksi, inilah status sebenarnya. **Penting: absen di kedua pustaka BUKAN
berarti karangan** — keduanya hanya sebagian (whatsmeow 26 nama, plogme 46).

### A. Ada di pustaka resmi (whatsmeow) — 25 nama
Kategori paling kuat. Termasuk `GenAIMarkdownTextUXPrimitive`,
`GenAICodeUXPrimitive`, `GenATableUXPrimitive`, `GenAIDividerPrimitive`,
`GenAISpacerPrimitive`, `GenAIImaginePrimitive`, `GenAIImagePrimitive`,
`GenAISearchResultPrimitive`, `GenAITaskPrimitive`, dll.

### B. Hanya di fork produksi (plogme) — 14 nama
`GenAIActionRowLayoutViewModel`, `GenAIVideoPrimitive`,
`GenAIVideoDeliveryResponse`, `GenAIProgressiveUrlResponse`,
`GenAIDashManifestResponse`, `GenAIFooterActionPrimitive`,
`GenAICompactEntityPrimitive`, `GenAI3PExtWidgetPrimitive`,
`GenAI3PExtWidgetStandardHeader`, `GenAI3PExtCalendarEventList`,
`GenAI3PExtWidgetCTA`, `GenAIaeacdsnwHtmlPrimitive`, `FOAIDNixelButtonSheets`,
`GenAIUnifiedResponseSection`. **Nyata** — dipancarkan fork yang jalan.

> **Catatan `3PExt` vs `Ext`:** `plogme` TIDAK konsisten sendiri — ia memancarkan
> `GenAI3PExtCalendarEventList` (baris 539, 556) DAN `GenAIExtCalendarEventList`
> (baris 611), serta `GenAI3PExtWidgetCTA` (386) dengan `GenAIExtWidgetToast`
> (384). Sumber ketiga independen (gist NIXCODE `MessageBuilderV4.7.js`) memakai
> bentuk **`GenAI3PExt*`** dengan struktur IDENTIK milik kita
> (header `GenAI3PExtWidgetStandardHeader` → body `GenAI3PExtCalendarEventList`
> berisi `sections`+`ctas`). Kita memakai bentuk `3PExt*` — terdukung 2 dari 3
> sumber. Karena klien menggagalkan `__typename` tak dikenal DIAM-DIAM, jalur
> `wa:widget3p` tetap diperlakukan **opt-in/belum terverifikasi di klien hidup**.

### C. Absen di kedua pustaka — TERBUKTI dari produksi nyata — 4 nama
- **`GenAILabelPrimitive`** — terverifikasi **dua kali terhadap klien WhatsApp
  hidup** (screenshot user; lihat `references/label-banner-every-card.md`).
- **`GenAIUnifiedResponseSection`** + **`GenAIUnifiedResponse`** — wajib ada
  menurut contoh payload resmi; tanpa keduanya override warna diabaikan klien.
- **`GenAIMultipleResponseLayoutViewModel`** — dipakai kartu multi-respons kita.

### D. Absen di kedua pustaka — hanya dari daftar spec user — 3 nama
- **`GenAIQuickReplyPrimitive`** — **tidak ada bukti independen.** Satu-satunya
  sumber adalah daftar user. Dikirim hanya bila model memakai `wa:quickreply`
  eksplisit (opt-in), **bukan** bagian alur otomatis. Kalau ternyata klien
  mengabaikannya, efeknya nol (bukan error) — tapi **jangan diklaim terverifikasi**.
- **`GenAIFollowUpSuggestionPillPrimitive`** — bagian dari konstruksi pill saran
  yang sudah jalan lama.
- **`GenAIMetaSubsQuotaUpsellPrimitive`** / **`...UpsellButton`** —
  `wa:upsell` opt-in, jarang dipakai.
- **`GenAI3PExtWidgetToast`**, **`FOAEmbeddedScreenContentTabbed`** — sub-node
  dari widget/layar yang sudah terverifikasi.

**Kesimpulan jujur:** kategori A–C aman (bukti kuat). Kategori D dikirim sebagai
opt-in dan tak diklaim terverifikasi. Yang **TERBUKTI tidak ada** (0 di kedua
sumber + web) tetap di-alias, bukan dipancarkan: `GenAIMapPrimitive`,
`GenAIAudioPrimitive`, `GenAISearchResultV2Primitive`, `GenAICodeBlockPrimitive`,
`GenAITablePrimitive`, `GenAICarouselViewModel`.

Untuk kebutuhan yang sepadan, kirim **pesan WhatsApp asli**: peta =
`locationMessage`, audio = `audioMessage`, video = `GenAIVideoPrimitive`
(grid) atau `GenAIImaginePrimitive` ANIMATE (satu video), sumber =
`GenAISearchResultPrimitive`, tombol = `GenAIFooterActionPrimitive`
(`cta_text`/`cta_type`/`cta_url`) atau `GenAIActionRowLayoutViewModel`.

---

## Kerangka PRODUKSI Meta AI (ditangkap user, 2026-10-12) — OTORITATIF

Payload asli dari bot Meta AI (`botJid 718584497008509@bot`) mengungkap dua hal
yang **tidak** terlihat di skema mana pun:

### 1. Section produksi TIDAK punya `__typename` di level section
```json
{ "view_model": { "__typename": "GenAISingleLayoutViewModel",
                  "primitive": { "__typename": "GenAIMarkdownTextUXPrimitive", "text": "…" } } }
```
Bukan `{ __typename: "GenAIUnifiedResponseSection", view_model: {…} }`. Artinya
`GenAIUnifiedResponseSection` **boleh** ada (plogme memakainya) tapi **tidak
wajib** — klien merender section murni dari `view_model.__typename`. Ini penting:
kalau kita mengirim nama level-section yang salah, section bisa gagal diam-diam.

### 2. Pembaca whatsmeow hanya mengenal **4 layout**
`viewModelTypes` = `GenAISingleLayoutViewModel`, `GenAIGridLayoutViewModel`,
`GenAIHScrollLayoutViewModel`, `GenAIVStackLayoutViewModel`.
Layout lain (`ActionRow`, `MultipleResponse`, `IGSuggestedBloomCard`) **tidak**
ada di pembaca → di klien lama bisa "external". Produksi Meta tetap mengirim
`ActionRow` (terbukti di plogme), jadi pembaca usang ≠ klien menolak.

### 3. Tiga kelas nama (diperbarui dari kerangka produksi)

| Kelas | Jumlah | Arti |
|---|---|---|
| **A. Dikenal whatsmeow** | 21 | pembaca resmi memahaminya |
| **B. Hanya plogme** | 4 | `GenAIVideoPrimitive`, `GenAIActionRowLayoutViewModel`, `GenAIFooterActionPrimitive`, `GenAIaeacdsnwHtmlPrimitive` |
| **C. Tidak di keduanya** | 12 | `GenAISearchResultV2Primitive`, `GenAICodeBlockPrimitive`, `GenAITablePrimitive`, `GenAIQuickReplyPrimitive`, `GenAICarouselViewModel`, `GenAIMapPrimitive`, `GenAIAudioPrimitive`, `GenAIMultipleResponseLayoutViewModel`, `GenAIIGSuggestedBloomCardLayoutViewModel`, `GenAIFollowUpSuggestionPillPrimitive`, `GenAIMetaSubsQuotaUpsellPrimitive`, `GenAIMetaSubsQuotaUpsellButton` |

Kelas C **tidak boleh** dipancarkan apa adanya ke produksi — klien menggagalkan
`__typename` tak dikenal **diam-diam**. Karena itu `airich.js` memetakannya
(`TYPENAME_ALIASES`) ke nama kanonik kelas A/B yang terbukti dirender.

Plugin `demo-genai-37` memancarkan **ke-37 nama asli** (menembus gerbang alias)
sebagai alat ukur empiris: mana yang tampil, mana yang hilang.
