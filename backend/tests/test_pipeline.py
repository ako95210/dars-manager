from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path

from PIL import Image

from backend.app.pipeline import generate_cover, render_static_video, write_analysis
from backend.app.rendering import compose_cover, render_animated_video
from drsm_core import CoursePart, TranscriptSegment, audio_duration, export_clips


class PipelineTests(unittest.TestCase):
    def test_template_cover_and_animated_video_are_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "audio.wav"
            background = root / "background.png"
            animated_source = root / "animated-source.mp4"
            cover = root / "rendered-cover.png"
            video = root / "rendered-video.mp4"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 8000)
            Image.new("RGB", (640, 360), "#17362c").save(background)
            render_static_video(background, audio, animated_source)
            zones = [{
                "kind": "title",
                "x": 0.1,
                "y": 0.65,
                "width": 0.8,
                "height": 0.2,
                "font_scale": 0.06,
                "color": "#ffffff",
                "align": "center",
            }]
            compose_cover(
                background,
                cover,
                source_kind="image",
                frame_seconds=0,
                output_format="1:1",
                zones=zones,
                values={"title": "Nouveau cours"},
            )
            with Image.open(cover) as rendered:
                self.assertEqual(rendered.size, (1080, 1080))
            render_animated_video(
                animated_source,
                audio,
                video,
                output_format="16:9",
                zones=zones,
                values={"title": "Nouveau cours"},
            )
            self.assertTrue(video.is_file())
            self.assertGreater(video.stat().st_size, 1000)
            self.assertGreater(audio_duration(video), 0.9)

    def test_non_contiguous_audio_ranges_are_concatenated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            selection = root / "selection.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b"\x00\x00" * 8000 * 3)

            export_clips(source, selection, [(0.0, 0.5), (2.0, 2.5)])

            self.assertTrue(selection.is_file())
            self.assertGreater(selection.stat().st_size, 1000)
            self.assertGreater(audio_duration(selection), 0.8)
            self.assertLess(audio_duration(selection), 1.3)

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
