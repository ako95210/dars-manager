from __future__ import annotations

from collections.abc import Iterable
import re


# Shared vocabulary for the religious context of every Dars. These are spelling
# hints, never replacements: prompts forbid inserting a term unsupported by audio.
DEFAULT_TRANSCRIPTION_GLOSSARY = (
    "Allah",
    "Allahu akbar",
    "Bismillah",
    "bismillah ar-Rahman ar-Rahim",
    "al-hamdulillah",
    "soubhanallah",
    "inchallah",
    "machallah",
    "astaghfirullah",
    "la ilaha illa Allah",
    "la hawla wa la quwwata illa billah",
    "wa Allahu a'lam",
    "soubhanahu wa ta'ala",
    "tabaraka wa ta'ala",
    "'azza wa jall",
    "jalla jalaluhu",
    "Allahumma",
    "bi idhnillah",
    "fi sabilillah",
    "hasbi Allah",
    "tawakkaltu 'ala Allah",
    "inna lillahi wa inna ilayhi raji'un",
    "barak Allahu fik",
    "barak Allahu fikoum",
    "jazaka Allahu khayran",
    "taqabbal Allahu minna wa minkoum",
    "sallallahu 'alayhi wa sallam",
    "'alayhi salam",
    "radiyallahu 'anhu",
    "radiyallahu 'anha",
    "radiyallahu 'anhum",
    "rahimahullah",
    "hafidhahullah",
    "amin",
    "assalamu 'alaykum",
    "wa 'alaykum as-salam",
    "rahmatullahi wa barakatuh",
    "Coran",
    "islam",
    "musulman",
    "Sunna",
    "hadith",
    "isnad",
    "matn",
    "sahih",
    "hasan",
    "da'if",
    "salawât",
    "salât",
    "sourate",
    "ayah",
    "tafsir",
    "tajwid",
    "tawhid",
    "shirk",
    "bid'a",
    "aqida",
    "fiqh",
    "usul al-fiqh",
    "ijma",
    "qiyas",
    "ijtihad",
    "charia",
    "halal",
    "haram",
    "makruh",
    "fard",
    "wajib",
    "mustahabb",
    "mubah",
    "wudu",
    "ghusl",
    "tayammum",
    "zakat",
    "sadaqa",
    "sawm",
    "Ramadan",
    "hajj",
    "omra",
    "qibla",
    "Kaaba",
    "mosquée",
    "imam",
    "muezzin",
    "khutba",
    "dhikr",
    "dou'a",
    "istighfar",
    "oumma",
    "sahaba",
    "tabi'in",
    "muhajirun",
    "ansar",
    "cheikh",
    "ouléma",
    "mufti",
    "hijra",
    "akhira",
    "dunya",
    "fitna",
    "sabr",
    "taqwa",
    "iman",
    "ihsan",
    "nafs",
    "shaytan",
    "djinn",
    "baraka",
)

PROMPT_CORE_TERMS = (
    "Allah",
    "Coran",
    "Sunna",
    "hadith",
    "salât",
    "sourate",
    "ayah",
    "sallallahu 'alayhi wa sallam",
)

MAX_CUSTOM_GLOSSARY_TERMS = 100
MAX_GLOSSARY_TERM_LENGTH = 80
MAX_EFFECTIVE_GLOSSARY_TERMS = 200


def normalize_glossary_terms(
    values: Iterable[object] | None,
    *,
    limit: int = MAX_CUSTOM_GLOSSARY_TERMS,
) -> list[str]:
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
        if len(terms) >= limit:
            break
    return terms


def effective_glossary(custom_terms: Iterable[object] | None) -> list[str]:
    # User vocabulary has priority when the provider's prompt size is limited.
    return normalize_glossary_terms(
        (*(custom_terms or ()), *DEFAULT_TRANSCRIPTION_GLOSSARY),
        limit=MAX_EFFECTIVE_GLOSSARY_TERMS,
    )


def normalize_religious_style(text: str) -> str:
    """Apply safe casing rules that do not require interpreting the sentence."""
    normalized = re.sub(r"\ballahu\b", "Allahu", text, flags=re.IGNORECASE)
    return re.sub(r"\ballah\b", "Allah", normalized, flags=re.IGNORECASE)


def transcription_prompt(
    glossary_terms: Iterable[object] | None,
    previous_context: str = "",
) -> str | None:
    terms = normalize_glossary_terms(glossary_terms)
    pieces: list[str] = []
    if terms:
        available = {term.casefold(): term for term in terms}
        prioritized = normalize_glossary_terms(
            (
                *(available[term.casefold()] for term in PROMPT_CORE_TERMS if term.casefold() in available),
                *terms,
            )
        )
        selected_terms: list[str] = []
        for term in prioritized:
            candidate = ", ".join((*selected_terms, term))
            if len(candidate) > 430:
                break
            selected_terms.append(term)
        pieces.append(
            "Vocabulaire possible (uniquement si l'audio le confirme) : "
            + ", ".join(selected_terms)
            + "."
        )
    pieces.append(
        "Écris les formules arabes en alphabet latin selon ces graphies, sans les "
        "traduire. Écris toujours Allah avec une majuscule."
    )
    base_prompt = " ".join(pieces).strip()
    context_prefix = "Contexte immédiatement précédent : "
    remaining = max(0, 800 - len(base_prompt) - len(context_prefix) - 1)
    normalized_context = " ".join(previous_context.strip().split())
    context = normalized_context[-min(300, remaining):] if remaining else ""
    if context:
        pieces.append(context_prefix + context)
    prompt = " ".join(pieces).strip()
    # whisper-1 accepts a short prompt (224 tokens). Keep a conservative bound.
    return prompt or None
