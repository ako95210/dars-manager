from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import require_client
from .config import settings
from .costs import cost_control, money_string, quote_usage
from .database import get_db
from .models import User
from .semantic_analysis import estimate_semantic_tokens


router = APIRouter(prefix="/api/transcription", tags=["transcription"])


class QuoteRequest(BaseModel):
    duration_seconds: float = Field(gt=0, le=24 * 60 * 60)
    transcription_mode: Literal["cloud", "local"] | None = None
    chaptering_mode: Literal["ai", "local"] | None = None


@router.post("/quote")
def quote_transcription(
    payload: QuoteRequest,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> dict:
    transcription_mode = payload.transcription_mode or (
        "cloud" if settings.transcription_backend == "openai" else "local"
    )
    chaptering_mode = payload.chaptering_mode or (
        "ai" if settings.semantic_analysis_backend == "openai" else "local"
    )
    quantity = math.ceil(payload.duration_seconds)
    transcription_amount_nanos = 0
    transcription_unit_amount_nanos = 0
    if transcription_mode == "cloud":
        quote = quote_usage(
            db,
            provider="openai",
            service="transcription",
            model=settings.transcription_model,
            quantity=quantity,
            unit="audio_second",
        )
        transcription_amount_nanos = quote.amount_nanos
        transcription_unit_amount_nanos = quote.unit_amount_nanos
    analysis_amount_nanos = 0
    analysis_input_tokens = 0
    analysis_output_tokens = 0
    if chaptering_mode == "ai":
        analysis_input_tokens, analysis_output_tokens = estimate_semantic_tokens(
            payload.duration_seconds
        )
        analysis_amount_nanos = sum(
            quote_usage(
                db,
                provider="openai",
                service="content_analysis",
                model=settings.semantic_analysis_model,
                quantity=quantity,
                unit=unit,
            ).amount_nanos
            for quantity, unit in (
                (analysis_input_tokens, "input_token"),
                (analysis_output_tokens, "output_token"),
            )
        )
    total_amount_nanos = transcription_amount_nanos + analysis_amount_nanos
    control = cost_control(
        db,
        user_id=user.id,
        proposed_amount_nanos=total_amount_nanos,
    )
    return {
        "provider": "openai" if transcription_mode == "cloud" else "local",
        "model": (
            settings.transcription_model
            if transcription_mode == "cloud"
            else settings.local_whisper_model
        ),
        "transcription_mode": transcription_mode,
        "chaptering_mode": chaptering_mode,
        "duration_seconds": payload.duration_seconds,
        "billed_seconds": quantity,
        "currency": "USD",
        "amount": money_string(total_amount_nanos),
        "transcription_amount": money_string(transcription_amount_nanos),
        "semantic_analysis": {
            "model": settings.semantic_analysis_model,
            "estimated_input_tokens": analysis_input_tokens,
            "estimated_output_tokens": analysis_output_tokens,
            "amount": money_string(analysis_amount_nanos),
        },
        "unit_amount": money_string(transcription_unit_amount_nanos),
        "requires_confirmation": control.requires_confirmation,
        "confirmation_reasons": list(control.confirmation_reasons),
        "monthly_committed": money_string(control.committed_nanos),
        "monthly_projected": money_string(control.projected_nanos),
        "monthly_budget": money_string(control.monthly_budget_nanos),
        "budget_state": control.state,
    }
