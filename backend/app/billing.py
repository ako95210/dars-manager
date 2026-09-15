from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import require_admin, require_client
from .billing_exports import statement_csv, statement_pdf
from .costs import NANOS_PER_CURRENCY_UNIT, cost_control, money_string
from .database import get_db
from .impact import weekly_impact
from .models import (
    BillingPolicy,
    CommunityContribution,
    ContributionAllocation,
    Payment,
    Project,
    ProviderInvoice,
    UsageEvent,
    User,
)


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
    allocations = list(
        db.scalars(
            select(ContributionAllocation)
            .where(
                ContributionAllocation.user_id == user.id,
                ContributionAllocation.period_start == start,
                ContributionAllocation.period_end == end,
            )
            .order_by(ContributionAllocation.created_at.desc())
        )
    )
    currencies = {item.currency for item in [*events, *payments, *allocations]}
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
    community_nanos = sum(allocation.amount_nanos for allocation in allocations)
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
            "community_nanos": 0,
            "operations": 0,
        })
        item[f"{event.status}_nanos"] += event.amount_nanos
        item["operations"] += 1
    for allocation in allocations:
        key = allocation.project_id or f"deleted:{allocation.project_title}"
        item = project_totals.setdefault(key, {
            "project_id": allocation.project_id,
            "project_title": allocation.project_title,
            "confirmed_nanos": 0,
            "estimated_nanos": 0,
            "community_nanos": 0,
            "operations": 0,
        })
        item["community_nanos"] += allocation.amount_nanos
    projects = [
        {
            "project_id": item["project_id"],
            "project_title": item["project_title"],
            "confirmed_cost": money_string(item["confirmed_nanos"]),
            "estimated_cost": money_string(item["estimated_nanos"]),
            "total_cost": money_string(item["confirmed_nanos"] + item["estimated_nanos"]),
            "community_funded": money_string(item["community_nanos"]),
            "amount_due": money_string(item["confirmed_nanos"] - item["community_nanos"]),
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
        "community_funded": money_string(community_nanos),
        "balance": money_string(confirmed_nanos - paid_nanos - community_nanos),
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
        "community_allocations": [
            {
                "id": allocation.id,
                "project_id": allocation.project_id,
                "project_title": allocation.project_title,
                "period": allocation.period_start.strftime("%Y-%m"),
                "category": allocation.category,
                "amount": money_string(allocation.amount_nanos),
                "currency": allocation.currency,
                "created_at": allocation.created_at,
            }
            for allocation in allocations
        ],
    }


@router.get("/summary")
def billing_summary(
    month: str | None = None,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict:
    return summary_response(db, user, month)


def statement_download(content: bytes, media_type: str, suffix: str, month: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="releve-dars-{month}.{suffix}"'
        },
    )


@router.get("/statement.csv")
def download_statement_csv(
    month: str | None = None,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> Response:
    summary = summary_response(db, user, month)
    return statement_download(
        statement_csv(summary), "text/csv", "csv", summary["period"]
    )


@router.get("/statement.pdf")
def download_statement_pdf(
    month: str | None = None,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> Response:
    summary = summary_response(db, user, month)
    return statement_download(
        statement_pdf(summary), "application/pdf", "pdf", summary["period"]
    )


@router.get("/impact")
def impact_summary(
    week: str | None = None,
    user: User = Depends(require_client),
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
    user: User = Depends(require_client),
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


class ProviderInvoiceRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    service: str = Field(default="", max_length=80)
    reference: str = Field(min_length=1, max_length=180)
    period: str
    invoiced_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    tolerance: Decimal = Field(default=Decimal("0.000001"), ge=0, max_digits=18, decimal_places=6)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    include_estimated: bool = False
    note: str = Field(default="", max_length=4000)

    @field_validator("provider", "service", mode="before")
    @classmethod
    def normalize_provider_fields(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("reference", "note", mode="before")
    @classmethod
    def strip_invoice_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency")
    @classmethod
    def normalize_invoice_currency(cls, value: str) -> str:
        return value.upper()


class CommunityContributionRequest(BaseModel):
    contributor_name: str = Field(default="", max_length=180)
    is_anonymous: bool = False
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    method: str = Field(default="manual", min_length=1, max_length=50)
    reference: str = Field(default="", max_length=180)
    campaign: str = Field(default="", max_length=180)
    note: str = Field(default="", max_length=4000)
    received_at: datetime | None = None

    @field_validator(
        "contributor_name", "method", "reference", "campaign", "note", mode="before"
    )
    @classmethod
    def strip_contribution_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency")
    @classmethod
    def normalize_contribution_currency(cls, value: str) -> str:
        return value.upper()


class ContributionAllocationRequest(BaseModel):
    contribution_id: str = Field(min_length=32, max_length=32)
    project_id: str = Field(min_length=32, max_length=32)
    period: str
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    category: str = Field(default="cloud_cost", min_length=1, max_length=80)
    note: str = Field(default="", max_length=4000)

    @field_validator("category", "note", mode="before")
    @classmethod
    def strip_allocation_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency")
    @classmethod
    def normalize_allocation_currency(cls, value: str) -> str:
        return value.upper()


def contribution_response(
    contribution: CommunityContribution, allocated_nanos: int
) -> dict:
    return {
        "id": contribution.id,
        "contributor_name": contribution.contributor_name,
        "contributor_display": (
            "Anonyme"
            if contribution.is_anonymous
            else contribution.contributor_name or "Communauté"
        ),
        "is_anonymous": contribution.is_anonymous,
        "amount": money_string(contribution.amount_nanos),
        "allocated": money_string(allocated_nanos),
        "remaining": money_string(contribution.amount_nanos - allocated_nanos),
        "currency": contribution.currency,
        "status": contribution.status,
        "method": contribution.method,
        "reference": contribution.reference,
        "campaign": contribution.campaign,
        "note": contribution.note,
        "received_at": contribution.received_at,
        "created_at": contribution.created_at,
    }


def allocation_response(
    allocation: ContributionAllocation,
    contribution: CommunityContribution | None = None,
) -> dict:
    response = {
        "id": allocation.id,
        "contribution_id": allocation.contribution_id,
        "user_id": allocation.user_id,
        "project_id": allocation.project_id,
        "project_title": allocation.project_title,
        "period": allocation.period_start.strftime("%Y-%m"),
        "category": allocation.category,
        "amount": money_string(allocation.amount_nanos),
        "currency": allocation.currency,
        "note": allocation.note,
        "created_at": allocation.created_at,
    }
    if contribution is not None:
        response["contributor_display"] = (
            "Anonyme"
            if contribution.is_anonymous
            else contribution.contributor_name or "Communauté"
        )
    return response


def provider_invoice_response(invoice: ProviderInvoice) -> dict:
    return {
        "id": invoice.id,
        "provider": invoice.provider,
        "service": invoice.service,
        "reference": invoice.reference,
        "period": invoice.period_start.strftime("%Y-%m"),
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "currency": invoice.currency,
        "invoiced_amount": money_string(invoice.invoiced_amount_nanos),
        "internal_amount": money_string(invoice.internal_amount_nanos),
        "variance": money_string(invoice.variance_amount_nanos),
        "tolerance": money_string(invoice.tolerance_amount_nanos),
        "include_estimated": invoice.include_estimated,
        "status": invoice.status,
        "note": invoice.note,
        "created_at": invoice.created_at,
    }


@admin_router.post("/provider-invoices", status_code=201)
def reconcile_provider_invoice(
    payload: ProviderInvoiceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if payload.currency != "USD":
        raise HTTPException(status_code=422, detail="La bêta utilise uniquement USD.")
    period_start, period_end = month_bounds(payload.period)
    existing = db.scalar(
        select(ProviderInvoice).where(
            ProviderInvoice.provider == payload.provider,
            ProviderInvoice.service == payload.service,
            ProviderInvoice.reference == payload.reference,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Cette facture est déjà enregistrée.")
    start_at = datetime(period_start.year, period_start.month, 1, tzinfo=timezone.utc)
    end_at = datetime(period_end.year, period_end.month, 1, tzinfo=timezone.utc)
    statuses = {"confirmed", "estimated"} if payload.include_estimated else {"confirmed"}
    statement = select(UsageEvent).where(
        UsageEvent.provider == payload.provider,
        UsageEvent.currency == payload.currency,
        UsageEvent.status.in_(statuses),
        UsageEvent.occurred_at >= start_at,
        UsageEvent.occurred_at < end_at,
    )
    if payload.service:
        statement = statement.where(UsageEvent.service == payload.service)
    events = db.scalars(statement).all()
    internal_nanos = sum(event.amount_nanos for event in events)
    invoiced_nanos = amount_nanos(payload.invoiced_amount)
    tolerance_nanos = amount_nanos(payload.tolerance)
    variance_nanos = invoiced_nanos - internal_nanos
    invoice = ProviderInvoice(
        provider=payload.provider,
        service=payload.service,
        reference=payload.reference,
        period_start=period_start,
        period_end=period_end,
        currency=payload.currency,
        invoiced_amount_nanos=invoiced_nanos,
        internal_amount_nanos=internal_nanos,
        variance_amount_nanos=variance_nanos,
        tolerance_amount_nanos=tolerance_nanos,
        include_estimated=payload.include_estimated,
        status="matched" if abs(variance_nanos) <= tolerance_nanos else "variance",
        recorded_by_user_id=admin.id,
        note=payload.note,
    )
    db.add(invoice)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cette facture est déjà enregistrée.") from exc
    db.refresh(invoice)
    return provider_invoice_response(invoice)


@admin_router.get("/provider-invoices")
def list_provider_invoices(
    month: str | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(ProviderInvoice).order_by(ProviderInvoice.created_at.desc())
    if month:
        start, end = month_bounds(month)
        statement = statement.where(
            ProviderInvoice.period_start == start,
            ProviderInvoice.period_end == end,
        )
    return [provider_invoice_response(row) for row in db.scalars(statement).all()]


@admin_router.post("/community-contributions", status_code=201)
def create_community_contribution(
    payload: CommunityContributionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if payload.currency != "USD":
        raise HTTPException(status_code=422, detail="La bêta utilise uniquement USD.")
    received_at = payload.received_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    contribution = CommunityContribution(
        contributor_name=payload.contributor_name,
        is_anonymous=payload.is_anonymous,
        amount_nanos=amount_nanos(payload.amount),
        currency=payload.currency,
        status="received",
        method=payload.method,
        reference=payload.reference,
        campaign=payload.campaign,
        note=payload.note,
        received_at=received_at,
        recorded_by_user_id=admin.id,
    )
    db.add(contribution)
    db.commit()
    db.refresh(contribution)
    return contribution_response(contribution, 0)


@admin_router.get("/community-contributions")
def list_community_contributions(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    contributions = list(
        db.scalars(
            select(CommunityContribution).order_by(
                CommunityContribution.received_at.desc(),
                CommunityContribution.created_at.desc(),
            )
        )
    )
    allocated = dict(
        db.execute(
            select(
                ContributionAllocation.contribution_id,
                func.sum(ContributionAllocation.amount_nanos),
            ).group_by(ContributionAllocation.contribution_id)
        ).all()
    )
    return [
        contribution_response(contribution, allocated.get(contribution.id, 0))
        for contribution in contributions
    ]


@admin_router.post("/community-allocations", status_code=201)
def create_contribution_allocation(
    payload: ContributionAllocationRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if payload.currency != "USD":
        raise HTTPException(status_code=422, detail="La bêta utilise uniquement USD.")
    period_start, period_end = month_bounds(payload.period)
    contribution = db.scalar(
        select(CommunityContribution)
        .where(CommunityContribution.id == payload.contribution_id)
        .with_for_update()
    )
    if contribution is None:
        raise HTTPException(status_code=404, detail="Contribution introuvable.")
    if contribution.status != "received":
        raise HTTPException(status_code=409, detail="La contribution n'est pas disponible.")
    if contribution.currency != payload.currency:
        raise HTTPException(status_code=409, detail="Les devises ne correspondent pas.")
    project = db.scalar(
        select(Project).where(Project.id == payload.project_id).with_for_update()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Projet introuvable.")

    allocation_nanos = amount_nanos(payload.amount)
    contribution_allocated = db.scalar(
        select(func.coalesce(func.sum(ContributionAllocation.amount_nanos), 0)).where(
            ContributionAllocation.contribution_id == contribution.id
        )
    )
    contribution_remaining = contribution.amount_nanos - int(contribution_allocated or 0)
    if allocation_nanos > contribution_remaining:
        raise HTTPException(
            status_code=409,
            detail="Le montant dépasse le solde disponible de la contribution.",
        )

    start_at = datetime(period_start.year, period_start.month, 1, tzinfo=timezone.utc)
    end_at = datetime(period_end.year, period_end.month, 1, tzinfo=timezone.utc)
    confirmed_cost = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.amount_nanos), 0)).where(
            UsageEvent.project_id == project.id,
            UsageEvent.status == "confirmed",
            UsageEvent.currency == payload.currency,
            UsageEvent.occurred_at >= start_at,
            UsageEvent.occurred_at < end_at,
        )
    )
    project_allocated = db.scalar(
        select(func.coalesce(func.sum(ContributionAllocation.amount_nanos), 0)).where(
            ContributionAllocation.project_id == project.id,
            ContributionAllocation.period_start == period_start,
            ContributionAllocation.period_end == period_end,
            ContributionAllocation.currency == payload.currency,
        )
    )
    cost_remaining = int(confirmed_cost or 0) - int(project_allocated or 0)
    if allocation_nanos > cost_remaining:
        raise HTTPException(
            status_code=409,
            detail="Le montant dépasse le coût confirmé restant de ce projet.",
        )

    allocation = ContributionAllocation(
        contribution_id=contribution.id,
        user_id=project.user_id,
        project_id=project.id,
        project_title=project.title,
        period_start=period_start,
        period_end=period_end,
        category=payload.category,
        amount_nanos=allocation_nanos,
        currency=payload.currency,
        note=payload.note,
        recorded_by_user_id=admin.id,
    )
    db.add(allocation)
    db.commit()
    db.refresh(allocation)
    return allocation_response(allocation, contribution)


@admin_router.get("/community-allocations")
def list_contribution_allocations(
    month: str | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(ContributionAllocation).order_by(
        ContributionAllocation.created_at.desc()
    )
    if month:
        start, end = month_bounds(month)
        statement = statement.where(
            ContributionAllocation.period_start == start,
            ContributionAllocation.period_end == end,
        )
    allocations = list(db.scalars(statement))
    contribution_ids = {allocation.contribution_id for allocation in allocations}
    contributions = (
        {
            contribution.id: contribution
            for contribution in db.scalars(
                select(CommunityContribution).where(
                    CommunityContribution.id.in_(contribution_ids)
                )
            )
        }
        if contribution_ids
        else {}
    )
    return [
        allocation_response(allocation, contributions.get(allocation.contribution_id))
        for allocation in allocations
    ]


@admin_router.post("/payments", status_code=201)
def create_manual_payment(
    payload: ManualPaymentRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    billed_user = db.get(User, payload.user_id)
    if billed_user is None or billed_user.role != "client":
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
