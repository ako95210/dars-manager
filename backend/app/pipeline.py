from __future__ import annotations

import json
import math
import re
import textwrap
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import av
import numpy as np
from av.audio.resampler import AudioResampler
from PIL import Image, ImageDraw, ImageFont

from drsm_core import (
    AnalysisCancelled,
    CoursePart,
    TranscriptSegment,
    audio_duration,
    export_clips,
    format_time,
    load_analysis,
    parse_time,
    segment_course,
    transcribe_audio,
)

from .transcription import TranscriptionProvider, UsageCallback, transcribe_in_chunks
from .semantic_analysis import SemanticAnalyzer, SemanticUsageCallback


ProgressCallback = Callable[[dict], None]


@dataclass(frozen=True)
class PipelineResult:
    analysis_path: Path
    audio_path: Path
    cover_path: Path
    video_path: Path
    segment_count: int
    part_count: int
    duration_seconds: float
    elapsed_seconds: float


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    suffix = "-Bold" if bold else ""
    path = Path(f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf")
    if path.exists():
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def generate_cover(output_path: Path, title: str, subtitle: str) -> None:
    width, height = 1280, 720
    image = Image.new("RGB", (width, height), "#0b1220")
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / height
        color = (
            int(11 + 13 * ratio),
            int(18 + 25 * ratio),
            int(32 + 38 * ratio),
        )
        draw.line((0, y, width, y), fill=color)

    draw.rounded_rectangle((72, 66, 272, 112), radius=20, fill="#34d399")
    draw.text((99, 76), "DARS MANAGER", font=_font(20, bold=True), fill="#052e2b")

    title_font = _font(54, bold=True)
    lines = textwrap.wrap(title, width=34)[:4] or ["Cours audio"]
    y = 205
    for line in lines:
        draw.text((78, y), line, font=title_font, fill="#f8fafc")
        y += 72

    draw.line((80, 584, 1200, 584), fill="#334155", width=2)
    draw.text((80, 612), subtitle, font=_font(26), fill="#94a3b8")
    image.save(output_path, format="PNG", optimize=True)


def render_static_video(cover_path: Path, audio_path: Path, output_path: Path, subtitles: dict | None = None) -> None:
    """Render a lightweight one-frame-per-second H.264/AAC video."""
    duration = audio_duration(audio_path)
    from .rendering import subtitle_frame

    cover_image = Image.open(cover_path).convert("RGB")
    cover = np.asarray(cover_image)
    rate = 5 if subtitles else 1

    output = av.open(str(output_path), mode="w", options={"movflags": "+faststart"})
    video_stream = output.add_stream("libx264", rate=rate)
    video_stream.width = int(cover.shape[1])
    video_stream.height = int(cover.shape[0])
    video_stream.pix_fmt = "yuv420p"
    video_stream.options = {"preset": "veryfast", "tune": "stillimage", "crf": "28"}

    source = av.open(str(audio_path))
    source_audio = next((stream for stream in source.streams if stream.type == "audio"), None)
    if source_audio is None:
        source.close()
        output.close()
        raise ValueError("No audio stream found for video rendering")

    audio_stream = output.add_stream("aac", rate=48000)
    audio_stream.layout = "stereo"
    audio_stream.bit_rate = 128_000
    resampler = AudioResampler(format="fltp", layout="stereo", rate=48000)

    try:
        frame_count = max(1, int(math.ceil(duration * rate)))
        for index in range(frame_count):
            pixels = np.asarray(subtitle_frame(cover_image, index / rate, subtitles)) if subtitles else cover
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts = index
            for packet in video_stream.encode(frame):
                output.mux(packet)
        for packet in video_stream.encode(None):
            output.mux(packet)

        for frame in source.decode(source_audio):
            for converted in resampler.resample(frame):
                converted.pts = None
                for packet in audio_stream.encode(converted):
                    output.mux(packet)
        for packet in audio_stream.encode(None):
            output.mux(packet)
    finally:
        source.close()
        output.close()


def write_analysis(
    output_path: Path,
    source_audio: Path,
    segments: list[TranscriptSegment],
    parts: list[CoursePart],
) -> None:
    payload = {
        "schema": 3,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audio_name": source_audio.name,
        "segments": [asdict(segment) for segment in segments],
        "parts": [asdict(part) for part in parts],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(
    input_path: Path,
    workspace: Path,
    *,
    model_name: str = "base",
    language: str = "fr",
    progress: ProgressCallback | None = None,
    should_pause: Callable[[], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    reuse_analysis: Path | None = None,
    cpu_threads: int = 1,
    transcription_provider: TranscriptionProvider | None = None,
    transcription_chunk_seconds: int = 540,
    transcription_chunk_max_bytes: int = 24_000_000,
    on_transcription_usage: UsageCallback | None = None,
    semantic_analyzer: SemanticAnalyzer | None = None,
    on_semantic_usage: SemanticUsageCallback | None = None,
    chaptering_mode: str = "local",
    course_title: str | None = None,
) -> PipelineResult:
    started = time.monotonic()

    def report(stage: str, message: str, fraction: float | None = None) -> None:
        if progress:
            progress({"stage": stage, "message": message, "progress": fraction})

    def control_point() -> None:
        if should_cancel and should_cancel():
            raise AnalysisCancelled("Analysis cancelled")
        announced = False
        while should_pause and should_pause():
            if should_cancel and should_cancel():
                raise AnalysisCancelled("Analysis cancelled")
            if not announced:
                report("paused", "Analysis paused")
                announced = True
            time.sleep(0.2)

    if reuse_analysis:
        report("transcription", "Reusing existing analysis for downstream validation", 0.5)
        _, segments, parts = load_analysis(reuse_analysis)
    else:
        def transcription_progress(message: str) -> None:
            fraction = None
            if "Chargement du modèle" in message:
                fraction = 0.02
            elif "Transcription en cours" in message:
                fraction = 0.05
            else:
                match = re.search(r"Transcription:\s*([0-9:.]+)\s*/\s*([0-9:.]+)", message)
                if match:
                    current = parse_time(match.group(1))
                    total = parse_time(match.group(2))
                    if total > 0:
                        fraction = 0.05 + 0.55 * min(current / total, 1.0)
            report("transcription", message, fraction)

        if transcription_provider is None:
            segments = transcribe_audio(
                input_path,
                model_name,
                language,
                transcription_progress,
                should_pause=should_pause,
                should_cancel=should_cancel,
                cpu_threads=cpu_threads,
            )
        else:
            segments = transcribe_in_chunks(
                input_path,
                workspace,
                transcription_provider,
                language,
                chunk_seconds=transcription_chunk_seconds,
                max_bytes=transcription_chunk_max_bytes,
                progress=lambda message, fraction: report(
                    "transcription", message, 0.05 + 0.55 * fraction
                ),
                control_point=control_point,
                on_usage=on_transcription_usage,
            )
        if not segments:
            raise ValueError("Whisper did not return any transcript segment")
        if should_cancel and should_cancel():
            raise AnalysisCancelled("Analysis cancelled")
        control_point()
        if chaptering_mode == "none":
            duration = audio_duration(input_path)
            parts = [CoursePart(
                1, 0.0, duration, course_title or "Cours audio", "",
                " ".join(segment.text for segment in segments),
            )]
        elif semantic_analyzer is None:
            report("segmentation", "Découpage heuristique du cours", 0.62)
            parts = segment_course(segments)
        else:
            report(
                "semantic_analysis",
                "Analyse des sous-sujets et création des titres",
                0.62,
            )
            semantic_result = semantic_analyzer.analyze(segments, language)
            parts = list(semantic_result.parts)
            if on_semantic_usage:
                on_semantic_usage(semantic_result.call)

    if not segments:
        raise ValueError("Whisper did not return any transcript segment")
    if not parts:
        raise ValueError("No course part was detected")

    duration = audio_duration(input_path)
    analysis_path = workspace / "analysis.json"
    write_analysis(analysis_path, input_path, segments, parts)

    control_point()
    report("audio_export", "Exporting WAV", 0.7)
    export_path = workspace / "audio-export.wav"
    export_clips(input_path, export_path, [(0.0, duration)])

    control_point()
    report("cover", "Generating cover", 0.82)
    cover_path = workspace / "cover.png"
    generate_cover(
        cover_path,
        parts[0].title,
        f"{len(parts)} parties · {format_time(duration)}",
    )

    control_point()
    report("video", "Rendering static video", 0.88)
    video_path = workspace / "video.mp4"
    render_static_video(cover_path, export_path, video_path)
    elapsed = time.monotonic() - started
    report("done", "Pipeline completed", 1.0)
    return PipelineResult(
        analysis_path=analysis_path,
        audio_path=export_path,
        cover_path=cover_path,
        video_path=video_path,
        segment_count=len(segments),
        part_count=len(parts),
        duration_seconds=duration,
        elapsed_seconds=elapsed,
    )
