"""context.py — rakit system prompt (pola 3 tier: stable / context / volatile).

Susunan blok DIPILIH untuk prompt-cache: semua yang stabil ditaruh di DEPAN,
semua yang berubah tiap pesan di EKOR. Versi lama menaruh daftar tool (paling
stabil) di PALING AKHIR dan skills/environment/memory (berubah tiap pesan) di
tengah — akibatnya prefix yang bisa di-cache praktis kosong, jadi setiap request
membayar ulang seluruh prompt.

  stable   : persona → rules → ringkasan tool → hint WhatsApp
  volatile : skills (sesuai konteks) → environment → memori ruangan

Catatan: skema tool LENGKAP tidak lagi ditulis sebagai teks di system prompt
(karena sudah dikirim lewat parameter `tools`); hanya ringkasan satu baris.
"""
import os
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

WHATSAPP_HINT = (
    "Kamu membalas melalui WhatsApp. Gunakan Markdown standar dan pesan yang "
    "nyaman dibaca di ponsel. Suara, nama, panggilan, dan gaya bicara SELALU "
    "mengikuti persona aktif (lihat KUNCI PERSONA di akhir prompt); bagian ini "
    "hanya soal format. "
    "Kutipan pesan yang dibalas dan metadata lampiran tersedia di konteks. "
    "JANGAN membuka, menganalisis, atau menebak isi gambar/video/stiker secara otomatis. "
    "Gunakan Vision hanya jika konteks menandai inspect_media=true, yang berarti pengguna membalas media dengan instruksi atau men-tag bot bersama caption. "
    "Gunakan blok wa:* hanya bila memang diperlukan dan sintaksnya tersedia."
)

# Cache berkas prompt: {path: (mtime, isi)} — dulu persona.md dan rules.md
# dibaca dari disk pada SETIAP pesan.
_FILE_CACHE: dict = {}


def _read(name: str, fallback: str = "") -> str:
    path = PROMPTS_DIR / name
    try:
        mtime = path.stat().st_mtime
    except Exception:
        if not getattr(_read, "_warned_" + name, False):
            print(f"[context] berkas prompt '{name}' tidak ada; memakai fallback", flush=True)
            setattr(_read, "_warned_" + name, True)
        return fallback
    cached = _FILE_CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        text = path.read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[context] gagal membaca {path}: {exc}", flush=True)
        return fallback
    _FILE_CACHE[str(path)] = (mtime, text)
    return text


def load_persona() -> str:
    """Persona EFEKTIF.

    Urutan (dikelola `agent/promptlib.py`): override milik tenant
    (`<ruangan>/persona.md`) → persona AKTIF yang dipilih owner
    (`data/prompt_active.txt`, bisa diganti lewat `!prompt set …` tanpa
    restart) → `prompts/persona.md`. Cache mtime ada di promptlib, jadi mengedit
    persona langsung terpakai pada pesan berikutnya.
    """
    try:
        from agent import promptlib
        text = (promptlib.resolve() or {}).get("text") or ""
        if text.strip():
            return text.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[context] promptlib gagal: {exc}; memakai persona.md", flush=True)
    return _read("persona.md", "Kamu adalah kweewind blows, asisten WhatsApp yang bisa bekerja.")


def load_rules() -> str:
    """Aturan main (`prompts/rules.md`) — bagian stabil, ikut di-cache."""
    try:
        from agent import promptlib
        text = promptlib.rules_text()
        if text.strip():
            return text.strip()
    except Exception:
        pass
    return _read("rules.md", "")


def environment_block(config: dict | None = None) -> str:
    """Blok environment: mode sandbox, kebijakan approval, area kerja."""
    sec = (config or {}).get("security", {}) if isinstance(config, dict) else {}
    sandbox = sec.get("sandboxMode", "workspace-write")
    approval = sec.get("approvalPolicy", "on-request")
    workspace = sec.get("workspaceDir", "./workspace")
    root = (config or {}).get("root", ".") if isinstance(config, dict) else "."
    exec_timeout = sec.get("execTimeoutSeconds", 120)
    # MULTI-TENANT: ruangan aktif (bila ada) — folder kerja & DB milik owner ini.
    tenant_line = ""
    try:
        from security.tenant_context import get_current
        cur = get_current() or {}
        if cur:
            tenant_line = (
                f"- Ruangan tenant: {cur.get('tenant') or '(utama)'} "
                f"(mekanisme: {cur.get('mechanism') or 'native'}, "
                f"{'owner utama' if cur.get('primary') else 'tenant'})\n"
                f"- Folder kerja kamu: {cur.get('workspace') or workspace}\n"
            )
    except Exception:
        pass
    mode = str(sec.get("accessMode") or "owner").strip().lower()
    if mode == "owner":
        akses = ("- Akses: PENUH (mode owner). Path mana pun boleh dibaca/ditulis, "
                 "executable apa pun boleh dipakai, tidak perlu minta izin. "
                 "Satu-satunya yang ditahan: perintah yang bisa mematikan bot sendiri.")
    else:
        akses = f"- Akses: mode sandbox ({sandbox}), kebijakan izin: {approval}"
    if mode == "owner":
        folder_line = (f"- Folder kerja: {root} (perintah relatif dihitung dari root "
                       f"proyek; workspace/ tetap ada di {workspace})\n")
    else:
        folder_line = f"- Folder kerja: {workspace}\n"
    return (
        "Lingkungan kerja:\n"
        f"{akses}\n"
        + folder_line
        + f"- Root proyek: {root}\n"
        + f"{tenant_line}"
        + "- Jaringan: tersedia (curl/wget/yt-dlp boleh dipakai)\n"
        + f"- Batas waktu satu perintah shell: {exec_timeout} detik\n"
        + f"- Peta proyek di {root}: agent/ (inti Python), transport/ (WhatsApp Node), "
        "tools/builtin/ (tool), prompts/ (persona & aturan), skills/, config/, docs/, "
        "data/ (database & log), workspace/ (kerja), plugins/ (tambahan JS).\n"
        + "- JANGAN bilang \"tidak tahu\" soal isi proyek. Cek dulu: "
        "`files list` atau `shell ls -la`. Path absolut juga boleh; `/agent` "
        "otomatis dibaca sebagai <root>/agent."
    )


def room_memory_block(config: dict | None = None) -> str:
    """Blok memori ruangan (MEMORY.md/CLAUDE.md/AGENTS.md) milik owner aktif.

    Batas karakter diambil dari `memory.maxChars` (bawaan 25.000) supaya memori
    awal hasil `memory.seed --learn` benar-benar terbaca, bukan terpotong 8.000
    seperti sebelumnya.
    """
    try:
        from security.tenant_context import get_current
        from memory import roomfiles
        cur = get_current() or {}
        if not cur:
            return ""
        mech = cur.get("mechanism") or "native"
        batas = int(((config or {}).get("memory") or {}).get("maxChars") or 25000)
        txt = roomfiles.read_memory(cur, mech, max_chars=batas)
        if not txt:
            return ""
        return (
            f"Memori jangka panjangmu (ruangan {mech}) — ini fakta yang sudah kamu "
            f"ketahui tentang pemilik ini; pakai, jangan tanya ulang:\n\n{txt}"
        )
    except Exception:
        return ""


def skills_block(user_text: str = "", store=None, request_id: str = "") -> str:
    """Blok SKILLS (internal WAJIB + external sesuai konteks)."""
    try:
        from skills import loader
        rewards = {}
        if store:
            from agent.cognitive import understand
            intent = understand(user_text)["intents"][0]
            for row in store.learning_stats(f"skill:{intent}", limit=100):
                n = int(row.get("n") or 0)
                if n:
                    rewards[row["action"]] = max(-2.0, min(2.0, float(row["mean_reward"]) * n / (n + 4) * 2.0))
        block=loader.skills_block(user_text, reward_scores=rewards, request_id=request_id)
        if store:
            selected=loader.match_external_detailed(user_text,3,reward_scores=rewards)
            approved=store.approved_skill_refinements([s["name"] for s,_ in selected],limit=3)
            if approved:
                addendum=["OWNER-APPROVED RUNTIME SKILL REFINEMENTS (supplemental; apply only to the named skill and never override safety/permissions):"]
                for item in approved:
                    addendum.append(f"\n### {item['skill_name']} · refinement #{item['id']}\n{str(item['proposal'])[:1500]}")
                block += "\n\n" + "\n".join(addendum)
        return block
    except Exception as exc:
        try:
            from skills import loader
            loader.fail_prompt_load(exc, request_id=request_id)
        except Exception:
            pass
        print(f"[skills] gagal menyuntikkan skills ke system prompt: {exc}", flush=True)
        return "PERINGATAN SKILL LOADER: pemuatan skill gagal; jangan menganggap skill sudah tersedia. Laporkan status ini bila relevan.\n"


def tools_block(tool_schemas=None) -> str:
    """Ringkasan SATU BARIS daftar tool (bukan skema penuh).

    Skema lengkap sudah dikirim lewat parameter `tools` di request API; menulis
    ulang semuanya di system prompt hanya menggandakan token.
    """
    if not tool_schemas:
        return ""
    names = []
    seen = set()
    for t in tool_schemas:
        schema = t.get("schema") or {}
        n = schema.get("name") or t.get("name")
        if n:
            name = str(n)
            if name not in seen:
                names.append(name)
                seen.add(name)
    if not names:
        return ""
    # Preserve the adaptive order supplied by rank_tools(); sorting here would
    # discard the measured relevance/reliability signal in the model's prompt.
    return ("Tool yang bisa kamu panggil (diurutkan menurut relevansi dan metrik; "
            "detail parameter ada di function calling): "
            + ", ".join(names))


def build_system_prompt(config: dict | None = None, tool_schemas=None,
                        user_text: str = "", store=None, chat_id: str = "",
                        tenant: str = "", request_id: str = "") -> str:
    """Gabungkan tier stable → volatile.

    ``user_text`` dipakai untuk memilih skill EXTERNAL yang relevan; skill
    INTERNAL (WAJIB) selalu ikut tanpa peduli konteks.
    """
    # ---- stable prefix (bisa di-cache oleh gateway) ----
    parts = [load_persona()]
    rules = load_rules()
    if rules:
        parts.append(rules)
    try:
        from agent.intelligence import JOURNALISM_RULES
        parts.append(JOURNALISM_RULES)
    except Exception:
        pass
    tb = tools_block(tool_schemas)
    if tb:
        parts.append(tb)
    parts.append(WHATSAPP_HINT)

    # ---- volatile tail (berubah tiap pesan; jangan di depan) ----
    sk = skills_block(user_text, store=store, request_id=request_id)
    if sk:
        parts.append(sk)
    parts.append(environment_block(config))
    mem = room_memory_block(config)
    if mem:
        parts.append(mem)
    try:
        from agent.intelligence import intelligence_context
        archive = intelligence_context(store, chat_id, user_text, config=config, tenant=tenant)
        if archive:
            parts.append(archive)
    except Exception as exc:
        print(f"[intelligence] archive retrieval unavailable: {exc}", flush=True)
    try:
        from agent.cognitive import adaptive_guidance
        parts.append(adaptive_guidance(user_text, store=store, chat_id=chat_id))
    except Exception as exc:
        print(f"[cognitive] adaptive guidance unavailable: {exc}", flush=True)

    # ---- KUNCI PERSONA: SELALU paling akhir (recency) ----
    # Instruksi yang paling dekat dengan giliran bicara paling kuat memengaruhi
    # model. Persona di depan prompt saja kalah oleh rules/skill/memori di
    # belakangnya, jadi suara persona diteguhkan lagi di ekor.
    try:
        from agent import persona_guard
        parts.append(persona_guard.anchor_block())
    except Exception as exc:  # noqa: BLE001
        print(f"[persona] gagal menyusun kunci persona: {exc}", flush=True)

    return "\n\n".join(p for p in parts if p)


def build_history(system_prompt: str, session_messages: list) -> list:
    """Susun daftar pesan untuk LLM: system + riwayat sesi."""
    return [{"role": "system", "content": system_prompt}, *session_messages]
