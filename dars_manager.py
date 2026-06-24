#!/usr/bin/env python3
"""GUI tool to transcribe, segment, and export course audio extracts."""

from __future__ import annotations

import json
import hashlib
import queue
import re
import threading
import time
import unicodedata
import wave
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    W,
    Button,
    DoubleVar,
    Entry,
    Frame,
    Label,
    Listbox,
    Menu,
    Scrollbar,
    StringVar,
    Text,
    Toplevel,
    Tk,
    filedialog,
    messagebox,
    ttk,
)

import av
from av.audio.resampler import AudioResampler
from faster_whisper import WhisperModel

try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
except Exception:
    Gst = None


APP_TITLE = "Dars Manager"
APP_VERSION = "1.0.0"
DEFAULT_MODEL = "base"
MIN_PART_SECONDS = 150
MAX_PART_SECONDS = 600
APP_DIR = Path(__file__).resolve().parent
WORK_DIR = APP_DIR / "work"
ANALYSIS_DIR = WORK_DIR / "analyses"
RECORDINGS_DIR = WORK_DIR / "recordings"


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


TRANSITION_RE = re.compile(
    r"\b("
    r"premier|première|deuxième|troisième|dernier|dernière|"
    r"ensuite|maintenant|on va passer|on passe|"
    r"le sujet|la question|la réponse|"
    r"premièrement|deuxièmement|pour conclure|je répète|"
    r"la première catégorie|la deuxième catégorie|"
    r"le premier cas|le deuxième cas|cas de figure"
    r")\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "alors",
    "avec",
    "avoir",
    "bien",
    "cela",
    "cette",
    "comme",
    "dans",
    "donc",
    "elle",
    "elles",
    "entre",
    "faire",
    "fait",
    "faut",
    "leur",
    "leurs",
    "mais",
    "meme",
    "nous",
    "parce",
    "pour",
    "quand",
    "quoi",
    "sans",
    "sont",
    "tout",
    "tres",
    "voila",
    "vous",
    "cest",
    "quil",
    "quils",
    "quelle",
    "quelles",
    "ainsi",
    "aussi",
    "autre",
    "autres",
    "chez",
    "cette",
    "ceux",
    "chose",
    "choses",
    "dire",
    "est",
    "etre",
    "gens",
    "ici",
    "plus",
    "puis",
    "quoi",
    "tous",
    "toutes",
    "une",
    "des",
    "les",
    "la",
    "le",
    "du",
    "de",
    "un",
    "en",
    "et",
    "ou",
    "au",
    "aux",
    "ce",
    "ça",
    "sa",
    "se",
    "ses",
    "son",
    "sur",
    "pas",
    "ne",
    "ni",
    "que",
    "qui",
    "il",
    "ils",
    "on",
    "allah",
    "azawajel",
    "salam",
    "professeur",
    "prophète",
    "prophete",
    "taib",
    "naam",
}

TITLE_RULES = [
    (
        ("gouverneur", "gouverneurs", "emir", "imam", "obeir", "obeissance", "ecoute"),
        "Obéissance au gouverneur",
        1.0,
    ),
    (
        ("preuve", "preuves", "verset", "hadith", "authentique", "comprehension", "salaf"),
        "Méthodologie des preuves",
        1.4,
    ),
    (
        (
            "hudhayfa",
            "hudaifa",
            "khalifa",
            "muslim",
            "mousselim",
            "imams",
            "suivront",
            "conformeront",
            "fouette",
            "argent",
            "injustice",
        ),
        "Hadith sur les gouverneurs injustes",
        2.4,
    ),
    (
        ("egypte", "moubarak", "morsi", "freres", "musulmans", "manifestation"),
        "Exemple politique contemporain",
        2.3,
    ),
    (
        ("peines", "legales", "butin", "zakat", "autorite", "mandate"),
        "Autorité publique et peines légales",
        2.2,
    ),
    (
        ("savants", "reseaux", "youtube", "twitter", "facebook", "fitna", "troubles"),
        "Parler des troubles et revenir aux savants",
        2.0,
    ),
    (
        ("vendredi", "priere", "imam", "mosquee", "raka", "innovation"),
        "Prière derrière l’imam",
        2.1,
    ),
    (
        ("bidat", "innovation", "innovateur", "islam", "contraint", "annule"),
        "Prier derrière un innovateur",
        2.0,
    ),
]

SUBTITLE_RULES = [
    (
        (
            "desobeissance",
            "createur",
            "interdit",
            "ordonne",
            "obeissance",
        ),
        "limites de l’obéissance",
        2.0,
    ),
    (
        ("fitna", "troubles", "reseaux", "huile", "feu", "rebeller"),
        "éviter l’agitation publique",
        2.1,
    ),
    (
        ("pieux", "pervers", "pervert", "difference"),
        "pieux ou pervers",
        2.2,
    ),
    (
        ("satisfaire", "gouverneurs", "ambiguite", "accusation", "preuve"),
        "réponse à l’accusation de complaisance",
        2.2,
    ),
    (
        ("preuve", "preuves", "verset", "hadith", "comprehension", "salaf"),
        "comment utiliser les preuves",
        2.0,
    ),
    (
        ("innovateur", "innovateurs", "melange", "vrai", "faux", "sectes"),
        "mélange du vrai et du faux",
        2.1,
    ),
    (
        (
            "hudhayfa",
            "hudaifa",
            "khalifa",
            "muslim",
            "mousselim",
            "imams",
            "sunnah",
            "compagnons",
            "suivront",
            "conformeront",
        ),
        "hadith de Hudhayfa",
        2.3,
    ),
    (
        ("fouette", "argent", "injustice", "injuste", "fouetter"),
        "obéir malgré l’injustice",
        3.2,
    ),
    (
        ("habach", "esclave", "raisin", "lointain", "tribu", "statut"),
        "statut social du gouverneur",
        3.5,
    ),
    (
        ("egypte", "moubarak", "morsi", "manifestations", "revolte"),
        "Égypte: Moubarak et Morsi",
        2.5,
    ),
    (
        ("constitution", "lois", "charia", "chia", "contradiction"),
        "contradictions politiques",
        2.2,
    ),
    (
        ("batailles", "campagnes", "militaires", "butin", "zakat"),
        "butin, zakat et campagnes",
        2.3,
    ),
    (
        ("peines", "legales", "voleur", "voler", "main", "appliquer"),
        "application des peines par l’autorité",
        2.3,
    ),
    (
        ("hierarchie", "famille", "mari", "femme", "enfants", "organisation"),
        "hiérarchie et ordre religieux",
        2.2,
    ),
    (
        ("savants", "jeunes", "sang", "guerres", "communaute", "questionner"),
        "affaires graves et grands savants",
        2.3,
    ),
    (
        ("youtube", "twitter", "facebook", "journalistes", "reseaux", "vues"),
        "réseaux sociaux et prises de parole",
        2.4,
    ),
    (
        ("palestine", "haine", "insulter", "medisance", "denigrer"),
        "dénigrement des gouverneurs",
        2.5,
    ),
    (
        ("vendredi", "jumu", "raka", "complete", "refait", "innovation"),
        "validité de la prière du vendredi",
        2.4,
    ),
    (
        ("annule", "annuler", "fatiha", "tachahoud", "refais", "recommences"),
        "innovation qui annule la prière",
        2.7,
    ),
    (
        ("moucaffer", "sortir", "islam", "hulul", "sacrifie", "contraint"),
        "innovation hors de la prière",
        2.4,
    ),
    (
        ("mosquee", "rang", "grossis", "recitation", "quartier", "salafides"),
        "choisir la mosquée à fréquenter",
        2.4,
    ),
]


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize(text: str) -> str:
    text = strip_accents(text.lower())
    text = text.replace("'", " ").replace("’", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def format_time(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


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
    return [
        word
        for word in normalize(text).split()
        if len(word) > 3 and word not in STOPWORDS
    ]


def score_rule(counts: Counter[str], keywords: tuple[str, ...], weight: float) -> float:
    return sum(counts[normalize(keyword)] for keyword in keywords) * weight


def best_title(text: str, index: int) -> str:
    words = words_for(text)
    counts = Counter(words)
    best_score = 0
    best_name = ""
    for keywords, title, weight in TITLE_RULES:
        score = score_rule(counts, keywords, weight)
        if score > best_score:
            best_score = score
            best_name = title
    if best_name:
        subtitle = best_subtitle(counts)
        if subtitle:
            return f"{best_name} - {subtitle}"
        return best_name
    top = [word for word, _ in counts.most_common(3)]
    if top:
        return "Partie " + str(index) + " - " + ", ".join(top)
    return f"Partie {index}"


def best_subtitle(counts: Counter[str]) -> str:
    best_score = 0.0
    best_name = ""
    for keywords, subtitle, weight in SUBTITLE_RULES:
        score = score_rule(counts, keywords, weight)
        if score > best_score:
            best_score = score
            best_name = subtitle
    return best_name if best_score >= 2 else ""


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
            if detail:
                part.title = f"{title} ({detail})"
            else:
                part.title = f"{title} ({seen[title]})"
    return parts


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
    start = segments[0].start
    end = segments[-1].end
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    return CoursePart(
        index=index,
        start=start,
        end=end,
        title=best_title(transcript, index),
        description=description_for(transcript),
        transcript=transcript,
    )


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
        part_segments = [
            TranscriptSegment(part.start, part.end, part.transcript)
        ]
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


def transcribe_audio(path: Path, model_name: str, language: str, progress) -> list[TranscriptSegment]:
    progress(f"Chargement du modèle Whisper '{model_name}'...")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    progress("Transcription en cours...")
    segments_iter, info = model.transcribe(
        str(path),
        language=language or None,
        vad_filter=True,
        beam_size=5,
        word_timestamps=False,
    )
    progress(
        f"Langue détectée: {info.language} "
        f"({info.language_probability:.0%}), durée {format_time(info.duration)}"
    )
    segments: list[TranscriptSegment] = []
    last_update = time.monotonic()
    for segment in segments_iter:
        text = segment.text.strip()
        if text:
            segments.append(TranscriptSegment(segment.start, segment.end, text))
        if time.monotonic() - last_update > 1.0:
            progress(f"Transcription: {format_time(segment.end)} / {format_time(info.duration)}")
            last_update = time.monotonic()
    return segments


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


def analysis_filename(audio_path: Path) -> str:
    safe_name = safe_filename(audio_path.stem)[:80] or "audio"
    try:
        stat = audio_path.stat()
        identity = f"{audio_path.resolve()}|{stat.st_size}|{stat.st_mtime}"
    except OSError:
        identity = str(audio_path)
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"{safe_name}_{digest}.drsm_analysis.json"


def load_analysis(path: Path) -> tuple[Path, list[TranscriptSegment], list[CoursePart]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    audio_path = Path(payload.get("audio") or payload.get("audio_path") or "")
    segments = [
        TranscriptSegment(float(item["start"]), float(item["end"]), str(item["text"]))
        for item in payload.get("segments", [])
    ]
    raw_parts = payload.get("parts") or []
    parts = [
        CoursePart(
            int(item["index"]),
            float(item["start"]),
            float(item["end"]),
            str(item["title"]),
            str(item.get("description", "")),
            str(item.get("transcript", "")),
        )
        for item in raw_parts
    ]
    if not parts and segments:
        parts = segment_course(segments)
    return audio_path, segments, parts


def safe_filename(text: str) -> str:
    text = strip_accents(text)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return text or "extrait"


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
                raise ValueError(
                    "Le fichier de remplacement doit avoir le même format WAV "
                    "(canaux, fréquence, profondeur)."
                )
            replacement = repl.readframes(repl.getnframes())

    with wave.open(str(output_path), "wb") as out:
        out.setparams(params)
        out.writeframes(before)
        out.writeframes(replacement)
        out.writeframes(after)


class GstAudioPlayer:
    def __init__(self) -> None:
        if Gst is None:
            raise RuntimeError("GStreamer Python n'est pas disponible.")
        self.pipeline = Gst.ElementFactory.make("playbin", "drsm-player")
        if self.pipeline is None:
            raise RuntimeError("Impossible de créer le lecteur GStreamer.")
        self.rate = 1.0

    def set_file(self, path: Path) -> None:
        self.stop()
        self.pipeline.set_property("uri", path.resolve().as_uri())
        self.rate = 1.0

    def play(self) -> None:
        self.pipeline.set_state(Gst.State.PLAYING)

    def pause(self) -> None:
        self.pipeline.set_state(Gst.State.PAUSED)

    def stop(self) -> None:
        self.pipeline.set_state(Gst.State.NULL)

    def query_position(self) -> float:
        ok, position = self.pipeline.query_position(Gst.Format.TIME)
        return float(position / Gst.SECOND) if ok else 0.0

    def query_duration(self) -> float:
        ok, duration = self.pipeline.query_duration(Gst.Format.TIME)
        return float(duration / Gst.SECOND) if ok and duration > 0 else 0.0

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
        if abs(self.rate - 1.0) < 0.001:
            self.pipeline.seek_simple(Gst.Format.TIME, flags, int(seconds * Gst.SECOND))
        else:
            self.pipeline.seek(
                self.rate,
                Gst.Format.TIME,
                flags,
                Gst.SeekType.SET,
                int(seconds * Gst.SECOND),
                Gst.SeekType.NONE,
                -1,
            )

    def set_rate(self, rate: float) -> None:
        self.rate = max(0.25, rate)
        self.seek(self.query_position())


class GstAudioRecorder:
    def __init__(self, output_path: Path, channels: int = 2, sample_rate: int = 48000) -> None:
        if Gst is None:
            raise RuntimeError("GStreamer Python n'est pas disponible.")
        self.output_path = output_path
        self.pipeline = Gst.Pipeline.new("drsm-recorder")
        elements = [
            Gst.ElementFactory.make("autoaudiosrc", "mic"),
            Gst.ElementFactory.make("audioconvert", "convert"),
            Gst.ElementFactory.make("audioresample", "resample"),
            Gst.ElementFactory.make("capsfilter", "caps"),
            Gst.ElementFactory.make("wavenc", "wav"),
            Gst.ElementFactory.make("filesink", "file"),
        ]
        if self.pipeline is None or any(element is None for element in elements):
            raise RuntimeError("Impossible de créer la chaîne d'enregistrement audio.")
        source, convert, resample, capsfilter, wavenc, filesink = elements
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string(f"audio/x-raw,format=S16LE,channels={channels},rate={sample_rate}"),
        )
        filesink.set_property("location", str(output_path))
        for element in elements:
            self.pipeline.add(element)
        for first, second in zip(elements, elements[1:]):
            if not first.link(second):
                raise RuntimeError("Impossible de connecter les éléments d'enregistrement.")

    def start(self) -> None:
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        self.pipeline.send_event(Gst.Event.new_eos())
        bus = self.pipeline.get_bus()
        if bus is not None:
            bus.timed_pop_filtered(3 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        self.pipeline.set_state(Gst.State.NULL)


def export_clip(input_path: Path, output_path: Path, start: float, end: float) -> None:
    export_clips(input_path, output_path, [(start, end)])


def export_clips(input_path: Path, output_path: Path, ranges: list[tuple[float, float]]) -> None:
    if not ranges:
        raise ValueError("Aucune partie à exporter.")
    for start, end in ranges:
        if end <= start:
            raise ValueError("Chaque fin doit être après le début.")

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


class CourseCutterApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x760")
        self.root.minsize(920, 620)

        self.audio_path: Path | None = None
        self.current_analysis_path: Path | None = None
        self.segments: list[TranscriptSegment] = []
        self.parts: list[CoursePart] = []
        self.exported_files: list[Path] = []
        self.current_generated_audio: Path | None = None
        self.player = self._create_player()
        self.recorder: GstAudioRecorder | None = None
        self.recording_path: Path | None = None
        self.player_duration = 0.0
        self.player_updating = False
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()

        self.model_var = StringVar(value=DEFAULT_MODEL)
        self.language_var = StringVar(value="fr")
        self.file_var = StringVar(value="Aucun fichier sélectionné")
        self.status_var = StringVar(value="Sélectionne un fichier audio pour commencer.")
        self.start_var = StringVar(value="00:00")
        self.end_var = StringVar(value="00:00")
        self.export_title_var = StringVar(value="extrait")
        self.player_file_var = StringVar(value="Aucun audio généré sélectionné")
        self.player_time_var = StringVar(value="00:00 / 00:00")
        self.player_position_var = DoubleVar(value=0.0)
        self.player_speed_var = StringVar(value="1.0x")
        self.generated_start_var = StringVar(value="00:00")
        self.generated_end_var = StringVar(value="00:00")
        self.generated_title_var = StringVar(value="sous-audio")
        self.replace_start_var = StringVar(value="00:00")
        self.replace_end_var = StringVar(value="00:00")
        self.replace_text_var = StringVar(value="")
        self.recording_status_var = StringVar(value="Aucune prise voix enregistrée")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(250, self.update_player_position)
        self.root.after(150, self._drain_messages)

    def _create_player(self) -> GstAudioPlayer | None:
        try:
            return GstAudioPlayer()
        except Exception:
            return None

    def _build_ui(self) -> None:
        menubar = Menu(self.root)
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Ouvrir un audio", command=self.select_audio)
        file_menu.add_command(label="Charger une analyse", command=self.load_analysis_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Quitter", command=self.root.destroy)
        menubar.add_cascade(label="Fichier", menu=file_menu)
        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="Version", command=self.show_version)
        help_menu.add_command(label="README", command=self.show_readme)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menubar)

        top = Frame(self.root, padx=12, pady=10)
        top.pack(fill="x")
        Button(top, text="Choisir audio", command=self.select_audio).pack(side=LEFT)
        Button(top, text="Charger analyse", command=self.load_analysis_dialog).pack(side=LEFT, padx=(6, 0))
        Label(top, textvariable=self.file_var, anchor=W).pack(side=LEFT, fill="x", expand=True, padx=10)
        Label(top, text="Modèle").pack(side=LEFT)
        ttk.Combobox(
            top,
            textvariable=self.model_var,
            values=("tiny", "base", "small", "medium"),
            width=8,
            state="readonly",
        ).pack(side=LEFT, padx=(4, 12))
        Label(top, text="Langue").pack(side=LEFT)
        Entry(top, textvariable=self.language_var, width=5).pack(side=LEFT, padx=(4, 12))
        self.analyze_button = Button(top, text="Analyser", command=self.analyze_audio)
        self.analyze_button.pack(side=LEFT)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True)
        self.analysis_tab = Frame(self.notebook)
        self.generated_tab = Frame(self.notebook)
        self.notebook.add(self.analysis_tab, text="Analyse")
        self.notebook.add(self.generated_tab, text="Audios générés")

        body = Frame(self.analysis_tab, padx=12, pady=4)
        body.pack(fill=BOTH, expand=True)

        left = Frame(body)
        left.pack(side=LEFT, fill=BOTH, expand=True)

        columns = ("index", "start", "end", "title")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=16, selectmode="extended")
        self.tree.heading("index", text="#")
        self.tree.heading("start", text="Début")
        self.tree.heading("end", text="Fin")
        self.tree.heading("title", text="Partie")
        self.tree.column("index", width=45, anchor="center")
        self.tree.column("start", width=85, anchor="center")
        self.tree.column("end", width=85, anchor="center")
        self.tree.column("title", width=360, anchor=W)
        self.tree.bind("<<TreeviewSelect>>", self.on_part_selected)
        tree_scroll = Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree_scroll.pack(side=RIGHT, fill="y")

        right = Frame(body, width=380)
        right.pack(side=RIGHT, fill=BOTH, padx=(12, 0))
        Label(right, text="Description").pack(anchor=W)
        self.description_box = Listbox(right, height=8)
        self.description_box.pack(fill=BOTH, expand=False)
        Label(right, text="Transcription de la partie").pack(anchor=W, pady=(12, 0))
        self.transcript = ttk.Treeview(right, columns=("text",), show="", height=10)
        self.transcript.column("text", width=420, anchor=W)
        transcript_scroll = Scrollbar(right, orient="vertical", command=self.transcript.yview)
        self.transcript.configure(yscrollcommand=transcript_scroll.set)
        self.transcript.pack(side=LEFT, fill=BOTH, expand=True)
        transcript_scroll.pack(side=RIGHT, fill="y")

        export = Frame(self.analysis_tab, padx=12, pady=8)
        export.pack(fill="x")
        Label(export, text="Titre export").pack(side=LEFT)
        Entry(export, textvariable=self.export_title_var, width=36).pack(side=LEFT, padx=(4, 12))
        Label(export, text="Début").pack(side=LEFT)
        Entry(export, textvariable=self.start_var, width=10).pack(side=LEFT, padx=(4, 12))
        Label(export, text="Fin").pack(side=LEFT)
        Entry(export, textvariable=self.end_var, width=10).pack(side=LEFT, padx=(4, 12))
        self.export_button = Button(export, text="Exporter en WAV", command=self.export_selected)
        self.export_button.pack(side=LEFT)
        Label(export, textvariable=self.status_var, anchor=W).pack(side=LEFT, fill="x", expand=True, padx=12)

        self._build_generated_tab()

    def _build_generated_tab(self) -> None:
        container = Frame(self.generated_tab, padx=12, pady=10)
        container.pack(fill=BOTH, expand=True)

        top = Frame(container)
        top.pack(fill=BOTH, expand=True)

        left = Frame(top)
        left.pack(side=LEFT, fill=BOTH, expand=True)
        Label(left, text="Audios générés").pack(anchor=W)
        self.generated_box = Listbox(left, height=12, exportselection=False)
        generated_scroll = Scrollbar(left, orient="vertical", command=self.generated_box.yview)
        self.generated_box.configure(yscrollcommand=generated_scroll.set)
        self.generated_box.pack(side=LEFT, fill=BOTH, expand=True)
        generated_scroll.pack(side=RIGHT, fill="y")
        self.generated_box.bind("<<ListboxSelect>>", self.on_generated_selected)
        self.generated_box.bind("<Double-Button-1>", self.play_selected_export)

        right = Frame(top, padx=12)
        right.pack(side=RIGHT, fill=BOTH)
        Button(right, text="Ajouter WAV", command=self.add_generated_audio).pack(fill="x", pady=(0, 6))
        Button(right, text="Lire", command=self.play_selected_export).pack(fill="x", pady=(0, 6))
        Button(right, text="Pause", command=self.pause_playback).pack(fill="x", pady=(0, 6))
        Button(right, text="Stop", command=self.stop_playback).pack(fill="x")

        player = Frame(container, pady=10)
        player.pack(fill="x")
        Label(player, textvariable=self.player_file_var, anchor=W).pack(fill="x")
        self.player_scale = ttk.Scale(
            player,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.player_position_var,
            command=self.on_player_scale,
        )
        self.player_scale.pack(fill="x", pady=(6, 2))
        controls = Frame(player)
        controls.pack(fill="x")
        Button(controls, text="-10s", command=lambda: self.jump_player(-10)).pack(side=LEFT)
        Button(controls, text="-5s", command=lambda: self.jump_player(-5)).pack(side=LEFT, padx=(6, 0))
        Label(controls, textvariable=self.player_time_var).pack(side=LEFT, padx=12)
        Button(controls, text="+5s", command=lambda: self.jump_player(5)).pack(side=LEFT)
        Button(controls, text="+10s", command=lambda: self.jump_player(10)).pack(side=LEFT, padx=(6, 12))
        Label(controls, text="Vitesse").pack(side=LEFT)
        speed_box = ttk.Combobox(
            controls,
            textvariable=self.player_speed_var,
            values=("0.75x", "1.0x", "1.25x", "1.5x", "2.0x"),
            width=7,
            state="readonly",
        )
        speed_box.pack(side=LEFT, padx=(4, 0))
        speed_box.bind("<<ComboboxSelected>>", self.on_speed_changed)

        extract = ttk.LabelFrame(container, text="Créer un sous-audio depuis l'audio généré")
        extract.pack(fill="x", pady=(8, 0))
        Label(extract, text="Titre").pack(side=LEFT, padx=(8, 4), pady=8)
        Entry(extract, textvariable=self.generated_title_var, width=28).pack(side=LEFT, padx=(0, 10))
        Label(extract, text="Début").pack(side=LEFT)
        Entry(extract, textvariable=self.generated_start_var, width=9).pack(side=LEFT, padx=(4, 4))
        Button(extract, text="= position", command=lambda: self.set_generated_mark("start")).pack(side=LEFT, padx=(0, 8))
        Label(extract, text="Fin").pack(side=LEFT)
        Entry(extract, textvariable=self.generated_end_var, width=9).pack(side=LEFT, padx=(4, 4))
        Button(extract, text="= position", command=lambda: self.set_generated_mark("end")).pack(side=LEFT, padx=(0, 8))
        Button(extract, text="Exporter sous-audio", command=self.export_generated_subclip).pack(side=LEFT)

        replace = ttk.LabelFrame(container, text="Correction ponctuelle d'une plage")
        replace.pack(fill="x", pady=(8, 0))
        Label(replace, text="Début").pack(side=LEFT, padx=(8, 4), pady=8)
        Entry(replace, textvariable=self.replace_start_var, width=9).pack(side=LEFT)
        Button(replace, text="= position", command=lambda: self.set_replace_mark("start")).pack(side=LEFT, padx=(4, 8))
        Label(replace, text="Fin").pack(side=LEFT)
        Entry(replace, textvariable=self.replace_end_var, width=9).pack(side=LEFT, padx=(4, 4))
        Button(replace, text="= position", command=lambda: self.set_replace_mark("end")).pack(side=LEFT, padx=(0, 8))
        Button(replace, text="Silence", command=self.replace_with_silence).pack(side=LEFT, padx=(0, 6))
        Button(replace, text="Fichier WAV", command=self.replace_with_audio_file).pack(side=LEFT, padx=(0, 8))
        Label(replace, text="Texte").pack(side=LEFT)
        Entry(replace, textvariable=self.replace_text_var, width=22).pack(side=LEFT, padx=(4, 4))
        Button(replace, text="TTS", command=self.replace_with_tts).pack(side=LEFT)

        voice = ttk.LabelFrame(container, text="Prise voix pour correction")
        voice.pack(fill="x", pady=(8, 0))
        Button(voice, text="Enregistrer micro", command=self.start_voice_recording).pack(side=LEFT, padx=(8, 6), pady=8)
        Button(voice, text="Arrêter", command=self.stop_voice_recording).pack(side=LEFT, padx=(0, 6))
        Button(voice, text="Lire prise", command=self.play_voice_recording).pack(side=LEFT, padx=(0, 6))
        Button(voice, text="Remplacer par prise", command=self.replace_with_voice_recording).pack(side=LEFT, padx=(0, 10))
        Label(voice, textvariable=self.recording_status_var, anchor=W).pack(side=LEFT, fill="x", expand=True)
        Label(container, textvariable=self.status_var, anchor=W).pack(fill="x", pady=(8, 0))

    def select_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="Choisir un fichier audio",
            filetypes=(
                ("Audio", "*.aac *.m4a *.mp3 *.wav *.ogg *.flac *.opus"),
                ("Tous les fichiers", "*.*"),
            ),
        )
        if not path:
            return
        self.audio_path = Path(path)
        self.file_var.set(str(self.audio_path))
        self.status_var.set("Fichier prêt. Lance l'analyse pour créer les parties.")
        self.parts = []
        self.segments = []
        self.current_analysis_path = None
        self.refresh_parts()
        expected = ANALYSIS_DIR / analysis_filename(self.audio_path)
        if expected.exists():
            self.status_var.set(f"Analyse existante trouvée: {expected}")

    def show_version(self) -> None:
        messagebox.showinfo(APP_TITLE, f"{APP_TITLE}\nVersion {APP_VERSION}")

    def show_readme(self) -> None:
        readme = APP_DIR / "README.md"
        try:
            content = readme.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de lire le README: {exc}")
            return
        window = Toplevel(self.root)
        window.title(f"{APP_TITLE} - README")
        window.geometry("820x640")
        frame = Frame(window, padx=10, pady=10)
        frame.pack(fill=BOTH, expand=True)
        text = Text(frame, wrap="word")
        scroll = Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill="y")
        text.insert("1.0", content)
        text.configure(state="disabled")

    def load_analysis_dialog(self) -> None:
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            title="Charger une analyse",
            initialdir=str(ANALYSIS_DIR),
            filetypes=(("Analyses JSON", "*.json"), ("Tous les fichiers", "*.*")),
        )
        if not path:
            return
        try:
            audio_path, segments, parts = load_analysis(Path(path))
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Analyse illisible: {exc}")
            return
        if not audio_path.exists():
            replacement = filedialog.askopenfilename(
                title="Retrouver le fichier audio original",
                filetypes=(
                    ("Audio", "*.aac *.m4a *.mp3 *.wav *.ogg *.flac *.opus"),
                    ("Tous les fichiers", "*.*"),
                ),
            )
            if not replacement:
                messagebox.showwarning(APP_TITLE, "Analyse chargée annulée: audio original introuvable.")
                return
            audio_path = Path(replacement)
        self.audio_path = audio_path
        self.segments = segments
        self.parts = parts
        self.current_analysis_path = Path(path)
        self.file_var.set(str(self.audio_path))
        self.refresh_parts()
        self.status_var.set(f"Analyse chargée: {path}")

    def analyze_audio(self) -> None:
        if self.audio_path is None:
            messagebox.showwarning(APP_TITLE, "Choisis d'abord un fichier audio.")
            return
        self.analyze_button.configure(state="disabled")
        self.status_var.set("Analyse en cours...")
        thread = threading.Thread(target=self._analyze_worker, daemon=True)
        thread.start()

    def _analyze_worker(self) -> None:
        assert self.audio_path is not None
        try:
            segments = transcribe_audio(
                self.audio_path,
                self.model_var.get(),
                self.language_var.get().strip(),
                lambda message: self.messages.put(("status", message)),
            )
            parts = segment_course(segments)
            analysis_path = save_analysis(self.audio_path, segments, parts)
            self.messages.put(("analysis", (segments, parts, analysis_path)))
        except Exception as exc:  # GUI boundary
            self.messages.put(("error", str(exc)))

    def _drain_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "analysis":
                    self.segments, self.parts, analysis_path = payload  # type: ignore[misc]
                    self.current_analysis_path = analysis_path
                    self.refresh_parts()
                    self.status_var.set(f"Analyse terminée. Sauvegardée: {analysis_path}")
                    self.analyze_button.configure(state="normal")
                elif kind == "export_done":
                    exported = Path(payload)
                    self.exported_files.append(exported)
                    self.refresh_generated_files(select_last=True)
                    self.notebook.select(self.generated_tab)
                    self.status_var.set(f"Export terminé: {exported}")
                    self.export_button.configure(state="normal")
                elif kind == "error":
                    self.status_var.set("Erreur.")
                    self.analyze_button.configure(state="normal")
                    self.export_button.configure(state="normal")
                    messagebox.showerror(APP_TITLE, str(payload))
        except queue.Empty:
            pass
        self.root.after(150, self._drain_messages)

    def refresh_parts(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for part in self.parts:
            self.tree.insert(
                "",
                END,
                iid=str(part.index - 1),
                values=(part.index, format_time(part.start), format_time(part.end), part.title),
            )
        self.description_box.delete(0, END)
        for item in self.transcript.get_children():
            self.transcript.delete(item)
        self.start_var.set("00:00")
        self.end_var.set("00:00")
        self.export_title_var.set("extrait")

    def selected_part(self) -> CoursePart | None:
        parts = self.selected_parts()
        if not parts:
            return None
        return parts[0]

    def selected_parts(self) -> list[CoursePart]:
        selected: list[CoursePart] = []
        for item in self.tree.selection():
            index = int(item)
            if 0 <= index < len(self.parts):
                selected.append(self.parts[index])
        return sorted(selected, key=lambda part: part.index)

    def on_part_selected(self, _event=None) -> None:
        parts = self.selected_parts()
        if not parts:
            return
        self.start_var.set(format_time(parts[0].start))
        self.end_var.set(format_time(parts[-1].end))
        self.description_box.delete(0, END)
        for item in self.transcript.get_children():
            self.transcript.delete(item)
        if len(parts) == 1:
            part = parts[0]
            self.export_title_var.set(export_title_for(parts))
            for line in wrap_text(part.description, 58):
                self.description_box.insert(END, line)
            for line in wrap_text(part.transcript, 70):
                self.transcript.insert("", END, values=(line,))
            return

        self.export_title_var.set(export_title_for(parts))
        self.description_box.insert(END, f"{len(parts)} parties sélectionnées")
        self.description_box.insert(END, f"Durée totale: {format_time(sum(part.end - part.start for part in parts))}")
        for part in parts:
            label = f"{part.index}. {format_time(part.start)}-{format_time(part.end)} {part.title}"
            for line in wrap_text(label, 58):
                self.description_box.insert(END, line)
        for part in parts:
            self.transcript.insert("", END, values=(f"--- {part.index}. {part.title} ---",))
            for line in wrap_text(part.description, 70):
                self.transcript.insert("", END, values=(line,))

    def export_selected(self) -> None:
        if self.audio_path is None:
            messagebox.showwarning(APP_TITLE, "Choisis d'abord un fichier audio.")
            return
        parts = self.selected_parts()
        if len(parts) > 1:
            ranges = [(part.start, part.end) for part in parts]
        else:
            try:
                start = parse_time(self.start_var.get())
                end = parse_time(self.end_var.get())
            except ValueError as exc:
                messagebox.showerror(APP_TITLE, f"Temps invalide: {exc}")
                return
            if end <= start:
                messagebox.showerror(APP_TITLE, "La fin doit être après le début.")
                return
            ranges = [(start, end)]

        export_title = self.export_title_var.get().strip() or export_title_for(parts)
        if len(parts) > 1:
            indexes = "_".join(str(part.index).zfill(2) for part in parts[:6])
            suffix = "_etc" if len(parts) > 6 else ""
            suggested = f"{safe_filename(export_title)}_{indexes}{suffix}.wav"
        elif parts:
            suggested = f"{parts[0].index:02d}_{safe_filename(export_title)}.wav"
        else:
            suggested = f"{safe_filename(export_title)}.wav"
        output = filedialog.asksaveasfilename(
            title="Enregistrer l'extrait",
            defaultextension=".wav",
            initialfile=suggested,
            filetypes=(("WAV", "*.wav"),),
        )
        if not output:
            return
        self.export_button.configure(state="disabled")
        self.status_var.set("Export audio en cours...")
        thread = threading.Thread(
            target=self._export_worker,
            args=(Path(output), ranges),
            daemon=True,
        )
        thread.start()

    def _export_worker(self, output: Path, ranges: list[tuple[float, float]]) -> None:
        assert self.audio_path is not None
        try:
            export_clips(self.audio_path, output, ranges)
            self.messages.put(("export_done", output))
        except Exception as exc:
            self.messages.put(("error", str(exc)))

    def refresh_generated_files(self, select_last: bool = False) -> None:
        self.generated_box.delete(0, END)
        for path in self.exported_files:
            self.generated_box.insert(END, path.name)
        if select_last and self.exported_files:
            last_index = len(self.exported_files) - 1
            self.generated_box.selection_set(last_index)
            self.generated_box.see(last_index)
            self.on_generated_selected()

    def add_generated_audio(self) -> None:
        path = filedialog.askopenfilename(
            title="Ajouter un audio généré",
            filetypes=(("WAV", "*.wav"), ("Audio", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus"), ("Tous", "*.*")),
        )
        if not path:
            return
        audio = Path(path)
        self.exported_files.append(audio)
        self.refresh_generated_files(select_last=True)

    def selected_generated_audio(self) -> Path | None:
        selection = self.generated_box.curselection()
        if selection:
            index = int(selection[0])
            if 0 <= index < len(self.exported_files):
                return self.exported_files[index]
        if self.exported_files:
            return self.exported_files[-1]
        return None

    def on_generated_selected(self, _event=None) -> None:
        path = self.selected_generated_audio()
        if path is None:
            return
        self.current_generated_audio = path
        self.player_file_var.set(str(path))
        try:
            duration = audio_duration(path)
        except Exception:
            duration = 0.0
        self.player_duration = duration
        self.player_updating = True
        self.player_scale.configure(to=max(duration, 1.0))
        self.player_position_var.set(0.0)
        self.player_updating = False
        self.player_time_var.set(f"00:00 / {format_time(duration)}")
        self.generated_start_var.set("00:00")
        self.generated_end_var.set(format_time(duration))
        self.replace_start_var.set("00:00")
        self.replace_end_var.set("00:00")
        self.generated_title_var.set(path.stem)

    def play_selected_export(self, _event=None) -> None:
        path = self.selected_generated_audio()
        if path is None:
            messagebox.showwarning(APP_TITLE, "Aucun audio généré à lire.")
            return
        if not path.exists():
            messagebox.showerror(APP_TITLE, f"Fichier introuvable: {path}")
            return
        if self.player is None:
            messagebox.showerror(APP_TITLE, "Lecteur audio GStreamer indisponible.")
            return
        try:
            if path != self.current_generated_audio:
                self.on_generated_selected()
            self.player.set_file(path)
            self.player.set_rate(self.parse_speed())
            self.player.play()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de lire l'audio: {exc}")
            return
        self.status_var.set(f"Lecture: {path.name}")

    def pause_playback(self) -> None:
        if self.player:
            self.player.pause()
            self.status_var.set("Lecture en pause.")

    def stop_playback(self, update_status: bool = True) -> None:
        if self.player:
            self.player.stop()
        if update_status:
            self.status_var.set("Lecture arrêtée.")

    def update_player_position(self) -> None:
        if self.player:
            try:
                position = self.player.query_position()
                duration = self.player.query_duration() or self.player_duration
                if duration > 0:
                    self.player_duration = duration
                    self.player_updating = True
                    self.player_scale.configure(to=max(duration, 1.0))
                    self.player_position_var.set(min(position, duration))
                    self.player_updating = False
                    self.player_time_var.set(f"{format_time(position)} / {format_time(duration)}")
            except Exception:
                self.player_updating = False
        self.root.after(250, self.update_player_position)

    def on_player_scale(self, value: str) -> None:
        if self.player_updating or self.player is None:
            return
        try:
            self.player.seek(float(value))
        except Exception:
            pass

    def jump_player(self, delta: float) -> None:
        if self.player is None:
            return
        try:
            current = self.player.query_position()
            self.player.seek(max(0.0, min(current + delta, self.player_duration or current + delta)))
        except Exception:
            pass

    def parse_speed(self) -> float:
        value = self.player_speed_var.get().replace("x", "").strip()
        try:
            return float(value)
        except ValueError:
            return 1.0

    def on_speed_changed(self, _event=None) -> None:
        if self.player:
            try:
                self.player.set_rate(self.parse_speed())
            except Exception as exc:
                messagebox.showerror(APP_TITLE, f"Impossible de changer la vitesse: {exc}")

    def current_player_position(self) -> float:
        if self.player:
            try:
                return self.player.query_position()
            except Exception:
                pass
        return self.player_position_var.get()

    def set_generated_mark(self, kind: str) -> None:
        value = format_time(self.current_player_position())
        if kind == "start":
            self.generated_start_var.set(value)
        else:
            self.generated_end_var.set(value)

    def set_replace_mark(self, kind: str) -> None:
        value = format_time(self.current_player_position())
        if kind == "start":
            self.replace_start_var.set(value)
        else:
            self.replace_end_var.set(value)

    def export_generated_subclip(self) -> None:
        path = self.selected_generated_audio()
        if path is None:
            messagebox.showwarning(APP_TITLE, "Sélectionne un audio généré.")
            return
        try:
            start = parse_time(self.generated_start_var.get())
            end = parse_time(self.generated_end_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, f"Temps invalide: {exc}")
            return
        if end <= start:
            messagebox.showerror(APP_TITLE, "La fin doit être après le début.")
            return
        suggested = f"{safe_filename(self.generated_title_var.get())}.wav"
        output = filedialog.asksaveasfilename(
            title="Enregistrer le sous-audio",
            defaultextension=".wav",
            initialfile=suggested,
            filetypes=(("WAV", "*.wav"),),
        )
        if not output:
            return
        try:
            export_clip(path, Path(output), start, end)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Export impossible: {exc}")
            return
        self.exported_files.append(Path(output))
        self.refresh_generated_files(select_last=True)
        self.status_var.set(f"Sous-audio généré: {output}")

    def replace_with_silence(self) -> None:
        self.replace_generated_range(None)

    def replace_with_audio_file(self) -> None:
        replacement = filedialog.askopenfilename(
            title="Choisir un WAV de remplacement",
            filetypes=(("WAV", "*.wav"), ("Tous", "*.*")),
        )
        if replacement:
            self.replace_generated_range(Path(replacement))

    def replace_with_tts(self) -> None:
        text = self.replace_text_var.get().strip()
        if not text:
            messagebox.showwarning(APP_TITLE, "Écris le mot ou la phrase de remplacement.")
            return
        messagebox.showinfo(
            APP_TITLE,
            "La génération vocale locale vers fichier n'est pas disponible sur cette machine. "
            "Tu peux pour l'instant remplacer la plage par un silence ou par un fichier WAV court.",
        )

    def recording_format_for_selected_audio(self) -> tuple[int, int]:
        path = self.selected_generated_audio()
        if path and path.suffix.lower() == ".wav" and path.exists():
            with wave.open(str(path), "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise ValueError("La prise voix nécessite un WAV cible en 16 bits.")
                return wav.getnchannels(), wav.getframerate()
        return 2, 48000

    def start_voice_recording(self) -> None:
        if self.recorder is not None:
            messagebox.showwarning(APP_TITLE, "Un enregistrement est déjà en cours.")
            return
        if self.selected_generated_audio() is None:
            messagebox.showwarning(APP_TITLE, "Sélectionne l'audio généré à corriger.")
            return
        try:
            channels, sample_rate = self.recording_format_for_selected_audio()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.recording_path = RECORDINGS_DIR / f"prise_voix_{timestamp}.wav"
        try:
            self.stop_playback(update_status=False)
            self.recorder = GstAudioRecorder(self.recording_path, channels=channels, sample_rate=sample_rate)
            self.recorder.start()
        except Exception as exc:
            self.recorder = None
            self.recording_path = None
            messagebox.showerror(APP_TITLE, f"Impossible d'enregistrer le micro: {exc}")
            return
        self.recording_status_var.set(f"Enregistrement en cours: {self.recording_path.name}")
        self.status_var.set("Enregistrement micro en cours...")

    def stop_voice_recording(self) -> None:
        if self.recorder is None:
            messagebox.showwarning(APP_TITLE, "Aucun enregistrement en cours.")
            return
        try:
            self.recorder.stop()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Arrêt d'enregistrement impossible: {exc}")
            return
        finally:
            self.recorder = None
        if self.recording_path and self.recording_path.exists():
            try:
                duration = audio_duration(self.recording_path)
                self.recording_status_var.set(f"Prise prête: {self.recording_path.name} ({format_time(duration)})")
            except Exception:
                self.recording_status_var.set(f"Prise prête: {self.recording_path.name}")
        self.status_var.set("Enregistrement micro terminé.")

    def play_voice_recording(self) -> None:
        if self.recording_path is None or not self.recording_path.exists():
            messagebox.showwarning(APP_TITLE, "Aucune prise voix à lire.")
            return
        if self.player is None:
            messagebox.showerror(APP_TITLE, "Lecteur audio GStreamer indisponible.")
            return
        try:
            duration = audio_duration(self.recording_path)
            self.player_file_var.set(str(self.recording_path))
            self.player_duration = duration
            self.player_updating = True
            self.player_scale.configure(to=max(duration, 1.0))
            self.player_position_var.set(0.0)
            self.player_updating = False
            self.player_time_var.set(f"00:00 / {format_time(duration)}")
            self.player.set_file(self.recording_path)
            self.player.set_rate(self.parse_speed())
            self.player.play()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de lire la prise voix: {exc}")
            return
        self.status_var.set(f"Lecture prise voix: {self.recording_path.name}")

    def replace_with_voice_recording(self) -> None:
        if self.recording_path is None or not self.recording_path.exists():
            messagebox.showwarning(APP_TITLE, "Enregistre d'abord une prise voix.")
            return
        self.replace_generated_range(self.recording_path)

    def replace_generated_range(self, replacement: Path | None) -> None:
        path = self.selected_generated_audio()
        if path is None:
            messagebox.showwarning(APP_TITLE, "Sélectionne un audio généré.")
            return
        if path.suffix.lower() != ".wav":
            messagebox.showerror(APP_TITLE, "La correction ponctuelle fonctionne sur les WAV générés.")
            return
        try:
            start = parse_time(self.replace_start_var.get())
            end = parse_time(self.replace_end_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, f"Temps invalide: {exc}")
            return
        suggested = f"{safe_filename(path.stem)}_corrige.wav"
        output = filedialog.asksaveasfilename(
            title="Enregistrer l'audio corrigé",
            defaultextension=".wav",
            initialfile=suggested,
            filetypes=(("WAV", "*.wav"),),
        )
        if not output:
            return
        try:
            replace_wav_range(path, Path(output), start, end, replacement)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Correction impossible: {exc}")
            return
        self.exported_files.append(Path(output))
        self.refresh_generated_files(select_last=True)
        self.status_var.set(f"Audio corrigé: {output}")

    def on_close(self) -> None:
        if self.recorder is not None:
            try:
                self.recorder.stop()
            except Exception:
                pass
            self.recorder = None
        self.stop_playback(update_status=False)
        self.root.destroy()


def wrap_text(text: str, width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        extra = 1 if current else 0
        if current and current_len + len(word) + extra > width:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + extra
    if current:
        lines.append(" ".join(current))
    return lines


def main() -> None:
    root = Tk()
    CourseCutterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
