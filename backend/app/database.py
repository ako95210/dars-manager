from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

INITIAL_REVISION = "20260908_0001"
CURRENT_REVISION = "20260914_0010"
INITIAL_TABLES = {
    "users",
    "auth_sessions",
    "projects",
    "assets",
    "jobs",
    "artifacts",
    "brand_kits",
}


if settings.database_url.startswith("sqlite"):
    @event.listens_for(Engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _alembic_config() -> Config:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(project_root / "backend" / "migrations"),
    )
    return config


def init_database() -> None:
    from . import models  # noqa: F401

    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        # The first web foundation used create_all(). Mark that exact schema as
        # the initial revision before applying later migrations.
        if (
            "alembic_version" not in tables
            and INITIAL_TABLES.issubset(tables)
        ):
            command.stamp(config, INITIAL_REVISION)

        command.upgrade(config, "head")


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
