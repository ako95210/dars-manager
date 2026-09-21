from __future__ import annotations

import math
import textwrap
from pathlib import Path
from typing import Any

import av
import numpy as np
from av.audio.resampler import AudioResampler
from PIL import Image, ImageDraw, ImageFont, ImageOps

from drsm_core import audio_duration


FORMAT_SIZES = {
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
    "9:16": (720, 1280),
}


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    return ImageFont.truetype(str(path), size=size) if path.exists() else ImageFont.load_default()


def subtitle_frame(image: Image.Image, at_seconds: float, subtitles: dict[str, Any] | None) -> Image.Image:
    if not subtitles:
        return image
    cue = next((item for item in subtitles.get("cues", []) if float(item["start"]) <= at_seconds < float(item["end"])), None)
    if cue is None:
        return image
    output = image.copy().convert("RGB")
    draw = ImageDraw.Draw(output)
    width, height = output.size
    font_name = {"sans": "DejaVuSans-Bold.ttf", "serif": "DejaVuSerif.ttf", "mono": "DejaVuSansMono.ttf"}.get(subtitles.get("font"), "DejaVuSans-Bold.ttf")
    font_path = Path("/usr/share/fonts/truetype/dejavu") / font_name
    size = max(22, round(height * 0.045))
    selected_font = ImageFont.truetype(str(font_path), size) if font_path.exists() else font(size)
    lines = textwrap.wrap(str(cue["text"]), width=max(12, int(width / (size * 0.55))))[:3]
    caption = "\n".join(lines)
    bounds = draw.multiline_textbbox((0, 0), caption, font=selected_font, spacing=6, align="center")
    caption_width, caption_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x, y = (width - caption_width) // 2, height - caption_height - max(20, height // 15)
    padding = max(10, size // 3)
    draw.rounded_rectangle((x - padding, y - padding, x + caption_width + padding, y + caption_height + padding), radius=12, fill="#101820")
    draw.multiline_text((x, y - bounds[1]), caption, font=selected_font, fill=str(subtitles.get("color", "#ffffff")), spacing=6, align="center")
    return output


def remap_subtitles(subtitles: dict[str, Any] | None, ranges: list[tuple[float, float]]) -> dict[str, Any] | None:
    if not subtitles:
        return None
    cues = []
    offset = 0.0
    for start, end in ranges:
        for cue in subtitles.get("cues", []):
            cue_start, cue_end = float(cue["start"]), float(cue["end"])
            if cue_end > start and cue_start < end:
                cues.append({"start": offset + max(start, cue_start) - start, "end": offset + min(end, cue_end) - start, "text": cue["text"]})
        offset += end - start
    return {**subtitles, "cues": cues}


def video_frame(path: Path, at_seconds: float = 0) -> Image.Image:
    container = av.open(str(path))
    try:
        stream = next((item for item in container.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("Template video has no video track")
        if at_seconds > 0:
            container.seek(int(at_seconds * av.time_base), any_frame=False, backward=True)
        chosen = None
        for candidate in container.decode(stream):
            chosen = candidate
            if at_seconds <= 0 or candidate.time is None or float(candidate.time) >= at_seconds:
                break
        if chosen is None:
            raise ValueError("Template video has no decodable frame")
        return Image.fromarray(chosen.to_ndarray(format="rgb24"), mode="RGB")
    finally:
        container.close()


def source_image(path: Path, source_kind: str, frame_seconds: float) -> Image.Image:
    if source_kind == "video":
        return video_frame(path, frame_seconds)
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def overlay_image(
    size: tuple[int, int],
    zones: list[dict[str, Any]],
    values: dict[str, str],
) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for zone in zones:
        value = str(values.get(str(zone.get("kind", "")), "")).strip()
        if not value:
            continue
        x = round(float(zone["x"]) * width)
        y = round(float(zone["y"]) * height)
        box_width = max(1, round(float(zone["width"]) * width))
        box_height = max(1, round(float(zone["height"]) * height))
        font_size = max(12, round(float(zone.get("font_scale", 0.055)) * height))
        selected_font = font(font_size)
        average_character = max(1, font_size * 0.58)
        lines = textwrap.wrap(value, width=max(4, int(box_width / average_character))) or [value]
        line_height = round(font_size * 1.18)
        max_lines = max(1, box_height // line_height)
        lines = lines[:max_lines]
        if len(lines) == max_lines and " ".join(lines) != value:
            lines[-1] = lines[-1].rstrip(" …") + "…"
        text = "\n".join(lines)
        align = str(zone.get("align", "left"))
        text_x = x + box_width / 2 if align == "center" else x + box_width if align == "right" else x
        draw.multiline_text(
            (text_x, y),
            text,
            font=selected_font,
            fill=str(zone.get("color", "#ffffff")),
            spacing=max(2, round(font_size * 0.18)),
            align=align,
            anchor="ma" if align == "center" else "ra" if align == "right" else "la",
        )
    return overlay


def compose_cover(
    source_path: Path,
    output_path: Path,
    *,
    source_kind: str,
    frame_seconds: float,
    output_format: str,
    zones: list[dict[str, Any]],
    values: dict[str, str],
) -> None:
    size = FORMAT_SIZES[output_format]
    background = ImageOps.fit(
        source_image(source_path, source_kind, frame_seconds),
        size,
        method=Image.Resampling.LANCZOS,
    ).convert("RGBA")
    background.alpha_composite(overlay_image(size, zones, values))
    background.convert("RGB").save(output_path, format="PNG", optimize=True)


def mux_audio(output, audio_path: Path, audio_stream) -> None:
    source = av.open(str(audio_path))
    source_audio = next((stream for stream in source.streams if stream.type == "audio"), None)
    if source_audio is None:
        source.close()
        raise ValueError("Audio export has no audio track")
    resampler = AudioResampler(format="fltp", layout="stereo", rate=48000)
    try:
        for frame in source.decode(source_audio):
            for converted in resampler.resample(frame):
                converted.pts = None
                for packet in audio_stream.encode(converted):
                    output.mux(packet)
        for packet in audio_stream.encode(None):
            output.mux(packet)
    finally:
        source.close()


def render_animated_video(
    template_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    output_format: str,
    zones: list[dict[str, Any]],
    values: dict[str, str],
    subtitles: dict[str, Any] | None = None,
) -> None:
    size = FORMAT_SIZES[output_format]
    duration = audio_duration(audio_path)
    rate = 5
    output = av.open(str(output_path), mode="w", options={"movflags": "+faststart"})
    video_stream = output.add_stream("libx264", rate=rate)
    video_stream.width, video_stream.height = size
    video_stream.pix_fmt = "yuv420p"
    video_stream.options = {"preset": "veryfast", "crf": "28"}
    audio_stream = output.add_stream("aac", rate=48000)
    audio_stream.layout = "stereo"
    audio_stream.bit_rate = 128_000
    overlay = overlay_image(size, zones, values)

    source = None
    decoder = None
    current_frame = None
    source_duration = 0.0

    def restart_decoder():
        nonlocal source, decoder, current_frame, source_duration
        if source is not None:
            source.close()
        source = av.open(str(template_path))
        stream = next((item for item in source.streams if item.type == "video"), None)
        if stream is None:
            raise ValueError("Template video has no video track")
        decoder = iter(source.decode(stream))
        current_frame = None
        source_duration = (
            float(stream.duration * stream.time_base)
            if stream.duration is not None and stream.time_base is not None
            else float(source.duration / av.time_base)
            if source.duration is not None
            else 0.0
        )

    try:
        restart_decoder()
        frame_count = max(1, math.ceil(duration * rate))
        previous_position = -1.0
        for index in range(frame_count):
            position = (index / rate) % source_duration if source_duration > 0 else index / rate
            if position < previous_position:
                restart_decoder()
            previous_position = position
            while current_frame is None or (
                current_frame.time is not None and float(current_frame.time) < position
            ):
                try:
                    current_frame = next(decoder)
                except StopIteration:
                    restart_decoder()
                    current_frame = next(decoder)
                    break
                if current_frame.time is None:
                    break
            decoded = current_frame
            image = ImageOps.fit(
                Image.fromarray(decoded.to_ndarray(format="rgb24"), mode="RGB"),
                size,
                method=Image.Resampling.BILINEAR,
            ).convert("RGBA")
            image.alpha_composite(overlay)
            frame = av.VideoFrame.from_ndarray(np.asarray(subtitle_frame(image, index / rate, subtitles).convert("RGB")), format="rgb24")
            frame.pts = index
            for packet in video_stream.encode(frame):
                output.mux(packet)
        for packet in video_stream.encode(None):
            output.mux(packet)
        mux_audio(output, audio_path, audio_stream)
    finally:
        if source is not None:
            source.close()
        output.close()
