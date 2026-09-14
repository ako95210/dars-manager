from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .costs import money_string
from .models import (
    Artifact,
    Asset,
    BrandTemplateFile,
    ImpactEvent,
    Project,
    UsageEvent,
)


IMPACT_KINDS = {
    "course_completed",
    "video_rendered",
    "archive_restored",
    "course_published",
}


def record_impact(
    db: Session,
    *,
    user_id: str,
    project_id: str | None,
    job_id: str | None,
    kind: str,
    duration_seconds: float = 0,
    storage_bytes: int = 0,
    channel: str = "",
    idempotency_key: str,
    details: dict | None = None,
) -> ImpactEvent:
    if kind not in IMPACT_KINDS:
        raise ValueError("Unsupported impact event kind")
    existing = db.scalar(
        select(ImpactEvent).where(ImpactEvent.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.user_id != user_id:
            raise ValueError("Impact idempotency key belongs to another user")
        return existing
    project_title = ""
    if project_id:
        project = db.scalar(
            select(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        if project is None:
            raise ValueError("Project not found")
        project_title = project.title
    event = ImpactEvent(
        user_id=user_id,
        project_id=project_id,
        project_title=project_title,
        job_id=job_id,
        kind=kind,
        channel=channel.strip().lower(),
        duration_milliseconds=max(0, round(duration_seconds * 1000)),
        storage_bytes=max(0, storage_bytes),
        idempotency_key=idempotency_key,
        details=details or {},
    )
    db.add(event)
    db.flush()
    return event


def week_bounds(value: str | None) -> tuple[date, date]:
    if value:
        selected = date.fromisoformat(value)
    else:
        selected = datetime.now(timezone.utc).date()
    start = selected - timedelta(days=selected.weekday())
    return start, start + timedelta(days=7)


def weekly_impact(db: Session, user_id: str, week: str | None = None) -> dict:
    start, end = week_bounds(week)
    start_at = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    end_at = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    events = list(
        db.scalars(
            select(ImpactEvent)
            .where(
                ImpactEvent.user_id == user_id,
                ImpactEvent.occurred_at >= start_at,
                ImpactEvent.occurred_at < end_at,
            )
            .order_by(ImpactEvent.occurred_at.desc())
        )
    )
    usage = list(
        db.scalars(
            select(UsageEvent).where(
                UsageEvent.user_id == user_id,
                UsageEvent.occurred_at >= start_at,
                UsageEvent.occurred_at < end_at,
                UsageEvent.status.in_({"confirmed", "estimated"}),
            )
        )
    )
    currencies = {item.currency for item in usage}
    currency = next(iter(currencies), "USD")
    if len(currencies) > 1:
        raise ValueError("Weekly impact cannot combine currencies")
    current_storage = sum(
        row.size_bytes
        for row in db.scalars(
            select(Asset).where(Asset.user_id == user_id, Asset.status == "ready")
        ).all()
    )
    current_storage += sum(
        row.size_bytes
        for row in db.scalars(select(Artifact).where(Artifact.user_id == user_id)).all()
    )
    current_storage += sum(
        row.size_bytes
        for row in db.scalars(
            select(BrandTemplateFile).where(
                BrandTemplateFile.user_id == user_id,
                BrandTemplateFile.uploaded_at.is_not(None),
            )
        ).all()
    )
    channels: dict[str, int] = {}
    for event in events:
        if event.kind == "course_published":
            channel = event.channel or "autre"
            channels[channel] = channels.get(channel, 0) + 1
    completed = [event for event in events if event.kind == "course_completed"]
    published = [event for event in events if event.kind == "course_published"]
    return {
        "week_start": start,
        "week_end": end - timedelta(days=1),
        "currency": currency,
        "courses_completed": len(completed),
        "videos_rendered": sum(event.kind == "video_rendered" for event in events),
        "archives_restored": sum(event.kind == "archive_restored" for event in events),
        "courses_published": len(published),
        "completed_duration_seconds": sum(
            event.duration_milliseconds for event in completed
        ) / 1000,
        "published_duration_seconds": sum(
            event.duration_milliseconds for event in published
        ) / 1000,
        "generated_storage_bytes": sum(event.storage_bytes for event in events),
        "current_storage_bytes": current_storage,
        "cost": money_string(sum(item.amount_nanos for item in usage)),
        "channels": channels,
    }
