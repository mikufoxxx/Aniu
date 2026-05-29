from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import session_scope
from app.services.historical_data_service import historical_data_service
from app.services.market_data_service import DEFAULT_UNIVERSE, normalize_symbol
from app.services.quant_service import quant_service


class MarketDataMaintenanceService:
    def __init__(self) -> None:
        self._completed_slots: set[str] = set()

    def reset_runtime_state(self) -> None:
        self._completed_slots.clear()

    def process_due_jobs(self, *, now: datetime | None = None) -> dict[str, Any]:
        settings = get_settings()
        if not settings.market_data_maintenance_enabled:
            return {"status": "disabled"}

        current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        due_time = self._due_time(current, settings.market_data_maintenance_times)
        if due_time is None:
            return {"status": "skipped", "reason": "not_due"}

        slot_key = f"{current.strftime('%Y%m%d')}:{due_time}"
        if slot_key in self._completed_slots:
            return {"status": "skipped", "reason": "already_completed", "slot": slot_key}

        with session_scope() as db:
            result = self.run_now(
                db,
                end_date=current.strftime("%Y%m%d"),
                lookback_days=settings.market_data_maintenance_lookback_days,
                symbols=None,
                dataset_limit=settings.market_data_maintenance_dataset_limit,
            )
        self._completed_slots.add(slot_key)
        return {"status": "completed", "slot": slot_key, "result": result}

    def run_now(
        self,
        db: Session,
        *,
        end_date: str | None = None,
        lookback_days: int | None = None,
        symbols: list[str] | None = None,
        dataset_limit: int | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        normalized_lookback = max(
            1,
            min(120, int(lookback_days or settings.market_data_maintenance_lookback_days)),
        )
        normalized_end = self._normalize_end_date(end_date)
        start_date = (
            datetime.strptime(normalized_end, "%Y%m%d").date()
            - timedelta(days=normalized_lookback - 1)
        ).strftime("%Y%m%d")
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        refresh = historical_data_service.refresh_daily_range(
            db,
            start_date=start_date,
            end_date=normalized_end,
            symbols=normalized_symbols,
        )
        dataset_symbols = normalized_symbols or DEFAULT_UNIVERSE
        dataset = quant_service.build_dataset(
            db,
            symbols=dataset_symbols,
            limit=max(1, min(500, int(dataset_limit or settings.market_data_maintenance_dataset_limit))),
            prefer_realtime=True,
            lookback_days=normalized_lookback,
        )
        return {
            "status": "completed",
            "refresh": refresh,
            "dataset": dataset,
        }

    def _normalize_end_date(self, end_date: str | None) -> str:
        if not end_date:
            return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        text = str(end_date).strip().replace("-", "")
        if len(text) != 8 or not text.isdigit():
            raise ValueError(f"交易日期格式不正确: {end_date}")
        return text

    def _due_time(self, now: datetime, configured_times: str) -> str | None:
        current_minutes = now.hour * 60 + now.minute
        due: list[str] = []
        for item in configured_times.split(","):
            text = item.strip()
            if not text:
                continue
            hour_text, _, minute_text = text.partition(":")
            if not hour_text.isdigit() or not minute_text.isdigit():
                continue
            target_minutes = int(hour_text) * 60 + int(minute_text)
            if 0 <= current_minutes - target_minutes < 10:
                due.append(f"{int(hour_text):02d}:{int(minute_text):02d}")
        return due[-1] if due else None


market_data_maintenance_service = MarketDataMaintenanceService()
