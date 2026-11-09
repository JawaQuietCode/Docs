import { generateWAMessageFromContent, proto } from "yowtech"

function helpText() {
    return [
        "*Sticker Premium*",
        "`Example :`",
        ".sperm",
        "> Reply sticker untuk mengirim ulang sebagai premium/AI sticker.",
        "> Opsional: .sperm 62812xxxxxxx untuk mengirim ke nomor tujuan."
    ].join("\n")
}

function getArgs(ctx) {
    if (Array.isArray(ctx.args)) return ctx.args.map(value => String(value ?? "").trim()).filter(Boolean)
    const raw = ctx.m?.arg || ctx.text || ""
    return String(raw).trim().split(/\s+/).filter(Boolean)
}

function normalizeTarget(value, fallback) {
    if (!value) return fallback
    const digits = String(value).replace(/\D/g, "")
    if (digits.length < 7 || digits.length > 15) {
        throw new Error("Nomor tujuan tidak valid. Gunakan format internasional, misalnya 62812xxxxxxx.")
    }
    return `${digits}@s.whatsapp.net`
}

export default {
    name: "Sperm",
    command: ["sperm", "sprem", "stickerpremium", "premiumsticker"],
    category: "Owner",
    description: helpText(),
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
        const { m, sock } = ctx
        const quoted = m.quoted
        const sticker = quoted?.msg?.stickerMessage || quoted?.message?.stickerMessage || (quoted?.isSticker ? quoted.msg : null)

        if (!sticker) return m.reply(helpText())
        if (typeof sock?.relayMessage !== "function" || typeof generateWAMessageFromContent !== "function") {
            return m.reply("❌ _Library WhatsApp tidak mendukung pengiriman ulang sticker._")
        }

        try {
            const targetJid = normalizeTarget(getArgs(ctx)[0], m.chat)
            const stickerMessage = proto.Message.StickerMessage.fromObject({
                url: sticker.url,
                fileSha256: sticker.fileSha256,
                fileEncSha256: sticker.fileEncSha256,
                mediaKey: sticker.mediaKey,
                mimetype: sticker.mimetype || "image/webp",
                height: sticker.height,
                width: sticker.width,
                directPath: sticker.directPath,
                fileLength: sticker.fileLength,
                mediaKeyTimestamp: sticker.mediaKeyTimestamp,
                isAnimated: Boolean(sticker.isAnimated),
                stickerSentTs: Date.now(),
                isAvatar: Boolean(sticker.isAvatar),
                isAiSticker: true,
                premium: 1,
                isLottie: Boolean(sticker.isLottie),
                accessibilityLabel: sticker.accessibilityLabel || ""
            })

            const waMsg = generateWAMessageFromContent(
                targetJid,
                { stickerMessage },
                {
                    userJid: sock.user?.id,
                    quoted: m
                }
            )

            await sock.relayMessage(targetJid, waMsg.message, {
                messageId: waMsg.key.id
            })
            await m.react?.("✅")
            return { silent: true }
        } catch (error) {
            ctx.logger?.error?.(error)
            return m.reply(`❌ _Gagal mengirim sticker premium._\n${error?.message || "Terjadi kesalahan."}`)
        }
    }
      }
