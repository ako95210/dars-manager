from __future__ import annotations

import unittest

from drsm_core import TranscriptSegment, segment_course


class LocalChapteringTests(unittest.TestCase):
    def test_lexical_topic_changes_create_generic_content_titles(self) -> None:
        texts = [
            "La photosynthèse utilise la lumière et la chlorophylle.",
            "La lumière permet la photosynthèse dans les feuilles.",
            "La chlorophylle capte cette énergie lumineuse.",
            "La respiration cellulaire libère l'énergie du glucose.",
            "Les cellules utilisent l'oxygène pendant la respiration.",
            "Le glucose produit ainsi de l'énergie cellulaire.",
            "Les planètes tournent autour de leur étoile.",
            "Une orbite dépend de la gravitation et de la vitesse.",
            "La gravitation organise les systèmes planétaires.",
        ]
        segments = [
            TranscriptSegment(index * 60, (index + 1) * 60, text)
            for index, text in enumerate(texts)
        ]

        parts = segment_course(segments)

        self.assertEqual(len(parts), 3)
        self.assertIn("photosynthèse", parts[0].title.lower())
        self.assertIn("respiration", parts[1].title.lower())
        self.assertTrue(
            "planètes" in parts[2].title.lower()
            or "gravitation" in parts[2].title.lower()
        )
        self.assertEqual(parts[0].start, 0)
        self.assertEqual(parts[-1].end, 540)


if __name__ == "__main__":
    unittest.main()
