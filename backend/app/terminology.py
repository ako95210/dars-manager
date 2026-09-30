from __future__ import annotations

from collections.abc import Iterable


# A deliberately small domain vocabulary. These are hints for recognition, not
# replacements: prompts explicitly forbid inserting a term unsupported by audio.
DEFAULT_TRANSCRIPTION_GLOSSARY = (
    "Allah",
    "Coran",
    "Sunna",
    "hadith",
    "salawât",
    "sourate",
    "ayah",
    "tawhid",
    "fiqh",
    "zakat",
    "Ramadan",
    "imam",
    "oumma",
)

MAX_CUSTOM_GLOSSARY_TERMS = 100
MAX_GLOSSARY_TERM_LENGTH = 80


def normalize_glossary_terms(values: Iterable[object] | None) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        term = " ".join(str(raw).strip().split())
        if not term or len(term) > MAX_GLOSSARY_TERM_LENGTH:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= MAX_CUSTOM_GLOSSARY_TERMS:
            break
    return terms


def effective_glossary(custom_terms: Iterable[object] | None) -> list[str]:
    # User vocabulary has priority when the provider's prompt size is limited.
    return normalize_glossary_terms((*(custom_terms or ()), *DEFAULT_TRANSCRIPTION_GLOSSARY))


def transcription_prompt(
    glossary_terms: Iterable[object] | None,
    previous_context: str = "",
) -> str | None:
    terms = normalize_glossary_terms(glossary_terms)
    pieces: list[str] = []
    if terms:
        selected_terms: list[str] = []
        for term in terms:
            candidate = ", ".join((*selected_terms, term))
            if len(candidate) > 430:
                break
            selected_terms.append(term)
        pieces.append(
            "Vocabulaire possible (uniquement si l'audio le confirme) : "
            + ", ".join(selected_terms)
            + "."
        )
    context = " ".join(previous_context.strip().split())[-300:]
    if context:
        pieces.append("Contexte immédiatement précédent : " + context)
    prompt = " ".join(pieces).strip()
    # whisper-1 accepts a short prompt (224 tokens). Keep a conservative bound.
    return prompt[:800] or None
