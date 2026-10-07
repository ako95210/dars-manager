from __future__ import annotations

import json
import unittest

from backend.app.semantic_analysis import (
    OpenAISemanticAnalyzer,
    SemanticAnalysisError,
    estimate_proofreading_tokens,
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

        proofread_short = estimate_proofreading_tokens(60)
        proofread_long = estimate_proofreading_tokens(3600)
        self.assertGreater(proofread_long[0], proofread_short[0])
        self.assertGreater(proofread_long[1], proofread_short[1])

    def test_transcript_proofreading_preserves_order_without_translation(self) -> None:
        class ProofreadResponses:
            def __init__(self) -> None:
                self.arguments = None

            def create(self, **kwargs):
                self.arguments = kwargs
                return type(
                    "Response",
                    (),
                    {
                        "output_text": json.dumps({
                            "items": [
                                {"index": 0, "text": "Première phrase corrigée.", "uncertain": False, "uncertainty_reason": ""},
                                {"index": 1, "text": "Deuxième phrase [inaudible].", "uncertain": False, "uncertainty_reason": ""},
                            ],
                            "glossary_terms": ["ahl as-Sunna", "Sunna"],
                        }),
                        "usage": type("Usage", (), {"input_tokens": 40, "output_tokens": 30})(),
                        "_request_id": "req_proofread_test",
                    },
                )()

        responses = ProofreadResponses()
        client = type("ProofreadClient", (), {"responses": responses})()
        analyzer = OpenAISemanticAnalyzer(api_key="", client=client)

        result = analyzer.proofread_transcript(
            ["Premiere phrase.", "Deuxieme phrase."],
            "fr",
            glossary_terms=["Sunna"],
            context_before="Contexte précédent.",
        )

        self.assertEqual(result.texts[0], "Première phrase corrigée.")
        self.assertEqual(result.texts[1], "Deuxieme phrase.")
        self.assertEqual(result.call.request_id, "req_proofread_test")
        self.assertEqual(result.uncertainties[0].index, 1)
        self.assertEqual(result.learned_glossary_terms, ("ahl as-Sunna",))
        self.assertIn("Ne traduis jamais", responses.arguments["instructions"])
        self.assertIn("N'ajoute jamais [inaudible]", responses.arguments["instructions"])
        self.assertIn("Il, Lui, Celui, Son, Sa, Ses", responses.arguments["instructions"])
        self.assertIn("Sunna", responses.arguments["input"])

    def test_transcript_proofreading_preserves_existing_inaudible_marker(self) -> None:
        class ProofreadResponses:
            @staticmethod
            def create(**_kwargs):
                return type(
                    "Response",
                    (),
                    {
                        "output_text": json.dumps({
                            "items": [{
                                "index": 0,
                                "text": "Passage [inaudible].",
                                "uncertain": True,
                                "uncertainty_reason": "Marqueur présent dans la source.",
                            }],
                            "glossary_terms": [],
                        }),
                        "usage": type("Usage", (), {"input_tokens": 10, "output_tokens": 10})(),
                        "_request_id": "req-existing-marker",
                    },
                )()

        client = type("ProofreadClient", (), {"responses": ProofreadResponses()})()
        result = OpenAISemanticAnalyzer(api_key="", client=client).proofread_transcript(
            ["Passage [inaudible]."],
            "fr",
        )

        self.assertEqual(result.texts, ("Passage [inaudible].",))


if __name__ == "__main__":
    unittest.main()
