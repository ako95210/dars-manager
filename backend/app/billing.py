from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_admin, require_user
from .costs import NANOS_PER_CURRENCY_UNIT, cost_control, money_string
from .database import get_db
from .impact import weekly_impact
from .models import BillingPolicy, Payment, UsageEvent, User


router = APIRouter(prefix="/api/billing", tags=["billing"])
admin_router = APIRouter(prefix="/api/admin/billing", tags=["admin-billing"])


def month_bounds(value: str | None) -> tuple[date, date]:
    if value is None:
        today = datetime.now(timezone.utc).date()
        start = today.replace(day=1)
    else:
        try:
            start = date.fromisoformat(f"{value}-01")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Month must use YYYY-MM format",
            ) from exc
        if value != start.strftime("%Y-%m"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Month must use YYYY-MM format",
            )
    end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, end


def usage_response(event: UsageEvent) -> dict:
    return {
        "id": event.id,
        "project_id": event.project_id,
        "project_title": event.project_title,
        "job_id": event.job_id,
        "provider": event.provider,
        "service": event.service,
        "model": event.model,
        "quantity": event.quantity,
        "unit": event.unit,
        "currency": event.currency,
        "amount": money_string(event.amount_nanos),
        "status": event.status,
        "occurred_at": event.occurred_at,
    }


def payment_response(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "amount": money_string(payment.amount_nanos),
        "currency": payment.currency,
        "method": payment.method,
        "reference": payment.reference,
        "note": payment.note,
        "period": payment.period_start.strftime("%Y-%m"),
        "paid_at": payment.paid_at,
    }


def policy_response(policy: BillingPolicy | None) -> dict:
    return {
        "currency": policy.currency if policy else "USD",
        "monthly_budget": money_string(policy.monthly_budget_nanos if policy else 0),
        "warning_percent": policy.warning_percent if policy else 80,
        "approval_threshold": money_string(
            policy.approval_threshold_nanos if policy else 0
        ),
        "enabled": bool(policy and policy.monthly_budget_nanos > 0),
    }


def summary_response(db: Session, user: User, month: str | None) -> dict:
    start, end = month_bounds(month)
    start_at = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    end_at = datetime(end.year, end.month, 1, tzinfo=timezone.utc)
    events = list(
        db.scalars(
            select(UsageEvent)
            .where(
                UsageEvent.user_id == user.id,
                UsageEvent.occurred_at >= start_at,
                UsageEvent.occurred_at < end_at,
            )
            .order_by(UsageEvent.occurred_at.desc())
        )
    )
    payments = list(
        db.scalars(
            select(Payment)
            .where(Payment.user_id == user.id, Payment.period_start == start)
            .order_by(Payment.paid_at.desc())
        )
    )
    currencies = {item.currency for item in [*events, *payments]}
    if len(currencies) > 1:
        raise HTTPException(status_code=409, detail="Multiple currencies require reconciliation")
    currency = next(iter(currencies), "USD")
    confirmed_nanos = sum(
        event.amount_nanos for event in events if event.status == "confirmed"
    )
    estimated_nanos = sum(
        event.amount_nanos for event in events if event.status == "estimated"
    )
    paid_nanos = sum(payment.amount_nanos for payment in payments)
    project_totals: dict[str, dict] = {}
    for event in events:
        if event.status not in {"confirmed", "estimated"}:
            continue
        key = event.project_id or f"deleted:{event.project_title or 'infrastructure'}"
        item = project_totals.setdefault(key, {
            "project_id": event.project_id,
            "project_title": event.project_title or "Infrastructure",
            "confirmed_nanos": 0,
            "estimated_nanos": 0,
            "operations": 0,
        })
        item[f"{event.status}_nanos"] += event.amount_nanos
        item["operations"] += 1
    projects = [
        {
            "project_id": item["project_id"],
            "project_title": item["project_title"],
            "confirmed_cost": money_string(item["confirmed_nanos"]),
            "estimated_cost": money_string(item["estimated_nanos"]),
            "total_cost": money_string(item["confirmed_nanos"] + item["estimated_nanos"]),
            "operations": item["operations"],
        }
        for item in sorted(
            project_totals.values(),
            key=lambda value: value["confirmed_nanos"] + value["estimated_nanos"],
            reverse=True,
        )
    ]
    policy = db.scalar(select(BillingPolicy).where(BillingPolicy.user_id == user.id))
    control = cost_control(db, user_id=user.id, at=start_at)
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
        "period": start.strftime("%Y-%m"),
        "period_start": start,
        "period_end": end,
        "currency": currency,
        "confirmed_cost": money_string(confirmed_nanos),
        "estimated_cost": money_string(estimated_nanos),
        "paid": money_string(paid_nanos),
        "balance": money_string(confirmed_nanos - paid_nanos),
        "policy": policy_response(policy),
        "budget": {
            "committed": money_string(control.committed_nanos),
            "remaining": money_string(control.remaining_nanos),
            "utilization_percent": control.utilization_percent,
            "state": control.state,
        },
        "projects": projects,
        "usage": [usage_response(event) for event in events],
        "payments": [payment_response(payment) for payment in payments],
    }


@router.get("/summary")
def billing_summary(
    month: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    return summary_response(db, user, month)


@router.get("/impact")
def impact_summary(
    week: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        return weekly_impact(db, user.id, week)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class BillingPolicyRequest(BaseModel):
    monthly_budget: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    warning_percent: int = Field(default=80, ge=1, le=100)
    approval_threshold: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_policy_currency(cls, value: str) -> str:
        return value.upper()


def amount_nanos(value: Decimal) -> int:
    return int(
        (value * NANOS_PER_CURRENCY_UNIT).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


@router.put("/policy")
def update_billing_policy(
    payload: BillingPolicyRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    if payload.currency != "USD":
        raise HTTPException(status_code=422, detail="La bêta utilise uniquement USD.")
    policy = db.scalar(
        select(BillingPolicy)
        .where(BillingPolicy.user_id == user.id)
        .with_for_update()
    )
    if policy is None:
        policy = BillingPolicy(user_id=user.id)
        db.add(policy)
    policy.currency = payload.currency
    policy.monthly_budget_nanos = amount_nanos(payload.monthly_budget)
    policy.warning_percent = payload.warning_percent
    policy.approval_threshold_nanos = amount_nanos(payload.approval_threshold)
    db.commit()
    db.refresh(policy)
    return policy_response(policy)


class ManualPaymentRequest(BaseModel):
    user_id: str = Field(min_length=32, max_length=32)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    period: str
    method: str = Field(default="manual", min_length=1, max_length=50)
    reference: str = Field(default="", max_length=180)
    note: str = Field(default="", max_length=4000)
    paid_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


@admin_router.post("/payments", status_code=201)
def create_manual_payment(
    payload: ManualPaymentRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    billed_user = db.get(User, payload.user_id)
    if billed_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    period_start, _ = month_bounds(payload.period)
    payment_amount_nanos = amount_nanos(payload.amount)
    payment = Payment(
        user_id=billed_user.id,
        recorded_by_user_id=admin.id,
        amount_nanos=payment_amount_nanos,
        currency=payload.currency,
        method=payload.method.strip(),
        reference=payload.reference.strip(),
        note=payload.note.strip(),
        period_start=period_start,
        paid_at=payload.paid_at or datetime.now(timezone.utc),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment_response(payment)


@admin_router.get("/clients")
def client_billing_summaries(
    month: str | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    clients = list(
        db.scalars(select(User).where(User.role == "client").order_by(User.display_name))
    )
    return [summary_response(db, client, month) for client in clients]
