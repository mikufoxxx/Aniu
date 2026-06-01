from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.ai_market_context_service import ai_market_context_service
from app.services.ai_selection_service import ai_selection_service
from app.services.market_data_maintenance_service import market_data_maintenance_service
from app.services.market_data_service import normalize_symbol
from app.services.quant_service import quant_service


class AIDataRequestService:
    def execute(
        self,
        db: Session,
        *,
        symbols: list[str],
        dimensions: list[str],
        limit: int = 20,
        lookback_days: int = 1825,
        prefer_realtime: bool = True,
        refresh: bool = False,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
        requested_dimensions = self._normalize_dimensions(dimensions)
        plan = {"lookback_days": lookback_days}
        actions = [
            ai_selection_service.dimension_action(dimension, plan)
            for dimension in requested_dimensions
        ]
        refresh_result = None
        if refresh and self._needs_maintenance(requested_dimensions):
            refresh_result = market_data_maintenance_service.run_now(
                db,
                end_date=end_date,
                lookback_days=lookback_days,
                symbols=normalized_symbols,
                dataset_limit=limit,
                report_type=None,
            )
        dataset_kwargs: dict[str, Any] = {
            "symbols": normalized_symbols,
            "limit": limit,
            "prefer_realtime": prefer_realtime,
            "lookback_days": lookback_days,
        }
        if end_date:
            dataset_kwargs["end_date"] = end_date
        dataset = quant_service.build_dataset(db, **dataset_kwargs)
        context = ai_market_context_service.format_dataset_context(db, dataset)
        return {
            "requested_symbols": normalized_symbols,
            "requested_dimensions": requested_dimensions,
            "actions": actions,
            "refresh": refresh_result,
            "dataset": dataset,
            "context": context,
            "context_length": len(context),
        }

    def _normalize_dimensions(self, dimensions: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in dimensions or ["quote", "daily_history"]:
            dimension = str(item or "").strip().lower()
            if not dimension or dimension in seen:
                continue
            seen.add(dimension)
            normalized.append(dimension)
        return normalized or ["quote", "daily_history"]

    def _needs_maintenance(self, dimensions: list[str]) -> bool:
        return any(dimension != "quote" for dimension in dimensions)


ai_data_request_service = AIDataRequestService()
