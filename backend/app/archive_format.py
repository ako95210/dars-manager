from __future__ import annotations

import hashlib
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ARCHIVE_SCHEMA = 1
MAX_ARCHIVE_ENTRIES = 12
MAX_UNCOMPRESSED_BYTES = 2_000_000_000
MAX_MANIFEST_BYTES = 1_000_000
MAX_ANALYSIS_BYTES = 50_000_000
ALLOWED_FILES = {
    "analysis": ("analysis.json", "application/json"),
    "audio": ("audio.wav", "audio/wav"),
    "selection_audio": ("selection-audio.wav", "audio/wav"),
    "cover": ("cover.png", "image/png"),
    "video": ("video.mp4", "video/mp4"),
}


class InvalidArchive(ValueError):
    pass


def validate_analysis_duration(payload: dict[str, Any], audio_seconds: float) -> None:
    """Reject archives whose transcript or chapters refer to missing source audio."""
    if not math.isfinite(audio_seconds) or audio_seconds <= 0:
        raise InvalidArchive("L'audio archivé est vide ou illisible.")
    for kind in ("segments", "parts"):
        entries = payload.get(kind)
        if not isinstance(entries, list):
            raise InvalidArchive("Le format de l'analyse archivée est invalide.")
        for entry in entries:
            if not isinstance(entry, dict):
                raise InvalidArchive("Le format de l'analyse archivée est invalide.")
            start, end = entry.get("start"), entry.get("end")
            if (
                isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start
            ):
                raise InvalidArchive("Les timestamps de l'analyse archivée sont invalides.")
            if end > audio_seconds + 1.0:
                raise InvalidArchive(
                    "La transcription ou le chapitrage dépasse la durée de l'audio archivé. "
                    "Cette archive contient probablement un extrait à la place de l'audio complet."
                )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_archive(
    output_path: Path,
    files: dict[str, Path],
    *,
    project_title: str,
    source_job_id: str,
    analysis_checksum: str,
    render_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if "analysis" not in files or "audio" not in files:
        raise ValueError("An archive requires analysis and audio files")
    entries: list[dict[str, Any]] = []
    for kind, path in files.items():
        if kind not in ALLOWED_FILES or not path.is_file():
            continue
        archive_name, mime_type = ALLOWED_FILES[kind]
        entries.append({
            "kind": kind,
            "path": archive_name,
            "mime_type": mime_type,
            "size_bytes": path.stat().st_size,
            "checksum_sha256": sha256_file(path),
        })
    if not {"analysis", "audio"}.issubset({entry["kind"] for entry in entries}):
        raise ValueError("An archive requires readable analysis and audio files")
    analysis_entry = next(entry for entry in entries if entry["kind"] == "analysis")
    if analysis_entry["checksum_sha256"] != analysis_checksum:
        raise ValueError("The analysis checksum does not match the archived file")
    manifest = {
        "schema": ARCHIVE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "Dars Manager",
        "project_title": project_title,
        "source_job_id": source_job_id,
        "analysis_checksum_sha256": analysis_checksum,
        "files": entries,
        "render": render_snapshot or None,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        for entry in entries:
            archive.write(files[entry["kind"]], entry["path"])
    return manifest


def extract_archive(archive_path: Path, destination: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise InvalidArchive("Le fichier .dars est invalide.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise InvalidArchive("L'archive contient trop de fichiers.")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise InvalidArchive("L'archive contient des fichiers dupliqués.")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES:
            raise InvalidArchive("L'archive décompressée est trop volumineuse.")
        for info in infos:
            logical = PurePosixPath(info.filename)
            if logical.is_absolute() or any(part in {"", ".", ".."} for part in logical.parts):
                raise InvalidArchive("L'archive contient un chemin non autorisé.")
        try:
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise InvalidArchive("Le manifeste de l'archive est trop volumineux.")
            manifest = json.loads(archive.read(manifest_info))
        except (KeyError, json.JSONDecodeError, UnicodeError) as exc:
            raise InvalidArchive("Le manifeste de l'archive est invalide.") from exc
        if not isinstance(manifest, dict) or manifest.get("schema") != ARCHIVE_SCHEMA:
            raise InvalidArchive("Version d'archive non prise en charge.")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise InvalidArchive("La liste des fichiers est invalide.")
        if len(entries) > len(ALLOWED_FILES):
            raise InvalidArchive("Le manifeste contient trop de fichiers.")
        declared_paths = {
            str(entry.get("path", "")) for entry in entries if isinstance(entry, dict)
        }
        if set(names) != {"manifest.json", *declared_paths}:
            raise InvalidArchive("L'archive contient un fichier non déclaré.")

        destination.mkdir(parents=True, exist_ok=True)
        extracted: dict[str, Path] = {}
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise InvalidArchive("Une entrée du manifeste est invalide.")
            kind = str(entry.get("kind", ""))
            expected = ALLOWED_FILES.get(kind)
            if expected is None or kind in seen or entry.get("path") != expected[0]:
                raise InvalidArchive("Le manifeste contient un fichier non autorisé.")
            seen.add(kind)
            try:
                info = archive.getinfo(expected[0])
            except KeyError as exc:
                raise InvalidArchive(f"Le fichier {expected[0]} est manquant.") from exc
            try:
                declared_size = int(entry.get("size_bytes", -1))
            except (TypeError, ValueError) as exc:
                raise InvalidArchive(f"La taille de {expected[0]} est invalide.") from exc
            if info.file_size != declared_size:
                raise InvalidArchive(f"La taille de {expected[0]} est incorrecte.")
            if kind == "analysis" and info.file_size > MAX_ANALYSIS_BYTES:
                raise InvalidArchive("Le fichier d'analyse est trop volumineux.")
            target = destination / expected[0]
            with archive.open(info) as source, target.open("wb") as output:
                digest = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != entry.get("checksum_sha256"):
                raise InvalidArchive(f"L'empreinte de {expected[0]} est incorrecte.")
            extracted[kind] = target
        if "analysis" not in extracted or "audio" not in extracted:
            raise InvalidArchive("L'archive doit contenir l'analyse et l'audio.")
        return manifest, extracted
