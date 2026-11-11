"""Tool for explicit video understanding via sampled frames."""
from tools.registry import register

SCHEMA = {
    "description": (
        "Analisis video WhatsApp yang sudah tersimpan sebagai aset. Tool ini "
        "mengekstrak beberapa frame dengan ffmpeg lalu menganalisisnya memakai Vision. "
        "Gunakan hanya ketika pengguna secara eksplisit meminta video diperiksa."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "asset": {"type": "string", "description": "ID aset media m_xxxx atau path lokal video"},
            "prompt": {"type": "string", "description": "Pertanyaan tentang isi video"},
            "max_frames": {"type": "integer", "description": "Jumlah frame sampel (1-6, default 4)"},
        },
        "required": ["asset"],
    },
}


def handler(args, config=None):
    args = args or {}
    from agent.video_vision import analyze_video
    return analyze_video(
        str(args.get("asset") or args.get("path") or ""),
        str(args.get("prompt") or "Ringkas isi video ini."),
        max_frames=max(1, min(int(args.get("max_frames") or 4), 6)),
    )


register("video_vision", SCHEMA, handler, toolset="core", dangerous=False)
