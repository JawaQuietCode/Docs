"""Extract representative frames from a video and analyze them with Vision."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def resolve_video(source: str) -> str:
    ref = str(source or "").strip()
    if not ref:
        return ""
    if ref.startswith(("http://", "https://")):
        return ref
    try:
        from media import store as media_store
        found = media_store.resolve_path(ref)
        if found:
            return str(found)
    except Exception:
        pass
    path = Path(ref)
    return str(path) if path.is_file() else ""


def analyze_video(source: str, prompt: str = "Ringkas isi video ini.", max_frames: int = 4) -> dict:
    path = resolve_video(source)
    if not path or path.startswith(("http://", "https://")):
        return {"error": "video lokal tidak ditemukan; unduh media WhatsApp terlebih dahulu"}
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return {"error": "ffmpeg dan ffprobe wajib tersedia untuk membaca video"}
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            check=True, capture_output=True, text=True, timeout=20,
        )
        duration = max(0.1, float(probe.stdout.strip()))
    except Exception as exc:
        return {"error": f"metadata video gagal dibaca: {exc}"}

    count = max(1, min(6, int(max_frames or 4)))
    times = sorted({round(duration * i / count, 2) for i in range(count)})
    from tools.builtin import vision as vision_tool
    frames = []
    with tempfile.TemporaryDirectory(prefix="kwee-video-vision-") as tmp:
        for index, stamp in enumerate(times, 1):
            frame = Path(tmp) / f"frame-{index}.jpg"
            try:
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(stamp), "-i", path,
                     "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2", "-q:v", "5", str(frame)],
                    check=True, capture_output=True, timeout=30,
                )
                result = vision_tool.handler({
                    "path": str(frame),
                    "prompt": f"Frame {index} pada detik {stamp:.1f} dari video. {prompt}. Jelaskan hanya hal yang terlihat pada frame ini.",
                    "detail": "low",
                })
                if result.get("text"):
                    frames.append({"timestamp": stamp, "text": result["text"]})
            except Exception as exc:
                frames.append({"timestamp": stamp, "error": str(exc)[:180]})
    if not any(x.get("text") for x in frames):
        return {"error": "tidak ada frame video yang berhasil dianalisis", "frames": frames}
    summary = "\n".join(f"[{x['timestamp']:.1f}s] {x['text']}" for x in frames if x.get("text"))
    return {"ok": True, "duration_seconds": duration, "frames": frames, "text": summary}
