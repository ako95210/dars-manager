from __future__ import annotations

import shutil
import time
import re
from pathlib import Path


class TemporaryStorage:
    """Filesystem-backed temporary storage that can later be replaced by S3."""

    def __init__(self, root: Path, ttl_seconds: int) -> None:
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def create_workspace(self, user_id: str, job_id: str) -> Path:
        identifier = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
        if not identifier.fullmatch(user_id) or not identifier.fullmatch(job_id):
            raise ValueError("Invalid workspace identifier")
        workspace = self.root / user_id / job_id
        workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
        return workspace

    def remove_workspace(self, workspace: Path) -> None:
        try:
            workspace.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Workspace outside temporary storage") from exc
        shutil.rmtree(workspace, ignore_errors=True)
        try:
            workspace.parent.rmdir()
        except OSError:
            pass

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        if not self.root.exists():
            return removed
        for user_dir in self.root.iterdir():
            if not user_dir.is_dir():
                continue
            for workspace in user_dir.iterdir():
                if not workspace.is_dir():
                    continue
                try:
                    expired = now - workspace.stat().st_mtime > self.ttl_seconds
                except OSError:
                    continue
                if expired:
                    self.remove_workspace(workspace)
                    removed += 1
            try:
                user_dir.rmdir()
            except OSError:
                pass
        return removed
