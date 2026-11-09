import fs from "node:fs/promises"
import path from "node:path"
import { fileURLToPath } from "node:url"
import {
    AssociationType,
    generateWAMessageFromContent,
    prepareWAMessageMedia
} from "yowtech"

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const STICKER_PATH = path.join(PROJECT_ROOT, "Assets", "Sticker", "Sticker2.webp")

const HELP = [
    "*React Sticker Normal*",
    "`Contoh :`",
    ".rsn _(reply pesan apa pun)_",
    "> Mengirim sticker WebP normal sebagai annotation pada pesan yang direply.",
    "> Aset harus berupa static WebP di `Assets/Sticker/Sticker2.webp`."
].join("\n")

function inspectStaticWebP(buffer) {
    if (
        !Buffer.isBuffer(buffer) ||
        buffer.length < 12 ||
        buffer.toString("ascii", 0, 4) !== "RIFF" ||
        buffer.toString("ascii", 8, 12) !== "WEBP"
    ) {
        return { valid: false, animated: false }
    }

    let hasAnim = false
    let hasFrame = false
    let offset = 12

    while (offset + 8 <= buffer.length) {
        const chunkType = buffer.toString("ascii", offset, offset + 4)
        const chunkSize = buffer.readUInt32LE(offset + 4)
        const nextOffset = offset + 8 + chunkSize + (chunkSize & 1)
        if (nextOffset > buffer.length) break
        if (chunkType === "ANIM") hasAnim = true
        if (chunkType === "ANMF") hasFrame = true
        offset = nextOffset
    }

    return { valid: true, animated: hasAnim && hasFrame }
}

function cleanJid(jid) {
    return jid ? String(jid).replace(/:\d+(?=@)/, "") : null
}

function buildParentMessageKey(ctx) {
    const quoted = ctx.quoted
    const quotedKey = quoted.key || {}
    const participant = quoted.sender || quoted.participant || quotedKey.participant || quotedKey.participantAlt
    const key = {
        remoteJid: quoted.chat || quotedKey.remoteJid || ctx.chat,
        id: quoted.id || quotedKey.id,
        fromMe: Boolean(quoted.fromMe ?? quotedKey.fromMe ?? false)
    }

    if (participant) {
        key.participant = participant
    }
    if (quoted.senderAlt || quotedKey.participantAlt) {
        key.participantAlt = quoted.senderAlt || quotedKey.participantAlt
    }
    if (quotedKey.remoteJidAlt) {
        key.remoteJidAlt = quotedKey.remoteJidAlt
    }

    if (participant) {
        const ownJids = [ctx.sock.user?.id, ctx.sock.user?.lid]
            .filter(Boolean)
            .map(cleanJid)
        key.fromMe = Boolean(quoted.fromMe ?? quotedKey.fromMe ?? ownJids.includes(cleanJid(participant)))
    }

    return key
}

function buildRelayOptions(ctx) {
    return {
        userJid: cleanJid(ctx.sock.user?.id || ctx.sock.user?.lid) || undefined,
        timestamp: new Date()
    }
}

export default {
    name: "Rsn",
    command: ["rsn"],
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
        if (!ctx.quoted?.id) {
            return ctx.m.reply(HELP, { fakeReply: false })
        }

        try {
            const stickerBuffer = await fs.readFile(STICKER_PATH)
            const stickerFormat = inspectStaticWebP(stickerBuffer)

            if (!stickerFormat.valid) {
                throw new Error("Sticker2.webp bukan file WebP yang valid.")
            }
            if (stickerFormat.animated) {
                throw new Error("Sticker2.webp terbaca sebagai WebP bergerak. Untuk .rsn gunakan sticker WebP normal/static.")
            }

            const prepared = await prepareWAMessageMedia(
                { sticker: stickerBuffer },
                { upload: (file, options) => ctx.sock.waUploadToServer(file, options), jid: ctx.chat }
            )
            const stickerMessage = prepared?.stickerMessage
            if (!stickerMessage) {
                throw new Error("Yowtech tidak menghasilkan stickerMessage dari file tersebut.")
            }

            stickerMessage.mimetype = "image/webp"
            stickerMessage.isAnimated = false

            const content = {
                messageContextInfo: {
                    messageAssociation: {
                        associationType: AssociationType.STICKER_ANNOTATION,
                        parentMessageKey: buildParentMessageKey(ctx)
                    }
                },
                stickerMessage
            }
            const waMessage = generateWAMessageFromContent(
                ctx.chat,
                content,
                buildRelayOptions(ctx)
            )

            await ctx.sock.relayMessage(
                ctx.chat,
                waMessage.message,
                { messageId: waMessage.key.id },
                waMessage
            )
            await ctx.m.react("✅").catch(() => {})
            return { silent: true }
        } catch (error) {
            ctx.logger?.error?.(error)
            const errorMessage = error?.code === "ENOENT"
                ? "Aset belum ditemukan. Simpan sticker normal sebagai Assets/Sticker/Sticker2.webp terlebih dahulu."
                : error?.message || "Terjadi kesalahan."
            return ctx.m.reply(`❌ Gagal mengirim sticker annotation: ${errorMessage}`, { fakeReply: false })
        }
    }
              }
