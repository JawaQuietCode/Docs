import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import video_vision


class VideoVisionTests(unittest.TestCase):
    def test_missing_video_returns_clear_error_without_calling_model(self):
        with patch.object(video_vision, "resolve_video", return_value=""):
            result = video_vision.analyze_video("missing.mp4")
        self.assertIn("tidak ditemukan", result["error"])

    def test_video_samples_frames_and_passes_them_to_vision(self):
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("ffmpeg/ffprobe not installed")
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "sample.mp4"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc=duration=2:size=320x240:rate=5", "-pix_fmt", "yuv420p", str(video),
            ], check=True, capture_output=True, timeout=30)
            with patch("tools.builtin.vision.handler", return_value={"text": "frame terlihat"}) as vision:
                result = video_vision.analyze_video(str(video), "jelaskan", max_frames=3)
            self.assertTrue(result["ok"], result)
            self.assertGreaterEqual(len([x for x in result["frames"] if x.get("text")]), 2)
            self.assertIn("frame terlihat", result["text"])
            self.assertGreaterEqual(vision.call_count, 2)


if __name__ == "__main__":
    unittest.main()
