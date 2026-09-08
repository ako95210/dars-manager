from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from backend.app import database
from backend.app.database import Base
from backend.app import models  # noqa: F401


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_foundation_schema_is_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            legacy_engine = create_engine(f"sqlite+pysqlite:///{database_path}")
            Base.metadata.create_all(legacy_engine)

            with patch.object(database, "engine", legacy_engine):
                database.init_database()

            with legacy_engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
            self.assertEqual(revision, database.INITIAL_REVISION)
            legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()
