from __future__ import annotations

import json
import unittest

from backend.app.semantic_analysis import (
    OpenAISemanticAnalyzer,
    SemanticAnalysisError,
    estimate_semantic_tokens,
)
from drsm_core import TranscriptSegment


class FakeResponses:
    def __init__(self, chapters: list[dict]) -> None:
        self.arguments = None
        self.chapters = chapters

    def create(self, **kwargs):
        self.arguments = kwargs
        return type(
            "Response",
            (),
            {
                "output_text": json.dumps({"chapters": self.chapters}),
                "usage": type("Usage", (), {"input_tokens": 1234, "output_tokens": 234})(),
                "_request_id": "req_semantic_test",
            },
        )()


class FakeClient:
    def __init__(self, chapters: list[dict]) -> None:
        self.responses = FakeResponses(chapters)


class SemanticAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            TranscriptSegment(0, 60, "Définition du premier sujet."),
            TranscriptSegment(60, 120, "Explication et exemple du premier sujet."),
            TranscriptSegment(120, 180, "Passage à une question différente."),
            TranscriptSegment(180, 240, "Réponse détaillée à cette question."),
        ]

    def test_structured_chapters_become_contiguous_course_parts(self) -> None:
        client = FakeClient([
            {
                "start_segment": 0,
                "end_segment": 1,
                "title": "Comprendre le premier sujet",
                "description": "Le cours définit le sujet et donne un exemple.",
            },
            {
                "start_segment": 2,
                "end_segment": 3,
                "title": "Une nouvelle question expliquée",
                "description": "Une question distincte est introduite puis traitée.",
            },
        ])
        analyzer = OpenAISemanticAnalyzer(api_key="", client=client)

        result = analyzer.analyze(self.segments, "fr")

        self.assertEqual(len(result.parts), 2)
        self.assertEqual(result.parts[0].start, 0)
        self.assertEqual(result.parts[0].end, 120)
        self.assertEqual(result.parts[1].start, 120)
        self.assertEqual(result.parts[1].end, 240)
        self.assertIn("premier sujet", result.parts[0].transcript)
        self.assertEqual(result.call.input_tokens, 1234)
        self.assertEqual(result.call.output_tokens, 234)
        self.assertEqual(result.call.request_id, "req_semantic_test")
        self.assertFalse(client.responses.arguments["store"])
        self.assertEqual(
            client.responses.arguments["text"]["format"]["type"],
            "json_schema",
        )

    def test_non_contiguous_model_output_is_rejected(self) -> None:
        client = FakeClient([
            {
                "start_segment": 1,
                "end_segment": 3,
                "title": "Chapitre incomplet",
                "description": "Le premier segment a été oublié.",
            }
        ])
        analyzer = OpenAISemanticAnalyzer(api_key="", client=client)
        with self.assertRaises(SemanticAnalysisError):
            analyzer.analyze(self.segments, "fr")

    def test_quote_estimate_scales_with_duration(self) -> None:
        short = estimate_semantic_tokens(60)
        long = estimate_semantic_tokens(3600)
        self.assertGreater(long[0], short[0])
        self.assertGreater(long[1], short[1])


if __name__ == "__main__":
    unittest.main()
