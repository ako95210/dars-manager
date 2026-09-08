from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.pipeline import run_pipeline
from backend.app.storage import TemporaryStorage


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the phase-one Dars Manager pipeline")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--analysis", type=Path, help="Reuse an existing analysis for downstream tests")
    parser.add_argument("--model", default="base", choices=("tiny", "base", "small"))
    parser.add_argument("--language", default="fr")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--workspace-root", type=Path, default=Path("/dev/shm/dars-manager-beta"))
    parser.add_argument("--keep", action="store_true", help="Keep artifacts after the run")
    args = parser.parse_args()

    storage = TemporaryStorage(args.workspace_root, ttl_seconds=7200)
    workspace = storage.create_workspace("pilot", uuid.uuid4().hex)
    source = workspace / f"input{args.audio.suffix.lower()}"
    shutil.copyfile(args.audio, source)
    print(json.dumps({"event": "workspace", "path": str(workspace)}, ensure_ascii=False), flush=True)

    try:
        result = run_pipeline(
            source,
            workspace,
            model_name=args.model,
            language=args.language,
            cpu_threads=max(1, args.cpu_threads),
            reuse_analysis=args.analysis,
            progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
        )
        print(json.dumps({"event": "result", **{k: str(v) if isinstance(v, Path) else v for k, v in asdict(result).items()}}, ensure_ascii=False), flush=True)
        for path in (result.analysis_path, result.audio_path, result.cover_path, result.video_path):
            print(f"{path.name}\t{path.stat().st_size} bytes", flush=True)
    finally:
        if args.keep:
            print(f"Artifacts kept in {workspace}", flush=True)
        else:
            storage.remove_workspace(workspace)
            print(f"Workspace removed: {not workspace.exists()}", flush=True)


if __name__ == "__main__":
    main()
