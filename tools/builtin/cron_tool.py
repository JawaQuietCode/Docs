"""cron_tool.py — tool `cron`: jadwalkan tugas berkala.

Eksekusi job ada di sisi Node (`transport/scheduler.js`) karena di sana socket
WhatsApp berada; modul ini hanya mengelola daftar job (lihat scheduler/cron.py).
Tool ini OWNER-ONLY dan mengubah keadaan → butuh konfirmasi pemilik.
"""
import re

from security.audit import audit
from scheduler import cron as cronlib
from tools.registry import register

SCHEMA = {
    "description": (
        "Kelola tugas terjadwal (cron). Aksi: add (tambah job), list (daftar), "
        "remove (hapus), enable/disable. Ekspresi cron 5-field: "
        "'menit jam tanggal bulan hari' (mis. '0 8 * * *' = tiap hari 08:00). "
        "kind: send (kirim teks ke chat), agent (minta AI mengerjakan tugas lalu "
        "kirim hasilnya), plugin (jalankan plugin JS di plugins/)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "remove", "enable", "disable"]},
            "when": {"type": "string", "description": "Ekspresi cron 5-field (untuk action=add)"},
            "kind": {"type": "string", "enum": ["send", "agent", "plugin"], "description": "Jenis job"},
            "args": {
                "type": "object",
                "description": (
                    "Argumen job. Untuk kind=send: {to, text}. "
                    "kind=agent: {to, prompt}. kind=plugin: {name, args}."
                ),
            },
            "job_id": {"type": "string", "description": "ID job (untuk remove/enable/disable)"},
        },
        "required": ["action"],
    },
}

# Aksi yang mengubah daftar job.
DESTRUCTIVE = {"add", "remove", "enable", "disable"}
_DIRECT_ADD_RE = re.compile(
    r"\b(jadwalkan|jadwalkanlah|buat(?:kan)?\s+(?:jadwal|cron|job)|"
    r"tambahkan\s+(?:jadwal|cron|job)|schedule|create\s+(?:a\s+)?(?:schedule|cron|job))\b",
    re.I,
)


def _is_direct_add_request(text: str) -> bool:
    """True only for a direct owner request to create a scheduled job."""
    return bool(_DIRECT_ADD_RE.search(str(text or "")))


def handler(args, config=None):
    args = args or {}
    action = str(args.get("action", "")).strip().lower()
    if action not in {"add", "list", "remove", "enable", "disable"}:
        return {"error": "action tidak dikenal (add/list/remove/enable/disable)"}

    if action in DESTRUCTIVE:
        ctx = (config or {}).get("context") or {}
        # Permintaan owner yang secara eksplisit meminta penjadwalan adalah
        # otorisasi untuk membuat job itu saja; tidak perlu dialog konfirmasi
        # kedua. Penghapusan/perubahan job tetap memakai konfirmasi eksplisit.
        request = str(ctx.get("owner_request_text") or "").lower()
        direct_add = action == "add" and _is_direct_add_request(request)
        if not ctx.get("owner_confirmed") and not direct_add:
            return {
                "error": (
                    "mengubah jadwal ditahan: pemilik belum menyatakan setuju di pesan "
                    "terakhirnya. Konfirmasi dulu ('Setuju saya buat job ini?'), "
                    "tunggu jawaban ya, baru ulangi."
                )
            }

    if action == "list":
        jobs = cronlib.list_jobs(config)
        return {"ok": True, "count": len(jobs), "jobs": jobs}

    if action == "add":
        when = str(args.get("when", "")).strip()
        kind = str(args.get("kind", "")).strip().lower()
        if kind not in {"send", "agent", "plugin"}:
            return {"error": "kind harus salah satu: send / agent / plugin"}
        job_args = args.get("args") or {}
        if kind in {"send", "agent"} and not job_args.get("to"):
            # Isi otomatis dari percakapan ini bila tidak disebutkan.
            job_args["to"] = ((config or {}).get("context") or {}).get("chat_id")
        if kind == "send" and not str(job_args.get("text", "")).strip():
            return {"error": "kind=send butuh args.text"}
        if kind == "agent" and not str(job_args.get("prompt", "")).strip():
            return {"error": "kind=agent butuh args.prompt"}
        if kind == "plugin" and not str(job_args.get("name", "")).strip():
            return {"error": "kind=plugin butuh args.name"}
        try:
            job = cronlib.add_job(when, kind, job_args, config)
        except cronlib.CronError as exc:
            return {"error": f"ekspresi cron tidak sah: {exc}"}
        audit("cron.add", job=job["id"], when=when, kind=kind)
        return {"ok": True, "job": job, "human": cronlib.describe(when)}

    job_id = str(args.get("job_id", "")).strip()
    if not job_id:
        return {"error": "job_id wajib"}
    if action == "remove":
        ok = cronlib.remove_job(job_id, config)
        audit("cron.remove", job=job_id, ok=ok)
        return {"ok": ok, "removed": job_id} if ok else {"error": f"job {job_id} tidak ditemukan"}
    # enable / disable
    job = cronlib.set_enabled(job_id, action == "enable", config)
    if job is None:
        return {"error": f"job {job_id} tidak ditemukan"}
    audit("cron.toggle", job=job_id, enabled=job["enabled"])
    return {"ok": True, "job": job}


register("cron", SCHEMA, handler, toolset="core", dangerous=True)
