from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Date, JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="client", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_user_updated", "user_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (Index("ix_assets_user_project", "user_id", "project_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    model_name: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str] = mapped_column(String(20), default="")
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(40), default="upload")
    message: Mapped[str] = mapped_column(Text, default="")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_user_job", "user_id", "job_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BrandKit(Base):
    __tablename__ = "brand_kits"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="Identité principale")
    primary_color: Mapped[str] = mapped_column(String(7), default="#0b1220")
    accent_color: Mapped[str] = mapped_column(String(7), default="#34d399")
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PriceRate(Base):
    __tablename__ = "price_rates"
    __table_args__ = (
        Index(
            "ix_price_rates_lookup",
            "provider",
            "service",
            "model",
            "unit",
            "effective_from",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(80))
    service: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    unit_amount_nanos: Mapped[int] = mapped_column(BigInteger)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_url: Mapped[str] = mapped_column(String(500), default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_user_occurred", "user_id", "occurred_at"),
        Index("ix_usage_events_project_occurred", "project_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_title: Mapped[str] = mapped_column(String(180), default="")
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    service: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[int] = mapped_column(BigInteger)
    unit: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    amount_nanos: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="estimated", index=True)
    price_rate_id: Mapped[str | None] = mapped_column(
        ForeignKey("price_rates.id"), nullable=True, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_user_paid", "user_id", "paid_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    recorded_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount_nanos: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    method: Mapped[str] = mapped_column(String(50), default="manual")
    reference: Mapped[str] = mapped_column(String(180), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    period_start: Mapped[date] = mapped_column(Date, index=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class BillingStatement(Base):
    __tablename__ = "billing_statements"
    __table_args__ = (
        Index("ix_billing_statements_user_period", "user_id", "period_start", "period_end"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    usage_amount_nanos: Mapped[int] = mapped_column(BigInteger)
    payment_amount_nanos: Mapped[int] = mapped_column(BigInteger)
    balance_amount_nanos: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
