from __future__ import annotations

import math

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .auth import require_user
from .config import settings
from .costs import money_string, quote_usage
from .database import get_db
from .models import User


router = APIRouter(prefix="/api/transcription", tags=["transcription"])


class QuoteRequest(BaseModel):
    duration_seconds: float = Field(gt=0, le=24 * 60 * 60)


@router.post("/quote")
def quote_transcription(
    payload: QuoteRequest,
    _user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    quantity = math.ceil(payload.duration_seconds)
    quote = quote_usage(
        db,
        provider="openai",
        service="transcription",
        model=settings.transcription_model,
        quantity=quantity,
        unit="audio_second",
    )
    return {
        "provider": quote.provider,
        "model": quote.model,
        "duration_seconds": payload.duration_seconds,
        "billed_seconds": quantity,
        "currency": quote.currency,
        "amount": money_string(quote.amount_nanos),
        "unit_amount": money_string(quote.unit_amount_nanos),
    }
