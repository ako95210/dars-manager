from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from backend.app.pipeline import generate_cover, render_static_video, write_analysis
from drsm_core import CoursePart, TranscriptSegment, audio_duration


class PipelineTests(unittest.TestCase):
    def test_cover_and_static_video_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            cover = root / "cover.png"
            video = root / "video.mp4"

            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 8000)

            generate_cover(cover, "Titre du cours", "1 partie · 00:01")
            with Image.open(cover) as image:
                self.assertEqual(image.size, (1280, 720))

            render_static_video(cover, audio, video)
            self.assertTrue(video.is_file())
            self.assertGreater(video.stat().st_size, 1000)
            self.assertGreater(audio_duration(video), 0.9)

    def test_analysis_does_not_persist_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis = root / "analysis.json"
            source = root / "private-course.wav"
            segments = [TranscriptSegment(0, 10, "Contenu du cours")]
            parts = [CoursePart(1, 0, 10, "Cours", "Description", "Contenu du cours")]

            write_analysis(analysis, source, segments, parts)
            payload = json.loads(analysis.read_text(encoding="utf-8"))
            self.assertEqual(payload["audio_name"], "private-course.wav")
            self.assertNotIn("audio", payload)


if __name__ == "__main__":
    unittest.main()
