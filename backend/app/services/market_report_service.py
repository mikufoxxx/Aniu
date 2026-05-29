from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyBar, MarketReport
from app.services.ai_market_context_service import ai_market_context_service
from app.services.market_data_service import normalize_symbol
from app.services.quant_service import quant_service


REPORT_TITLES = {
    "morning": "早盘推荐",
    "closing": "收盘分析",
}
_MAX_REPORT_LOOKBACK_DAYS = 1825


class MarketReportService:
    def generate_report(
        self,
        db: Session,
        *,
        report_type: str,
        symbols: list[str] | None = None,
        limit: int = 10,
        lookback_days: int = 20,
    ) -> dict[str, Any]:
        normalized_type = self._normalize_report_type(report_type)
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        normalized_limit = max(1, min(50, int(limit)))
        normalized_lookback = max(1, min(_MAX_REPORT_LOOKBACK_DAYS, int(lookback_days)))
        dataset = quant_service.build_dataset(
            db,
            symbols=normalized_symbols,
            limit=normalized_limit,
            prefer_realtime=True,
            lookback_days=normalized_lookback,
        )
        context = ai_market_context_service.build_context(
            db,
            symbols=normalized_symbols,
            limit=normalized_limit,
            lookback_days=normalized_lookback,
        )
        recommendations = self._recommendations(dataset.get("items") or [], normalized_type)
        summary = self._summary(
            report_type=normalized_type,
            coverage=dataset.get("coverage") or {},
            recommendations=recommendations,
            data_sources=dataset.get("data_sources") or [],
        )
        record = MarketReport(
            report_type=normalized_type,
            title=REPORT_TITLES[normalized_type],
            symbols_json=normalized_symbols or [],
            lookback_days=normalized_lookback,
            data_sources_json=list(dataset.get("data_sources") or []),
            coverage_payload=dict(dataset.get("coverage") or {}),
            recommendations_payload=recommendations,
            dataset_payload=dataset,
            context_text=context,
            summary=summary,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return self._payload(record)

    def list_reports(
        self,
        db: Session,
        *,
        report_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        stmt = select(MarketReport).order_by(MarketReport.id.desc())
        if report_type:
            stmt = stmt.where(MarketReport.report_type == self._normalize_report_type(report_type))
        reports = db.scalars(stmt.limit(max(1, min(100, int(limit))))).all()
        return {"items": [self._payload(report) for report in reports]}

    def evaluate_performance(
        self,
        db: Session,
        *,
        report_id: int,
        horizon_days: int = 1,
    ) -> dict[str, Any]:
        report = db.get(MarketReport, report_id)
        if report is None:
            raise LookupError("报告不存在。")

        normalized_horizon = max(1, min(20, int(horizon_days)))
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
            ).all()
            items.append(self._performance_item(recommendation, bars, normalized_horizon))

        evaluated = [item for item in items if item["status"] == "evaluated"]
        returns = [float(item["return_pct"]) for item in evaluated]
        return {
            "report_id": report.id,
            "report_type": report.report_type,
            "title": report.title,
            "horizon_days": normalized_horizon,
            "evaluated_count": len(evaluated),
            "pending_count": len(items) - len(evaluated),
            "average_return_pct": round(sum(returns) / len(returns), 4) if returns else None,
            "best_return_pct": round(max(returns), 4) if returns else None,
            "worst_return_pct": round(min(returns), 4) if returns else None,
            "items": items,
        }

    def _normalize_report_type(self, report_type: str) -> str:
        text = str(report_type or "").strip().lower()
        if text not in REPORT_TITLES:
            raise ValueError("报告类型必须是 morning 或 closing。")
        return text

    def _recommendations(
        self,
        items: list[dict[str, Any]],
        report_type: str,
    ) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        for item in items:
            daily = item.get("daily_factors") or {}
            score = float(item.get("score") or 0)
            momentum = float(daily.get("momentum_pct") or 0)
            action = "WATCH"
            if report_type == "closing":
                action = "REVIEW"
            if score < 35 or momentum < -8:
                action = "AVOID"
            recommendations.append(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name") or item.get("symbol"),
                    "action": action,
                    "score": round(score, 4),
                    "price": item.get("price"),
                    "change_pct": item.get("change_pct"),
                    "daily_momentum_pct": round(momentum, 4),
                    "reason": self._reason(item, action),
                }
            )
        return recommendations

    def _performance_item(
        self,
        recommendation: dict[str, Any],
        bars: list[DailyBar],
        horizon_days: int,
    ) -> dict[str, Any]:
        base = {
            "symbol": recommendation.get("symbol"),
            "name": recommendation.get("name") or recommendation.get("symbol"),
            "action": recommendation.get("action"),
            "score": float(recommendation.get("score") or 0),
            "entry_date": bars[0].trade_date if bars else None,
            "entry_close": bars[0].close if bars else None,
            "evaluation_date": None,
            "evaluation_close": None,
            "return_pct": None,
            "status": "pending",
        }
        if not bars:
            base["status"] = "no_entry"
            return base
        if len(bars) <= horizon_days:
            return base
        entry = bars[0]
        evaluation = bars[horizon_days]
        if not entry.close:
            base["status"] = "no_entry"
            return base
        base.update(
            {
                "evaluation_date": evaluation.trade_date,
                "evaluation_close": evaluation.close,
                "return_pct": round(
                    ((float(evaluation.close or 0) - float(entry.close)) / float(entry.close)) * 100,
                    4,
                ),
                "status": "evaluated",
            }
        )
        return base

    def _reason(self, item: dict[str, Any], action: str) -> str:
        daily = item.get("daily_factors") or {}
        if action == "AVOID":
            return "评分或日线动量偏弱，暂不纳入主动候选。"
        return (
            f"综合评分 {float(item.get('score') or 0):.2f}，"
            f"当日涨幅 {float(item.get('change_pct') or 0):+.2f}%，"
            f"日线动量 {float(daily.get('momentum_pct') or 0):+.2f}%。"
        )

    def _summary(
        self,
        *,
        report_type: str,
        coverage: dict[str, Any],
        recommendations: list[dict[str, Any]],
        data_sources: list[str],
    ) -> str:
        title = REPORT_TITLES[report_type]
        top = recommendations[0] if recommendations else None
        top_text = f"{top['symbol']} {top['name']}" if top else "暂无候选"
        return (
            f"{title}已生成，数据源 {', '.join(data_sources) or '--'}；"
            f"实时覆盖 {coverage.get('realtime_symbols', 0)}，"
            f"日线覆盖 {coverage.get('daily_history_symbols', 0)}；"
            f"首位候选 {top_text}。"
        )

    def _payload(self, report: MarketReport) -> dict[str, Any]:
        return {
            "id": report.id,
            "report_type": report.report_type,
            "title": report.title,
            "symbols": report.symbols_json or [],
            "lookback_days": report.lookback_days,
            "data_sources": report.data_sources_json or [],
            "coverage": report.coverage_payload or {},
            "recommendations": report.recommendations_payload or [],
            "dataset": report.dataset_payload or {},
            "context": report.context_text,
            "summary": report.summary,
            "created_at": report.created_at,
        }


market_report_service = MarketReportService()
