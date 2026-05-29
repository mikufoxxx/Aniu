from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.ai_market_context_service import ai_market_context_service
from app.services.historical_data_service import historical_data_service
from app.services.market_data_service import normalize_symbol
from app.services.quant_service import quant_service


class AIStockPickerService:
    def build_snapshot(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        limit: int = 50,
        prefer_realtime: bool = True,
        lookback_days: int = 120,
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        dataset = quant_service.build_dataset(
            db,
            symbols=normalized_symbols,
            limit=limit,
            prefer_realtime=prefer_realtime,
            lookback_days=lookback_days,
        )
        context = ai_market_context_service.format_dataset_context(db, dataset)
        coverage = historical_data_service.summarize_daily_coverage(db)
        return {
            "snapshot_id": self._snapshot_id(),
            "selection_mode": "custom_symbols" if normalized_symbols else "auto_universe",
            "data_sources": dataset["data_sources"],
            "coverage": coverage,
            "dataset": dataset,
            "recommendations": dataset["items"],
            "context": context,
            "context_length": len(context),
        }

    def _snapshot_id(self) -> str:
        now = datetime.now(timezone.utc)
        return f"ai-picks-{now.strftime('%Y%m%d%H%M%S')}"


ai_stock_picker_service = AIStockPickerService()
