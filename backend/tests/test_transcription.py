from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from backend.app.transcription import (
    OpenAIWhisperProvider,
    ProviderTranscription,
    create_audio_chunks,
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

    def transcribe(self, path: Path, _language: str) -> ProviderTranscription:
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
                "text": "bonjour",
                "segments": [
                    {"start": 0.25, "end": 1.5, "text": " bonjour "},
                ],
                "_request_id": "req_openai_test",
            },
        )()


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.audio = type("Audio", (), {})()
        self.audio.transcriptions = FakeTranscriptions()


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

    def test_openai_provider_requests_segment_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            silent_wav(source, 2)
            client = FakeOpenAIClient()
            provider = OpenAIWhisperProvider(api_key="", client=client)
            result = provider.transcribe(source, "fr")
            self.assertEqual(result.request_id, "req_openai_test")
            self.assertEqual(result.segments[0], TranscriptSegment(0.25, 1.5, "bonjour"))
            self.assertEqual(
                client.audio.transcriptions.arguments["timestamp_granularities"],
                ["segment"],
            )
            self.assertEqual(
                client.audio.transcriptions.arguments["response_format"],
                "verbose_json",
            )


if __name__ == "__main__":
    unittest.main()
