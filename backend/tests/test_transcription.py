from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from backend.app.transcription import (
    OpenAIWhisperProvider,
    ProviderTranscription,
    create_audio_chunks,
    estimated_transcription_billed_seconds,
    transcribe_in_chunks,
)
from drsm_core import TranscriptSegment


def silent_wav(path: Path, seconds: float, rate: int = 8_000) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x00\x00" * int(seconds * rate))


class FakeProvider:
    provider = "fake"
    model = "timestamp-test"

    def transcribe(self, path: Path, _language: str, *, prompt: str | None = None) -> ProviderTranscription:
        index = int(path.stem.split("-")[-1])
        return ProviderTranscription(
            segments=(TranscriptSegment(0.1, 0.8, f"fragment {index}"),),
            duration_seconds=1.0,
            request_id=f"request-{index}",
        )


class FakeTranscriptions:
    def __init__(self) -> None:
        self.arguments = None

    def create(self, **kwargs):
        self.arguments = kwargs
        return type(
            "Response",
            (),
            {
                "duration": 2.0,
                "text": "Bonjour le monde complet.",
                "segments": [
                    {"start": 0.25, "end": 1.5, "text": " Bonjour le monde complet. "},
                ],
                "words": [
                    {"start": 0.25, "end": 0.7, "word": "Bonjour"},
                ],
                "_request_id": "req_openai_test",
            },
        )()


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.audio = type("Audio", (), {})()
        self.audio.transcriptions = FakeTranscriptions()


class LoopRetryTranscriptions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            duration = 20.0
            segments = [
                {"start": 0.0, "end": 4.0, "text": "Introduction normale."},
                {"start": 5.0, "end": 8.0, "text": "à l'éducation"},
                {"start": 8.0, "end": 11.0, "text": "à l'éducation"},
                {"start": 11.0, "end": 14.0, "text": "à l'éducation"},
                {"start": 15.0, "end": 19.0, "text": "Conclusion normale."},
            ]
        else:
            duration = 13.0
            segments = [
                {"start": 0.0, "end": 4.0, "text": "Contexte repris."},
                {"start": 4.0, "end": 8.0, "text": "Cours sur le mariage."},
                {"start": 8.0, "end": 13.0, "text": "Cours sur la croyance authentique."},
            ]
        return type(
            "Response",
            (),
            {
                "duration": duration,
                "text": " ".join(item["text"] for item in segments),
                "segments": segments,
                "words": [],
                "_request_id": f"req-loop-{self.calls}",
            },
        )()


class BoundaryProvider:
    provider = "fake"
    model = "boundary-test"

    def transcribe(self, path: Path, _language: str, *, prompt: str | None = None) -> ProviderTranscription:
        index = int(path.stem.split("-")[-1])
        segment = (
            TranscriptSegment(0.9, 1.1, "phrase frontière")
            if index == 1
            else TranscriptSegment(0.1, 0.3, "phrase frontière")
        )
        return ProviderTranscription(segments=(segment,), duration_seconds=1.2)


class TranscriptionTests(unittest.TestCase):
    def test_audio_is_normalized_and_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            silent_wav(source, 2.2)
            chunks = create_audio_chunks(
                source,
                root / "chunks",
                chunk_seconds=1,
                max_bytes=1_000_000,
            )
            self.assertEqual(len(chunks), 3)
            self.assertEqual([round(item.start_seconds, 1) for item in chunks], [0.0, 1.0, 2.0])
            for chunk in chunks:
                with wave.open(str(chunk.path), "rb") as encoded:
                    self.assertEqual(encoded.getnchannels(), 1)
                    self.assertEqual(encoded.getframerate(), 16_000)

    def test_audio_chunks_include_context_around_fixed_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            silent_wav(source, 2.2)
            chunks = create_audio_chunks(
                source,
                root / "chunks",
                chunk_seconds=1,
                overlap_seconds=0.2,
                max_bytes=1_000_000,
            )

            self.assertEqual(
                [round(item.start_seconds, 1) for item in chunks],
                [0.0, 0.8, 1.8],
            )
            self.assertEqual(
                [round(item.end_seconds, 1) for item in chunks],
                [1.2, 2.2, 2.2],
            )
            self.assertEqual(
                [round(item.core_start_seconds, 1) for item in chunks],
                [0.0, 1.0, 2.0],
            )
            self.assertEqual(
                [round(item.core_end_seconds, 1) for item in chunks],
                [1.0, 2.0, 2.2],
            )

    def test_fragment_timestamps_and_usage_are_merged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            silent_wav(source, 2.2)
            calls = []
            segments = transcribe_in_chunks(
                source,
                root,
                FakeProvider(),
                "fr",
                chunk_seconds=1,
                max_bytes=1_000_000,
                on_usage=calls.append,
            )
            self.assertEqual([round(item.start, 1) for item in segments], [0.1, 1.1, 2.1])
            self.assertEqual([item.request_id for item in calls], [
                "request-1", "request-2", "request-3"
            ])
            self.assertFalse((root / "transcription-chunks").exists())

    def test_overlap_keeps_boundary_phrase_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            silent_wav(source, 2)
            segments = transcribe_in_chunks(
                source,
                root,
                BoundaryProvider(),
                "fr",
                chunk_seconds=1,
                overlap_seconds=0.2,
                max_bytes=1_000_000,
            )

            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0].text, "phrase frontière")
            self.assertAlmostEqual(segments[0].start, 0.9)
            self.assertAlmostEqual(segments[0].end, 1.1)

    def test_billed_duration_includes_chunk_overlap(self) -> None:
        self.assertEqual(estimated_transcription_billed_seconds(540, 540, 5), 540)
        self.assertEqual(estimated_transcription_billed_seconds(541, 540, 5), 551)

    def test_openai_provider_builds_cues_from_word_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            silent_wav(source, 2)
            client = FakeOpenAIClient()
            provider = OpenAIWhisperProvider(api_key="", client=client)
            result = provider.transcribe(source, "fr", prompt="Sunna, hadith")
            self.assertEqual(result.request_id, "req_openai_test")
            self.assertEqual(
                result.segments[0],
                TranscriptSegment(0.25, 1.5, "Bonjour le monde complet."),
            )
            self.assertEqual(
                client.audio.transcriptions.arguments["timestamp_granularities"],
                ["segment"],
            )
            self.assertEqual(
                client.audio.transcriptions.arguments["response_format"],
                "verbose_json",
            )
            self.assertEqual(client.audio.transcriptions.arguments["prompt"], "Sunna, hadith")

    def test_openai_provider_retries_an_obvious_repetition_loop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            silent_wav(source, 20)
            transcriptions = LoopRetryTranscriptions()
            client = type(
                "LoopRetryClient",
                (),
                {"audio": type("Audio", (), {"transcriptions": transcriptions})()},
            )()

            result = OpenAIWhisperProvider(api_key="", client=client).transcribe(
                source,
                "fr",
            )

            self.assertEqual(transcriptions.calls, 2)
            self.assertEqual(result.retry_count, 1)
            self.assertEqual(result.request_ids, ("req-loop-1", "req-loop-2"))
            self.assertAlmostEqual(result.billed_duration_seconds or 0, 33.0, places=1)
            self.assertNotIn(
                "à l'éducation",
                " ".join(segment.text for segment in result.segments),
            )
            self.assertIn(
                "Cours sur le mariage.",
                " ".join(segment.text for segment in result.segments),
            )


if __name__ == "__main__":
    unittest.main()
