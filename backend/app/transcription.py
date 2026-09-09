from __future__ import annotations

import hashlib
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import av
from av.audio.resampler import AudioResampler

from drsm_core import TranscriptSegment, audio_duration, transcribe_audio


@dataclass(frozen=True)
class ProviderTranscription:
    segments: tuple[TranscriptSegment, ...]
    duration_seconds: float
    request_id: str | None = None
    reused: bool = False


class TranscriptionProvider(Protocol):
    provider: str
    model: str

    def transcribe(self, path: Path, language: str) -> ProviderTranscription: ...


@dataclass(frozen=True)
class AudioChunk:
    index: int
    path: Path
    start_seconds: float
    end_seconds: float
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


def _value(item: object, name: str, default: object = None) -> object:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


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

    def transcribe(self, path: Path, language: str) -> ProviderTranscription:
        with path.open("rb") as audio:
            response = self.client.audio.transcriptions.create(
                file=audio,
                model=self.model,
                language=language or None,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        duration = float(_value(response, "duration", 0.0) or audio_duration(path))
        raw_segments = _value(response, "segments", ()) or ()
        segments: list[TranscriptSegment] = []
        for raw in raw_segments:
            text = str(_value(raw, "text", "")).strip()
            if not text:
                continue
            start = max(0.0, float(_value(raw, "start", 0.0) or 0.0))
            end = max(start, float(_value(raw, "end", start) or start))
            segments.append(TranscriptSegment(start, end, text))
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

    def transcribe(self, path: Path, language: str) -> ProviderTranscription:
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
            for converted in resampler.resample(frame):
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
) -> list[AudioChunk]:
    total = audio_duration(source_path)
    if total <= 0:
        raise ValueError("Audio duration must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = [
        (start, min(start + chunk_seconds, total))
        for start in range(0, int(math.ceil(total)), chunk_seconds)
    ]
    encoded: list[tuple[Path, float, float]] = []
    sequence = 0
    while pending:
        start, end = pending.pop(0)
        sequence += 1
        path = output_dir / f"chunk-{sequence:04d}.wav"
        _encode_wav_range(source_path, path, start, end)
        if path.stat().st_size > max_bytes:
            path.unlink(missing_ok=True)
            if end - start <= 30:
                raise ValueError("Transcription fragment remains too large after compression")
            middle = start + (end - start) / 2
            pending[0:0] = [(start, middle), (middle, end)]
            continue
        encoded.append((path, start, end))
    encoded.sort(key=lambda item: item[1])
    return [
        AudioChunk(
            index=index,
            path=path,
            start_seconds=start,
            end_seconds=end,
            duration_seconds=audio_duration(path),
            checksum_sha256=_sha256(path),
        )
        for index, (path, start, end) in enumerate(encoded)
    ]


def transcribe_in_chunks(
    source_path: Path,
    workspace: Path,
    provider: TranscriptionProvider,
    language: str,
    *,
    chunk_seconds: int,
    max_bytes: int,
    progress: Callable[[str, float], None] | None = None,
    control_point: Callable[[], None] | None = None,
    on_usage: UsageCallback | None = None,
) -> list[TranscriptSegment]:
    chunk_dir = workspace / "transcription-chunks"
    try:
        chunks = create_audio_chunks(
            source_path,
            chunk_dir,
            chunk_seconds=chunk_seconds,
            max_bytes=max_bytes,
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
            result = provider.transcribe(chunk.path, language)
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
                start = chunk.start_seconds + max(0.0, segment.start)
                end = chunk.start_seconds + max(segment.start, segment.end)
                merged.append(
                    TranscriptSegment(
                        min(start, chunk.end_seconds),
                        min(max(start, end), chunk.end_seconds),
                        segment.text,
                    )
                )
            chunk.path.unlink(missing_ok=True)
        if progress:
            progress("Transcription cloud terminée", 1.0)
        return merged
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)
