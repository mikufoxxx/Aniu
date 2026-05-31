from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.database import get_db
from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
from app.services.ai_forecast_service import ai_forecast_service
from app.services.settings_service import settings_service

router = APIRouter(prefix="/settings", tags=["aniu-settings"])


def _settings_read_payload(settings: object) -> dict:
    payload = AppSettingsRead.model_validate(settings).model_dump()
    payload["forecast_ai_config"] = ai_forecast_service.public_config()
    return payload


@router.get("", response_model=AppSettingsRead)
def get_settings(
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AppSettingsRead:
    return _settings_read_payload(settings_service.get_or_create_settings(db))


@router.put("", response_model=AppSettingsRead)
def update_settings(
    payload: AppSettingsUpdate,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AppSettingsRead:
    return _settings_read_payload(settings_service.update_settings(db, payload))
