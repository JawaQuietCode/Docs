#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/kwee-system-skills-src"
EXT="$ROOT/skills/external"
mkdir -p "$WORK" "$EXT"

clone_or_update() {
  local repo="$1" dest="$2"
  if [[ -d "$dest/.git" ]]; then
    git -C "$dest" fetch --depth 1 origin
    git -C "$dest" checkout -q FETCH_HEAD
  else
    rm -rf "$dest"
    git clone --depth 1 "$repo" "$dest"
  fi
}

# copy_skills <dir-sumber> <prefix> <kedalaman-find...>
# Salin tiap folder ber-SKILL.md ke skills/external/<prefix>-<slug>. Cetak jumlahnya.
copy_skills() {
  local src="$1" prefix="$2"
  shift 2
  local count=0 skill_file dir slug dest
  while IFS= read -r -d '' skill_file; do
    dir="$(dirname "$skill_file")"
    slug="$(basename "$dir")"
    dest="$EXT/$prefix-$slug"
    rm -rf "$dest"
    cp -a "$dir" "$dest"
    count=$((count + 1))
  done < <(find "$src" "$@" -name SKILL.md -type f -not -path '*/.git/*' -print0 | sort -z)
  echo "$count"
}

REPO_REACH=https://github.com/Panniantong/Agent-Reach.git
REPO_SCIENCE=https://github.com/K-Dense-AI/scientific-agent-skills.git
REPO_WRITER=https://github.com/K-Dense-AI/claude-scientific-writer.git
REPO_KARPATHY=https://github.com/multica-ai/andrej-karpathy-skills.git

clone_or_update "$REPO_REACH" "$WORK/Agent-Reach"
clone_or_update "$REPO_SCIENCE" "$WORK/scientific-agent-skills"
clone_or_update "$REPO_WRITER" "$WORK/claude-scientific-writer"
clone_or_update "$REPO_KARPATHY" "$WORK/andrej-karpathy-skills"

reach_skill=""
for candidate in "$WORK/Agent-Reach/agent_reach/skill" "$WORK/Agent-Reach/skills/agent-reach" "$WORK/Agent-Reach/skill"; do
  if [[ -f "$candidate/SKILL.md" ]]; then reach_skill="$candidate"; break; fi
done
if [[ -z "$reach_skill" ]]; then
  echo "Agent-Reach clone did not contain a recognized SKILL.md path" >&2
  exit 2
fi
rm -rf "$EXT/agent-reach"
cp -a "$reach_skill" "$EXT/agent-reach"

count_science="$(copy_skills "$WORK/scientific-agent-skills/skills" scientific -mindepth 2 -maxdepth 2)"
count_writer="$(copy_skills "$WORK/claude-scientific-writer" writer -mindepth 1)"
count_karpathy="$(copy_skills "$WORK/andrej-karpathy-skills" karpathy -mindepth 1)"

reach_sha="$(git -C "$WORK/Agent-Reach" rev-parse HEAD)"
science_sha="$(git -C "$WORK/scientific-agent-skills" rev-parse HEAD)"
writer_sha="$(git -C "$WORK/claude-scientific-writer" rev-parse HEAD)"
karpathy_sha="$(git -C "$WORK/andrej-karpathy-skills" rev-parse HEAD)"

cat > "$ROOT/skills/external-sources.lock" <<LOCK_EOF
{
  "installed_at": "$(date -Is)",
  "agent_reach": {"repository": "Panniantong/Agent-Reach", "commit": "$reach_sha"},
  "scientific_agent_skills": {"repository": "K-Dense-AI/scientific-agent-skills", "commit": "$science_sha", "skill_count": $count_science},
  "claude_scientific_writer": {"repository": "K-Dense-AI/claude-scientific-writer", "commit": "$writer_sha", "skill_count": $count_writer},
  "andrej_karpathy_skills": {"repository": "multica-ai/andrej-karpathy-skills", "commit": "$karpathy_sha", "skill_count": $count_karpathy, "injected": false}
}
LOCK_EOF

# Agent-Reach CLI tidak dipasang diam-diam; hanya bila operator memintanya.
if [[ "${INSTALL_AGENT_REACH_CLI:-0}" == "1" ]]; then
  python3 -m pip install --user "$WORK/Agent-Reach"
fi

echo "terpasang: agent-reach, scientific=$count_science, writer=$count_writer, karpathy=$count_karpathy (on-demand; tidak disuntik ke persona/rules/memori)"
