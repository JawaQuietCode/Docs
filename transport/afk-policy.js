/** AFK is a low-risk self-status command available to participants in groups. */
export function isAfkGroupCommand(text, isGroup) {
  return Boolean(isGroup && /^!afk(?:\s|$)/i.test(String(text || '').trim()))
}
