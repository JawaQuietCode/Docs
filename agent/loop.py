"""loop.py — agent loop: LLM <-> tool, multi-langkah.

Aturan penting (dari pola Hermes/opencode):
  - handler tool mengembalikan dict/objek; error dibungkus jadi {"error": ...}
  - hasil tool disusun sebagai pesan role=tool
  - batas keras: iterasi + total waktu
  - hasil tool PARALEL (bila tool ditandai `concurrent`) disusun ulang sesuai
    urutan asli tool_call

Perbaikan dibanding versi sebelumnya:
  * ANGGARAN JAWABAN AKHIR: 15% waktu turn disisihkan supaya `_final_answer`
    punya waktu nyata. Dulu pemeriksaan timeout baru terjadi setelah anggaran
    habis, sehingga jawaban akhir dipanggil dengan sisa ≤1 detik dan selalu
    gagal → pengguna menerima pesan "batas langkah" yang hampa.
  * RESUME tidak lagi memundurkan penghitung iterasi (dulu `!lanjut` mereset
    `iteration` ke 1 sehingga checkpoint mencatat angka lebih kecil dan tugas
    panjang bisa diulang tanpa batas).
  * Tool `concurrent` dijalankan paralel (dulu berurutan meski docstring
    menjanjikan paralel) — `shell`/`wa`/`cron` tetap serial.
  * Panggilan tool IDENTIK berulang dalam satu turn dikembalikan dari cache,
    supaya model tidak berputar-putar di tool yang sama.
  * Hasil tool dibatasi ukurannya di loop (jaring terakhir, walau tool lupa).
  * Pembatalan (`!stop`) menembus tool panjang lewat `context.cancel`.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from agent import execution_guard
from agent.format import replies_to_text, to_replies
from tools.schema import truncate

# Kata kunci penanda tugas berhenti karena batas.
STOP_MESSAGE = "Batas langkah tercapai; saya berhenti di sini. Sebagian pekerjaan mungkin sudah selesai."
# Tugas dihentikan atas permintaan pemilik (mis. perintah !stop).
CANCEL_MESSAGE = "🛑 Oke, kuhentikan. Sebagian pekerjaan mungkin sudah tersimpan — ketik !lanjut kalau mau kuteruskan."

# Batas ukuran satu hasil tool yang dimasukkan ke konteks (byte).
# 16 KB terlalu kecil: `cat` satu berkas 400 baris, `git diff`, atau keluaran
# `npm install` langsung terpotong dan model menyimpulkan "gagal". 64 KB cukup
# untuk hampir semua hasil nyata, dan bisa dinaikkan lewat
# `security.toolResultLimitBytes` di config.
TOOL_RESULT_LIMIT = 65536
# Berapa kali model boleh "menyerahkan perintah" sebelum kita biarkan
# balasannya lewat. Dua cukup: sekali untuk kasus ragu, sekali lagi kalau model
# tetap ngotot (mis. memang tidak punya tool yang cocok).
MAX_HANDOFF_NUDGE = 2
# Porsi waktu turn yang DISISIHKAN untuk menulis jawaban akhir.
FINAL_RESERVE_RATIO = 0.15
FINAL_RESERVE_MIN = 20.0

# State turn yang disimpan berkala (checkpoint) supaya tugas panjang bisa
# DILANJUTKAN dari titik terakhir, bukan dimulai dari nol.
_CHECKPOINT_KEYS = ("msgs", "iteration", "continue_count", "acc", "token_budget")


def _make_state(msgs, iteration, continue_count, acc, token_budget):
    """Ambil potret state turn untuk checkpoint/resume."""
    return {
        "msgs": msgs,
        "iteration": iteration,
        "continue_count": continue_count,
        "acc": list(acc or []),
        "token_budget": token_budget,
    }


def _restore_state(state):
    """Kembalikan (msgs, iteration, continue_count, acc, token_budget) dari checkpoint."""
    st = state or {}
    return (
        list(st.get("msgs") or []),
        int(st.get("iteration") or 0),
        int(st.get("continue_count") or 0),
        list(st.get("acc") or []),
        st.get("token_budget"),
    )


def _tool_schema_for_api(tool: dict) -> dict:
    """Ubah tool internal -> format `tools` OpenAI."""
    schema = tool.get("schema") or {}
    return {
        "type": "function",
        "function": {
            "name": schema.get("name", tool.get("name", "")),
            "description": schema.get("description", ""),
            "parameters": schema.get("parameters", {"type": "object", "properties": {}}),
        },
    }


def _handler_arity(fn):
    """Berapa argumen posisional yang diterima handler (1 atau 2)."""
    try:
        import inspect
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return 2
    params = [p for p in sig.parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_varargs = any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values())
    if has_varargs:
        return 2
    return len(params)


def _friendly_tool_error(name, exc):
    kind = type(exc).__name__
    if isinstance(exc, ModuleNotFoundError):
        message = "dependensi atau modul project tidak ditemukan; jalankan setup.sh dan pastikan folder modul ikut diunggah"
    elif isinstance(exc, FileNotFoundError):
        message = "berkas yang dibutuhkan tidak ditemukan; periksa path dan kelengkapan upload"
    elif isinstance(exc, PermissionError):
        message = "izin akses ditolak; periksa hak akses folder dan batas workspace"
    elif isinstance(exc, (TimeoutError,)):
        message = "operasi melewati batas waktu; coba dengan cakupan lebih kecil atau periksa layanan terkait"
    elif isinstance(exc, ConnectionError):
        message = "layanan tujuan tidak dapat dihubungi; periksa apakah proses dan port terkait aktif"
    else:
        message = "operasi gagal; detail teknis dicatat di log server"
    return {"error": f"Tool {name} gagal: {message}.", "error_type": kind}


def _run_tool(tool: dict, args: dict, config=None):
    """Jalankan handler tool; tidak pernah melempar ke pemanggil.

    `config` diteruskan bila handler menerimanya, supaya tool menghormati
    allowlist executable, denyPaths, dan batas output dari config.

    GERBANG PERAN (keras): bila `config["context"]["tool_allow"]` diisi (daftar
    nama tool yang boleh dipakai peran ini), tool di LUAR daftar itu DITOLAK di
    sini — bukan hanya disembunyikan dari model. Ini mencegah prompt injection
    (atau model yang menulis `confirm=true` sendiri) memakai tool terlarang.

    PENTING (perbaikan insiden bunuh-diri): JANGAN pernah menurunkan ke
    `handler(args)` tanpa config hanya karena handler melempar `TypeError`.
    Dulu `except TypeError: handler(args)` dipakai untuk handler lama ber-arg
    tunggal — tetapi bila handler 2-arg melempar TypeError DI DALAM (mis.
    allowlist berisi bool → `join` gagal), percobaan ulang tanpa config membuat
    `allowed=[]` → allowlist MATI → perintah berbahaya lolos. Sekarang aritas
    ditentukan dari SIGNATURE, bukan dari menangkap TypeError.
    """
    if tool is None:
        return {"error": "tool tidak dikenal"}
    handler = tool.get("handler")
    if not callable(handler):
        return {"error": "tool tidak punya handler"}
    # GERBANG PERAN (keras). `tool_allow` = None → tanpa batasan (perilaku lama,
    # mis. sub-agent internal & tes). Diisi → fail-closed.
    if isinstance(config, dict):
        ctx = config.get("context") or {}
        allow = ctx.get("tool_allow")
        if allow is not None:
            name = tool.get("name") or (tool.get("schema") or {}).get("name") or ""
            if name not in set(allow):
                role = ctx.get("role") or "?"
                return {"error": (
                    f"tool '{name}' tidak diizinkan untuk peran '{role}'. "
                    "Hubungi owner utama bila kamu butuh akses ini."
                )}
    try:
        if _handler_arity(handler) <= 1:
            result = handler(args)
        else:
            result = handler(args, config)
        if result is None:
            return {"ok": True}
        ctx = (config.get("context") or {}) if isinstance(config, dict) else {}
        return _maybe_pending(tool.get("name") or "", args, result, ctx)
    except Exception as exc:  # noqa: BLE001 - sengaja menangkap semua
        return _friendly_tool_error(tool.get("name") or "unknown", exc)


# Penanda di pesan error tool bahwa aksi DITAHAN karena butuh persetujuan owner.
# Ditemukan di wa.py / owner_ops.py / pterodactyl.py / cron_tool.py / plugin.py.
_CONSENT_HINTS = (
    "ditahan", "belum menyatakan setuju", "minta persetujuan",
    "konfirmasi dulu", "tanpa persetujuan", "butuh persetujuan",
)


def _maybe_pending(name: str, args: dict, result, ctx: dict):
    """Daftarkan aksi TERTUNDA bila tool ditahan oleh gerbang persetujuan.

    Alur alami "bot: mau kuhapus 40 file? → owner: ya" dulu mustahil: gerbang
    `owner_confirmed` hanya membaca pesan yang SAMA, jadi model harus menebak
    dan memanggil ulang dengan confirm=true di turn yang sama.

    Sekarang, ketika tool menjawab "ditahan / minta persetujuan", aksi itu
    disimpan (`agent/consent.ask`) dan pesan berikutnya dari owner yang berisi
    "ya/setuju/gas" akan membuka gerbangnya (lihat agent/main.py → consent.take).
    """
    if not isinstance(result, dict) or not result.get("error"):
        return result
    if not ctx:
        return result
    chat_id = str(ctx.get("chat_id") or "")
    if not chat_id:
        return result
    msg = str(result.get("error") or "")
    low = msg.lower()
    if not any(h in low for h in _CONSENT_HINTS):
        return result
    try:
        from agent import consent
        nonce = consent.ask(chat_id, name, args, reason=msg)
    except Exception:
        return result
    out = dict(result)
    out["pending_confirmation"] = {"tool": name, "nonce": nonce}
    out["hint"] = (
        "Aksi disimpan sebagai permintaan TERTUNDA (kode "
        f"{nonce}). Tanya pemilik dengan singkat: apa yang akan kamu lakukan, "
        "lalu sebut kode itu. Bila pemilik membalas 'ya/setuju/gas', aksi ini "
        "dijalankan otomatis pada pesan berikutnya."
    )
    return out


def _remaining(started, timeout_seconds):
    """Sisa anggaran waktu turn (detik). Selalu > 0."""
    return max(1.0, timeout_seconds - (time.time() - started))


def _final_answer(llm, msgs, timeout_seconds, started):
    """Panggil model SEKALI tanpa tool, supaya ia menulis jawaban akhir.

    Dipakai saat batas langkah/waktu tercapai: alih-alih membalas pesan
    "berhenti" yang hampa, model merangkum hasil yang sudah ada jadi jawaban
    nyata untuk pengguna. Kegagalan apa pun -> string kosong (pemanggil yang
    memutuskan fallback).
    """
    remain = _remaining(started, timeout_seconds)
    # Sisakan margin kecil untuk pengiriman balasan.
    budget = max(1.0, remain - 2)
    try:
        out = llm.chat(
            list(msgs) + [{
                "role": "user",
                "content": "(Sistem) Kamu sudah memakai banyak langkah. Sekarang TULIS "
                           "jawaban AKHIR untuk pengguna berdasarkan hasil yang sudah ada — "
                           "ringkas apa yang sudah dikerjakan dan hasilnya, sertakan bagian "
                           "penting (kode/angka/tautan) yang sudah kamu dapat. Jangan panggil "
                           "tool lagi, cukup tulis jawabannya. JANGAN menulis perintah "
                           "terminal untuk dijalankan pengguna: kalau ada langkah yang belum "
                           "selesai, sebutkan apa yang sudah dicoba dan di mana ia berhenti. "
                           "Tulis dengan suara persona aktifmu, bukan nada laporan robot.",
            }],
            None,
            timeout=budget,
        ) or {}
        return (out.get("content") or "").strip()
    except Exception:
        return ""


def _json_result(result, limit: int = TOOL_RESULT_LIMIT) -> str:
    """Serialisasi hasil tool dengan batas ukuran (jaring terakhir konteks)."""
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps({"ok": True, "note": "hasil tidak bisa diserialisasi"}, ensure_ascii=False)
    return truncate(text, max(4096, int(limit or TOOL_RESULT_LIMIT)))


def _tool_result_limit(config) -> int:
    """Batas hasil tool dari config (`security.toolResultLimitBytes`)."""
    try:
        sec = (config or {}).get("security") or {}
        return int(sec.get("toolResultLimitBytes") or TOOL_RESULT_LIMIT)
    except Exception:
        return TOOL_RESULT_LIMIT


def _call_key(name: str, args: dict) -> str:
    try:
        return name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return name + "|" + str(args)


def run_turn(llm, history, tools, max_iterations=25, timeout_seconds=180,
             on_tool=None, on_reply=None, tool_config=None,
             should_stop=None, on_checkpoint=None, resume_state=None,
             on_step=None, persona_lock=False):
    """Jalankan satu turn.

    Args:
        llm: objek dengan .chat(messages, tools) -> {"content", "tool_calls"}
        history: daftar pesan awal (role user/assistant/system)
        tools: daftar {"name", "schema", "handler"}
        max_iterations: batas jumlah panggilan LLM
        timeout_seconds: batas total waktu
        on_tool: opsional callback(tool_name, args, result) untuk audit
        on_step: opsional callback(dict) — JEJAK LANGKAH untuk UI kaya:
                 {"kind": "think"|"tool", "name", "detail", "ok", "ms"}
        on_reply: opsional callback untuk mengirim balasan bertahap
        should_stop: opsional callback() -> bool; True = pemilik minta berhenti
        on_checkpoint: opsional callback(state: dict) untuk menyimpan progres
        resume_state: opsional dict dari checkpoint sebelumnya untuk melanjutkan
        persona_lock: True = jaga SUARA persona aktif: pengingat sementara
                 sebelum model menulis setelah hasil tool, dan satu kali nudge
                 bila balasan keluar karakter (agent/persona_guard.py).

    Returns:
        {"replies": [{"type": "text", "text": ...}]}
    """
    started = time.time()
    tools = tools or []
    by_name = {(t.get("schema") or {}).get("name", t.get("name")): t for t in tools}
    api_tools = [_tool_schema_for_api(t) for t in tools] or None
    msgs = list(history)
    iteration = 0
    # Berapa kali model boleh mengembalikan KOSONG sebelum kita menyerah.
    # Gateway LLM kadang membalas HTTP 200 dengan content kosong; tanpa retry
    # ini turn berakhir tanpa balasan sama sekali (user melihat bot "diam").
    empty_streak = 0
    MAX_EMPTY = 3
    # Reasoning model yang kehabisan token saat berpikir: content kosong +
    # finish_reason="length". Beri kesempatan tulis ulang (tanpa berpikir).
    length_streak = 0
    MAX_LENGTH = 2
    tried_fallback = False
    # Berapa kali model boleh MELANJUTKAN balasan yang terpotong (finish_reason
    # "length"). Kode panjang (mis. file 200+ baris) tak muat dalam satu
    # max_tokens, jadi potongannya harus disambung — bukan dikirim setengah.
    MAX_CONTINUE = 4
    continue_count = 0
    acc = []  # potongan balasan yang sudah diterima, untuk disambung
    # Anggaran token khusus untuk panggilan ini (None = pakai config). Dinaikkan
    # saat model reasoning kehabisan token untuk berpikir.
    token_budget = None
    # Cache hasil tool per-turn: panggilan identik tidak diulang.
    tool_cache: dict = {}
    # Teks permintaan pengguna terakhir — dipakai gerbang eksekusi untuk tahu
    # apakah ia MEMANG minta teks perintah (mis. "kasih perintahnya, jangan
    # dijalankan"). Tanpa ini, permintaan sah seperti itu ikut dipaksa jalan.
    user_hint = ""
    for _m in reversed(msgs):
        if isinstance(_m, dict) and _m.get("role") == "user":
            user_hint = str(_m.get("content") or "")
            break
    handoff_nudges = 0
    # PERSONA LOCK: satu nudge maksimal per turn (cukup untuk menegur, tidak
    # sampai memicu loop tulis-ulang yang boros token).
    persona_nudges = 0
    _pg = None
    _persona_note = ""
    if persona_lock:
        try:
            from agent import persona_guard as _pg
            _persona_note = _pg.reminder()
        except Exception as _exc:  # noqa: BLE001
            print(f"[loop] persona lock nonaktif: {_exc}", flush=True)
            _pg = None

    def _call_view(messages):
        """Pesan yang DIKIRIM ke model: tambahkan pengingat persona sementara
        bila giliran berikutnya datang tepat setelah hasil tool. Tidak disimpan
        ke riwayat."""
        if not _persona_note or not messages:
            return messages
        last = messages[-1]
        if isinstance(last, dict) and last.get("role") == "tool":
            return list(messages) + [{"role": "user", "content": _persona_note}]
        return messages
    # Setelah gerbang eksekusi menyala, panggilan LLM berikutnya memakai
    # `tool_choice="required"` supaya model WAJIB memanggil tool, bukan menulis
    # perintah lagi. Gateway yang tidak mendukung parameter ini ditangani
    # dengan fallback di bawah (jangan sampai turn gagal karena fitur opsional).
    force_tool_next = False

    # RESUME: pulihkan state dari checkpoint supaya tugas panjang dilanjutkan
    # dari titik terakhir, bukan diulang dari nol. `base_iteration` menyimpan
    # hitungan lama agar checkpoint berikutnya MONOTON (dulu penghitungnya
    # mundur, sehingga `!lanjut` bisa berulang tanpa batas).
    base_iteration = 0
    if resume_state:
        msgs, base_iteration, continue_count, acc, token_budget = _restore_state(resume_state)

    # Anggaran waktu untuk menulis jawaban akhir (lihat docstring modul).
    # Dibatasi maksimum setengah turn: dengan timeout pendek (mis. 10s), reserve
    # 20s akan membuat turn langsung melompat ke jawaban akhir tanpa bekerja.
    reserve = min(max(FINAL_RESERVE_MIN, float(timeout_seconds) * FINAL_RESERVE_RATIO),
                  float(timeout_seconds) * 0.5)

    # EVENT PEMBATALAN: diteruskan ke tool (mis. shell) supaya `!stop` bisa
    # menghentikan perintah yang sedang berjalan, bukan menunggu sampai selesai.
    cancel_event = threading.Event()
    monitor_stop = threading.Event()

    def _monitor():
        """Poll `should_stop` di latar; nyalakan cancel_event saat owner minta stop."""
        while not monitor_stop.wait(0.5):
            try:
                if should_stop and should_stop():
                    cancel_event.set()
                    return
            except Exception:
                return

    monitor = None
    if should_stop is not None:
        monitor = threading.Thread(target=_monitor, daemon=True, name="kwee-cancel-monitor")
        monitor.start()

    def _save_checkpoint():
        """Simpan progres turn (best-effort) supaya bisa dilanjutkan (!lanjut)."""
        if not on_checkpoint:
            return
        try:
            on_checkpoint(_make_state(msgs, iteration, continue_count, acc, token_budget))
        except Exception:
            pass

    def _cancelled():
        """Kembalikan balasan saat pemilik minta berhenti (dengan progres tersimpan)."""
        _save_checkpoint()
        return {"replies": to_replies(CANCEL_MESSAGE), "raw": CANCEL_MESSAGE,
                "cancelled": True}

    def _step(**info):
        """Catat satu langkah untuk ditampilkan di kartu (thinking/tool).

        Callback-nya tidak boleh mematikan turn: kegagalan UI bukan kegagalan
        pekerjaan. Karena itu semua error ditelan di sini.
        """
        if not on_step:
            return
        try:
            on_step(dict(info))
        except Exception:
            pass

    def _call_config(remain_seconds):
        """Config tool + batas waktu yang dipangkas ke sisa anggaran turn.

        SELALU mengembalikan dict: `context.cancel` harus sampai ke tool supaya
        `!stop` bisa menghentikan perintah panjang. `security` hanya diisi bila
        pemanggil menyediakannya — tool shell fail-closed bila bagian itu tidak
        ada (jadi tes/sub-agent tanpa config tidak bisa menjalankan perintah).
        """
        cfg = dict(tool_config) if isinstance(tool_config, dict) else {}
        sec = dict(cfg.get("security") or {})
        if "execTimeoutSeconds" in sec:
            configured = int(sec.get("execTimeoutSeconds") or 120)
            sec["execTimeoutSeconds"] = max(1, min(configured, int(remain_seconds)))
            cfg["security"] = sec
        ctx = dict(cfg.get("context") or {})
        ctx["cancel"] = cancel_event
        cfg["context"] = ctx
        return cfg

    def _exec_calls(tool_calls, remain_seconds):
        """Jalankan tool call; hasil dikembalikan berurutan seperti aslinya."""
        prepared = []
        for call in tool_calls:
            fn = (call.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            prepared.append((call, name, args))

        # Semua tool dalam batch ini harus concurrent agar aman diparalelkan.
        # Satu saja yang non-concurrent (shell/wa/cron) → jalankan berurutan,
        # supaya urutan efek samping tidak berubah.
        all_concurrent = all(
            (by_name.get(n) or {}).get("concurrent", True) for _, n, _ in prepared
        ) and len(prepared) > 1

        cfg = _call_config(remain_seconds)

        def run_one(item):
            call, name, args = item
            tool = by_name.get(name)
            if tool is None:
                return {"error": f"tool tidak dikenal: {name}"}
            # Tool tanpa efek samping (concurrent) yang dipanggil identik lagi
            # → kembalikan hasil sebelumnya, jangan ulangi pekerjaannya.
            key = _call_key(name, args)
            if tool.get("concurrent", True) and key in tool_cache:
                return {"ok": True, "cached": True,
                        "note": "panggilan identik sudah dijalankan di turn ini; hasilnya sama",
                        "result": tool_cache[key]}
            # Emit status SEBELUM tool berjalan. Tanpa ini, UI hanya melihat
            # fase berpikir selama shell/browser/media sedang bekerja lama.
            _detail = ""
            if isinstance(args, dict):
                _detail = str(args.get("command") or args.get("path")
                              or args.get("action") or args.get("goal") or "")[:80]
            _step(kind="tool_start", name=name, detail=_detail, ok=True, event_id=call.get("id", ""))
            _t0 = time.time()
            result = _run_tool(tool, args, cfg)
            _ms = int((time.time() - _t0) * 1000)
            _ok = not (isinstance(result, dict) and result.get("error"))
            _step(kind="tool", name=name, detail=_detail, ok=_ok, ms=_ms, event_id=call.get("id", ""))
            if tool.get("concurrent", True) and _ok:
                tool_cache[key] = result
            return result

        if all_concurrent:
            with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as pool:
                results = list(pool.map(run_one, prepared))
        else:
            results = [run_one(item) for item in prepared]

        for (call, name, args), result in zip(prepared, results):
            if on_tool:
                try:
                    on_tool(name, args, result)
                except Exception:
                    pass
            msgs.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": _json_result(result, _tool_result_limit(tool_config)),
            })

    try:
        for _it in range(max(1, int(max_iterations))):
            # Pembatalan oleh pemilik (perintah !stop) diperiksa TIAP langkah.
            if cancel_event.is_set() or (should_stop and should_stop()):
                return _cancelled()

            elapsed = time.time() - started
            # Sisakan anggaran untuk jawaban akhir: berhenti memulai langkah BARU
            # bila sisa waktu < reserve. Tanpa ini, `_final_answer` dipanggil
            # dengan sisa ≤1 detik dan selalu gagal.
            if elapsed > timeout_seconds - reserve or elapsed > timeout_seconds:
                _save_checkpoint()
                final = _final_answer(llm, msgs, timeout_seconds, started)
                if final:
                    reps = to_replies(final)
                    return {"replies": reps, "raw": replies_to_text(reps),
                            "checkpoint": True}
                resume_msg = (STOP_MESSAGE + "\n\nKetik !lanjut untuk melanjutkan dari "
                              "titik terakhir.")
                return {"replies": to_replies(resume_msg), "raw": resume_msg,
                        "checkpoint": True}

            # Batasi SATU panggilan LLM oleh sisa anggaran turn (dikurangi reserve).
            llm_budget = max(1.0, timeout_seconds - elapsed - reserve)
            _t0_llm = time.time()
            force = bool(force_tool_next and api_tools)
            call_msgs = _call_view(msgs)
            try:
                out = llm.chat(call_msgs, api_tools, timeout=llm_budget,
                               max_tokens=token_budget,
                               **({"tool_choice": "required"} if force else {})) or {}
            except TypeError:
                # Objek LLM tanpa parameter `tool_choice` (mis. sub-agent lama).
                out = llm.chat(call_msgs, api_tools, timeout=llm_budget,
                               max_tokens=token_budget) or {}
            except Exception as exc:  # noqa: BLE001
                if not force:
                    raise
                # Gateway menolak `tool_choice` — jangan gagalkan turn karena
                # fitur opsional; ulangi tanpa paksaan.
                print(f"[loop] tool_choice ditolak gateway ({exc}); ulangi tanpa paksa",
                      flush=True)
                out = llm.chat(call_msgs, api_tools, timeout=llm_budget,
                               max_tokens=token_budget) or {}
            force_tool_next = False
            tool_calls = out.get("tool_calls")
            _step(kind="think", name="berpikir",
                  detail=(f"{len(tool_calls)} tool" if tool_calls else "menyusun jawaban"),
                  ok=True, ms=int((time.time() - _t0_llm) * 1000))
            iteration = base_iteration + _it + 1

            if not tool_calls:
                text = (out.get("content") or "").strip()
                finish = out.get("finish_reason")
                if not text:
                    # Model mengembalikan `content` kosong. Dua sebab umum:
                    #  1) reasoning model kehabisan token DI `reasoning_content`
                    #     (finish_reason="length") — beri token lebih & minta tulis.
                    #  2) gateway membalas finish_reason non-standar ("in_progress")
                    #     atau hiccup — ulangi beberapa kali.
                    # Kalau `reasoning_content` BERISI, pakai itu sebagai jaring
                    # terakhir supaya pengguna tidak dibiarkan tanpa jawaban.
                    reason = (out.get("reasoning_content") or "").strip()
                    if finish == "length":
                        length_streak += 1
                        if length_streak <= MAX_LENGTH:
                            if token_budget is None:
                                token_budget = 16384
                            else:
                                token_budget = min(token_budget * 2, 65536)
                            msgs.append({
                                "role": "user",
                                "content": "(Sistem) Tokenmu habis saat berpikir sehingga "
                                           "jawaban tidak tertulis. JANGAN berpikir panjang — "
                                           "langsung tulis jawaban akhir untuk pengguna "
                                           "(ringkas, boleh sedikit langkah).",
                            })
                            continue
                        if reason:
                            # Model berpikir panjang tapi tak pernah menulis jawaban.
                            # Kirim pikiran itu sebagai jawaban (lebih baik daripada
                            # diam) dan tandai sudah lewat jalur panjang.
                            reps = to_replies(reason)
                            return {"replies": reps, "raw": replies_to_text(reps),
                                    "degraded": "reasoning-only"}
                        # Masih gagal: pakai model lain bila ada, jangan menyerah.
                        if not tried_fallback:
                            tried_fallback = True
                            length_streak = 0
                            msgs.append({
                                "role": "user",
                                "content": "(Sistem) Tulis jawaban akhir singkat untuk pengguna "
                                           "berdasarkan hasil yang sudah ada, tanpa berpikir panjang.",
                            })
                            continue
                        fallback = ("Maaf, jawabannya tidak selesai karena model kehabisan "
                                    "ruang berpikir. Coba pecah pertanyaannya jadi bagian lebih kecil.")
                        return {"replies": to_replies(fallback), "raw": fallback}
                    # Model mengembalikan kosong TANPA tool call. Coba lagi beberapa
                    # kali (kadang hanya hiccup), lalu beri pesan yang jelas supaya
                    # user tidak dibiarkan tanpa jawaban.
                    empty_streak += 1
                    if empty_streak < MAX_EMPTY:
                        msgs.append({
                            "role": "user",
                            "content": "(Sistem) Balasanmu kosong. Lanjutkan dan tulis jawaban "
                                       "akhir untuk pengguna, atau panggil tool bila masih perlu.",
                        })
                        continue
                    if reason:
                        reps = to_replies(reason)
                        return {"replies": reps, "raw": replies_to_text(reps),
                                "degraded": "reasoning-only"}
                    fallback = ("Maaf, otak AI-nya mengembalikan balasan kosong berkali-kali. "
                                "Coba ulangi permintaanmu, atau pecah jadi langkah yang lebih kecil.")
                    return {"replies": to_replies(fallback), "raw": fallback}

                # Balasan TERPOTONG (kehabisan token): minta lanjutkan, lalu
                # sambung potongannya. Ini akar "code terpotong" — sebelumnya
                # potongan itu dikirim apa adanya.
                if finish == "length" and continue_count < MAX_CONTINUE:
                    continue_count += 1
                    msgs.append({"role": "assistant", "content": text})
                    msgs.append({
                        "role": "user",
                        "content": "(Sistem) Balasanmu terpotong karena batas token. LANJUTKAN "
                                   "tepat dari karakter terakhir — jangan ulangi bagian yang sudah "
                                   "ada, jangan buka fence baru bila belum ditutup. Kalau sudah "
                                   "selesai, akhiri dengan penutup yang benar.",
                    })
                    acc.append(text)
                    continue

                if acc:
                    text = "".join(acc) + text

                # GERBANG EKSEKUSI (keluhan "kwee cuma kasih code terminal"):
                # model membalas dengan blok perintah untuk dijalankan pengguna,
                # padahal ia punya tool `terminal`/`shell`. Jangan kirim balasan
                # itu — suntik pesan sistem dan paksa satu langkah lagi.
                if handoff_nudges < MAX_HANDOFF_NUDGE:
                    hit = execution_guard.find_handoff(text, user_hint)
                    if hit:
                        handoff_nudges += 1
                        force_tool_next = True
                        msgs.append({"role": "assistant", "content": text})
                        msgs.append({"role": "user", "content": execution_guard.NUDGE})
                        print(f"[loop] menyerahkan perintah, bukan menjalankan "
                              f"({execution_guard.describe(hit)}); langkah paksa "
                              f"{handoff_nudges}/{MAX_HANDOFF_NUDGE}", flush=True)
                        continue

                # GERBANG PERSONA: balasan mengaku model lain / ditutup boilerplate
                # asisten generik → minta tulis ulang SEKALI dengan suara persona.
                if _pg is not None and persona_nudges < 1:
                    why = _pg.find_break(text, user_hint)
                    if why:
                        persona_nudges += 1
                        msgs.append({"role": "assistant", "content": text})
                        msgs.append({"role": "user", "content": _pg.NUDGE.format(alasan=why)})
                        print(f"[loop] keluar persona ({why}); tulis ulang 1x", flush=True)
                        continue

                if on_reply:
                    try:
                        on_reply(text)
                    except Exception:
                        pass
                # `raw` = teks yang DISIMPAN ke riwayat. Susun ulang dari blok yang
                # sudah di-parse -> Markdown kanonik (fence asli, penanda native
                # sudah dinormalkan). Jadi model meniru Markdown, bukan penanda
                # native lama (mis. mono sebaris ```kick``` yang bikin kartu hampa).
                reps = to_replies(text)
                return {"replies": reps, "raw": replies_to_text(reps)}

            empty_streak = 0
            msgs.append({
                "role": "assistant",
                "content": out.get("content") or "",
                "tool_calls": tool_calls,
            })

            # Jalankan semua tool call; hasil disusun sesuai URUTAN ASLI.
            # Timeout tool (mis. shell) dipangkas ke sisa anggaran turn supaya
            # satu perintah panjang tidak melewati batas router.
            remain = max(1.0, timeout_seconds - (time.time() - started) - reserve)
            _exec_calls(tool_calls, remain)

            # CHECKPOINT berkala: simpan progres tiap langkah supaya bila turn
            # dipotong (timeout/dibatalkan) tugas bisa DILANJUTKAN, bukan diulang.
            _save_checkpoint()

        # Batas langkah tercapai: simpan checkpoint + minta jawaban akhir, bukan
        # pesan "berhenti" hampa.
        _save_checkpoint()
        final = _final_answer(llm, msgs, timeout_seconds, started)
        if final:
            reps = to_replies(final)
            return {"replies": reps, "raw": replies_to_text(reps), "checkpoint": True}
        resume_msg = STOP_MESSAGE + "\n\nKetik !lanjut untuk melanjutkan dari titik terakhir."
        return {"replies": to_replies(resume_msg), "raw": resume_msg, "checkpoint": True}
    finally:
        monitor_stop.set()
        if monitor is not None:
            monitor.join(timeout=1.0)
