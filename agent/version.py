"""version.py — satu sumber versi untuk sisi Python (agent/).

`package.json` memegang versi sisi Node (transport/). Angka di sini HARUS
disamakan manual setiap rilis — tidak ada build step yang menyatukan
keduanya, jadi `!diag` dan log startup menampilkan dua-duanya supaya
ketidakcocokan kelihatan, bukan diam-diam berbeda.
"""
from __future__ import annotations

AGENT_VERSION = "0.4.0"

# Changelog singkat versi ini (dibaca `!diag` / dicetak saat start.sh berjalan).
CHANGELOG = (
    "0.4.0: vendor upstream AI Engineering from Scratch tutor skills for native on-demand learning; bootstrap Free Claude Code CLI best-effort at startup; native peers retain Kwee provider and tenant learning. "
    "0.3.0: native peer/team roles (peer, researcher, architect, critic, mentor, builder) "
    "using the same trusted provider configuration; tenant-shared peer learning notes "
    "with unverified provenance; recursive nested Agent Skills discovery; verified prompt "
    "injection status for native and OpenHands paths; feminine Velvet Noir progress UI and "
    "immersive final dossier. "
    "0.2.2: adaptive native agent learning, preference feedback, reward-aware tool/skill "
    "routing, per-turn prompt adaptation, browser intelligence, and statistical reasoning. "
    "0.2.1: FastAPI/Starlette startup fixes, persona lock, and Aurora Editorial."
)


def version_line() -> str:
    return f"kwee-agentic v{AGENT_VERSION}"
