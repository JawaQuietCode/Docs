/** Explicit opt-in gate: never inspect unsolicited media. */
export function shouldInspectMedia({ text = '', attachment = null, quotedAttachment = null, isMentionBot = false } = {}) {
  const prompt = String(text || '').trim()
  const requestText = prompt.replace(/@\d{5,}/g, '').replace(/@kwee/gi, '').trim()
  const commandText = prompt.replace(/^@\S+\s*/, '').trim()
  if (!requestText || commandText.startsWith('!')) return false
  const isVisual = (item) => item
    && ['image', 'video', 'sticker'].includes(String(item.kind || '').toLowerCase())
    && Boolean(item.path || item.id || item.url)
  if (isVisual(quotedAttachment)) return true
  return Boolean(isMentionBot && isVisual(attachment) && requestText)
}
