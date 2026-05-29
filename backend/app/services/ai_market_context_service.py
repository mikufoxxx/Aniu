from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DailyBar, MarketDataMaintenanceRun, MarketReport
from app.services.market_data_service import normalize_symbol
from app.services.quant_service import quant_service
from app.services.settings_service import settings_service
from skills.mx_core.client import MXClient


_MAX_AI_MARKET_CONTEXT_LIMIT = 100
_MAX_AI_MARKET_CONTEXT_LOOKBACK_DAYS = 1825


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
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        normalized_limit = max(
            1,
            min(_MAX_AI_MARKET_CONTEXT_LIMIT, int(limit or settings.ai_market_context_limit)),
        )
        normalized_lookback = max(
            1,
            min(
                _MAX_AI_MARKET_CONTEXT_LOOKBACK_DAYS,
                int(lookback_days or settings.ai_market_context_lookback_days),
            ),
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
        return self._format_dataset(
            dataset,
            self._recent_report_performance(db),
            self._latest_data_quality(db),
            self._mx_supplemental_context(db),
        )

    def _format_dataset(
        self,
        dataset: dict[str, Any],
        report_performance: list[dict[str, Any]] | None = None,
        data_quality: dict[str, Any] | None = None,
        mx_supplement: dict[str, Any] | None = None,
    ) -> str:
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
        ]
        if data_quality:
            lines.extend(
                [
                    "数据质量:",
                    (
                        f"- 最近维护 {data_quality['created_at']}；"
                        f"刷新 {data_quality['refresh_range']}；"
                        f"入库 {data_quality['stored_count']} 条；"
                        f"最新日 {data_quality['latest_trade_date']} "
                        f"覆盖 {data_quality['latest_trade_date_symbols']} 只；"
                        f"补数状态 {data_quality['refresh_status']}"
                    ),
                ]
            )
        lines.append("候选信号:")
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
        if mx_supplement:
            lines.append("妙想补充信号:")
            if mx_supplement.get("screen"):
                lines.append(
                    f"- 自然语言选股: {self._compact_payload(mx_supplement['screen'])}"
                )
            if mx_supplement.get("news"):
                lines.append(f"- 资讯检索: {self._compact_payload(mx_supplement['news'])}")
            if mx_supplement.get("error"):
                lines.append(f"- 获取失败: {mx_supplement['error']}")
        if report_performance:
            lines.append("历史推荐表现:")
            for item in report_performance:
                lines.append(
                    (
                        f"- {item['title']} {item['created_date']} "
                        f"1日均值 {item['average_return_pct']:+.2f}% "
                        f"已评估 {item['evaluated_count']}只; "
                        f"{item['top_symbol']} {item['top_return_pct']:+.2f}%"
                    ).strip()
                )
        return "\n".join(lines).strip()

    def format_dataset_context(self, db: Session, dataset: dict[str, Any]) -> str:
        return self._format_dataset(
            dataset,
            self._recent_report_performance(db),
            self._latest_data_quality(db),
            self._mx_supplemental_context(db),
        )

    def _mx_supplemental_context(self, db: Session) -> dict[str, Any] | None:
        app_settings = settings_service.get_or_create_settings(db)
        api_key = str(app_settings.mx_api_key or "").strip()
        if not api_key:
            return None
        env_settings = get_settings()
        try:
            with MXClient(api_key=api_key, base_url=env_settings.mx_api_url) as client:
                screen = client.screen_stocks(app_settings.screener_query)
                news = client.search_news(app_settings.news_query)
            return {"screen": screen, "news": news}
        except Exception as exc:
            return {"error": str(exc)[:240]}

    def _latest_data_quality(self, db: Session) -> dict[str, Any] | None:
        row = db.scalar(
            select(MarketDataMaintenanceRun)
            .order_by(MarketDataMaintenanceRun.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {
            "created_at": row.created_at.strftime("%Y%m%d %H:%M"),
            "refresh_range": (
                f"{row.refresh_start_date or '--'}-{row.refresh_end_date or '--'}"
            ),
            "stored_count": int(row.stored_count or 0),
            "latest_trade_date": row.latest_trade_date or "--",
            "latest_trade_date_symbols": int(row.latest_trade_date_symbols or 0),
            "refresh_status": row.refresh_reason if row.refresh_needed else "覆盖充足",
        }

    def _recent_report_performance(self, db: Session) -> list[dict[str, Any]]:
        reports = db.scalars(
            select(MarketReport).order_by(MarketReport.id.desc()).limit(3)
        ).all()
        rows: list[dict[str, Any]] = []
        for report in reports:
            evaluated = self._report_return_items(db, report)
            if not evaluated:
                continue
            returns = [item["return_pct"] for item in evaluated]
            top = max(evaluated, key=lambda item: item["return_pct"])
            rows.append(
                {
                    "title": report.title,
                    "created_date": report.created_at.strftime("%Y%m%d"),
                    "evaluated_count": len(evaluated),
                    "average_return_pct": round(sum(returns) / len(returns), 4),
                    "top_symbol": top["symbol"],
                    "top_return_pct": top["return_pct"],
                }
            )
        return rows

    def _report_return_items(self, db: Session, report: MarketReport) -> list[dict[str, Any]]:
        report_date = report.created_at.strftime("%Y%m%d")
        items: list[dict[str, Any]] = []
        for recommendation in report.recommendations_payload or []:
            symbol = str(recommendation.get("symbol") or "").strip()
            if not symbol:
                continue
            bars = db.scalars(
                select(DailyBar)
                .where(
                    DailyBar.symbol == symbol,
                    DailyBar.trade_date >= report_date,
                    DailyBar.close.is_not(None),
                )
                .order_by(DailyBar.trade_date)
                .limit(2)
            ).all()
            if len(bars) < 2 or not bars[0].close:
                continue
            entry_close = float(bars[0].close)
            evaluation_close = float(bars[1].close or 0)
            return_pct = ((evaluation_close - entry_close) / entry_close) * 100
            items.append({"symbol": symbol, "return_pct": round(return_pct, 4)})
        return items

    def _format_optional_float(self, value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return "--"

    def _compact_payload(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:800]


ai_market_context_service = AIMarketContextService()
