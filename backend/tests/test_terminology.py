from __future__ import annotations

import unittest

from backend.app.terminology import (
    DEFAULT_TRANSCRIPTION_GLOSSARY,
    effective_glossary,
    normalize_religious_style,
    transcription_prompt,
)


class TerminologyTests(unittest.TestCase):
    def test_default_glossary_covers_religious_terms_and_formulas(self) -> None:
        self.assertGreaterEqual(len(DEFAULT_TRANSCRIPTION_GLOSSARY), 90)
        for term in (
            "Allah",
            "Coran",
            "Sunna",
            "inchallah",
            "soubhanahu wa ta'ala",
            "'azza wa jall",
            "sallallahu 'alayhi wa sallam",
            "radiyallahu 'anhu",
        ):
            self.assertIn(term, DEFAULT_TRANSCRIPTION_GLOSSARY)

    def test_custom_terms_extend_defaults_without_removing_them(self) -> None:
        glossary = effective_glossary(["Nom du conférencier"])
        self.assertEqual(glossary[0], "Nom du conférencier")
        self.assertIn("Allah", glossary)
        self.assertIn("sallallahu 'alayhi wa sallam", glossary)

    def test_prompt_preserves_arabic_formulas_without_translation(self) -> None:
        prompt = transcription_prompt(
            effective_glossary(["Nom du conférencier"]),
            "Le contexte immédiatement prononcé avant ce nouveau fragment.",
        )
        self.assertIsNotNone(prompt)
        self.assertLessEqual(len(prompt or ""), 800)
        self.assertIn("Allah", prompt or "")
        self.assertIn("sans les traduire", prompt or "")
        self.assertIn("Contexte immédiatement précédent", prompt or "")

    def test_allah_casing_is_enforced_deterministically(self) -> None:
        self.assertEqual(
            normalize_religious_style("allah sait mieux et ALLAHU a'lam"),
            "Allah sait mieux et Allahu a'lam",
        )


if __name__ == "__main__":
    unittest.main()
