from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from pathlib import Path

from PIL import Image, ImageDraw

from backend.app.rendering import FORMAT_SIZES, remap_subtitles, subtitle_cue_indices, subtitle_frame, subtitle_layout
from backend.app.semantic_analysis import OpenAISemanticAnalyzer, SemanticAnalysisError


class SubtitleTests(unittest.TestCase):
    def test_remap_across_selected_audio_ranges(self) -> None:
        source = {"font": "sans", "color": "#ffffff", "cues": [
            {"start": 3, "end": 6, "text": "Un"},
            {"start": 12, "end": 15, "text": "Deux"},
        ]}
        remapped = remap_subtitles(source, [(4, 8), (10, 14)])
        self.assertEqual(remapped["cues"], [
            {"start": 0, "end": 2, "text": "Un"},
            {"start": 6, "end": 8, "text": "Deux"},
        ])
        self.assertEqual(source["cues"][0]["start"], 3)

    def test_selected_audio_limits_subtitle_cues(self) -> None:
        source = {"cues": [
            {"start": 3, "end": 6, "text": "Un"},
            {"start": 12, "end": 15, "text": "Deux"},
            {"start": 30, "end": 35, "text": "Trois"},
        ]}
        self.assertEqual(subtitle_cue_indices(source, [(10, 20)]), [1])

    def test_subtitle_frame_only_changes_active_interval(self) -> None:
        blank = Image.new("RGB", (640, 360), "#ffffff")
        cues = {"font": "sans", "color": "#ffffff", "cues": [{"start": 1, "end": 2, "text": "Texte"}]}
        self.assertEqual(subtitle_frame(blank, 0.5, cues).tobytes(), blank.tobytes())
        self.assertNotEqual(subtitle_frame(blank, 1.5, cues).tobytes(), blank.tobytes())

    def test_long_subtitle_is_fitted_without_truncation_in_every_format(self) -> None:
        text = " ".join(f"expression-{index}" for index in range(80))
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        for width, height in FORMAT_SIZES.values():
            image = Image.new("RGB", (width, height), "#ffffff")
            caption, _font, bounds, _spacing = subtitle_layout(
                ImageDraw.Draw(image), text, font_path, width, height
            )
            self.assertEqual(caption.replace("\n", " ").split(), text.split())
            self.assertLessEqual(bounds[2] - bounds[0], round(width * 0.84))
            self.assertLessEqual(bounds[3] - bounds[1], round(height * 0.34))

    def test_proofreader_preserves_number_and_order(self) -> None:
        class Client:
            responses = SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(
                output_text=json.dumps({"items": [{"index": 1, "text": "Deux."}, {"index": 0, "text": "Un."}]}),
                usage={"input_tokens": 10, "output_tokens": 8},
                _request_id="request-1",
            ))
        corrected, call = OpenAISemanticAnalyzer(api_key="", client=Client()).proofread_subtitles(["un", "deux"], "fr")
        self.assertEqual(corrected, ["Un.", "Deux."])
        self.assertEqual(call.input_tokens, 10)

        class IncompleteClient:
            responses = SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(output_text=json.dumps({"items": []})))
        with self.assertRaises(SemanticAnalysisError):
            OpenAISemanticAnalyzer(api_key="", client=IncompleteClient()).proofread_subtitles(["un"], "fr")
