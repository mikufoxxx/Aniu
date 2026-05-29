from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.market_data_service import DEFAULT_UNIVERSE, normalize_symbol
from app.services.quant_service import quant_service


class AIMarketContextService:
    def build_context(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        limit: int | None = None,
        lookback_days: int | None = None,
    ) -> str:
        settings = get_settings()
        if not settings.ai_market_context_enabled:
            return ""
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else DEFAULT_UNIVERSE
        normalized_limit = max(1, min(50, int(limit or settings.ai_market_context_limit)))
        normalized_lookback = max(
            1,
            min(120, int(lookback_days or settings.ai_market_context_lookback_days)),
        )
        try:
            dataset = quant_service.build_dataset(
                db,
                symbols=normalized_symbols,
                limit=normalized_limit,
                prefer_realtime=True,
                lookback_days=normalized_lookback,
            )
        except Exception as exc:
            return f"AI量化市场上下文\n- 数据集构建失败: {exc}"
        return self._format_dataset(dataset)

    def _format_dataset(self, dataset: dict[str, Any]) -> str:
        sources = ", ".join(dataset.get("data_sources") or []) or "--"
        coverage = dataset.get("coverage") or {}
        items = dataset.get("items") or []
        universe_size = int(dataset.get("universe_size") or len(items))
        lines = [
            "AI量化市场上下文",
            f"数据源: {sources}",
            (
                "覆盖: "
                f"实时 {int(coverage.get('realtime_symbols') or 0)}/{universe_size}, "
                f"日线 {int(coverage.get('daily_history_symbols') or 0)}/{universe_size}"
            ),
            "候选信号:",
        ]
        for index, item in enumerate(items[:10], start=1):
            daily = item.get("daily_factors") or {}
            lines.append(
                (
                    f"{index}. {item.get('symbol')} {item.get('name') or ''} "
                    f"评分 {float(item.get('score') or 0):.2f}; "
                    f"现价 {self._format_optional_float(item.get('price'))}; "
                    f"涨幅 {float(item.get('change_pct') or 0):+.2f}%; "
                    f"日线动量 {float(daily.get('momentum_pct') or 0):+.2f}%; "
                    f"日线覆盖 {int(daily.get('bars_used') or 0)}日; "
                    f"来源 {item.get('source') or '--'}"
                ).strip()
            )
        return "\n".join(lines).strip()

    def _format_optional_float(self, value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "--"


ai_market_context_service = AIMarketContextService()
