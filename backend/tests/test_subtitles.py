from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from PIL import Image

from backend.app.rendering import remap_subtitles, subtitle_frame
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

    def test_subtitle_frame_only_changes_active_interval(self) -> None:
        blank = Image.new("RGB", (640, 360), "#ffffff")
        cues = {"font": "sans", "color": "#ffffff", "cues": [{"start": 1, "end": 2, "text": "Texte"}]}
        self.assertEqual(subtitle_frame(blank, 0.5, cues).tobytes(), blank.tobytes())
        self.assertNotEqual(subtitle_frame(blank, 1.5, cues).tobytes(), blank.tobytes())

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
