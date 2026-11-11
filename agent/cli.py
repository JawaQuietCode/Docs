"""cli.py — `kwee`, CLI milik agent (bukan perintah chat).

Semua yang bisa dilakukan bot dari WhatsApp bisa dilakukan dari shell, karena
keduanya memakai lapisan yang sama: `agent/config.py` → tool → engine.

    kwee tool download '{"url": "…"}'     # panggil tool Kwee dari shell
    kwee knowledge status                 # status knowledge workbench
    kwee knowledge claim_add --json '{...}'  # simpan klaim lokal CLI
    kwee install                          # pasang engine OpenHands (bawaan)
    kwee ask "perbaiki test yang gagal"   # engine OpenHands (agent-canvas)
    kwee canvas status|start|stop         # kelola stack agent-canvas
    kwee shell "ls -la agent"             # jalankan perintah lewat tool shell
    kwee mechanisms                       # engine apa saja yang tersedia
    kwee diag                             # setelan yang benar-benar berlaku
    kwee serve                            # jalankan inti agent (uvicorn)

`kwee ask` sengaja TIDAK memakai loop native: engine canvas sudah punya
agent-server sendiri (tool, shell, editor). Kalau `agent-canvas` belum
terpasang, perintahnya berhenti dengan instruksi pemasangan — bukan diam-diam
jatuh ke loop lain yang perilakunya berbeda.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _config() -> dict:
    try:
        sys.path.insert(0, str(ROOT))
        from agent.config import load_config  # type: ignore
        return load_config()
    except Exception as exc:  # noqa: BLE001
        print(f"[kwee] config tidak terbaca ({exc}); memakai setelan minimal", file=sys.stderr)
        return {"root": str(ROOT), "security": {"accessMode": "owner"}}


# --------------------------------------------------------------------------
# subperintah
# --------------------------------------------------------------------------
def cmd_install(args) -> int:
    """Pasang engine OpenHands: paket npm global + uv/uvx."""
    if shutil.which("agent-canvas") and shutil.which("uvx"):
        print("agent-canvas dan uvx sudah terpasang.")
        return 0
    if not shutil.which("npm"):
        print("npm tidak ditemukan. Pasang Node >= 24 dulu.", file=sys.stderr)
        return 3
    if not shutil.which("agent-canvas"):
        print("[kwee] npm install -g @openhands/agent-canvas …", flush=True)
        rc = subprocess.call(["npm", "install", "-g", "@openhands/agent-canvas"], cwd=str(ROOT))
        if rc != 0:
            print("[kwee] pemasangan agent-canvas gagal", file=sys.stderr)
            return rc
    if not shutil.which("uvx"):
        print("[kwee] memasang uv (untuk agent-server) …", flush=True)
        if shutil.which("curl"):
            rc = subprocess.call("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True, cwd=str(ROOT))
            if rc != 0:
                print("[kwee] uv gagal dipasang; agent-server butuh uvx", file=sys.stderr)
                return rc
        else:
            print("curl tidak ada; pasang uv manual: https://docs.astral.sh/uv/", file=sys.stderr)
            return 3
    print("selesai. Cek: kwee mechanisms")
    return 0


def cmd_ask(args) -> int:
    from agent import canvas_engine
    cfg = _config()
    tugas = " ".join(args.task).strip()
    if not tugas:
        print('pakai: kwee ask "tugasnya"', file=sys.stderr)
        return 2
    if not canvas_engine.binary():
        print("agent-canvas belum terpasang. Jalankan: kwee install\n"
              "  (setara: npm install -g @openhands/agent-canvas + uv)", file=sys.stderr)
        return 3
    hasil = canvas_engine.ask(tugas, cfg, timeout=args.timeout or None)
    if hasil.get("ok"):
        print(hasil.get("text") or "")
        return 0
    print(f"[kwee] gagal: {hasil.get('error')}", file=sys.stderr)
    return 1


def cmd_canvas(args) -> int:
    from agent import canvas_engine
    cfg = _config()
    aksi = args.action or "status"
    if aksi == "status":
        print(json.dumps(canvas_engine.status(cfg), ensure_ascii=False, indent=2))
        return 0
    if aksi == "start":
        print(json.dumps(canvas_engine.ensure_stack(cfg), ensure_ascii=False, indent=2))
        return 0
    if aksi == "stop":
        print(json.dumps(canvas_engine.stop(cfg), ensure_ascii=False, indent=2))
        return 0
    print("aksi: status | start | stop", file=sys.stderr)
    return 2


def cmd_shell(args) -> int:
    """Jalankan perintah lewat tool shell — policy, audit, dan batasnya sama."""
    from tools.builtin import shell as shell_tool
    cfg = _config()
    perintah = " ".join(args.command).strip()
    if not perintah:
        print('pakai: kwee shell "ls -la"', file=sys.stderr)
        return 2
    hasil = shell_tool.handler({"command": perintah, "cwd": args.cwd or None}, cfg)
    if hasil.get("error"):
        print(hasil["error"], file=sys.stderr)
        return 1
    out = hasil.get("stdout") or ""
    err = hasil.get("stderr") or ""
    if out:
        sys.stdout.write(out if out.endswith("\n") else out + "\n")
    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    return int(hasil.get("exit_code") or 0)


def cmd_tool(args) -> int:
    """Panggil tool Kwee dari shell — dipakai engine OpenHands lewat `bash`.

    Engine OpenHands tidak punya akses langsung ke tool Python (wa, download,
    plugin_run, files, …). Perintah ini menjembatani: satu tool, argumen JSON,
    hasil JSON. Aturannya sama dengan saat bot memanggil tool itu sendiri.
    """
    from tools import registry
    cfg = _config()
    registry.discover()
    nama = str(args.name or "").strip()
    tersedia = {t["name"] for t in registry.all_tools()}
    if nama not in tersedia:
        print(json.dumps({"error": f"tool '{nama}' tidak ada", "tersedia": sorted(tersedia)}, ensure_ascii=False))
        return 2
    mentah = " ".join(args.args or []).strip() or "{}"
    try:
        data = json.loads(mentah)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"argumen bukan JSON: {exc}"}, ensure_ascii=False))
        return 2
    for t in registry.all_tools():
        if t["name"] == nama:
            hasil = t["handler"](data, cfg)
            print(json.dumps(hasil, ensure_ascii=False, default=str)[:200000])
            return 0 if not (isinstance(hasil, dict) and hasil.get("error")) else 1
    return 2



def cmd_knowledge(args) -> int:
    """Gunakan knowledge workbench native pada ruang lokal khusus CLI."""
    from tools import registry
    registry.discover()
    tool = registry.get("knowledge_engine")
    if not tool:
        print(json.dumps({"ok": False, "error": "knowledge_engine tidak terdaftar"}, ensure_ascii=False))
        return 3
    try:
        payload = json.loads(args.payload or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"JSON tidak valid: {exc}"}, ensure_ascii=False))
        return 2
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "--json harus berisi objek JSON"}, ensure_ascii=False))
        return 2

    cfg = _config()
    local_root = Path(cfg.get("root") or ROOT).expanduser().resolve() / "data" / "cli"
    context = dict(cfg.get("context") or {})
    # CLI sessions have an explicit isolated store, never reuse another chat's
    # tenant root or accept a database path from JSON input.
    context.update({"tenant_root": str(local_root), "tenant": "local-cli"})
    cfg = {**cfg, "context": context}
    payload["action"] = args.action
    result = tool["handler"](payload, cfg)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0 if isinstance(result, dict) and result.get("ok") else 1

def cmd_progress_html(args) -> int:
    """Ubah snapshot progres JSON menjadi dokumen HTML mandiri."""
    from agent.progres_html import simpan_html
    try:
        if args.input == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.input).expanduser().read_text(encoding="utf-8")
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError("snapshot progres harus berupa objek JSON")
        path = simpan_html(state, args.output)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"[kwee] gagal membuat HTML progres: {exc}", file=sys.stderr)
        return 2
    print(f"HTML progres tersimpan: {path}")
    return 0


def cmd_mechanisms(_args) -> int:
    from agent import mechanisms
    av = mechanisms.available()
    for nama, siap in av.items():
        tanda = "siap" if siap else "tidak ada"
        print(f"{nama:8} {tanda}")
    return 0


def cmd_diag(_args) -> int:
    cfg = _config()
    sec = cfg.get("security") or {}
    llm = cfg.get("llm") or {}
    ui = cfg.get("ui") or {}
    canvas = (cfg.get("mechanisms") or {}).get("canvas") or {}
    try:
        from agent.version import AGENT_VERSION
    except Exception:
        AGENT_VERSION = "?"
    baris = {
        "agentVersion": AGENT_VERSION,
        "root": cfg.get("root"),
        "accessMode": sec.get("accessMode", "owner"),
        "guardSelfKill": sec.get("guardSelfKill", True),
        "outputLimitBytes": sec.get("outputLimitBytes"),
        "toolResultLimitBytes": sec.get("toolResultLimitBytes"),
        "execTimeoutSeconds": sec.get("execTimeoutSeconds"),
        "model": llm.get("model"),
        "maxTokens": llm.get("maxTokens"),
        "llmTimeout": llm.get("timeoutSeconds"),
        "forceCard": ui.get("forceCard"),
        "engine": (cfg.get("engine") or {}).get("mode", "openhands"),
        "canvasPort": canvas.get("port"),
        "canvasBinary": shutil.which("agent-canvas") or "",
    }
    print(json.dumps(baris, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args) -> int:
    host = args.host or os.environ.get("AGENT_HOST", "127.0.0.1")
    port = str(args.port or os.environ.get("AGENT_PORT", "8787"))
    cmd = [sys.executable, "-m", "uvicorn", "agent.main:app",
           "--host", host, "--port", port, "--workers", "1"]
    try:
        from agent.version import version_line
        print(f"[kwee] {version_line()}", flush=True)
    except Exception:
        pass
    print("[kwee] " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kwee", description="CLI agent (engine OpenHands / agent-canvas)")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("ask", help="jalankan satu tugas lewat engine canvas")
    a.add_argument("task", nargs="+")
    a.add_argument("--timeout", type=int, default=0)
    a.set_defaults(fn=cmd_ask)

    c = sub.add_parser("canvas", help="kelola stack agent-canvas")
    c.add_argument("action", nargs="?", default="status", choices=["status", "start", "stop"])
    c.set_defaults(fn=cmd_canvas)

    s = sub.add_parser("shell", help="jalankan perintah shell lewat tool agent")
    s.add_argument("command", nargs="+")
    s.add_argument("--cwd", default="")
    s.set_defaults(fn=cmd_shell)

    t = sub.add_parser("tool", help="panggil tool Kwee dari shell (argumen JSON)")
    t.add_argument("name")
    t.add_argument("args", nargs="*")
    t.set_defaults(fn=cmd_tool)

    k = sub.add_parser("knowledge", help="kelola knowledge workbench native (database CLI terisolasi)")
    k.add_argument("action", nargs="?", default="status", choices=[
        "status", "claim_add", "claim_search", "claim_reconcile", "claim_revise",
        "claim_link", "claim_provenance", "claim_reconstruct", "research_create",
        "hypothesis_add", "study_design", "observation_add", "research_report", "welch_test",
    ])
    k.add_argument("--json", dest="payload", default="{}", help="objek JSON untuk parameter action")
    k.set_defaults(fn=cmd_knowledge)

    sub.add_parser("install", help="pasang engine OpenHands (agent-canvas + uv)").set_defaults(fn=cmd_install)
    sub.add_parser("mechanisms", help="daftar engine dan ketersediaannya").set_defaults(fn=cmd_mechanisms)
    sub.add_parser("diag", help="cetak setelan efektif").set_defaults(fn=cmd_diag)

    ph = sub.add_parser("progress-html", help="ubah snapshot progres JSON menjadi file HTML mandiri")
    ph.add_argument("input", help="file JSON hasil Progres.ke_dict(), atau '-' untuk stdin")
    ph.add_argument("-o", "--output", default="progress-kwee.html", help="tujuan file .html")
    ph.set_defaults(fn=cmd_progress_html)

    v = sub.add_parser("serve", help="jalankan inti agent (uvicorn)")
    v.add_argument("--host", default="")
    v.add_argument("--port", type=int, default=0)
    v.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    if not getattr(args, "fn", None):
        p.print_help()
        return 2
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
