"""Core audio/transcription functions shared by web and desktop frontends."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import time
import unicodedata
import wave
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path


APP_TITLE = "Dars Manager"
APP_VERSION = "1.0.3"
DEFAULT_MODEL = "base"
MIN_PART_SECONDS = 150
MAX_PART_SECONDS = 600
APP_DIR = Path(__file__).resolve().parent
WORK_DIR = Path(os.environ.get("DRSM_WORK_DIR", str(APP_DIR / "work"))).expanduser().resolve()
UPLOADS_DIR = WORK_DIR / "uploads"
ANALYSIS_DIR = WORK_DIR / "analyses"
EXPORTS_DIR = WORK_DIR / "exports"

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class CoursePart:
    index: int
    start: float
    end: float
    title: str
    description: str
    transcript: str


class AnalysisCancelled(Exception):
    """Raised when a user cancels an audio analysis."""


TRANSITION_RE = re.compile(
    r"\b(premier|première|deuxième|troisième|dernier|dernière|ensuite|"
    r"maintenant|on va passer|on passe|le sujet|la question|la réponse|"
    r"premièrement|deuxièmement|pour conclure|je répète|"
    r"la première catégorie|la deuxième catégorie|le premier cas|"
    r"le deuxième cas|cas de figure)\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "alors", "avec", "avoir", "bien", "cela", "cette", "comme", "dans",
    "donc", "elle", "elles", "entre", "faire", "fait", "faut", "leur",
    "leurs", "mais", "meme", "nous", "parce", "pour", "quand", "quoi",
    "sans", "sont", "tout", "tres", "voila", "vous", "cest", "quil",
    "quils", "quelle", "quelles", "ainsi", "aussi", "autre", "autres",
    "chez", "ceux", "chose", "choses", "dire", "est", "etre", "gens",
    "ici", "plus", "puis", "tous", "toutes", "une", "des", "les", "la",
    "le", "du", "de", "un", "en", "et", "ou", "au", "aux", "ce", "ça",
    "sa", "se", "ses", "son", "sur", "pas", "ne", "ni", "que", "qui",
    "il", "ils", "on", "allah", "azawajel", "salam", "professeur",
    "prophète", "prophete", "taib", "naam",
}

TITLE_RULES = [
    (("gouverneur", "gouverneurs", "emir", "imam", "obeir", "obeissance", "ecoute"), "Obéissance au gouverneur", 1.0),
    (("preuve", "preuves", "verset", "hadith", "authentique", "comprehension", "salaf"), "Méthodologie des preuves", 1.4),
    (("hudhayfa", "hudaifa", "khalifa", "muslim", "mousselim", "imams", "suivront", "conformeront", "fouette", "argent", "injustice"), "Hadith sur les gouverneurs injustes", 2.4),
    (("egypte", "moubarak", "morsi", "freres", "musulmans", "manifestation"), "Exemple politique contemporain", 2.3),
    (("peines", "legales", "butin", "zakat", "autorite", "mandate"), "Autorité publique et peines légales", 2.2),
    (("savants", "reseaux", "youtube", "twitter", "facebook", "fitna", "troubles"), "Parler des troubles et revenir aux savants", 2.0),
    (("vendredi", "priere", "imam", "mosquee", "raka", "innovation"), "Prière derrière l’imam", 2.1),
    (("bidat", "innovation", "innovateur", "islam", "contraint", "annule"), "Prier derrière un innovateur", 2.0),
]

SUBTITLE_RULES = [
    (("desobeissance", "createur", "interdit", "ordonne", "obeissance"), "limites de l’obéissance", 2.0),
    (("fitna", "troubles", "reseaux", "huile", "feu", "rebeller"), "éviter l’agitation publique", 2.1),
    (("pieux", "pervers", "pervert", "difference"), "pieux ou pervers", 2.2),
    (("satisfaire", "gouverneurs", "ambiguite", "accusation", "preuve"), "réponse à l’accusation de complaisance", 2.2),
    (("preuve", "preuves", "verset", "hadith", "comprehension", "salaf"), "comment utiliser les preuves", 2.0),
    (("innovateur", "innovateurs", "melange", "vrai", "faux", "sectes"), "mélange du vrai et du faux", 2.1),
    (("hudhayfa", "hudaifa", "khalifa", "muslim", "mousselim", "imams", "sunnah", "compagnons", "suivront", "conformeront"), "hadith de Hudhayfa", 2.3),
    (("fouette", "argent", "injustice", "injuste", "fouetter"), "obéir malgré l’injustice", 3.2),
    (("habach", "esclave", "raisin", "lointain", "tribu", "statut"), "statut social du gouverneur", 3.5),
    (("egypte", "moubarak", "morsi", "manifestations", "revolte"), "Égypte: Moubarak et Morsi", 2.5),
    (("constitution", "lois", "charia", "chia", "contradiction"), "contradictions politiques", 2.2),
    (("batailles", "campagnes", "militaires", "butin", "zakat"), "butin, zakat et campagnes", 2.3),
    (("peines", "legales", "voleur", "voler", "main", "appliquer"), "application des peines par l’autorité", 2.3),
    (("hierarchie", "famille", "mari", "femme", "enfants", "organisation"), "hiérarchie et ordre religieux", 2.2),
    (("savants", "jeunes", "sang", "guerres", "communaute", "questionner"), "affaires graves et grands savants", 2.3),
    (("youtube", "twitter", "facebook", "journalistes", "reseaux", "vues"), "réseaux sociaux et prises de parole", 2.4),
    (("palestine", "haine", "insulter", "medisance", "denigrer"), "dénigrement des gouverneurs", 2.5),
    (("vendredi", "jumu", "raka", "complete", "refait", "innovation"), "validité de la prière du vendredi", 2.4),
    (("annule", "annuler", "fatiha", "tachahoud", "refais", "recommences"), "innovation qui annule la prière", 2.7),
    (("moucaffer", "sortir", "islam", "hulul", "sacrifie", "contraint"), "innovation hors de la prière", 2.4),
    (("mosquee", "rang", "grossis", "recitation", "quartier", "salafides"), "choisir la mosquée à fréquenter", 2.4),
]


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    text = strip_accents(text.lower()).replace("'", " ").replace("’", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def safe_filename(text: str) -> str:
    text = strip_accents(text)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return text or "extrait"


def format_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def parse_time(value: str) -> float:
    value = value.strip()
    if not value:
        raise ValueError("temps vide")
    if ":" not in value:
        return float(value)
    parts = [float(p) for p in value.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"temps invalide: {value}")


def words_for(text: str) -> list[str]:
    return [word for word in normalize(text).split() if len(word) > 3 and word not in STOPWORDS]


def score_rule(counts: Counter[str], keywords: tuple[str, ...], weight: float) -> float:
    return sum(counts[normalize(keyword)] for keyword in keywords) * weight


def best_subtitle(counts: Counter[str]) -> str:
    best_score = 0.0
    best_name = ""
    for keywords, subtitle, weight in SUBTITLE_RULES:
        score = score_rule(counts, keywords, weight)
        if score > best_score:
            best_score = score
            best_name = subtitle
    return best_name if best_score >= 2 else ""


def best_title(text: str, index: int) -> str:
    counts = Counter(words_for(text))
    best_score = 0.0
    best_name = ""
    for keywords, title, weight in TITLE_RULES:
        score = score_rule(counts, keywords, weight)
        if score > best_score:
            best_score = score
            best_name = title
    if best_name:
        subtitle = best_subtitle(counts)
        return f"{best_name} - {subtitle}" if subtitle else best_name
    top = [word for word, _ in counts.most_common(3)]
    return f"Partie {index} - {', '.join(top)}" if top else f"Partie {index}"


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+|(?:\s+-\s+)", cleaned)
    if len(parts) == 1:
        parts = re.split(r"\s+(?=(?:Ensuite|Donc|Puis|Maintenant|Le|La|Les)\b)", cleaned)
    return [part.strip(" ,;:-") for part in parts if len(part.strip()) > 20]


def description_for(text: str) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return text[:240].strip()
    counts = Counter(words_for(text))
    scored = []
    for pos, sentence in enumerate(sentences):
        score = sum(counts[word] for word in set(words_for(sentence)))
        score += max(0, 2 - pos) * 1.5
        scored.append((score, pos, sentence))
    selected = sorted(scored, reverse=True)[:2]
    selected = [sentence for _, _, sentence in sorted(selected, key=lambda item: item[1])]
    return " ".join(selected)[:520].strip()


def make_part(index: int, segments: list[TranscriptSegment]) -> CoursePart:
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    return CoursePart(
        index=index,
        start=segments[0].start,
        end=segments[-1].end,
        title=best_title(transcript, index),
        description=description_for(transcript),
        transcript=transcript,
    )


def distinctive_keywords(text: str, title: str) -> str:
    title_words = set(words_for(title))
    candidates = [
        word
        for word, _ in Counter(words_for(text)).most_common(12)
        if word not in title_words and len(word) > 4
    ]
    return ", ".join(candidates[:3])


def refine_repeated_titles(parts: list[CoursePart]) -> list[CoursePart]:
    seen: dict[str, int] = {}
    for part in parts:
        title = part.title
        seen[title] = seen.get(title, 0) + 1
        if seen[title] > 1:
            detail = distinctive_keywords(part.transcript, title)
            part.title = f"{title} ({detail})" if detail else f"{title} ({seen[title]})"
    return parts


def segment_course(segments: list[TranscriptSegment]) -> list[CoursePart]:
    if not segments:
        return []
    parts: list[CoursePart] = []
    current: list[TranscriptSegment] = []
    for segment in segments:
        if current:
            current_duration = current[-1].end - current[0].start
            gap = segment.start - current[-1].end
            is_transition = bool(TRANSITION_RE.search(normalize(segment.text)))
            should_split = (
                (is_transition and current_duration >= MIN_PART_SECONDS)
                or current_duration >= MAX_PART_SECONDS
                or (gap >= 8 and current_duration >= 90)
            )
            if should_split:
                parts.append(make_part(len(parts) + 1, current))
                current = []
        current.append(segment)
    if current:
        parts.append(make_part(len(parts) + 1, current))

    merged: list[CoursePart] = []
    buffer: list[TranscriptSegment] = []
    for part in parts:
        part_segments = [TranscriptSegment(part.start, part.end, part.transcript)]
        if not buffer:
            buffer = part_segments
        elif part.end - part.start < 75:
            buffer.extend(part_segments)
        else:
            merged.append(make_part(len(merged) + 1, buffer))
            buffer = part_segments
    if buffer:
        merged.append(make_part(len(merged) + 1, buffer))
    return refine_repeated_titles(merged)


def dependency_status() -> list[dict[str, str]]:
    packages = ["streamlit", "pandas", "numpy", "av", "faster-whisper", "ctranslate2"]
    status = []
    for package in packages:
        module = package.replace("-", "_")
        installed = importlib.util.find_spec(module) is not None
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "non installé"
        status.append(
            {
                "package": package,
                "module": module,
                "statut": "ok" if installed else "introuvable",
                "version": version,
            }
        )
    return status


def import_av():
    try:
        import av
    except Exception as exc:
        raise RuntimeError(
            "La dépendance PyAV n'est pas disponible. Vérifie l'installation du paquet `av`."
        ) from exc
    return av


def import_whisper_model_class():
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise RuntimeError(
            "La dépendance faster-whisper n'est pas disponible. "
            "Sur Streamlit Cloud, vérifie `runtime.txt` et `requirements.txt`."
        ) from exc
    return WhisperModel


@lru_cache(maxsize=1)
def load_whisper_model(model_name: str):
    WhisperModel = import_whisper_model_class()
    return WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=1,
        num_workers=1,
    )


def transcribe_audio(
    path: Path,
    model_name: str,
    language: str,
    progress,
    should_pause=None,
    should_cancel=None,
) -> list[TranscriptSegment]:
    def wait_if_requested() -> None:
        if should_cancel and should_cancel():
            raise AnalysisCancelled("Analyse annulée.")
        paused_notified = False
        while should_pause and should_pause():
            if should_cancel and should_cancel():
                raise AnalysisCancelled("Analyse annulée.")
            if not paused_notified:
                progress("Analyse en pause.")
                paused_notified = True
            time.sleep(0.25)
        if paused_notified:
            progress("Analyse reprise.")

    wait_if_requested()
    progress(f"Chargement du modèle Whisper '{model_name}'...")
    model = load_whisper_model(model_name)
    wait_if_requested()
    progress("Transcription en cours...")
    segments_iter, info = model.transcribe(
        str(path),
        language=language or None,
        vad_filter=True,
        beam_size=1,
        word_timestamps=False,
    )
    wait_if_requested()
    progress(
        f"Langue détectée: {info.language} "
        f"({info.language_probability:.0%}), durée {format_time(info.duration)}"
    )
    segments: list[TranscriptSegment] = []
    last_update = time.monotonic()
    for segment in segments_iter:
        wait_if_requested()
        text = segment.text.strip()
        if text:
            segments.append(TranscriptSegment(segment.start, segment.end, text))
        if time.monotonic() - last_update > 1.0:
            progress(f"Transcription: {format_time(segment.end)} / {format_time(info.duration)}")
            last_update = time.monotonic()
    return segments


def analysis_filename(audio_path: Path) -> str:
    safe_name = safe_filename(audio_path.stem)[:80] or "audio"
    try:
        stat = audio_path.stat()
        identity = f"{audio_path.resolve()}|{stat.st_size}|{stat.st_mtime}"
    except OSError:
        identity = str(audio_path)
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{safe_name}_{digest}.drsm_analysis.json"


def save_analysis(audio_path: Path, segments: list[TranscriptSegment], parts: list[CoursePart]) -> Path:
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    out = ANALYSIS_DIR / analysis_filename(audio_path)
    stat = audio_path.stat() if audio_path.exists() else None
    payload = {
        "schema": 2,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "audio": str(audio_path),
        "audio_name": audio_path.name,
        "audio_size": stat.st_size if stat else None,
        "audio_mtime": stat.st_mtime if stat else None,
        "segments": [asdict(segment) for segment in segments],
        "parts": [asdict(part) for part in parts],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_analysis(path: Path) -> tuple[Path, list[TranscriptSegment], list[CoursePart]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    audio_path = Path(payload.get("audio") or payload.get("audio_path") or "")
    segments = [
        TranscriptSegment(float(item["start"]), float(item["end"]), str(item["text"]))
        for item in payload.get("segments", [])
    ]
    parts = [
        CoursePart(
            int(item["index"]),
            float(item["start"]),
            float(item["end"]),
            str(item["title"]),
            str(item.get("description", "")),
            str(item.get("transcript", "")),
        )
        for item in payload.get("parts", [])
    ]
    if not parts and segments:
        parts = segment_course(segments)
    return audio_path, segments, parts


def export_title_for(parts: list[CoursePart]) -> str:
    if not parts:
        return "extrait"
    if len(parts) == 1:
        return parts[0].title
    themes = [part.title.split(" - ", 1)[0] for part in parts]
    if len(set(themes)) == 1:
        return f"{themes[0]} - sélection {parts[0].index} à {parts[-1].index}"
    return f"Sélection parties {parts[0].index} à {parts[-1].index}"


def audio_duration(path: Path) -> float:
    av = import_av()
    container = av.open(str(path))
    try:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream and stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if container.duration is not None:
            return float(container.duration / av.time_base)
        duration = 0.0
        if stream:
            for frame in container.decode(stream):
                duration = max(
                    duration,
                    float(frame.time or 0.0) + frame.samples / float(frame.sample_rate or 48000),
                )
        return duration
    finally:
        container.close()


def export_clip(input_path: Path, output_path: Path, start: float, end: float) -> None:
    export_clips(input_path, output_path, [(start, end)])


def export_clips(input_path: Path, output_path: Path, ranges: list[tuple[float, float]]) -> None:
    if not ranges:
        raise ValueError("Aucune partie à exporter.")
    for start, end in ranges:
        if end <= start:
            raise ValueError("Chaque fin doit être après le début.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    av = import_av()
    from av.audio.resampler import AudioResampler

    container = av.open(str(input_path))
    audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
    if audio_stream is None:
        raise ValueError("Aucune piste audio trouvée.")

    sample_rate = audio_stream.rate or 48000
    layout = audio_stream.layout.name if audio_stream.layout else "stereo"
    if layout not in {"mono", "stereo"}:
        layout = "stereo"

    output = av.open(str(output_path), "w")
    out_stream = output.add_stream("pcm_s16le", rate=sample_rate)
    out_stream.layout = layout
    resampler = AudioResampler(format="s16", layout=layout, rate=sample_rate)

    try:
        for start, end in ranges:
            seek_time = max(0.0, start - 1.0)
            try:
                container.seek(int(seek_time * av.time_base), any_frame=False, backward=True)
            except Exception:
                pass
            for frame in container.decode(audio_stream):
                frame_start = float(frame.time or 0.0)
                frame_end = frame_start + (frame.samples / float(frame.sample_rate or sample_rate))
                if frame_end < start:
                    continue
                if frame_start > end:
                    break
                for converted in resampler.resample(frame):
                    converted.pts = None
                    for packet in out_stream.encode(converted):
                        output.mux(packet)
        for packet in out_stream.encode(None):
            output.mux(packet)
    finally:
        output.close()
        container.close()


def replace_wav_range(
    input_path: Path,
    output_path: Path,
    start: float,
    end: float,
    replacement_path: Path | None = None,
) -> None:
    if end <= start:
        raise ValueError("La fin doit être après le début.")
    with wave.open(str(input_path), "rb") as src:
        params = src.getparams()
        frame_rate = src.getframerate()
        start_frame = max(0, int(start * frame_rate))
        end_frame = max(start_frame, int(end * frame_rate))
        total_frames = src.getnframes()
        start_frame = min(start_frame, total_frames)
        end_frame = min(end_frame, total_frames)
        src.setpos(0)
        before = src.readframes(start_frame)
        src.setpos(end_frame)
        after = src.readframes(total_frames - end_frame)

    if replacement_path is None:
        replacement_frames = end_frame - start_frame
        replacement = b"\x00" * replacement_frames * params.nchannels * params.sampwidth
    else:
        with wave.open(str(replacement_path), "rb") as repl:
            repl_params = repl.getparams()
            if (
                repl_params.nchannels != params.nchannels
                or repl_params.sampwidth != params.sampwidth
                or repl_params.framerate != params.framerate
            ):
                raise ValueError("Le fichier de remplacement doit avoir le même format WAV.")
            replacement = repl.readframes(repl.getnframes())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as out:
        out.setparams(params)
        out.writeframes(before)
        out.writeframes(replacement)
        out.writeframes(after)
