from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import BillingPolicy, PriceRate, Project, UsageEvent
from .config import settings


NANOS_PER_CURRENCY_UNIT = 1_000_000_000


@dataclass(frozen=True)
class CostQuote:
    price_rate_id: str
    provider: str
    service: str
    model: str
    quantity: int
    unit: str
    currency: str
    unit_amount_nanos: int
    amount_nanos: int


@dataclass(frozen=True)
class CostControl:
    currency: str
    monthly_budget_nanos: int
    warning_percent: int
    approval_threshold_nanos: int
    committed_nanos: int
    projected_nanos: int
    remaining_nanos: int
    utilization_percent: float
    state: str
    requires_confirmation: bool
    confirmation_reasons: tuple[str, ...]


def money_decimal(amount_nanos: int) -> Decimal:
    return (Decimal(amount_nanos) / NANOS_PER_CURRENCY_UNIT).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def money_string(amount_nanos: int) -> str:
    return format(money_decimal(amount_nanos), "f")


def cost_control(
    db: Session,
    *,
    user_id: str,
    proposed_amount_nanos: int = 0,
    at: datetime | None = None,
    lock_policy: bool = False,
) -> CostControl:
    moment = at or datetime.now(timezone.utc)
    start_at = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_at.month == 12:
        end_at = start_at.replace(year=start_at.year + 1, month=1)
    else:
        end_at = start_at.replace(month=start_at.month + 1)
    statement = select(BillingPolicy).where(BillingPolicy.user_id == user_id)
    if lock_policy:
        statement = statement.with_for_update()
    policy = db.scalar(statement)
    events = db.scalars(
        select(UsageEvent).where(
            UsageEvent.user_id == user_id,
            UsageEvent.occurred_at >= start_at,
            UsageEvent.occurred_at < end_at,
            UsageEvent.status.in_({"confirmed", "estimated"}),
        )
    ).all()
    committed = sum(event.amount_nanos for event in events)
    projected = committed + max(0, proposed_amount_nanos)
    budget = int(policy.monthly_budget_nanos) if policy else 0
    warning_percent = int(policy.warning_percent) if policy else 80
    threshold = int(policy.approval_threshold_nanos) if policy else 0
    utilization = (projected * 100 / budget) if budget > 0 else 0.0
    if budget <= 0:
        state = "disabled"
    elif projected > budget:
        state = "exceeded"
    elif utilization >= warning_percent:
        state = "warning"
    else:
        state = "ok"
    reasons: list[str] = []
    if proposed_amount_nanos > 0 and threshold > 0 and proposed_amount_nanos >= threshold:
        reasons.append("approval_threshold")
    if proposed_amount_nanos > 0 and budget > 0 and projected > budget:
        reasons.append("monthly_budget")
    return CostControl(
        currency=policy.currency if policy else "USD",
        monthly_budget_nanos=budget,
        warning_percent=warning_percent,
        approval_threshold_nanos=threshold,
        committed_nanos=committed,
        projected_nanos=projected,
        remaining_nanos=max(0, budget - projected) if budget > 0 else 0,
        utilization_percent=round(utilization, 2),
        state=state,
        requires_confirmation=bool(reasons),
        confirmation_reasons=tuple(reasons),
    )


def seed_default_rates(db: Session) -> None:
    """Install known rates once; existing rows are never changed in place."""
    defaults = [
        {
            "provider": "openai",
            "service": "transcription",
            "model": "whisper-1",
            "unit": "audio_second",
            "currency": "USD",
            # USD 0.006/minute = USD 0.0001/second.
            "unit_amount_nanos": 100_000,
            "effective_from": datetime(2026, 9, 9, tzinfo=timezone.utc),
            "source_url": "https://developers.openai.com/api/docs/models/whisper-1",
            "details": {"published_rate": "0.006 USD/audio minute"},
        },
        {
            "provider": settings.storage_provider,
            "service": "object_storage",
            "model": settings.storage_model,
            "unit": "micro_gb_month",
            "currency": "USD",
            # One unit is one millionth of a decimal GB-month.
            "unit_amount_nanos": int(
                (settings.storage_gb_month_usd * Decimal("1000")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            ),
            "effective_from": datetime(2026, 9, 9, tzinfo=timezone.utc),
            "source_url": settings.storage_price_source_url,
            "details": {
                "published_rate": f"{settings.storage_gb_month_usd} USD/GB-month",
                "gb_definition": "1000000000 bytes",
                "month_definition": "30 days",
            },
        },
        {
            "provider": "openai",
            "service": "content_analysis",
            "model": settings.semantic_analysis_model,
            "unit": "input_token",
            "currency": "USD",
            # USD 0.20 / 1M input tokens.
            "unit_amount_nanos": 200,
            "effective_from": datetime(2026, 9, 16, tzinfo=timezone.utc),
            "source_url": (
                "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
            ),
            "details": {"published_rate": "0.20 USD/1M input tokens"},
        },
        {
            "provider": "openai",
            "service": "content_analysis",
            "model": settings.semantic_analysis_model,
            "unit": "output_token",
            "currency": "USD",
            # USD 1.20 / 1M output tokens.
            "unit_amount_nanos": 1_200,
            "effective_from": datetime(2026, 9, 16, tzinfo=timezone.utc),
            "source_url": (
                "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
            ),
            "details": {"published_rate": "1.20 USD/1M output tokens"},
        },
    ]
    for values in defaults:
        exists = db.scalar(
            select(PriceRate.id).where(
                PriceRate.provider == values["provider"],
                PriceRate.service == values["service"],
                PriceRate.model == values["model"],
                PriceRate.unit == values["unit"],
                PriceRate.effective_from == values["effective_from"],
            )
        )
        if exists is None:
            db.add(PriceRate(**values))
            try:
                db.commit()
            except IntegrityError:
                # API, worker and maintenance may bootstrap simultaneously.
                # The unique rate key makes the winning insert authoritative.
                db.rollback()


def active_rate(
    db: Session,
    *,
    provider: str,
    service: str,
    model: str,
    unit: str,
    at: datetime | None = None,
) -> PriceRate:
    moment = at or datetime.now(timezone.utc)
    rate = db.scalar(
        select(PriceRate)
        .where(
            PriceRate.provider == provider,
            PriceRate.service == service,
            PriceRate.model == model,
            PriceRate.unit == unit,
            PriceRate.effective_from <= moment,
            or_(PriceRate.effective_until.is_(None), PriceRate.effective_until > moment),
        )
        .order_by(PriceRate.effective_from.desc())
        .limit(1)
    )
    if rate is None:
        raise ValueError(f"No active price for {provider}/{service}/{model}/{unit}")
    return rate


def quote_usage(
    db: Session,
    *,
    provider: str,
    service: str,
    model: str,
    quantity: int,
    unit: str,
    at: datetime | None = None,
) -> CostQuote:
    if quantity < 0:
        raise ValueError("Usage quantity cannot be negative")
    rate = active_rate(
        db,
        provider=provider,
        service=service,
        model=model,
        unit=unit,
        at=at,
    )
    return CostQuote(
        price_rate_id=rate.id,
        provider=provider,
        service=service,
        model=model,
        quantity=quantity,
        unit=unit,
        currency=rate.currency,
        unit_amount_nanos=rate.unit_amount_nanos,
        amount_nanos=quantity * rate.unit_amount_nanos,
    )


def record_usage(
    db: Session,
    *,
    user_id: str,
    project_id: str | None,
    job_id: str | None,
    provider: str,
    service: str,
    model: str,
    quantity: int,
    unit: str,
    status: str,
    idempotency_key: str,
    provider_request_id: str | None = None,
    details: dict | None = None,
    occurred_at: datetime | None = None,
) -> UsageEvent:
    if status not in {"estimated", "confirmed"}:
        raise ValueError("Usage status must be estimated or confirmed")
    existing = db.scalar(
        select(UsageEvent).where(UsageEvent.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.user_id != user_id:
            raise ValueError("Idempotency key already belongs to another user")
        return existing

    project_title = ""
    if project_id:
        project = db.scalar(
            select(Project).where(Project.id == project_id, Project.user_id == user_id)
        )
        if project is None:
            raise ValueError("Project not found")
        project_title = project.title

    moment = occurred_at or datetime.now(timezone.utc)
    quote = quote_usage(
        db,
        provider=provider,
        service=service,
        model=model,
        quantity=quantity,
        unit=unit,
        at=moment,
    )
    event = UsageEvent(
        user_id=user_id,
        project_id=project_id,
        project_title=project_title,
        job_id=job_id,
        provider=provider,
        service=service,
        model=model,
        quantity=quantity,
        unit=unit,
        currency=quote.currency,
        amount_nanos=quote.amount_nanos,
        status=status,
        price_rate_id=quote.price_rate_id,
        idempotency_key=idempotency_key,
        provider_request_id=provider_request_id,
        details=details or {},
        occurred_at=moment,
    )
    db.add(event)
    db.flush()
    return event


def reconcile_job_estimates(db: Session, job_id: str, service: str) -> int:
    """Keep estimates auditable but exclude them once actual usage is known."""
    events = db.scalars(
        select(UsageEvent).where(
            UsageEvent.job_id == job_id,
            UsageEvent.service == service,
            UsageEvent.status == "estimated",
        )
    ).all()
    for event in events:
        event.status = "reconciled"
    return len(events)
