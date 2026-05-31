from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import DEFAULT_SYSTEM_PROMPT
from app.db.models import AppSettings
from app.domain.schedule.policy import assume_utc
from app.schemas.aniu import AppSettingsUpdate

logger = logging.getLogger(__name__)


class SettingsService:
    def get_or_create_settings(self, db: Session) -> AppSettings:
        instance = db.scalar(select(AppSettings).limit(1))
        if instance is None:
            env = get_settings()
            instance = AppSettings(
                provider_name="openai-compatible",
                mx_api_key=env.mx_apikey,
                tushare_token=env.tushare_token,
                tushare_api_url=env.tushare_api_url,
                llm_base_url=env.openai_base_url,
                llm_api_key=env.openai_api_key,
                llm_model=env.openai_model,
                llm_provider_configs={},
                arena_initial_cash=env.arena_initial_cash,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )
            db.add(instance)
            db.commit()
            db.refresh(instance)
        if not getattr(instance, "arena_initial_cash", None):
            instance.arena_initial_cash = get_settings().arena_initial_cash
            db.add(instance)
            db.commit()
            db.refresh(instance)
        if instance.llm_provider_configs is None:
            instance.llm_provider_configs = {}
        instance.created_at = assume_utc(instance.created_at)
        instance.updated_at = assume_utc(instance.updated_at)
        return instance

    def update_settings(self, db: Session, payload: AppSettingsUpdate) -> AppSettings:
        instance = self.get_or_create_settings(db)
        sensitive_fields = {"mx_api_key", "tushare_token", "llm_api_key"}
        changed_fields: list[str] = []
        for field, value in payload.model_dump().items():
            if field in sensitive_fields and isinstance(value, str) and "****" in value:
                continue
            if field == "llm_provider_configs":
                value = self._merge_masked_provider_configs(
                    existing=getattr(instance, "llm_provider_configs", None),
                    incoming=value,
                )
            old_value = getattr(instance, field, None)
            if old_value != value:
                changed_fields.append(field)
            setattr(instance, field, value)
        db.add(instance)
        db.commit()
        db.refresh(instance)
        instance.created_at = assume_utc(instance.created_at)
        instance.updated_at = assume_utc(instance.updated_at)
        logger.info("settings updated: changed_fields=%s", changed_fields)
        return instance

    def _merge_masked_provider_configs(
        self,
        *,
        existing: object,
        incoming: object,
    ) -> dict[str, object]:
        if not isinstance(incoming, dict):
            return {}
        existing_configs = existing if isinstance(existing, dict) else {}
        merged: dict[str, object] = {}
        for provider, raw_config in incoming.items():
            if not isinstance(raw_config, dict):
                continue
            provider_key = str(provider).strip()
            if not provider_key:
                continue
            config = dict(raw_config)
            api_key = config.get("api_key")
            existing_config = existing_configs.get(provider_key)
            if (
                isinstance(api_key, str)
                and "****" in api_key
                and isinstance(existing_config, dict)
            ):
                config["api_key"] = existing_config.get("api_key")
            merged[provider_key] = config
        return merged


settings_service = SettingsService()
