from __future__ import annotations

import json
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from backend.app.archive_format import (
    InvalidArchive,
    build_archive,
    compress_audio_for_archive,
    extract_archive,
    sha256_file,
    validate_analysis_duration,
)
from drsm_core import audio_duration


class ArchiveFormatTests(unittest.TestCase):
    def test_compressed_archive_uses_m4a_and_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            analysis = root / "analysis.json"
            source_audio = root / "audio.wav"
            compressed_audio = root / "audio.m4a"
            analysis.write_text('{"segments": [], "parts": []}', encoding="utf-8")
            with wave.open(str(source_audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(48_000)
                output.writeframes(b"\x00\x00" * 48_000 * 3)

            compress_audio_for_archive(source_audio, compressed_audio)
            archive = root / "compressed.dars"
            manifest = build_archive(
                archive,
                {"analysis": analysis, "audio": compressed_audio},
                project_title="Cours compressé",
                source_job_id="c" * 32,
                analysis_checksum=sha256_file(analysis),
            )
            restored_manifest, restored = extract_archive(archive, root / "restored")

            self.assertEqual(manifest["schema"], 2)
            self.assertEqual(restored_manifest["schema"], 2)
            self.assertEqual(restored["audio"].name, "audio.m4a")
            self.assertAlmostEqual(audio_duration(restored["audio"]), 3.0, delta=0.1)
            self.assertLess(archive.stat().st_size, source_audio.stat().st_size // 3)

    def test_analysis_must_fit_archived_full_audio(self) -> None:
        payload = {
            "segments": [{"start": 0.0, "end": 1865.18}],
            "parts": [{"start": 657.72, "end": 952.22}],
        }
        with self.assertRaisesRegex(InvalidArchive, "dépasse la durée"):
            validate_analysis_duration(payload, 430.126)
        validate_analysis_duration(payload, 1865.97)

    def test_archive_round_trip_preserves_declared_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            analysis = root / "source-analysis.json"
            audio = root / "source-audio.wav"
            cover = root / "source-cover.png"
            analysis.write_text('{"schema": 3, "segments": [], "parts": []}\n', encoding="utf-8")
            audio.write_bytes(b"audio-content")
            cover.write_bytes(b"cover-content")
            archive = root / "course.dars"

            manifest = build_archive(
                archive,
                {"analysis": analysis, "audio": audio, "cover": cover},
                project_title="Cours portable",
                source_job_id="a" * 32,
                analysis_checksum=sha256_file(analysis),
                render_snapshot={"output_format": "16:9"},
            )
            restored_manifest, restored = extract_archive(archive, root / "restored")

            self.assertEqual(restored_manifest, manifest)
            self.assertEqual(set(restored), {"analysis", "audio", "cover"})
            self.assertEqual(restored["analysis"].read_bytes(), analysis.read_bytes())
            self.assertEqual(restored["audio"].read_bytes(), audio.read_bytes())
            self.assertEqual(restored_manifest["render"]["output_format"], "16:9")

    def test_modified_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            analysis = root / "analysis.json"
            audio = root / "audio.wav"
            analysis.write_text('{"segments": [], "parts": []}', encoding="utf-8")
            audio.write_bytes(b"original-audio")
            original = root / "original.dars"
            build_archive(
                original,
                {"analysis": analysis, "audio": audio},
                project_title="Cours",
                source_job_id="b" * 32,
                analysis_checksum=sha256_file(analysis),
            )
            with zipfile.ZipFile(original) as source:
                manifest = source.read("manifest.json")
                analysis_content = source.read("analysis.json")
            modified = root / "modified.dars"
            with zipfile.ZipFile(modified, "w", compression=zipfile.ZIP_DEFLATED) as target:
                target.writestr("manifest.json", manifest)
                target.writestr("analysis.json", analysis_content)
                target.writestr("audio.wav", b"modified-audio")

            with self.assertRaisesRegex(InvalidArchive, "taille|empreinte"):
                extract_archive(modified, root / "rejected")

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "malicious.dars"
            manifest = {"schema": 1, "files": []}
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("manifest.json", json.dumps(manifest))
                target.writestr("../outside.txt", "forbidden")

            with self.assertRaisesRegex(InvalidArchive, "chemin"):
                extract_archive(archive, root / "restored")
            self.assertFalse((root / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
