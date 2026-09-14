from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeAlias

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .costs import record_usage
from .models import Artifact, Asset, BrandTemplateFile, utc_now
from .runtime import media_storage


MediaRow: TypeAlias = Asset | Artifact | BrandTemplateFile
BYTES_PER_GB = 1_000_000_000
SECONDS_PER_MONTH = 30 * 86400
MICRO_UNITS_PER_GB_MONTH = 1_000_000


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def media_started_at(row: MediaRow) -> datetime | None:
    if isinstance(row, Asset):
        return aware(row.uploaded_at) if row.uploaded_at else None
    if isinstance(row, BrandTemplateFile):
        return aware(row.uploaded_at) if row.uploaded_at else None
    return aware(row.created_at)


def cumulative_storage_units(row: MediaRow, through: datetime) -> int:
    started_at = media_started_at(row)
    if started_at is None:
        return 0
    end = aware(through)
    if not isinstance(row, BrandTemplateFile):
        end = min(end, aware(row.expires_at))
    seconds = max(0, int((end - started_at).total_seconds()))
    return (
        row.size_bytes * seconds * MICRO_UNITS_PER_GB_MONTH
        // (BYTES_PER_GB * SECONDS_PER_MONTH)
    )


def meter_media(db: Session, row: MediaRow, through: datetime | None = None) -> int:
    model = (
        Asset
        if isinstance(row, Asset)
        else BrandTemplateFile
        if isinstance(row, BrandTemplateFile)
        else Artifact
    )
    locked = db.get(model, row.id, with_for_update=True)
    if locked is None:
        return 0
    row = locked
    moment = aware(through or utc_now())
    total_units = cumulative_storage_units(row, moment)
    previous_units = int(row.storage_metered_units or 0)
    delta = max(0, total_units - previous_units)
    if delta:
        object_type = (
            "asset"
            if isinstance(row, Asset)
            else "brand_template_file"
            if isinstance(row, BrandTemplateFile)
            else "artifact"
        )
        job_id = None if isinstance(row, (Asset, BrandTemplateFile)) else row.job_id
        project_id = None if isinstance(row, BrandTemplateFile) else row.project_id
        record_usage(
            db,
            user_id=row.user_id,
            project_id=project_id,
            job_id=job_id,
            provider=settings.storage_provider,
            service="object_storage",
            model=settings.storage_model,
            quantity=delta,
            unit="micro_gb_month",
            status="estimated",
            idempotency_key=f"storage:{object_type}:{row.id}:total:{total_units}",
            details={
                "object_type": object_type,
                "object_id": row.id,
                "kind": row.kind,
                "size_bytes": row.size_bytes,
                "cumulative_micro_gb_month": total_units,
                "metered_through": moment.isoformat(),
            },
            occurred_at=moment,
        )
        row.storage_metered_units = total_units
    row.storage_metered_at = moment
    return delta


def meter_all_media(db: Session, through: datetime | None = None) -> int:
    moment = through or utc_now()
    total_delta = 0
    assets = db.scalars(select(Asset).where(Asset.status == "ready")).all()
    artifacts = db.scalars(select(Artifact)).all()
    template_files = db.scalars(select(BrandTemplateFile)).all()
    for row in [*assets, *artifacts, *template_files]:
        total_delta += meter_media(db, row, moment)
    db.commit()
    return total_delta


def purge_expired_media(db: Session, through: datetime | None = None) -> int:
    moment = aware(through or utc_now())
    assets = db.scalars(select(Asset).where(Asset.expires_at <= moment)).all()
    artifacts = db.scalars(select(Artifact).where(Artifact.expires_at <= moment)).all()
    removed = 0
    for row in [*assets, *artifacts]:
        meter_media(db, row, min(moment, aware(row.expires_at)))
        try:
            if row.storage_key:
                media_storage.delete(row.storage_key)
        except Exception:
            continue
        db.delete(row)
        removed += 1
    db.commit()
    return removed
