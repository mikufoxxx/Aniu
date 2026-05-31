from __future__ import annotations

import secrets
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_SKILL_WORKSPACE_DIRNAME = "skill_workspace"
_JWT_SECRET_FILENAME = "jwt_secret.txt"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        enable_decoding=False,
        extra="ignore",
    )

    app_name: str = "Aniu"
    api_prefix: str = "/api/aniu"
    sqlite_db_path: Path = Field(
        default=Path("./data/aniu.sqlite3"), alias="SQLITE_DB_PATH"
    )

    mx_apikey: str | None = Field(default=None, alias="MX_APIKEY")
    mx_api_url: str = Field(
        default="https://mkapi2.dfcfs.com/finskillshub", alias="MX_API_URL"
    )
    tushare_token: str | None = Field(default=None, alias="TUSHARE_TOKEN")
    tushare_api_url: str | None = Field(default=None, alias="TUSHARE_API_URL")
    realtime_quote_cache_ttl_seconds: int = Field(
        default=5, alias="REALTIME_QUOTE_CACHE_TTL_SECONDS"
    )

    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    forecast_ai_base_url: str | None = Field(default=None, alias="FORECAST_AI_BASE_URL")
    forecast_ai_api_key: str | None = Field(default=None, alias="FORECAST_AI_API_KEY")
    forecast_ai_models: str = Field(
        default=(
            "gpt-5.5,deepseek-v4-flash,grok-4.20-0309-non-reasoning,"
            "minimax-m2.7,deepseek-v4-pro"
        ),
        alias="FORECAST_AI_MODELS",
    )

    account_overview_cache_ttl_seconds: int = Field(
        default=30, alias="ACCOUNT_OVERVIEW_CACHE_TTL_SECONDS"
    )

    scheduler_poll_seconds: int = Field(default=15, alias="SCHEDULER_POLL_SECONDS")
    market_data_maintenance_enabled: bool = Field(
        default=False, alias="MARKET_DATA_MAINTENANCE_ENABLED"
    )
    market_data_maintenance_times: str = Field(
        default="08:45,15:30", alias="MARKET_DATA_MAINTENANCE_TIMES"
    )
    market_data_maintenance_lookback_days: int = Field(
        default=1825, alias="MARKET_DATA_MAINTENANCE_LOOKBACK_DAYS"
    )
    market_data_maintenance_dataset_limit: int = Field(
        default=1000, alias="MARKET_DATA_MAINTENANCE_DATASET_LIMIT"
    )
    arena_initial_cash: float = Field(default=200000.0, alias="ARENA_INITIAL_CASH")
    arena_automation_enabled: bool = Field(
        default=True, alias="ARENA_AUTOMATION_ENABLED"
    )
    arena_schedule_grace_minutes: int = Field(
        default=4, alias="ARENA_SCHEDULE_GRACE_MINUTES"
    )
    ai_market_context_enabled: bool = Field(
        default=True, alias="AI_MARKET_CONTEXT_ENABLED"
    )
    ai_market_context_limit: int = Field(default=100, alias="AI_MARKET_CONTEXT_LIMIT")
    ai_market_context_lookback_days: int = Field(
        default=1825, alias="AI_MARKET_CONTEXT_LOOKBACK_DAYS"
    )
    app_login_password: str | None = Field(default=None, alias="APP_LOGIN_PASSWORD")
    jwt_secret: str | None = Field(default=None, alias="JWT_SECRET")
    jwt_expire_hours: int = Field(default=24, alias="JWT_EXPIRE_HOURS")
    trust_x_forwarded_for: bool = Field(
        default=False,
        alias="TRUST_X_FORWARDED_FOR",
    )
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["*"], alias="CORS_ALLOW_ORIGINS"
    )

    @field_validator(
        "mx_apikey",
        "tushare_token",
        "tushare_api_url",
        "openai_base_url",
        "openai_api_key",
        "forecast_ai_base_url",
        "forecast_ai_api_key",
        "app_login_password",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, value: object) -> str | None:
        """Normalize empty / whitespace-only env vars to None."""
        if isinstance(value, str) and not value.strip():
            return None
        return value  # type: ignore[return-value]

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def normalize_jwt_secret(cls, value: object) -> str | None:
        if not value or (isinstance(value, str) and not value.strip()):
            return None
        return str(value).strip()

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item) for item in value]
        return ["*"]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.sqlite_db_path.is_absolute():
        settings.sqlite_db_path = Path.cwd() / settings.sqlite_db_path
    settings.sqlite_db_path = settings.sqlite_db_path.resolve()

    configured_db_path = settings.sqlite_db_path
    default_db_path = Path.cwd() / "data" / "aniu.sqlite3"
    legacy_db_path = Path.cwd() / "data" / "aniu.db"
    using_default_relative_path = configured_db_path == default_db_path
    # Backward-compatible fallback for older deployments that persisted the
    # SQLite file as ./data/aniu.db before the default name was unified.
    if using_default_relative_path and not configured_db_path.exists() and legacy_db_path.exists():
        settings.sqlite_db_path = legacy_db_path.resolve()

    _merge_legacy_skill_workspace(settings)
    if not settings.jwt_secret:
        settings.jwt_secret = _load_or_create_jwt_secret(
            get_persistent_jwt_secret_file(settings)
        )
    return settings


def get_runtime_data_dir(settings: Settings | None = None) -> Path:
    current = settings or get_settings()
    cwd = Path.cwd().resolve()
    if cwd.name == "backend" and cwd in current.sqlite_db_path.parents:
        return (cwd.parent / "data").resolve()
    return current.sqlite_db_path.parent.resolve()


def get_skill_workspace_root(settings: Settings | None = None) -> Path:
    return get_runtime_data_dir(settings) / _SKILL_WORKSPACE_DIRNAME


def get_skill_workspace_skills_dir(settings: Settings | None = None) -> Path:
    return get_skill_workspace_root(settings) / "skills"


def get_persistent_jwt_secret_file(settings: Settings | None = None) -> Path:
    return get_runtime_data_dir(settings) / _JWT_SECRET_FILENAME


def _legacy_skill_workspace_root(settings: Settings) -> Path:
    return settings.sqlite_db_path.parent / _SKILL_WORKSPACE_DIRNAME


def _merge_legacy_skill_workspace(settings: Settings) -> None:
    legacy_root = _legacy_skill_workspace_root(settings)
    target_root = get_skill_workspace_root(settings)
    if not legacy_root.exists():
        return
    if legacy_root.resolve() == target_root.resolve():
        return

    target_root.mkdir(parents=True, exist_ok=True)
    for child in legacy_root.iterdir():
        destination = target_root / child.name
        if destination.exists():
            continue
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copy2(child, destination)


def _load_or_create_jwt_secret(secret_file: Path) -> str:
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.is_file():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    secret = secrets.token_urlsafe(32)
    secret_file.write_text(secret, encoding="utf-8")
    return secret
