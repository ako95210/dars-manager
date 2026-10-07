from __future__ import annotations

import hashlib
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import av
from av.audio.resampler import AudioResampler

from drsm_core import TranscriptSegment, audio_duration, transcribe_audio, trim_audio_frame

from .terminology import transcription_prompt


@dataclass(frozen=True)
class ProviderTranscription:
    segments: tuple[TranscriptSegment, ...]
    duration_seconds: float
    request_id: str | None = None
    reused: bool = False


class TranscriptionProvider(Protocol):
    provider: str
    model: str

    def transcribe(
        self,
        path: Path,
        language: str,
        *,
        prompt: str | None = None,
    ) -> ProviderTranscription: ...


@dataclass(frozen=True)
class AudioChunk:
    index: int
    path: Path
    start_seconds: float
    end_seconds: float
    core_start_seconds: float
    core_end_seconds: float
    duration_seconds: float
    checksum_sha256: str


@dataclass(frozen=True)
class TranscriptionCall:
    provider: str
    model: str
    chunk_index: int
    chunk_count: int
    duration_seconds: float
    checksum_sha256: str
    request_id: str | None
    reused: bool


UsageCallback = Callable[[TranscriptionCall], None]


def estimated_transcription_billed_seconds(
    duration_seconds: float,
    chunk_seconds: int,
    overlap_seconds: float,
) -> int:
    duration = max(0.0, float(duration_seconds))
    if duration == 0:
        return 0
    chunks = max(1, math.ceil(duration / chunk_seconds))
    overlap = max(0.0, min(float(overlap_seconds), chunk_seconds / 4))
    return math.ceil(duration + 2 * overlap * max(0, chunks - 1))


def _value(item: object, name: str, default: object = None) -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _join_word(previous: str, word: str) -> str:
    raw = str(word)
    stripped = raw.strip()
    if not stripped:
        return previous
    if not previous:
        return stripped
    if raw[:1].isspace() or stripped[0] in ".,;:!?…،؛؟)]}»":
        return previous + raw.rstrip()
    return f"{previous} {stripped}"


def _subtitle_segments_from_words(words: object) -> tuple[TranscriptSegment, ...]:
    """Build short readable cues while retaining real word-level timestamps."""
    if not isinstance(words, (list, tuple)):
        return ()
    prepared: list[tuple[float, float, str]] = []
    for item in words:
        text = str(_value(item, "word", "") or "")
        start = max(0.0, float(_value(item, "start", 0.0) or 0.0))
        end = max(start, float(_value(item, "end", start) or start))
        if text.strip() and end > start:
            prepared.append((start, end, text))
    if not prepared:
        return ()

    cues: list[TranscriptSegment] = []
    cue_start = prepared[0][0]
    cue_end = cue_start
    cue_text = ""
    word_count = 0
    for start, end, word in prepared:
        if cue_text and start - cue_end >= 0.8:
            cues.append(TranscriptSegment(cue_start, cue_end, cue_text.strip()))
            cue_start, cue_text, word_count = start, "", 0
        candidate = _join_word(cue_text, word)
        would_overflow = bool(cue_text) and (
            word_count >= 8
            or len(candidate) > 72
            or end - cue_start > 3.5
        )
        if would_overflow:
            cues.append(TranscriptSegment(cue_start, cue_end, cue_text.strip()))
            cue_start, cue_text, word_count = start, "", 0
        cue_text = _join_word(cue_text, word)
        cue_end = end
        word_count += 1
        if word_count >= 3 and cue_text.rstrip().endswith((".", "!", "?", "…", "؟")):
            cues.append(TranscriptSegment(cue_start, cue_end, cue_text.strip()))
            cue_text, word_count = "", 0
    if cue_text:
        cues.append(TranscriptSegment(cue_start, cue_end, cue_text.strip()))
    return tuple(cues)


class OpenAIWhisperProvider:
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "whisper-1",
        timeout_seconds: float = 900,
        client: object | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("OPENAI_API_KEY is required for cloud transcription")
        self.model = model
        if client is None:
            from openai import OpenAI

            # Disable implicit SDK retries: every paid retry must remain under
            # the durable worker's control and be visible in its attempt log.
            client = OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)
        self.client = client

    def transcribe(
        self,
        path: Path,
        language: str,
        *,
        prompt: str | None = None,
    ) -> ProviderTranscription:
        with path.open("rb") as audio:
            request = dict(
                file=audio,
                model=self.model,
                language=language or None,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
            if prompt:
                request["prompt"] = prompt
            response = self.client.audio.transcriptions.create(**request)
        duration = float(_value(response, "duration", 0.0) or audio_duration(path))
        word_segments = _subtitle_segments_from_words(_value(response, "words", ()) or ())
        raw_segments = _value(response, "segments", ()) or ()
        segments: list[TranscriptSegment] = []
        for raw in raw_segments:
            text = str(_value(raw, "text", "")).strip()
            if not text:
                continue
            start = max(0.0, float(_value(raw, "start", 0.0) or 0.0))
            end = max(start, float(_value(raw, "end", start) or start))
            segments.append(TranscriptSegment(start, end, text))
        # Provider segments carry the complete transcript. Word timestamp lists
        # can omit short words and contractions on difficult or multilingual
        # recordings, so they are only a fallback when no segment was returned.
        if not segments and word_segments:
            segments = list(word_segments)
        if not segments:
            text = str(_value(response, "text", "")).strip()
            if text:
                segments.append(TranscriptSegment(0.0, duration, text))
        return ProviderTranscription(
            segments=tuple(segments),
            duration_seconds=duration,
            request_id=str(_value(response, "_request_id", "") or "") or None,
        )


class LocalWhisperProvider:
    provider = "local"

    def __init__(
        self,
        model: str,
        *,
        cpu_threads: int = 1,
        progress: Callable[[str], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self.model = model
        self.cpu_threads = cpu_threads
        self.progress = progress or (lambda _message: None)
        self.should_pause = should_pause
        self.should_cancel = should_cancel

    def transcribe(
        self,
        path: Path,
        language: str,
        *,
        prompt: str | None = None,
    ) -> ProviderTranscription:
        segments = transcribe_audio(
            path,
            self.model,
            language,
            self.progress,
            should_pause=self.should_pause,
            should_cancel=self.should_cancel,
            cpu_threads=self.cpu_threads,
        )
        return ProviderTranscription(
            segments=tuple(segments),
            duration_seconds=audio_duration(path),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _encode_wav_range(source_path: Path, output_path: Path, start: float, end: float) -> None:
    """Create a predictable mono/16 kHz PCM fragment accepted by Whisper."""
    source = av.open(str(source_path))
    source_stream = next((stream for stream in source.streams if stream.type == "audio"), None)
    if source_stream is None:
        source.close()
        raise ValueError("No audio stream found")
    output = av.open(str(output_path), "w")
    output_stream = output.add_stream("pcm_s16le", rate=16_000)
    output_stream.layout = "mono"
    resampler = AudioResampler(format="s16", layout="mono", rate=16_000)
    try:
        source.seek(int(max(0.0, start - 1.0) * av.time_base), backward=True)
        for frame in source.decode(source_stream):
            frame_start = float(frame.time or 0.0)
            frame_end = frame_start + frame.samples / float(frame.sample_rate or 48_000)
            if frame_end <= start:
                continue
            if frame_start >= end:
                break
            frame_rate = float(frame.sample_rate or 48_000)
            first_sample = math.ceil(max(0.0, start - frame_start) * frame_rate - 1e-9)
            last_sample = math.ceil(
                max(0.0, min(frame_end, end) - frame_start) * frame_rate - 1e-9
            )
            cropped = trim_audio_frame(av, frame, first_sample, last_sample)
            if cropped.samples <= 0:
                continue
            for converted in resampler.resample(cropped):
                converted.pts = None
                for packet in output_stream.encode(converted):
                    output.mux(packet)
        for converted in resampler.resample(None):
            converted.pts = None
            for packet in output_stream.encode(converted):
                output.mux(packet)
        for packet in output_stream.encode(None):
            output.mux(packet)
    finally:
        output.close()
        source.close()


def create_audio_chunks(
    source_path: Path,
    output_dir: Path,
    *,
    chunk_seconds: int,
    max_bytes: int,
    overlap_seconds: float = 0.0,
) -> list[AudioChunk]:
    total = audio_duration(source_path)
    if total <= 0:
        raise ValueError("Audio duration must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = [
        (start, min(start + chunk_seconds, total))
        for start in range(0, int(math.ceil(total)), chunk_seconds)
    ]
    overlap = max(0.0, min(float(overlap_seconds), chunk_seconds / 4))
    encoded: list[tuple[Path, float, float, float, float]] = []
    sequence = 0
    while pending:
        core_start, core_end = pending.pop(0)
        start = max(0.0, core_start - overlap)
        end = min(total, core_end + overlap)
        sequence += 1
        path = output_dir / f"chunk-{sequence:04d}.wav"
        _encode_wav_range(source_path, path, start, end)
        if path.stat().st_size > max_bytes:
            path.unlink(missing_ok=True)
            if core_end - core_start <= 30:
                raise ValueError("Transcription fragment remains too large after compression")
            middle = core_start + (core_end - core_start) / 2
            pending[0:0] = [(core_start, middle), (middle, core_end)]
            continue
        encoded.append((path, start, end, core_start, core_end))
    encoded.sort(key=lambda item: item[3])
    return [
        AudioChunk(
            index=index,
            path=path,
            start_seconds=start,
            end_seconds=end,
            core_start_seconds=core_start,
            core_end_seconds=core_end,
            duration_seconds=audio_duration(path),
            checksum_sha256=_sha256(path),
        )
        for index, (path, start, end, core_start, core_end) in enumerate(encoded)
    ]


def transcribe_in_chunks(
    source_path: Path,
    workspace: Path,
    provider: TranscriptionProvider,
    language: str,
    *,
    chunk_seconds: int,
    max_bytes: int,
    overlap_seconds: float = 0.0,
    progress: Callable[[str, float], None] | None = None,
    control_point: Callable[[], None] | None = None,
    on_usage: UsageCallback | None = None,
    glossary_terms: list[str] | None = None,
) -> list[TranscriptSegment]:
    chunk_dir = workspace / "transcription-chunks"
    try:
        chunks = create_audio_chunks(
            source_path,
            chunk_dir,
            chunk_seconds=chunk_seconds,
            max_bytes=max_bytes,
            overlap_seconds=overlap_seconds,
        )
        merged: list[TranscriptSegment] = []
        for position, chunk in enumerate(chunks, start=1):
            if control_point:
                control_point()
            if progress:
                progress(
                    f"Transcription du fragment {position}/{len(chunks)}",
                    (position - 1) / len(chunks),
                )
            previous_context = " ".join(segment.text for segment in merged[-12:])
            prompt = transcription_prompt(glossary_terms, previous_context)
            result = provider.transcribe(chunk.path, language, prompt=prompt)
            if on_usage:
                on_usage(
                    TranscriptionCall(
                        provider=provider.provider,
                        model=provider.model,
                        chunk_index=chunk.index,
                        chunk_count=len(chunks),
                        duration_seconds=chunk.duration_seconds,
                        checksum_sha256=chunk.checksum_sha256,
                        request_id=result.request_id,
                        reused=result.reused,
                    )
                )
            for segment in result.segments:
                start = min(
                    chunk.start_seconds + max(0.0, segment.start),
                    chunk.end_seconds,
                )
                end = min(
                    chunk.start_seconds + max(segment.start, segment.end),
                    chunk.end_seconds,
                )
                if end <= start:
                    continue
                midpoint = start + (end - start) / 2
                if (
                    midpoint < chunk.core_start_seconds
                    or midpoint >= chunk.core_end_seconds
                ):
                    continue
                merged.append(
                    TranscriptSegment(
                        start,
                        end,
                        segment.text,
                    )
                )
            chunk.path.unlink(missing_ok=True)
        if progress:
            progress("Transcription cloud terminée", 1.0)
        return merged
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)
