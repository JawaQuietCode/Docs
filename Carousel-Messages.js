import fs from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import crypto from "node:crypto"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import { fileURLToPath } from "node:url"
import sharp from "sharp"
import {
    generateWAMessageFromContent,
    prepareWAMessageMedia
} from "yowtech"

import { relayInteractive } from "../Cube/Lib/InteractiveRelay.js"
import { resolveFfmpeg } from "../Cube/Lib/Sticker.js"

const execFileAsync = promisify(execFile)

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const DEFAULT_IMAGE = path.join(PROJECT_ROOT, "Assets", "Images", "UrlPreview.png")

const CARD_SIZE = 800
const UPLOAD_TIMEOUT = 25_000
const MAX_CARDS = 10

//━━━━━━━━━━━━━━━━━━━━//
// TOMBOL
//━━━━━━━━━━━━━━━━━━━━//
const quickReply = (displayText, id) => ({
    name: "quick_reply",
    buttonParamsJson: JSON.stringify({ display_text: displayText, id })
})

const ctaUrl = (displayText, url) => ({
    name: "cta_url",
    buttonParamsJson: JSON.stringify({ display_text: displayText, url, merchant_url: url })
})

const ctaCopy = (displayText, code) => ({
    name: "cta_copy",
    buttonParamsJson: JSON.stringify({ display_text: displayText, id: `copy-${code}`, copy_code: code })
})

const ctaCall = (displayText, phone) => ({
    name: "cta_call",
    buttonParamsJson: JSON.stringify({ display_text: displayText, phone_number: `+${phone}` })
})

const singleSelect = (title, sections) => ({
    name: "single_select",
    buttonParamsJson: JSON.stringify({ title, sections })
})

//━━━━━━━━━━━━━━━━━━━━//
// GAMBAR KARTU
//━━━━━━━━━━━━━━━━━━━━//
// Setiap kartu carousel WAJIB punya header gambar/video, jadi kartu "teks + tombol"
// memakai gambar gradasi polos (tanpa teks, tidak butuh font di server).
const GRADIENTS = [
    ["#5865f2", "#8b5cf6"],
    ["#0ea5e9", "#22d3ee"],
    ["#f97316", "#facc15"],
    ["#10b981", "#84cc16"],
    ["#ec4899", "#f43f5e"],
    ["#6366f1", "#06b6d4"],
    ["#14b8a6", "#3b82f6"]
]

function gradientImage(index) {
    const [from, to] = GRADIENTS[index % GRADIENTS.length]
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${CARD_SIZE}" height="${CARD_SIZE}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="${from}"/><stop offset="1" stop-color="${to}"/>
</linearGradient></defs>
<rect width="100%" height="100%" fill="url(#g)"/>
</svg>`
    return sharp(Buffer.from(svg)).jpeg({ quality: 90 }).toBuffer()
}

async function photoImage() {
    try {
        const input = await fs.readFile(DEFAULT_IMAGE)
        return await sharp(input, { limitInputPixels: 50_000_000 })
            .rotate()
            .resize(CARD_SIZE, CARD_SIZE, { fit: "cover", position: "centre", kernel: "lanczos3" })
            .flatten({ background: "#ffffff" })
            .jpeg({ quality: 90, chromaSubsampling: "4:4:4", mozjpeg: true })
            .toBuffer()
    } catch {
        return null
    }
}

//━━━━━━━━━━━━━━━━━━━━//
// VIDEO KARTU
//━━━━━━━━━━━━━━━━━━━━//
// Pakai video dari pesan/reply kalau ada, kalau tidak buat video 3 detik dengan ffmpeg.
async function getVideoBuffer(m) {
    const source = m.isVideo ? m : m.quoted?.isVideo ? m.quoted : null
    if (source) {
        const buffer = await source.download().catch(() => null)
        if (buffer?.length) return buffer
    }

    const ffmpeg = await resolveFfmpeg()
    if (!ffmpeg) return null

    const output = path.join(os.tmpdir(), `test-${crypto.randomBytes(6).toString("hex")}.mp4`)
    try {
        await execFileAsync(ffmpeg, [
            "-y",
            "-f", "lavfi",
            "-i", "color=c=0x5865f2:s=640x640:d=3:r=15",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-movflags", "+faststart",
            "-an",
            output
        ], { timeout: 60_000 })
        return await fs.readFile(output)
    } catch {
        return null
    } finally {
        await fs.rm(output, { force: true }).catch(() => {})
    }
}

//━━━━━━━━━━━━━━━━━━━━//
// UPLOAD HEADER
//━━━━━━━━━━━━━━━━━━━━//
async function uploadHeader(sock, kind, buffer) {
    let timer
    try {
        const prepared = await Promise.race([
            prepareWAMessageMedia(
                { [kind]: buffer },
                { upload: (file, options) => sock.waUploadToServer(file, options) }
            ),
            new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error(`Upload ${kind} timeout.`)), UPLOAD_TIMEOUT)
            })
        ])

        const media = kind === "image" ? prepared?.imageMessage : prepared?.videoMessage
        if (!media) throw new Error(`Upload ${kind} tidak mengembalikan media.`)

        return {
            hasMediaAttachment: true,
            ...(kind === "image" ? { imageMessage: media } : { videoMessage: media })
        }
    } finally {
        clearTimeout(timer)
    }
}

//━━━━━━━━━━━━━━━━━━━━//
// DAFTAR KARTU
//━━━━━━━━━━━━━━━━━━━━//
function cardDefinitions(prefix) {
    const owner = global.yB?.ownerNumber || "6285743003734"
    const github = global.yB?.sosmedGithub || "https://github.com/Yowtech/Base"

    return [
        {
            title: "Quick Reply",
            text: "Tombol balasan. Saat ditekan, bot menerima id tombolnya.",
            footer: "quick_reply",
            media: "gradient",
            buttons: [
                quickReply("Balas Ping", `${prefix}test reply ping`),
                quickReply("Balas Pong", `${prefix}test reply pong`)
            ]
        },
        {
            title: "URL",
            text: "Tombol yang membuka link di browser.",
            footer: "cta_url",
            media: "gradient",
            buttons: [ctaUrl("Buka GitHub", github)]
        },
        {
            title: "Copy",
            text: "Tombol yang menyalin teks ke clipboard.",
            footer: "cta_copy",
            media: "gradient",
            buttons: [ctaCopy("Salin Kode", "SINGULARITA-2026")]
        },
        {
            title: "Call",
            text: "Tombol yang langsung menelepon nomor.",
            footer: "cta_call",
            media: "gradient",
            buttons: [ctaCall("Telepon Owner", owner)]
        },
        {
            title: "List",
            text: "Tombol yang membuka daftar pilihan.",
            footer: "single_select",
            media: "gradient",
            buttons: [
                singleSelect("Pilih Opsi", [{
                    title: "Opsi Test",
                    rows: [
                        { header: "A", title: "Opsi A", description: "Pilihan pertama", id: `${prefix}test reply a` },
                        { header: "B", title: "Opsi B", description: "Pilihan kedua", id: `${prefix}test reply b` }
                    ]
                }])
            ]
        },
        {
            title: "Campuran",
            text: "Beberapa jenis tombol dalam satu kartu.",
            footer: "quick_reply + cta_url + cta_copy",
            media: "gradient",
            buttons: [
                quickReply("Balas", `${prefix}test reply mix`),
                ctaUrl("Buka Link", github),
                ctaCopy("Salin", "MIX-TEST")
            ]
        },
        {
            title: "Gambar",
            text: "Kartu dengan header gambar asli + tombol.",
            footer: "image + button",
            media: "photo",
            buttons: [
                quickReply("Suka", `${prefix}test reply image`),
                ctaUrl("Lihat", github)
            ]
        },
        {
            title: "Video",
            text: "Kartu dengan header video + tombol.",
            footer: "video + button",
            media: "video",
            buttons: [
                quickReply("Putar", `${prefix}test reply video`),
                ctaUrl("Lihat", github)
            ]
        }
    ].slice(0, MAX_CARDS)
}

async function buildCards(ctx, prefix) {
    const { sock, m } = ctx
    const definitions = cardDefinitions(prefix)
    const skipped = []

    const photo = await photoImage()
    const video = await getVideoBuffer(m)

    const cards = await Promise.all(definitions.map(async (card, index) => {
        let header

        if (card.media === "video") {
            if (!video) {
                skipped.push("Video (ffmpeg tidak tersedia)")
                return null
            }
            header = await uploadHeader(sock, "video", video)
        } else if (card.media === "photo") {
            const image = photo || await gradientImage(index)
            header = await uploadHeader(sock, "image", image)
        } else {
            header = await uploadHeader(sock, "image", await gradientImage(index))
        }

        return {
            header,
            body: { text: `*${card.title}*\n${card.text}` },
            footer: { text: card.footer },
            nativeFlowMessage: {
                buttons: card.buttons,
                messageParamsJson: "{}"
            }
        }
    }))

    return { cards: cards.filter(Boolean), skipped }
}

//━━━━━━━━━━━━━━━━━━━━//
// KIRIM
//━━━━━━━━━━━━━━━━━━━━//
function buildPayload(cards, wrapViewOnce) {
    const interactiveMessage = {
        header: { hasMediaAttachment: false },
        body: { text: "Test Carousel • geser untuk melihat semua jenis tombol." },
        footer: { text: `${global.yB?.botName || "Bot"} • Carousel Test` },
        carouselMessage: { cards }
    }

    return wrapViewOnce
        ? { viewOnceMessage: { message: { interactiveMessage } } }
        : { interactiveMessage }
}

const HELP = [
    "*Test Carousel*",
    "`Example :`",
    ".test",
    "> Mengirim satu carousel berisi semua jenis tombol dan kartu gambar/video.",
    "> Video: reply/kirim video dengan caption .test untuk memakai video sendiri.",
    ".test vo",
    "> Variasi dibungkus viewOnce, dipakai kalau .test biasa tidak tampil."
].join("\n")

//━━━━━━━━━━━━━━━━━━━━//
// PLUGIN
//━━━━━━━━━━━━━━━━━━━━//
export default {
    name: "Test",
    command: ["test"],
    category: "Owner",
    description: HELP,
    menuVisible: true,
    permission: {
        owner: true,
        admin: false,
        botAdmin: false,
        group: false,
        private: false,
        premium: false
    },
    cooldown: 5,

    async execute(ctx) {
        const { sock, m, args, logger } = ctx
        const prefix = typeof ctx.prefix === "string" && ctx.prefix ? ctx.prefix : "."
        const mode = String(args?.[0] || "").toLowerCase()

        // Balasan dari tombol / list
        if (mode === "reply") {
            return m.reply(`✅ Tombol diterima: *${args?.[1] || "-"}*`)
        }

        if (mode === "help") return m.reply(HELP)

        try {
            const { cards, skipped } = await buildCards(ctx, prefix)
            if (!cards.length) return m.reply("❌ Tidak ada kartu yang berhasil dibuat.")

            const payload = buildPayload(cards, mode === "vo")
            const waMsg = generateWAMessageFromContent(m.chat, payload, {
                userJid: sock.user?.id,
                quoted: m
            })

            await relayInteractive(sock, m.chat, waMsg.message, { messageId: waMsg.key?.id })

            if (skipped.length) {
                await m.reply(`ℹ️ Kartu dilewati: ${skipped.join(", ")}.`)
            }

            return { silent: true }
        } catch (error) {
            logger?.error?.(error)
            return m.reply(`❌ Gagal mengirim carousel.\n${error?.message || "Terjadi kesalahan."}`)
        }
    }
    }
