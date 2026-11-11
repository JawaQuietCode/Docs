"""format.py — ubah keluaran LLM menjadi balasan WhatsApp yang BENAR.

Teks, kode, dan tabel dikirim sebagai **kartu AI Rich** (lihat
``transport/api/rich.js``). Dokumen HTML biasa dikirim sebagai lampiran `.html`;
preview inline hanya dipakai bila diminta eksplisit lewat ``wa:web2ui``. Teks di dalam kartu
dirender WhatsApp sebagai **Markdown** (``GenAIMarkdownTextUXPrimitive``), jadi
Markdown **dipertahankan apa adanya**:

    **tebal**   # judul   - daftar   `kode`   [tautan](url)   | tabel |

Ini berbeda dari bubble teks biasa yang TIDAK merender Markdown. Karena itu tidak
ada lagi konversi ke penanda native (``*tebal*``/``~coret~``/``•``).

Modul ini memecah balasan menjadi **blok terstruktur**:

    {"type": "text",  "text": "..."}                        → submessage teks (Markdown)
    {"type": "code",  "language": "python", "code": "..."}  → kartu kode berwarna
    {"type": "table", "title": "...", "rows": [["A","B"]]}  → tabel asli WhatsApp

dan **blok kaya** yang dikirim transport sebagai pesan WhatsApp asli
(``eventMessage``, ``pollCreationMessageV3``, dst), dipicu lewat fence ``wa:*``:

    ```wa:event
    {"name": "...", "startDate": "..."}
    ```

Bahasa yang dikenali: ``wa:event``, ``wa:poll``, ``wa:invite``,
``wa:widget`` (A2UI), ``wa:web2ui`` (preview inline), ``wa:html`` (file HTML).

CATATAN PENTING tentang fence: fence **hanya** dikenali bila berdiri sendiri di
awal baris (lihat ``FENCE_RE``). Tanpa aturan ini, kode sebaris di tengah daftar
(``- `kick`, `add` ``) salah dianggap blok kode dan daftar pecah menjadi banyak
"kartu kode" hampa.
"""
import json
import re

# Fenced code: fence WAJIB di awal baris (bukan sebaris), info tanpa backtick,
# dan penutup berdiri sendiri di barisnya. Pola ```` ```x``` ```` sebaris TIDAK
# cocok, sehingga kode sebaris tidak pernah dianggap blok kode.
FENCE_RE = re.compile(r"^[ \t]*```([^\n`]*)\n(.*?)^[ \t]*```[ \t]*$", re.S | re.M)
# Bahasa fenced yang berarti "blok kaya" (bukan sekadar kode).
RICH_PREFIX = "wa:"
RICH_LANGS = {
    "wa:event",
    "wa:poll",
    "wa:invite",
    "wa:groupinvite",
    "wa:widget",
    "wa:web2ui",
    "wa:rich",
    "wa:html",
    # Primitif kaya tambahan (diadaptasi dari MessageBuilder.js):
    "wa:image",     # gambar (auto unduh + upload ke WA)
    "wa:sources",   # daftar sumber/sitasi (kartu hasil pencarian)
    "wa:suggest",   # pill saran lanjutan (follow-up)
    "wa:footer",    # tombol aksi footer (cta)
    "wa:quickreply",# tombol balasan cepat (GenAIQuickReplyPrimitive)
    "wa:compact",   # kartu ringkas (entity)
    "wa:widget3p",  # widget pihak ketiga (3P ext widget)
    "wa:metadata",  # teks metadata kecil / tip
    "wa:product",   # kartu produk (belanja)
    "wa:post",      # kartu postingan sosial (IG/TikTok)
    "wa:reel",      # reel / video pendek
    "wa:math",      # rumus matematika / LaTeX (dirender jadi gambar)
    "wa:latex",     # rumus LaTeX sebagai blok tersendiri (GenAILatexUXPrimitive)
    "wa:video",     # video animasi (GenAIImaginePrimitive ANIMATE)
    "wa:videogrid", # grid banyak video (GenAIVideoPrimitive + Grid layout)
    "wa:fullimage", # gambar penuh (GenAIImagePrimitive)
    "wa:divider",   # garis pemisah
    "wa:spacer",    # spasi kosong
    "wa:thinking",  # status "berpikir" bot
    "wa:progress",  # status progress bot
    "wa:igsuggest", # kartu saran IG
    "wa:carousel",  # korsel gambar (HScroll, banyak gambar)
    "wa:label",     # banner label berwarna (GenAILabelPrimitive)
    "wa:multirow",  # layout multi-respons
    "wa:upsell",    # kartu upsell langganan Meta
    "wa:task",      # kartu tugas berlangkah
    "wa:foatext",   # teks jalur FOA
    "wa:bloks",     # layar Bloks (A2UI) dari server Meta
    "wa:html",      # dokumen HTML sebagai file unduhan
    "wa:preview",   # pratinjau HTML/CSS/JS (disusun jadi dokumen WebView)
    "wa:location",  # lokasi peta (pesan WhatsApp asli)
    "wa:contact",   # kartu kontak/vCard (pesan WhatsApp asli)
    "wa:audio",     # audio / voice note (pesan WhatsApp asli)
}

# Blok kaya yang diteruskan apa adanya dari parser ke transport (tanpa transformasi).
RICH_PASSTHROUGH = {"image", "sources", "suggest", "footer", "quickreply", "compact", "widget3p",
                    "metadata", "product", "post", "reel", "math", "latex", "video",
                    "videogrid", "fullimage", "divider", "spacer", "thinking", "progress",
                    "igsuggest", "multirow", "upsell", "task", "foatext", "bloks", "html",
                    "html_file", "web2ui", "carousel", "label"}

# Alias nama blok ``wa:*`` yang beredar di spec → kind kanonik. Menerima sinonim
# agar model/penulis spec boleh memakai nama mana pun; semuanya mendarat di
# primitif yang SAMA (dan benar-benar dirender klien).
_KIND_ALIASES = {
    "quick": "quickreply",
    "quickreplies": "quickreply",
    "quick_reply": "quickreply",
    "quick_replies": "quickreply",
    "replybuttons": "quickreply",
    "buttons": "quickreply",
    "suggestion": "suggest",
    "suggestions": "suggest",
    "followup": "suggest",
    "followups": "suggest",
    "cta": "footer",
    "actions": "footer",
    "searchresult": "sources",
    "searchresults": "sources",
    "search": "sources",
    "citation": "sources",
    "citations": "sources",
    "codeblock": "code",
    "code_block": "code",
    "latexblock": "latex",
    "latex_block": "latex",
    "equation": "math",
    "meta": "metadata",
    "productcard": "product",
    "product_card": "product",
    "socialpost": "post",
    "shortvideo": "reel",
    "map": "location",
    "maps": "location",
    "voice": "audio",
    "voicenote": "audio",
    "voice_note": "audio",
    "imagine": "video",
    "animate": "video",
    "webview": "html",
    "web": "html",
}
# Baris tabel Markdown: minimal satu `|`.
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# Baris pemisah tabel: |---|:--:|---|
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
# Baris yang HANYA berisi satu penanda bullet (sisa "- ", "* ", "• ") → buang.
_MARKER_ONLY = re.compile(r"^[\s]*[-*+•][\s]*$")

# --- Tabel ASCII (kotak gambar tangan model) ------------------------------
# Model kadang "menggambar" tabel dengan karakter kotak alih-alih Markdown,
# dan membungkusnya dalam fence ```text. Akibatnya tabel tampil sebagai KARTU
# KODE ("Lihat kode"), bukan tabel asli. Deteksi & ubah jadi tabel native.
_BOX_VERT = "│┃"                       # pemisah kolom vertikal
_BOX_ANY = "│┃╭╮╰╯┌┐└┘├┤┼┬┴━─═╔╗╚╝╠╣╬║"  # semua karakter kotak
# Baris border: hanya karakter kotak/garis, tanpa huruf/angka.
_BORDER_ONLY = re.compile(r"^[\s" + _BOX_ANY + r"+\-=_]+$")
_VERT_SPLIT = re.compile("[" + _BOX_VERT + r"]")

# --- Deteksi otomatis blok kaya (Fitur #22) --------------------------------
# Model TIDAK perlu disuruh menulis fence `wa:*`. Lapisan format memilih
# primitif GenAI* yang tepat dari keluaran Markdown biasa memakai if/if-else:
#   IF fence ```latex/```math berisi RUMUS   -> blok matematika (GenAILatexUXPrimitive)
#   ELSE IF fence ```latex berisi DOKUMEN    -> tetap kode (memang untuk disalin)
#   ELSE IF judul "Sumber"/"Referensi" + daftar tautan -> blok sources
#   ELSE IF judul "Saran"/"Kamu bisa" + daftar         -> blok suggest
#   ELSE                                     -> teks Markdown biasa
# Bahasa fence yang isinya RUMUS, bukan kode program.
_LATEX_LANGS = {"latex", "tex", "math", "katex", "amsmath", "matematika", "rumus"}
# Penanda dokumen LaTeX UTUH (layak disalin) — bukan rumus tunggal.
_LATEX_DOC_RE = re.compile(
    r"\\(?:documentclass|usepackage|begin\s*\{\s*document\s*\}|"
    r"end\s*\{\s*document\s*\}|maketitle|title\s*\{|author\s*\{)"
)
# Perintah LaTeX yang menandakan KODE/skrip (bukan rumus): definisi perintah,
# pengaturan, referensi. Fence ```tex berisi ini HARUS jadi blok kode.
_LATEX_CMD_DEF_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|def|DeclareMathOperator|"
    r"usepackage|documentclass|setlength|setcounter|label|ref|cite|"
    r"bibliography|includegraphics|input|include|title|author|maketitle|date)\b"
)
# Sinyal RUMUS: perintah matematika (\frac, \int, …) atau operator inti
# (^ _ = < >). Prosa biasa ("Berikut rumusnya:") TIDAK punya sinyal ini.
_MATH_SIGNAL_RE = re.compile(r"\\[a-zA-Z]+|[\^_=]|[<>]=?|\\[<>]")
# Judul bagian sumber/saran: HARUS berbentuk heading (bold `**…**`, `# …`, atau
# baris pendek berakhiran `:`) supaya prosa seperti "Kamu bisa coba…" tidak
# salah diubah menjadi pill saran.
_HEAD_BOLD_RE = re.compile(r"^\s*(?:\*\*|\*|__)(.+?)(?:\*\*|\*|__)\s*:?\s*$")
_HEAD_HASH_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_HEAD_COLON_RE = re.compile(r"^\s*([^\n]{2,40}?)\s*:\s*$")
_SRC_WORDS_RE = re.compile(r"\b(?:sumber|referensi|rujukan|sources?|references?)\b", re.I)
_SUG_WORDS_RE = re.compile(
    r"\b(?:saran|suggestions?|mungkin kamu|mungkin anda|coba tanya|"
    r"kamu bisa|anda bisa|kamu dapat|anda dapat|lanjutkan dengan)\b",
    re.I,
)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

# --- Fitur #38: deteksi blok KAYA OTOMATIS dari respons default --------------
# Respons default dulu hanya Markdown (GenAIMarkdownTextUXPrimitive). Lapisan
# ini mengenali POLA yang paling wajar ditulis model lalu mengubahnya menjadi
# primitif GenAI* yang benar-benar dirender klien:
#   judul "Produk:/Belanja:/Katalog:/Harga:" + daftar berharga   -> product
#   judul "Tombol:/Aksi:/CTA:/Tautan:/Link:" + daftar bertautan  -> footer
#   judul "Pilihan:/Opsi:/Balasan:" + daftar tanpa tautan        -> quickreply
#   judul "Catatan:/Tips:/Info:" atau baris "Catatan: …"         -> metadata
# Semua tetap menyatu ke SATU kartu AI Rich (transport/api/airich.js).
_PROD_WORDS_RE = re.compile(
    r"\b(?:produk|produknya|product|belanja|katalog|catalog|"
    r"daftar\s+harga|harga|price|toko|shop|etalase)\b", re.I)
_CTA_WORDS_RE = re.compile(
    r"\b(?:tombol|tombolnya|aksi|actions?|cta|tautan|link|links|"
    r"hubungi|kunjungi)\b", re.I)
_QR_WORDS_RE = re.compile(
    r"\b(?:pilihan|pilih|opsi|balasan|jawaban|quick\s*reply|quickreply|replies)\b", re.I)
_META_WORDS_RE = re.compile(
    r"\b(?:catatan|note|tips|info|informasi|peringatan|perhatian|disclaimer)\b", re.I)
# Baris metadata sebaris: "Catatan: …" / "**Tips:** …" (isi ada di baris sama).
_INLINE_META_RE = re.compile(
    r"^\s*(?:\*\*|__)?\s*"
    r"(?:catatan|note|tips|info|informasi|peringatan|perhatian|disclaimer)"
    r"\s*(?:\*\*|__)?\s*[:：]\s*(.+?)\s*$", re.I)
# Pola harga (Rp/IDR/$/€) — dipakai untuk mengenali item PRODUK.
# Lookbehind `(?<![A-Za-z])` WAJIB: tanpa itu "rp" di dalam kata seperti
# "Sharp 5000 TV" / "Corp 500" cocok sebagai harga palsu (dan judul terpotong).
# Tidak boleh pakai \b — itu akan gagal untuk "$"/"€" yang didahului spasi.
_PRICE_RE = re.compile(r"(?<![A-Za-z])(?:rp\.?|idr|\$|usd|€|£)\s?\d[\d.,]*", re.I)

# --- Deteksi HTML otomatis --------------------------------------------------
# HTML biasa adalah FILE dokumen yang bisa diunduh/dibuka terpisah. Hanya
# ``wa:web2ui`` / ``wa:preview`` yang meminta preview inline di kartu AI.
#   IF fence ```html berisi markup                   -> lampiran html_file
#   ELSE IF fence bahasa apa pun berisi DOKUMEN HTML -> lampiran html_file
#   ELSE IF teks biasa berupa DOKUMEN HTML utuh      -> lampiran html_file
#   ELSE                                             -> seperti semula
# Fragmen HTML eksplisit di fence ```html juga menjadi file HTML mandiri.
_HTML_LANGS = {"html", "htm", "xhtml", "html5"}
# Tag yang menandakan markup HTML yang LAYAK dirender (bukan teks biasa).
_HTML_TAG_RE = re.compile(
    r"</?(?:html|head|body|title|meta|link|style|script|div|p|span|h[1-6]|a|img|"
    r"ul|ol|li|table|thead|tbody|tfoot|tr|td|th|section|article|aside|header|"
    r"footer|nav|main|form|input|button|select|option|textarea|label|svg|canvas|"
    r"iframe|video|audio|source|figure|figcaption|blockquote|pre|code|details|"
    r"summary|template|marquee|center|font)\b[^>]*>",
    re.I,
)
_HTML_DOC_RE = re.compile(r"^\s*(?:<!doctype\s+html|<html\b|<body\b|<head\b)", re.I)
_HTML_TAG_COUNT_RE = re.compile(
    r"</?(?:div|section|article|header|footer|nav|main|aside|style|script|svg|"
    r"canvas|button|form|table|ul|ol|h[1-6]|p|span|a|img)\b[^>]*>",
    re.I,
)


def _is_html_document(text: str) -> bool:
    """True bila teks adalah DOKUMEN HTML (bukan sekadar sebutan tag)."""
    t = str(text or "").strip()
    if not t:
        return False
    low = t.lower()
    # Dokumen eksplisit: <!doctype html> / <html … / <body … / <head ….
    if _HTML_DOC_RE.match(t):
        return True
    # Isyarat kuat: ada <style>/<script>/<body> plus beberapa tag struktural.
    if "<style" in low or "<script" in low or "<body" in low:
        return len(_HTML_TAG_COUNT_RE.findall(t)) >= 2
    # Fragmen kaya (>= 4 tag HTML) tetap dianggap halaman bila semua barisnya
    # memang markup (bukan prosa yang menyebut satu-dua tag).
    if len(_HTML_TAG_COUNT_RE.findall(t)) >= 4 and not re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}\s+[A-Za-z]{3,}", t):
        return True
    return False
_MD_IMAGE_RE = re.compile(r"^\s*!\[([^\]]*)\]\((https?://[^)\s]+)\)\s*$")
_THEMATIC_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_BARE_URL_RE = re.compile(r"https?://[^\s<>()\"']+")
_ITEM_PREFIX_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")

# --- Deteksi otomatis URL media sosial (Fitur #29) -------------------------
# Model cukup menulis URL biasa; lapisan format memilih kartu reel/post IG
# yang tepat memakai if/if-else — tanpa perlu fence `wa:reel`/`wa:post`.
# (kind, source_app, regex)
_SOCIAL_PATTERNS = (
    ("reel", "INSTAGRAM", re.compile(r"https?://(?:www\.)?instagram\.com/reels?/[A-Za-z0-9_-]+", re.I)),
    ("post", "INSTAGRAM", re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|tv)/[A-Za-z0-9_-]+", re.I)),
    ("reel", "TIKTOK", re.compile(r"https?://(?:www\.|m\.)?tiktok\.com/(@[A-Za-z0-9_.]+)/video/\d+", re.I)),
    ("reel", "TIKTOK", re.compile(r"https?://(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]+", re.I)),
    ("reel", "YOUTUBE", re.compile(r"https?://(?:www\.|m\.)?youtube\.com/shorts/[A-Za-z0-9_-]+", re.I)),
)

# --- Normalisasi penanda NATIVE → Markdown (Bug #11) -----------------------
# Kartu AI Rich merender teks sebagai **Markdown** (GenAIMarkdownTextUXPrimitive).
# Model sering meniru riwayat balasannya sendiri dan masih menulis penanda gaya
# WhatsApp lama. Yang paling merusak: mono sebaris ditulis dengan TIGA backtick
# (```` ```files```` ), karena Markdown membacanya sebagai FENCE KODE → muncul
# kartu "Lihat kode" hampa di dalam kartu. Semua penanda native dinormalkan di
# sini supaya tidak ada lagi yang bocor ke perender Markdown.
_INLINE_MONO = re.compile(r"```([^`]*?)```", re.S)
_BACKTICK_RUN = re.compile(r"`{2,}")
_NATIVE_BULLET = re.compile(r"^([ \t]*)•[ \t]+", re.M)
_NATIVE_STRIKE = re.compile(r"(?<![\w~])~([^~\n]+?)~(?!~)")
# CATATAN PENTING: `*x*` TIDAK dinormalkan. Kartu AI Rich merender Markdown, dan
# di Markdown `*x*` = *miring* (italic) yang SAH. Dulu baris ini mengubahnya jadi
# `**x**` (tebal), sehingga miring hilang — itu akar "Markdown kurang lengkap".
# Model sudah diarahkan menulis Markdown (`prompts/rules.md`), jadi penanda
# tunggal dibiarkan apa adanya. Riwayat kanonik juga menyimpan `**tebal**`,
# sehingga model meniru bentuk Markdown yang benar.


def normalize_markdown(text: str) -> str:
    """Ubah penanda native WhatsApp → Markdown agar kartu AI Rich merender benar.

    Urutan penting: mono sebaris dirapikan lebih dulu, baru sisa run backtick
    dipadatkan. Bila tidak, sisa ```` ``` ```` tetap jadi fence Markdown dan
    muncul sebagai kartu kode kosong.
    """
    if not text:
        return ""
    s = str(text)

    # 1) mono sebaris ```x``` → `x` (baris dalamnya dirapatkan jadi satu spasi).
    def _mono(m):
        body = re.sub(r"\s*\n\s*", " ", m.group(1)).strip()
        return f"`{body}`" if body else ""
    s = _INLINE_MONO.sub(_mono, s)

    # 2) sisa run backtick >= 2 → satu. Tanpa ini, sisa ``` jadi fence Markdown.
    s = _BACKTICK_RUN.sub("`", s)

    # 3) bullet native "• " → daftar Markdown "- ".
    s = _NATIVE_BULLET.sub(r"\1- ", s)

    # 4) coret native ~x~ → Markdown ~~x~~.
    s = _NATIVE_STRIKE.sub(r"~~\1~~", s)

    # 5) `*x*` DIBIARKAN — di Markdown itu *miring* yang sah (lihat catatan
    #    _NATIVE_BOLD di atas). Jangan diubah jadi `**x**`.
    return s


def clean_text(text: str) -> str:
    """Rapikan teks untuk kartu AI Rich — **Markdown dipertahankan**.

    Markdown yang sah dibiarkan apa adanya; yang dirapikan hanyalah spasi/newline
    berlebih, baris sisa penanda, dan penanda native yang bocor (lihat
    :func:`normalize_markdown`).
    """
    if not text:
        return ""
    text = normalize_markdown(text)
    out = []
    for line in str(text).split("\n"):
        s = line.rstrip()
        # Sisa bullet kosong (baris hanya berisi "-" / "*" / "•") → buang.
        if _MARKER_ONLY.match(s):
            out.append("")
            continue
        # Sisa satu asterisk penanda di awal baris (mis. "*1." dari tebal native
        # yang tak pernah ditutup) → buang. Bukan bullet ("* ") dan bukan tebal
        # Markdown ("**") karena keduanya tidak lolos syarat di bawah.
        if s.count("*") == 1 and re.match(r"^[ \t]*\*(?=\S)", s):
            s = re.sub(r"^[ \t]*\*", "", s)
        out.append(s)
    res = "\n".join(out)
    res = re.sub(r"\n{3,}", "\n\n", res)
    return res.strip("\n")


# --- Deteksi & konversi tabel ---------------------------------------------

def _is_ascii_table_row(line: str) -> bool:
    """True bila baris ini bagian dari tabel kotak ASCII (│ … │)."""
    s = line.strip()
    if not s or _BORDER_ONLY.match(s):
        return False
    return bool(_VERT_SPLIT.search(s)) and len(_VERT_SPLIT.split(s)) >= 3


def ascii_table_to_rows(block: str) -> list | None:
    """Ubah tabel kotak ASCII jadi baris-baris sel (list of list of str).

    ``None`` bila blok ini ternyata bukan tabel ASCII.

    Contoh masukan (dibungkus fence ```text oleh model)::

        ╭──────────┬──────────╮
        │ Kategori │ Detail   │
        ├──────────┼──────────┤
        │ OS       │ Debian   │
        ╰──────────┴──────────╯
    """
    if not block:
        return None
    data_rows = []
    started = False
    for raw in str(block).split("\n"):
        line = raw.rstrip()
        if not line.strip():
            # Baris kosong sebelum ada data = abaikan; setelah data = akhir tabel.
            if started:
                break
            continue
        if _BORDER_ONLY.match(line.strip()):
            continue  # garis kotak: abaikan
        if not _VERT_SPLIT.search(line):
            # Baris non-tabel: hentikan tabel bila sudah mulai, abaikan bila belum.
            if started:
                break
            return None
        started = True
        # Buang border tepi, pecah di pemisah kolom. PENTING: hanya buang sel
        # kosong di TEPI (dari border `│`), bukan sel kosong di tengah — baris
        # seperti `│ Proses Bot │          │` punya kolom kedua kosong yang sah.
        parts = _VERT_SPLIT.split(line)
        if parts and parts[0].strip() == "":
            parts = parts[1:]
        if parts and parts[-1].strip() == "":
            parts = parts[:-1]
        cells = [c.strip() for c in parts]
        if len(cells) < 2:
            return None
        data_rows.append(cells)
    if len(data_rows) < 2:
        return None  # butuh minimal header + 1 baris
    # Samakan lebar kolom.
    width = max(len(r) for r in data_rows)
    return [r + [""] * (width - len(r)) for r in data_rows]


def fence_to_block(language: str, body: str) -> dict:
    """Ubah isi fence menjadi blok yang tepat.

    Fence berisi tabel sering ditulis model sebagai "```text" — baik tabel
    ASCII kotak (``╭─┬─╮``) maupun tabel pipa Markdown. Kita ubah jadi blok
    ``table`` supaya WhatsApp merendernya sebagai tabel ASLI, bukan kartu kode.
    """
    lang = (language or "").strip().lower()
    # IF fence ```latex/```math berisi RUMUS → blok matematika (GenAILatexUXPrimitive).
    auto = _auto_math_block(lang, body)
    if auto:
        return auto
    # HTML eksplisit / dokumen HTML utuh dikirim sebagai file .html, bukan widget.
    # Preview inline tetap tersedia lewat fence ``wa:web2ui`` atau ``wa:preview``.
    if (lang in _HTML_LANGS and _HTML_TAG_RE.search(body)) or _is_html_document(body):
        return {"type": "html_file", "html": body, "filename": "kwee-output.html", "mimetype": "text/html"}
    # Fence non-teks (js/python/…) tetap kode.
    if lang and lang not in ("text", "txt", "plaintext", "plain", ""):
        return {"type": "code", "language": lang, "code": body}
    rows = ascii_table_to_rows(body)
    if rows:
        header = rows[0]
        data = rows[1:]
        # Kalau baris pertama ternyata juga bukan header (mis. angka), tetap
        # jadikan header — WhatsApp butuh satu baris heading.
        return {"type": "table", "title": "", "rows": [header, *data]}
    # Fence ```text berisi tabel PIPA Markdown → tetap tabel, bukan kode.
    blocks = _text_blocks(body)
    if len(blocks) == 1 and blocks[0].get("type") == "table":
        return blocks[0]
    return {"type": "code", "language": lang or "text", "code": body}


# Nama lama dipertahankan agar pemanggil lain tidak putus. Sekarang TIDAK lagi
# membuang Markdown — hanya merapikan (lihat clean_text).
strip_markdown = clean_text


def _split_row(line: str) -> list:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return [clean_text(c).strip() for c in cells]


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW.match(line))


def _is_table_sep(line: str) -> bool:
    return bool(_TABLE_SEP.match(line))


def _strip_math_delims(s: str) -> str:
    """Buang pembatas rumus (`$$…$$`, `\\[…\\]`, `\\(…\\)`, `$…$`) bila ada."""
    s = s.strip()
    for a, b in (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            return s[len(a):-len(b)].strip()
    return s


def _auto_math_block(language: str, body: str):
    """IF fence ```latex/```math berisi RUMUS → blok matematika.

    ELSE (berisi dokumen LaTeX utuh, mis. ``\\documentclass``, atau perintah
    definisi seperti ``\\newcommand``) → None, supaya tetap jadi blok KODE yang
    bisa disalin (memang itu maksudnya).

    Baris yang TIDAK punya sinyal rumus (prosa seperti "Berikut rumusnya:")
    dibuang, bukan diubah jadi rumus. Kalau tak ada satu pun baris berumus,
    kembalikan None → fence tetap jadi blok kode.
    """
    if language not in _LATEX_LANGS:
        return None
    text = body or ""
    # Dokumen LaTeX utuh ATAU perintah definisi/pengaturan → ini KODE, bukan rumus.
    if _LATEX_DOC_RE.search(text) or _LATEX_CMD_DEF_RE.search(text):
        return None
    lines = [ln for ln in text.split("\n") if _strip_math_delims(ln)]
    if not lines:
        return None
    # HANYA bila SEMUA baris non-kosong punya sinyal rumus. Kalau campuran
    # (prosa + rumus), kembalikan None → tetap blok kode, supaya prosa TIDAK
    # hilang dan rumus tak dirender dari teks yang bukan rumus.
    if not all(_MATH_SIGNAL_RE.search(_strip_math_delims(ln)) for ln in lines):
        return None
    items = [{"expr": _strip_math_delims(ln), "display": True} for ln in lines]
    return {"type": "math", "items": items}


def _heading_kind(line: str):
    """Kembalikan kind bagian bila baris adalah JUDUL bagian, else None.

    Kind yang dikenali (urut prioritas): ``sources``, ``suggest``, ``product``,
    ``footer``, ``quickreply``, ``metadata``. Urutan ini penting: judul seperti
    "Sumber:" harus menang atas "Tautan:", dan "Catatan:" tidak boleh dicuri
    oleh pola lain.
    """
    explicit = _HEAD_BOLD_RE.match(line) or _HEAD_HASH_RE.match(line)
    title = explicit.group(1) if explicit else None
    if title is None:
        # Judul gaya "Sumber:" hanya sah bila SANGAT pendek (≤3 kata); prosa
        # seperti "Kamu bisa coba langkah berikut:" harus tetap teks biasa.
        mc = _HEAD_COLON_RE.match(line)
        if mc and len(mc.group(1).split()) <= 3:
            title = mc.group(1)
    if not title:
        return None
    title = title.strip().strip("*_ ").strip()
    if not title or len(title) > 40:
        return None
    if _SRC_WORDS_RE.search(title):
        return "sources"
    if _SUG_WORDS_RE.search(title):
        return "suggest"
    if _PROD_WORDS_RE.search(title):
        return "product"
    if _CTA_WORDS_RE.search(title):
        return "footer"
    if _QR_WORDS_RE.search(title):
        return "quickreply"
    if _META_WORDS_RE.search(title):
        return "metadata"
    return None


def _items_and_urls(body: str) -> list:
    """Ambil [(teks, url, apakah_baris_daftar)] dari bullet/bernomor/tautan."""
    out = []
    for line in (body or "").split("\n"):
        s = line.strip()
        if not s:
            continue
        is_item = bool(_ITEM_PREFIX_RE.match(line))
        s = _ITEM_PREFIX_RE.sub("", s)
        m = _MD_LINK_RE.search(s)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip(), True))
            continue
        u = _BARE_URL_RE.search(s)
        if u:
            out.append((s[:u.start()].strip(" -–—:"), u.group(0), is_item))
            continue
        out.append((s, "", is_item))
    return out


def _social_line(line: str):
    """IF baris = SATU URL media sosial → blok reel/post IG (if/if-else).

    Model cukup menulis URL biasa; lapisan ini memilih kartu yang tepat:
    IG reel / TikTok / YT Shorts → ``reel`` (GenAIReelPrimitive),
    IG post/p/IGTV → ``post`` (GenAIPostPrimitive). URL yang menyatu dalam
    kalimat TIDAK dipindah (biar jadi inline link) — hanya baris URL tunggal.
    """
    s = (line or "").strip()
    if not s or any(ch.isspace() for ch in s):
        return None
    for kind, app, rx in _SOCIAL_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        # Sisa setelah URL hanya boleh tanda baca URL (slash/query/fragmen).
        rest = s[m.end():]
        if rest and rest[0] not in "/?#":
            continue
        url = s.rstrip("/")
        item = {"post_url": url, "url": url, "source_app": app}
        if kind == "reel":
            item["video_url"] = url
        mu = re.search(r"tiktok\.com/(@[A-Za-z0-9_.]+)/", url, re.I)
        if mu:
            item["username"] = mu.group(1)
        return {"type": kind, "items": [item]}
    return None


def _image_at(lines: list, j: int):
    """Kembalikan match gambar Markdown pada baris ke-j, atau None."""
    if 0 <= j < len(lines):
        m = _MD_IMAGE_RE.match(lines[j])
        if m and m.group(2):
            return m
    return None


def _is_body_item(line: str) -> bool:
    """True bila baris adalah ITEM daftar/tautan (bukan prosa).

    Dipakai untuk menentukan batas badan bagian: baris kosong diikuti PROSA
    mengakhiri bagian, sedangkan baris kosong diikuti item daftar lanjut.
    Definisi sengaja sempit (bullet/bernomor atau baris yang SELURUHNYA satu
    tautan) supaya prosa seperti "Silakan buka https://…" tidak dianggap item.
    """
    s = str(line or "").strip()
    if not s:
        return False
    if _ITEM_PREFIX_RE.match(line):
        return True
    return bool(_BARE_URL_RE.fullmatch(s))


def _section_block(kind: str, pairs: list, body_lines: list):
    """Bangun blok kaya dari satu judul bagian + badannya. None bila tak layak.

    ``pairs`` = keluaran :func:`_items_and_urls` — ``(teks, url, apakah_item)``.
    Hanya mengembalikan blok bila isinya benar-benar cocok, supaya prosa biasa
    tidak berubah bentuk (mis. "Produk:" tanpa harga/tautan tetap teks).
    """
    if kind == "sources":
        srcs = [{"title": t or u, "url": u} for t, u, _ in pairs if u]
        return {"type": "sources", "sources": srcs} if srcs else None
    if kind == "suggest":
        prompts = [t for t, _u, it in pairs if t and it]
        return {"type": "suggest", "prompts": prompts[:6], "scroll": True} if prompts else None
    if kind == "product":
        # Tiap baris: "Judul - Rp85.000 - https://…" → {title, price, product_url}.
        # Harga diambil dari pola Rp/IDR/$; sisa teks jadi judul. HANYA baris
        # DAFTAR (bullet/bernomor/tautan) yang dihitung — prosa seperti
        # "Produk kami berkualitas…" TIDAK boleh jadi kartu produk hampa.
        items = []
        for t, u, it in pairs:
            # Baris nyata = bullet/bernomor (it) ATAU baris yang isinya HANYA
            # tautan (t kosong). Prosa bertautan ("lihat di https://…") bukan item.
            if not (it or (u and not t)):
                continue
            title = (t or "").strip()
            price = ""
            m = _PRICE_RE.search(title)
            if m:
                price = m.group(0).strip()
                title = (title[:m.start()] + " " + title[m.end():]).strip(" -–—:|·\t")
            title = re.sub(r"\s{2,}", " ", title).strip(" -–—:|·")
            if not title and not u:
                continue
            it = {"title": title or u}
            if price:
                it["price"] = price
            if u:
                it["product_url"] = u
            items.append(it)
        # Kartu produk hanya bila ADA sinyal harga atau tautan (bukan daftar hampa).
        if items and any(i.get("price") or i.get("product_url") for i in items):
            return {"type": "product", "items": items}
        return None
    if kind == "footer":
        # Tombol aksi (GenAIFooterActionPrimitive) — WAJIB punya URL, dan baris
        # harus baris daftar nyata (bullet/bernomor) atau tautan telanjang.
        acts = [{"text": (t or u).strip() or u, "url": u, "type": "OPEN_URL"}
                for t, u, it in pairs if u and (it or not t)]
        return {"type": "footer", "actions": acts} if acts else None
    if kind == "quickreply":
        # Tombol balasan cepat (GenAIQuickReplyPrimitive) — label DAFTAR tanpa
        # tautan. Prosa biasa tidak dihitung (hanya baris bullet/bernomor).
        labels = [t for t, u, it in pairs if t and not u and it]
        return {"type": "quickreply", "items": labels[:6]} if labels else None
    if kind == "metadata":
        body = clean_text("\n".join(body_lines)).strip()
        return {"type": "metadata", "text": body, "tip": True} if body else None
    return None


def _leftover_body_lines(kind: str, body_lines: list) -> list:
    """Baris badan bagian yang TIDAK terwakili blok kaya (agar tak hilang).

    Logika pencocokan HARUS sama persis dengan :func:`_section_block` supaya
    baris yang dipakai blok tidak muncul dua kali. Mengembalikan daftar baris
    (asli) yang perlu dikembalikan sebagai teks.
    """
    def is_item(line):
        s = str(line or "").strip()
        if not s:
            return False
        return bool(_ITEM_PREFIX_RE.match(line)) or bool(_BARE_URL_RE.fullmatch(s))

    used = []
    for ln in body_lines:
        s = str(ln or "").strip()
        if not s:
            continue
        if kind == "sources":
            has_url = bool(_MD_LINK_RE.search(s)) or bool(_BARE_URL_RE.search(s))
            used.append(has_url)
        elif kind == "suggest":
            used.append(is_item(s))
        elif kind == "product":
            if not is_item(s):
                used.append(False)
            else:
                title = _ITEM_PREFIX_RE.sub("", s)
                m = _MD_LINK_RE.search(title)
                if m:
                    title = title[:m.start()]
                else:
                    um = _BARE_URL_RE.search(title)
                    if um:
                        title = title[:um.start()]
                mm = _PRICE_RE.search(title)
                has_price = bool(mm)
                if mm:
                    title = (title[:mm.start()] + " " + title[mm.end():])
                title = re.sub(r"\s{2,}", " ", title).strip(" -–—:|·")
                has_url = bool(_MD_LINK_RE.search(s)) or bool(_BARE_URL_RE.search(s))
                used.append(bool((title or has_url) and (has_price or has_url)))
        elif kind == "footer":
            used.append(bool((_MD_LINK_RE.search(s) or _BARE_URL_RE.search(s)) and is_item(s)))
        elif kind == "quickreply":
            used.append(bool(is_item(s) and not (_MD_LINK_RE.search(s) or _BARE_URL_RE.search(s))))
        else:
            used.append(True)
    return [ln for ln, u in zip(body_lines, used) if not u]


def _auto_sections_from_text(text: str) -> list:
    """Pecah teks Markdown menjadi blok; deteksi bagian kaya otomatis.

    Model TIDAK perlu disuruh menulis fence ``wa:*``. Lapisan ini memakai logika
    if/if-else atas judul bagian + isinya: judul **Sumber/Saran/Produk/Harga/
    Tombol/CTA/Pilihan/Catatan** berubah menjadi primitif GenAI* yang benar-
    benar dirender klien (sources/suggest/product/footer/quickreply/metadata).
    """
    blocks, buf = [], []
    lines = str(text).split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # IF baris = gambar Markdown → blok image (GenAIImaginePrimitive).
        # ELSE IF >=2 gambar berurutan (hanya dipisah baris kosong) → SATU
        #   carousel (HScroll) supaya tidak jadi banyak kartu terpisah.
        mi = _MD_IMAGE_RE.match(line)
        if mi and mi.group(2):
            j = i + 1
            shots = [{"url": mi.group(2), "caption": mi.group(1)}]
            while True:
                k = j
                while k < len(lines) and lines[k].strip() == "":
                    k += 1
                nxt = _image_at(lines, k)
                if not nxt:
                    break
                shots.append({"url": nxt.group(2), "caption": nxt.group(1)})
                j = k + 1
            if buf:
                t = clean_text("\n".join(buf))
                if t.strip():
                    blocks.extend(_auto_sections_from_text(t))
                buf = []
            if len(shots) >= 2:
                blocks.append({"type": "carousel", "items": shots})
            else:
                blocks.append({"type": "image", **shots[0]})
            i = j
            continue
        # ELSE IF baris = SATU URL media sosial → kartu reel/post IG otomatis.
        social = _social_line(line)
        if social:
            if buf:
                t = clean_text("\n".join(buf))
                if t.strip():
                    blocks.extend(_auto_sections_from_text(t))
                buf = []
            blocks.append(social)
            i += 1
            continue
        # ELSE IF baris = garis pemisah (`---`/`***`) → divider (GenAIDividerPrimitive).
        if _THEMATIC_RE.match(line):
            if buf:
                t = clean_text("\n".join(buf))
                if t.strip():
                    blocks.extend(_auto_sections_from_text(t))
                buf = []
            blocks.append({"type": "divider", "style": "HORIZONTAL_LINE"})
            i += 1
            continue
        # ELSE IF baris = metadata sebaris ("Catatan: …" / "**Tips:** …") →
        #   blok metadata (GenAIMetadataTextPrimitive) tanpa memutus paragraf.
        mm = _INLINE_META_RE.match(line)
        if mm and mm.group(1).strip():
            if buf:
                t = clean_text("\n".join(buf))
                if t.strip():
                    blocks.extend(_auto_sections_from_text(t))
                buf = []
            blocks.append({"type": "metadata", "text": mm.group(1).strip(), "tip": True})
            i += 1
            continue
        kind = _heading_kind(line)
        if not kind:
            buf.append(line)
            i += 1
            continue
        # Kumpulkan badan bagian. BERHENTI di: judul bagian lain, metadata
        # sebaris ("Catatan: …"), baris kosong GANDA, atau SATU baris kosong
        # yang diikuti PROSA. Tanpa aturan terakhir, prosa penutup ("Terima
        # kasih!") tertelan ke badan bagian lalu HILANG (mis. jadi item produk
        # yang dibuang). Satu baris kosong yang diikuti ITEM daftar lanjut.
        body_lines, j, seen_blank = [], i + 1, False
        while j < len(lines):
            nxt = lines[j]
            if _heading_kind(nxt) or _INLINE_META_RE.match(nxt):
                break
            if nxt.strip() == "":
                if seen_blank:
                    break
                if j + 1 < len(lines) and not _is_body_item(lines[j + 1]):
                    break
                seen_blank = True
                body_lines.append(nxt)
                j += 1
                continue
            body_lines.append(nxt)
            j += 1
        pairs = _items_and_urls("\n".join(body_lines))
        blk = _section_block(kind, pairs, body_lines)
        if blk:
            if buf:
                t = clean_text("\n".join(buf))
                if t.strip():
                    blocks.append({"type": "text", "text": t})
                buf = []
            blocks.append(blk)
            # Sisa baris badan yang TIDAK terwakili blok (mis. prosa di antara
            # item daftar) TIDAK boleh hilang — masukkan ke buffer teks.
            leftover = _leftover_body_lines(kind, body_lines)
            if leftover:
                buf.extend(leftover)
                buf.append("")
            i = j
            continue
        # Tidak memenuhi syarat → biarkan sebagai teks biasa.
        buf.append(line)
        i += 1
    if buf:
        t = clean_text("\n".join(buf))
        if t.strip():
            blocks.append({"type": "text", "text": t})
    return blocks or [{"type": "text", "text": clean_text(text)}]


# Awal dokumen HTML yang menyusul SETELAH prosa pembuka. Hanya tag level-blok
# (bukan sebaris seperti <b>/<i>) agar prosa yang menyebut satu tag tidak pecah.
_HTML_START_RE = re.compile(
    r"^[ \t]*<\s*(?:!doctype\s+html|html\b|body\b|head\b|style\b|script\b|div\b|"
    r"section\b|main\b|article\b|header\b|footer\b|nav\b|svg\b|canvas\b|table\b|"
    r"form\b|template\b)",
    re.I | re.M,
)


def _split_embedded_html(chunk: str):
    """Pisahkan prosa pembuka dari DOKUMEN HTML yang menyusul.

    Model sering menulis "Berikut mesin slotnya:\\n\\n<!DOCTYPE html>…" — HTML
    yang didahului prosa. Tanpa pemisahan ini seluruh potongan jatuh ke blok
    teks dan HTML bocor sebagai teks bertag (bukan WebView GenAI).

    Mengembalikan ``(prose, html)``; ``html`` None bila tak ada dokumen HTML.
    """
    s = str(chunk or "")
    m = _HTML_START_RE.search(s)
    if not m:
        return s, None
    tail = s[m.start():]
    if not _is_html_document(tail):
        return s, None
    return s[:m.start()], tail.strip()


def _text_blocks(chunk: str) -> list:
    """Pecah potongan teks biasa menjadi blok text + table."""
    # Dokumen HTML utuh (utuh, atau setelah prosa pembuka) menjadi file .html.
    # Fragmen sebaris & prosa yang menyebut satu tag tidak terkena deteksi ini.
    prose, embedded = _split_embedded_html(chunk)
    if embedded:
        out = []
        if prose.strip():
            out.extend(_text_blocks(prose))
        out.append({"type": "html_file", "html": embedded, "filename": "kwee-output.html", "mimetype": "text/html"})
        return out
    lines = str(chunk).split("\n")
    out, buf, i = [], [], 0
    while i < len(lines):
        # awal tabel: baris `| ... |` diikuti baris pemisah `|---|`
        if _is_table_row(lines[i]) and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            rows, j = [], i
            while j < len(lines) and _is_table_row(lines[j]):
                if not _is_table_sep(lines[j]):
                    rows.append(_split_row(lines[j]))
                j += 1
            if buf:
                t = clean_text("\n".join(buf))
                if t.strip():
                    out.extend(_auto_sections_from_text(t))
                buf = []
            if rows:
                out.append({"type": "table", "title": "", "rows": rows})
            i = j
            continue
        # awal tabel ASCII kotak (│ … │) tanpa fence.
        if _is_ascii_table_row(lines[i]):
            block, j = [], i
            while j < len(lines) and (_is_ascii_table_row(lines[j]) or _BORDER_ONLY.match(lines[j].strip())):
                block.append(lines[j])
                j += 1
            rows = ascii_table_to_rows("\n".join(block))
            if rows:
                if buf:
                    t = clean_text("\n".join(buf))
                    if t.strip():
                        out.extend(_auto_sections_from_text(t))
                    buf = []
                out.append({"type": "table", "title": "", "rows": rows})
                i = j
                continue
        buf.append(lines[i])
        i += 1
    if buf:
        t = clean_text("\n".join(buf))
        if t.strip():
            out.extend(_auto_sections_from_text(t))
    return out


def _rich_from_raw(kind: str, body: str):
    """Ubah blok kaya ``wa:*`` menjadi balasan wire. None bila JSON tidak sah."""
    k = (kind or "").strip().lower()
    # Buang prefiks `wa:` supaya pencocokan `endswith` dan tabel alias konsisten.
    if k.startswith(RICH_PREFIX):
        k = k[len(RICH_PREFIX):]
    # Terima alias/varian nama yang beredar di spec → kind kanonik.
    k = _KIND_ALIASES.get(k, k)
    raw = (body or "").strip()
    try:
        data = json.loads(raw)
    except Exception:
        # Bukan JSON: HTML biasa menjadi file; preview inline harus eksplisit.
        if k.endswith("html"):
            return {"type": "html_file", "html": raw, "filename": "kwee-output.html", "mimetype": "text/html"}
        if k.endswith("web2ui") or k.endswith("preview"):
            return {"type": "web2ui", "html": raw, "title": "Preview", "sources": []}
        return None
    if k.endswith("event"):
        return {"type": "event", "event": data}
    if k.endswith("poll"):
        return {"type": "poll", "poll": data}
    if k.endswith("invite"):
        return {"type": "groupInvite", "groupInvite": data}
    if k.endswith("widget"):
        return {"type": "widget", "widget": data}
    if k.endswith("location"):
        # Lokasi peta — pesan WhatsApp asli. {"lat":…,"lng":…,"name":…}
        if not isinstance(data, dict):
            return None
        return {"type": "location", "location": {
            "degreesLatitude": data.get("lat", data.get("latitude", data.get("degreesLatitude"))),
            "degreesLongitude": data.get("lng", data.get("lon", data.get("longitude", data.get("degreesLongitude")))),
            "name": data.get("name", ""),
            "address": data.get("address", ""),
        }}
    if k.endswith("contact"):
        # Kartu kontak (vCard) — pesan WhatsApp asli.
        if not isinstance(data, dict):
            return None
        name = data.get("name") or data.get("displayName") or ""
        phone = str(data.get("phone") or data.get("number") or "").lstrip("+")
        if isinstance(data.get("contacts"), list) and data["contacts"]:
            contacts = data["contacts"]
        elif phone:
            vcard = ("BEGIN:VCARD\nVERSION:3.0\n"
                     f"FN:{name}\n"
                     f"TEL;type=CELL;type=VOICE;waid={phone}:+{phone}\n"
                     "END:VCARD")
            contacts = [{"vcard": vcard}]
        else:
            return None
        return {"type": "contacts", "contacts": {
            "displayName": name or "Kontak",
            "contacts": contacts,
        }}
    if k.endswith("audio"):
        # Audio / voice note — pesan WhatsApp asli (media diunggah dulu).
        if isinstance(data, str):
            return {"type": "audio", "url": data, "ptt": False}
        if not isinstance(data, dict) or not (data.get("url") or data.get("media")):
            return None
        return {"type": "audio", "url": data.get("url") or data.get("media"),
                "ptt": bool(data.get("ptt", data.get("voice", False)))}
    if k.endswith("fullimage"):
        # HARUS dicek SEBELUM `image` (karena "fullimage" juga berakhiran "image").
        if isinstance(data, dict) and (data.get("url") or data.get("media")):
            return {"type": "fullimage", "url": data.get("url") or data.get("media"),
                    "preview": data.get("preview") or data.get("thumbnail"),
                    "width": data.get("width"), "height": data.get("height"),
                    "alt": data.get("alt", "")}
        return None
    if k.endswith("image"):
        # {"url": "...", "width": 300, "height": 300}
        if isinstance(data, str):
            return {"type": "image", "url": data}
        return {
            "type": "image",
            "url": data.get("url") or data.get("media") or "",
            "width": data.get("width"),
            "height": data.get("height"),
            "status": data.get("status", "READY"),
            "update_text": data.get("update_text"),
        }
    if k.endswith("sources"):
        src = data.get("sources") if isinstance(data, dict) else data
        if isinstance(src, list):
            norm = []
            for s in src:
                if isinstance(s, str):
                    norm.append({"url": s})
                elif isinstance(s, dict):
                    norm.append({
                        "title": s.get("title", ""), "url": s.get("url", ""),
                        "subtitle": s.get("subtitle", ""), "icon": s.get("icon", s.get("favicon", "")),
                        "site": s.get("site", s.get("domain", "")),
                    })
            if norm:
                return {"type": "sources", "sources": norm}
        return None
    if k.endswith("igsuggest"):
        # HARUS dicek SEBELUM `suggest` ("igsuggest" juga berakhiran "suggest").
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            return {"type": "igsuggest", "items": items}
        return None
    if k.endswith("suggest"):
        p = data.get("prompts") if isinstance(data, dict) else data
        if isinstance(p, str):
            p = [p]
        if isinstance(p, list) and p:
            return {"type": "suggest", "prompts": [str(x) for x in p], "scroll": True}
        return None
    if k.endswith("footer"):
        acts = data.get("actions") if isinstance(data, dict) else data
        if isinstance(acts, list) and acts:
            return {"type": "footer", "actions": acts}
        return None
    if k.endswith("quickreply"):
        # Tombol balasan cepat (GenAIQuickReplyPrimitive). Terima daftar string,
        # {"items":[{label,reply_text}]}, atau {"replies":[...]}.
        items = data.get("items") if isinstance(data, dict) else data
        if items is None and isinstance(data, dict):
            items = data.get("replies") or data.get("buttons")
        if isinstance(items, (str, int, float)):
            items = [items]
        if isinstance(items, list):
            # Normalisasi ke bentuk yang pasti valid: hanya string / dict
            # yang punya label. Item sampah dibuang, bukan diteruskan (agar
            # tak hilang diam-diam di sisi Node).
            norm = []
            for it in items:
                if isinstance(it, str):
                    if it.strip():
                        norm.append(it.strip())
                elif isinstance(it, dict):
                    label = str(it.get("label") or it.get("text") or it.get("title") or "").strip()
                    if label:
                        norm.append(it)
            if norm:
                return {"type": "quickreply", "items": norm}
        return None
    if k.endswith("codeblock") or k == "code":
        # Blok kode eksplisit → kartu kode (GenAICodeUXPrimitive).
        if isinstance(data, str):
            return {"type": "code", "language": "text", "code": data} if data.strip() else None
        if isinstance(data, dict):
            code = data.get("code") or data.get("content") or ""
            if str(code).strip():
                return {"type": "code",
                        "language": data.get("language") or data.get("lang") or "text",
                        "code": str(code)}
        return None
    if k.endswith("tableprim") or k == "table":
        # Tabel eksplisit → kartu tabel (GenATableUXPrimitive).
        rows = data.get("rows") if isinstance(data, dict) else data
        if isinstance(rows, list) and rows:
            norm = [[str(c) for c in (r.get("items") or r.get("cells") or [])]
                    if isinstance(r, dict) else [str(c) for c in (r if isinstance(r, list) else [r])]
                    for r in rows]
            norm = [r for r in norm if r]
            if norm:
                return {"type": "table",
                        "title": (data.get("title", "") if isinstance(data, dict) else ""),
                        "rows": norm}
        return None
    if k.endswith("compact"):
        if isinstance(data, dict):
            return {"type": "compact", "data": data}
        return None
    if k.endswith("widget3p"):
        return {"type": "widget3p", "data": data}
    if k.endswith("metadata"):
        text = data.get("text") if isinstance(data, dict) else str(data)
        if text and str(text).strip():
            return {"type": "metadata", "text": str(text), "tip": (data.get("tip", True) if isinstance(data, dict) else True)}
        return None
    if k.endswith("product"):
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            return {"type": "product", "items": items}
        return None
    if k.endswith("post"):
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            return {"type": "post", "items": items}
        return None
    if k.endswith("reel"):
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            return {"type": "reel", "items": items}
        return None
    if k.endswith("carousel"):
        # Korsel gambar (HScroll). Terima {"items":[{url,caption}]}, {"urls":[...]},
        # atau daftar string URL langsung.
        items = None
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("items") or data.get("urls") or data.get("images")
        if not isinstance(items, list):
            return None
        norm = []
        for it in items:
            if isinstance(it, str):
                if it.strip():
                    norm.append({"url": it})
            elif isinstance(it, dict) and (it.get("url") or it.get("media")):
                norm.append({"url": it.get("url") or it.get("media"),
                             "caption": it.get("caption", "")})
        if norm:
            return {"type": "carousel", "items": norm}
        return None
    if k.endswith("math"):
        # Bisa: {"expr": "..."} atau {"items": [{"expr": "...", "display": true}]}
        # atau string LaTeX langsung.
        if isinstance(data, str):
            if data.strip():
                return {"type": "math", "items": [{"expr": data, "display": True}]}
            return None
        if isinstance(data, dict):
            if isinstance(data.get("items"), list) and data["items"]:
                return {"type": "math", "items": data["items"]}
            expr = data.get("expr") or data.get("latex") or data.get("formula") or ""
            if str(expr).strip():
                return {"type": "math",
                        "items": [{"expr": str(expr), "display": bool(data.get("display", True))}]}
        return None
    if k.endswith("latex"):
        # Rumus LaTeX sebagai blok tersendiri (GenAILatexUXPrimitive).
        if isinstance(data, str):
            return {"type": "latex", "expr": data} if data.strip() else None
        if isinstance(data, dict):
            expr = data.get("expr") or data.get("latex") or data.get("formula") or ""
            if str(expr).strip():
                return {"type": "latex", "expr": str(expr),
                        "url": data.get("url", ""), "width": data.get("width"),
                        "height": data.get("height")}
        return None
    if k.endswith("videogrid"):
        # Grid video (GenAIVideoPrimitive). HARUS dicek SEBELUM `video`
        # (karena "videogrid" juga berakhiran "video"... sebenarnya tidak —
        # "videogrid".endswith("video") == False, tapi urutan tetap aman).
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            norm = []
            for it in items:
                if isinstance(it, str):
                    if it.strip():
                        norm.append({"url": it.strip()})
                elif isinstance(it, dict) and (it.get("url") or it.get("video_url") or it.get("reels_url")):
                    norm.append(it)
            if norm:
                return {"type": "videogrid", "items": norm}
        return None
    if k.endswith("video"):
        # Video animasi — GenAIImaginePrimitive (imagine_type ANIMATE).
        if isinstance(data, dict) and (data.get("url") or data.get("media")):
            return {"type": "video", "url": data.get("url") or data.get("media"),
                    "thumbnail": data.get("thumbnail") or data.get("preview"),
                    "width": data.get("width"), "height": data.get("height"),
                    "time": data.get("time")}
        return None
    if k.endswith("fullimage"):
        if isinstance(data, dict) and (data.get("url") or data.get("media")):
            return {"type": "fullimage", "url": data.get("url") or data.get("media"),
                    "preview": data.get("preview") or data.get("thumbnail"),
                    "width": data.get("width"), "height": data.get("height"),
                    "alt": data.get("alt", "")}
        return None
    if k.endswith("divider"):
        style = data.get("style") if isinstance(data, dict) else None
        return {"type": "divider", "style": style}
    if k.endswith("label"):
        # Banner label (GenAILabelPrimitive). Terima {"text":...} atau string.
        d = data if isinstance(data, dict) else {"text": data}
        text = d.get("text") or d.get("label") or ""
        if not str(text).strip():
            return None
        return {"type": "label", "text": str(text),
                "detail": d.get("detail") or d.get("subtitle"),
                "icon": d.get("icon"),
                "variant": d.get("variant", "POSITIVE"),
                "background": d.get("background") or d.get("bg"),
                "textColor": d.get("textColor") or d.get("fg")}
    if k.endswith("spacer"):
        d = data if isinstance(data, dict) else {}
        return {"type": "spacer", "spacing": d.get("spacing"), "height": d.get("height")}
    if k.endswith("thinking"):
        d = data if isinstance(data, dict) else {}
        return {"type": "thinking", "data": {
            "title": d.get("title", "Berpikir…"),
            "thought_duration_sec": d.get("thought_duration_sec"),
            "meta_search_apps": d.get("meta_search_apps"),
        }}
    if k.endswith("progress"):
        d = data if isinstance(data, dict) else {}
        return {"type": "progress", "data": {
            "title": d.get("title", "Memproses…"),
            "is_in_progress": bool(d.get("is_in_progress", True)),
        }}
    if k.endswith("multirow"):
        sections = data.get("sections") if isinstance(data, dict) else data
        if isinstance(sections, list) and sections:
            return {"type": "multirow", "sections": sections}
        return None
    if k.endswith("upsell"):
        d = data if isinstance(data, dict) else {}
        return {"type": "upsell", "data": d}
    if k.endswith("task"):
        d = data if isinstance(data, dict) else {}
        return {"type": "task", "data": d}
    if k.endswith("foatext"):
        text = data if isinstance(data, str) else (data.get("text", "") if isinstance(data, dict) else "")
        return {"type": "foatext", "text": text}
    if k.endswith("bloks"):
        d = data if isinstance(data, dict) else {}
        return {"type": "bloks", "data": d}
    if k.endswith("html"):
        # `wa:html` berarti file dokumen yang bisa diunduh; `wa:web2ui` preview.
        d = data if isinstance(data, dict) else {"html": data}
        return {
            "type": "html_file",
            "html": str(d.get("html", "") or ""),
            "filename": str(d.get("filename") or "kwee-output.html"),
            "mimetype": "text/html",
            "caption": str(d.get("caption") or ""),
        }
    if k.endswith("preview"):
        # Pratinjau HTML/CSS/JS: susun jadi satu dokumen lalu kirim via WebView.
        if isinstance(data, str):
            return {"type": "web2ui", "html": data, "title": "Preview", "sources": []}
        d = data if isinstance(data, dict) else {}
        html = d.get("html", "") or ""
        css = d.get("css", "") or ""
        js = d.get("js", "") or d.get("javascript", "") or ""
        title = d.get("title", "Preview")
        parts = []
        if css.strip():
            parts.append(f"<style>\n{css}\n</style>")
        parts.append(html or "")
        if js.strip():
            parts.append(f"<script>\n{js}\n</script>")
        body = "\n".join(p for p in parts if p.strip())
        doc = ("<!doctype html><html><head><meta charset=\"utf-8\">"
               "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
               f"<title>{title}</title></head><body>\n{body}\n</body></html>")
        return {"type": "web2ui", "html": doc, "title": title,
                "sources": d.get("sources", []) or []}
    if k.endswith("web2ui") or k.endswith("rich"):
        if isinstance(data, str):
            return {"type": "web2ui", "html": data, "title": "Preview", "sources": []}
        return {
            "type": "web2ui",
            "html": data.get("html", ""),
            "title": data.get("title", "Preview"),
            "sources": data.get("sources", []) or [],
        }
    return None


def parse_reply(text: str) -> list:
    """Pecah balasan LLM menjadi daftar blok terstruktur."""
    text = str(text or "")
    blocks, pos = [], 0
    for m in FENCE_RE.finditer(text):
        before = text[pos:m.start()]
        if before.strip():
            blocks.extend(_text_blocks(before))
        info = (m.group(1) or "").strip()
        body = m.group(2)
        lang = (info.split()[0] if info else "").lower()
        if lang in RICH_LANGS or lang.startswith(RICH_PREFIX):
            blocks.append({"type": "richraw", "kind": lang, "body": body})
        else:
            # Fence berisi tabel ASCII (```text + ╭─┬─╮) → blok TABEL asli,
            # bukan kartu kode. Fence bahasa lain tetap kode.
            blocks.append(fence_to_block(lang, body.rstrip("\n")))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        # Fence PEMBUKA yang tidak pernah ditutup (balasan terpotong) harus
        # tetap jadi blok kode — kalau dibiarkan, ```bahasa ikut bocor ke teks.
        open_fence = re.search(r"^[ \t]*```([^\n`]*)\n", tail, re.M)
        if open_fence:
            head = tail[:open_fence.start()]
            if head.strip():
                blocks.extend(_text_blocks(head))
            info = (open_fence.group(1) or "").strip()
            lang = (info.split()[0] if info else "").lower()
            body = tail[open_fence.end():]
            if lang in RICH_LANGS or lang.startswith(RICH_PREFIX):
                blocks.append({"type": "richraw", "kind": lang, "body": body})
            else:
                blocks.append(fence_to_block(lang, body.rstrip("\n")))
        else:
            blocks.extend(_text_blocks(tail))
    return blocks


def to_replies(text: str) -> list:
    """Ubah balasan LLM menjadi daftar balasan siap-kirim (wire format)."""
    replies = []
    for b in parse_reply(text):
        t = b.get("type")
        if t == "text":
            body = b.get("text", "").strip()
            if body:
                replies.append({"type": "text", "text": body})
        elif t == "code":
            code = b.get("code", "")
            if code.strip():
                replies.append({"type": "code", "language": b.get("language", "text"), "code": code})
        elif t == "table":
            if b.get("rows"):
                replies.append({"type": "table", "title": b.get("title", ""), "rows": b["rows"]})
        elif t == "richraw":
            r = _rich_from_raw(b.get("kind", ""), b.get("body", ""))
            if r:
                replies.append(r)
            else:
                # JSON tidak sah -> jatuhkan ke teks agar tidak hilang
                fallback = clean_text(b.get("body", ""))
                if fallback.strip():
                    replies.append({"type": "text", "text": fallback})
        elif t in RICH_PASSTHROUGH:
            # Primitif kaya (image/sources/suggest/footer/compact/widget3p) —
            # diteruskan apa adanya ke transport Node (lihat transport/api/airich.js).
            replies.append(b)
    if not replies:
        replies.append({"type": "text", "text": ""})
    return replies


def replies_to_text(replies: list) -> str:
    """Susun kembali balasan menjadi teks Markdown kanonik (untuk riwayat).

    Riwayat harus menyimpan bentuk yang **ingin ditiru model** — Markdown dengan
    fence asli, bukan penanda native. Menyusun ulang dari blok yang sudah
    di-parse menjamin blok kode tetap fenced dan penanda native tidak bocor.
    """
    parts = []
    for r in replies or []:
        t = (r or {}).get("type")
        if t == "text":
            body = clean_text(r.get("text", ""))
            if body.strip():
                parts.append(body.strip("\n"))
        elif t == "code":
            lang = (r.get("language") or "text").strip()
            code = r.get("code", "")
            parts.append(f"```{lang}\n{code}\n```")
        elif t == "table":
            rows = r.get("rows") or []
            if rows:
                lines = ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
                sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
                parts.append("\n".join([lines[0], sep, *lines[1:]]))
        elif t == "image":
            parts.append(f"[gambar: {r.get('url') or r.get('media') or ''}]")
        elif t == "sources":
            srcs = r.get("sources") or []
            lines = [f"- [{s.get('title') or s.get('url')}]({s.get('url')})" for s in srcs]
            if lines:
                parts.append("Sumber:\n" + "\n".join(lines))
        elif t == "suggest":
            prompts = r.get("prompts") or []
            if prompts:
                parts.append("Saran lanjutan: " + "; ".join(str(p) for p in prompts))
        elif t == "footer":
            acts = r.get("actions") or []
            lines = [f"- [{a.get('text') or a.get('cta_text')}]({a.get('url') or a.get('cta_url')})" for a in acts]
            if lines:
                parts.append("Aksi:\n" + "\n".join(lines))
        elif t == "compact":
            d = r.get("data") or {}
            parts.append(f"[kartu: {d.get('title', '')} — {d.get('subtitle', '')}]")
        elif t == "widget3p":
            parts.append("[widget]")
        elif t == "metadata":
            parts.append(str(r.get("text", "")))
        elif t == "product":
            items = r.get("items") or []
            parts.append("Produk: " + "; ".join(
                f"{i.get('title','')} ({i.get('price','')})" for i in items if isinstance(i, dict)))
        elif t == "post":
            items = r.get("items") or []
            parts.append("Post: " + "; ".join(
                f"{i.get('username','')}: {i.get('post_caption') or i.get('title','')}" for i in items if isinstance(i, dict)))
        elif t == "reel":
            items = r.get("items") or []
            parts.append("Reel: " + "; ".join(
                f"{i.get('username','')} {i.get('title','')}" for i in items if isinstance(i, dict)))
        elif t == "carousel":
            items = r.get("items") or []
            parts.append("Korsel: " + "; ".join(
                f"[gambar: {i.get('url','')}]" for i in items if isinstance(i, dict)))
        elif t == "math":
            items = r.get("items") or []
            parts.append("Rumus: " + "; ".join(
                f"${i.get('expr','')}$" for i in items if isinstance(i, dict)))
        elif t == "latex":
            parts.append(f"$${r.get('expr','')}$$")
        elif t == "video":
            parts.append(f"[video]({r.get('url','')})")
        elif t == "fullimage":
            parts.append(f"![gambar]({r.get('url','')})")
        elif t == "divider":
            parts.append("---")
        elif t == "label":
            parts.append(f"[label: {r.get('text','')}]")
        elif t == "spacer":
            parts.append("")
        elif t == "thinking":
            parts.append(f"[status: {r.get('data',{}).get('title','')}]")
        elif t == "progress":
            parts.append(f"[status: {r.get('data',{}).get('title','')}]")
        elif t == "igsuggest":
            items = r.get("items") or []
            parts.append("Saran: " + "; ".join(
                f"{i.get('title','')}" for i in items if isinstance(i, dict)))
        elif t == "upsell":
            parts.append(f"[upsell: {r.get('data',{}).get('title','')}]")
        elif t == "task":
            d = r.get("data", {})
            parts.append(f"[tugas: {d.get('title','')} — {d.get('status','')}]")
        elif t == "foatext":
            parts.append(str(r.get("text", "")))
        elif t == "bloks":
            parts.append("[layar interaktif]")
        elif t in {"html", "html_file"}:
            parts.append("[dokumen HTML]")
        elif t == "web2ui":
            parts.append("[halaman HTML/WebView]")
        elif t == "location":
            loc = r.get("location") or {}
            parts.append(f"[lokasi: {loc.get('name','')} ({loc.get('degreesLatitude')}, {loc.get('degreesLongitude')})]")
        elif t == "contacts":
            c = r.get("contacts") or {}
            parts.append(f"[kontak: {c.get('displayName','')}]")
        elif t == "audio":
            parts.append(f"[audio]({r.get('url','')})")
        elif t == "quickreply":
            # Tombol balasan cepat — jangan hilang dari riwayat, kalau tidak
            # model tak pernah belajar memakainya lagi.
            items = r.get("items") or []
            labels = []
            for i in items:
                if isinstance(i, str):
                    labels.append(i)
                elif isinstance(i, dict):
                    lbl = i.get("label") or i.get("text") or i.get("title") or ""
                    if lbl:
                        labels.append(str(lbl))
            if labels:
                parts.append("Tombol balasan cepat: " + "; ".join(labels))
        elif t == "multirow":
            # Beberapa bagian jawaban dalam satu kartu.
            secs = r.get("sections") or r.get("items") or []
            texts = []
            for s in secs:
                if isinstance(s, dict):
                    tx = s.get("text") or s.get("title") or ""
                    if tx:
                        texts.append(str(tx))
                elif isinstance(s, str):
                    texts.append(s)
            if texts:
                parts.append("\n\n".join(texts))
        elif t == "poll":
            p = r.get("poll") or {}
            q = p.get("name") or p.get("question") or ""
            opts = p.get("options") or p.get("values") or []
            names = [o.get("name") if isinstance(o, dict) else str(o) for o in opts]
            names = [str(n) for n in names if n]
            parts.append(f"[polling: {q}]" + (f" ({', '.join(names)})" if names else ""))
        elif t == "event":
            ev = r.get("event") or {}
            nm = ev.get("name") or ev.get("title") or ""
            parts.append(f"[acara: {nm}]")
        elif t == "widget":
            parts.append("[widget interaktif]")
    return "\n\n".join(p for p in parts if p).strip("\n")
