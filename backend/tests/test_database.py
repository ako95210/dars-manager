from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alembic import command
from sqlalchemy import create_engine, text

from backend.app import database


class DatabaseMigrationTests(unittest.TestCase):
    def test_existing_foundation_schema_is_adopted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.db"
            legacy_engine = create_engine(f"sqlite+pysqlite:///{database_path}")

            config = database._alembic_config()
            with legacy_engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, database.INITIAL_REVISION)
                connection.execute(text("DROP TABLE alembic_version"))

            with patch.object(database, "engine", legacy_engine):
                database.init_database()

            with legacy_engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
            self.assertEqual(revision, database.CURRENT_REVISION)
            legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()
