from __future__ import annotations

import json
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DailyBar, IndexBar, MarketDataMaintenanceRun, MarketReport, SectorBar
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
            self._latest_index_snapshot(db),
            self._latest_sector_heat(db),
            self._recent_report_performance(db),
            self._latest_data_quality(db),
            self._mx_supplemental_context(db),
        )

    def _format_dataset(
        self,
        dataset: dict[str, Any],
        index_snapshot: list[dict[str, Any]] | None = None,
        sector_heat: list[dict[str, Any]] | None = None,
        report_performance: list[dict[str, Any]] | None = None,
        data_quality: dict[str, Any] | None = None,
        mx_supplement: dict[str, Any] | None = None,
    ) -> str:
        source_items = list(dataset.get("data_sources") or [])
        if index_snapshot and "tushare_index" not in source_items:
            source_items.append("tushare_index")
        if sector_heat and "tushare_sector" not in source_items:
            source_items.append("tushare_sector")
        sources = ", ".join(source_items) or "--"
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
        if index_snapshot:
            lines.append("指数环境:")
            for item in index_snapshot:
                lines.append(
                    (
                        f"- {item['name']} {item['close']:.2f} "
                        f"({item['pct_chg']:+.2f}%)"
                    )
                )
        if sector_heat:
            lines.append("板块热度:")
            for item in sector_heat:
                lines.append(
                    (
                        f"- {item['name']} {item['pct_chg']:+.2f}% "
                        f"换手 {item['turnover_rate']:.2f}%"
                    )
                )
        lines.append("候选信号:")
        for index, item in enumerate(items[:10], start=1):
            daily = item.get("daily_factors") or {}
            profile = item.get("profile") or {}
            financial = item.get("financial_factors") or {}
            profile_parts = self._profile_parts(profile)
            daily_basic_parts = self._daily_basic_parts(daily)
            financial_parts = self._financial_parts(financial)
            limit_parts = self._limit_event_parts(daily.get("limit_event") or {})
            margin_parts = self._margin_parts(daily.get("margin_detail") or {})
            dragon_tiger_parts = self._dragon_tiger_parts(daily.get("dragon_tiger") or {})
            block_trade_parts = self._block_trade_parts(daily.get("block_trade") or {})
            shareholder_number_parts = self._shareholder_number_parts(
                daily.get("shareholder_number") or {}
            )
            shareholder_trade_parts = self._shareholder_trade_parts(
                daily.get("shareholder_trade") or {}
            )
            lines.append(
                (
                    f"{index}. {item.get('symbol')} {item.get('name') or ''} "
                    f"评分 {float(item.get('score') or 0):.2f}; "
                    f"现价 {self._format_optional_float(item.get('price'))}; "
                    f"涨幅 {float(item.get('change_pct') or 0):+.2f}%; "
                    f"日线动量 {float(daily.get('momentum_pct') or 0):+.2f}%; "
                    f"日线覆盖 {int(daily.get('bars_used') or 0)}日; "
                    f"{profile_parts}"
                    f"{daily_basic_parts}"
                    f"{financial_parts}"
                    f"{limit_parts}"
                    f"{margin_parts}"
                    f"{dragon_tiger_parts}"
                    f"{block_trade_parts}"
                    f"{shareholder_number_parts}"
                    f"{shareholder_trade_parts}"
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
            self._latest_index_snapshot(db),
            self._latest_sector_heat(db),
            self._recent_report_performance(db),
            self._latest_data_quality(db),
            self._mx_supplemental_context(db),
        )

    def _latest_index_snapshot(self, db: Session) -> list[dict[str, Any]]:
        trade_date = db.scalar(
            select(IndexBar.trade_date).order_by(desc(IndexBar.trade_date)).limit(1)
        )
        if not trade_date:
            return []
        rows = db.scalars(
            select(IndexBar)
            .where(IndexBar.trade_date == trade_date)
            .order_by(IndexBar.symbol)
        ).all()
        names = {
            "000001.SH": "上证指数",
            "399001.SZ": "深证成指",
            "399006.SZ": "创业板指",
            "000300.SH": "沪深300",
            "000905.SH": "中证500",
        }
        return [
            {
                "symbol": row.symbol,
                "name": names.get(row.symbol, row.symbol),
                "close": float(row.close or 0),
                "pct_chg": float(row.pct_chg or 0),
            }
            for row in rows
            if row.close is not None
        ][:5]

    def _latest_sector_heat(self, db: Session) -> list[dict[str, Any]]:
        trade_date = db.scalar(
            select(SectorBar.trade_date).order_by(desc(SectorBar.trade_date)).limit(1)
        )
        if not trade_date:
            return []
        rows = db.scalars(
            select(SectorBar)
            .where(SectorBar.trade_date == trade_date)
            .order_by(desc(SectorBar.pct_chg))
            .limit(5)
        ).all()
        return [
            {
                "symbol": row.symbol,
                "name": row.name or row.symbol,
                "pct_chg": float(row.pct_chg or 0),
                "turnover_rate": float(row.turnover_rate or 0),
            }
            for row in rows
            if row.pct_chg is not None
        ]

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

    def _daily_basic_parts(self, daily: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, label, suffix, allow_negative in (
            ("turnover_rate", "换手", "%", False),
            ("volume_ratio", "量比", "", False),
            ("pe_ttm", "PE", "", False),
            ("pb", "PB", "", False),
            ("moneyflow_net_amount", "净流入", "万", True),
            ("moneyflow_buy_lg_amount_rate", "大单", "%", True),
        ):
            value = daily.get(key)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric == 0 or (numeric < 0 and not allow_negative):
                continue
            parts.append(f"{label} {numeric:.2f}{suffix}")
        sectors = daily.get("sector_heat") or []
        if sectors and isinstance(sectors[0], dict):
            sector = sectors[0]
            parts.append(
                f"热板块 {sector.get('name')} {float(sector.get('pct_chg') or 0):+.2f}%"
            )
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _financial_parts(self, financial: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, label, signed in (
            ("roe", "ROE", False),
            ("grossprofit_margin", "毛利", False),
            ("netprofit_yoy", "净利同比", True),
            ("debt_to_assets", "负债率", False),
        ):
            value = financial.get(key)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric == 0:
                continue
            sign = "+" if signed else ""
            parts.append(f"{label} {numeric:{sign}.2f}%")
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _limit_event_parts(self, event: dict[str, Any]) -> str:
        limit_type = str(event.get("limit_type") or "").upper()
        if not limit_type:
            return ""
        label = {"U": "涨停", "D": "跌停", "Z": "炸板"}.get(limit_type, limit_type)
        parts = [label]
        limit_times = int(event.get("limit_times") or 0)
        open_times = int(event.get("open_times") or 0)
        if limit_times > 0:
            parts.append(f"{limit_times}连板")
        if open_times > 0:
            parts.append(f"开板 {open_times}次")
        up_stat = event.get("up_stat")
        if up_stat:
            parts.append(f"统计 {up_stat}")
        return " ".join(parts) + "; "

    def _margin_parts(self, margin: dict[str, Any]) -> str:
        parts: list[str] = []
        net_buy = margin.get("net_financing_buy")
        rzrqye = margin.get("rzrqye")
        try:
            net_buy_number = float(net_buy)
        except (TypeError, ValueError):
            net_buy_number = 0.0
        try:
            balance_number = float(rzrqye)
        except (TypeError, ValueError):
            balance_number = 0.0
        if net_buy_number != 0:
            parts.append(f"融资净买 {net_buy_number / 10000:.2f}万")
        if balance_number > 0:
            parts.append(f"两融余额 {balance_number / 10000:.2f}万")
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _dragon_tiger_parts(self, item: dict[str, Any]) -> str:
        parts: list[str] = []
        net_amount = item.get("net_amount")
        institution_net_buy = item.get("institution_net_buy")
        try:
            net_amount_number = float(net_amount)
        except (TypeError, ValueError):
            net_amount_number = 0.0
        try:
            inst_number = float(institution_net_buy)
        except (TypeError, ValueError):
            inst_number = 0.0
        if net_amount_number != 0:
            parts.append(f"龙虎榜净买 {net_amount_number / 10000:.2f}万")
        if inst_number != 0:
            parts.append(f"机构净买 {inst_number / 10000:.2f}万")
        reason = item.get("reason")
        if reason:
            parts.append(f"上榜 {reason}")
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _block_trade_parts(self, item: dict[str, Any]) -> str:
        parts: list[str] = []
        total_amount = item.get("total_amount")
        try:
            amount_number = float(total_amount)
        except (TypeError, ValueError):
            amount_number = 0.0
        trade_count = int(item.get("trade_count") or 0)
        price_vs_close = item.get("price_vs_close_pct")
        try:
            price_vs_close_number = float(price_vs_close)
        except (TypeError, ValueError):
            price_vs_close_number = 0.0
        if amount_number > 0:
            parts.append(f"大宗成交 {amount_number:.2f}万")
        if trade_count > 0:
            parts.append(f"{trade_count}笔")
        if price_vs_close_number:
            parts.append(f"折溢价 {price_vs_close_number:+.2f}%")
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _shareholder_number_parts(self, item: dict[str, Any]) -> str:
        parts: list[str] = []
        holder_num = int(item.get("holder_num") or 0)
        if holder_num > 0:
            parts.append(f"股东户数 {holder_num}")
        change_pct = item.get("holder_num_change_pct")
        try:
            change_number = float(change_pct)
        except (TypeError, ValueError):
            change_number = 0.0
        if change_number:
            parts.append(f"户数变化 {change_number:+.2f}%")
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _shareholder_trade_parts(self, item: dict[str, Any]) -> str:
        parts: list[str] = []
        net_change_vol = item.get("net_change_vol")
        net_change_ratio = item.get("net_change_ratio")
        try:
            vol_number = float(net_change_vol)
        except (TypeError, ValueError):
            vol_number = 0.0
        try:
            ratio_number = float(net_change_ratio)
        except (TypeError, ValueError):
            ratio_number = 0.0
        if vol_number > 0:
            parts.append(f"重要股东净增持 {vol_number:.2f}万股")
        elif vol_number < 0:
            parts.append(f"重要股东净减持 {abs(vol_number):.2f}万股")
        if ratio_number:
            parts.append(f"净变动 {ratio_number:.2f}%")
        if not parts:
            return ""
        return "; ".join(parts) + "; "

    def _profile_parts(self, profile: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, label in (
            ("industry", "行业"),
            ("area", "地域"),
            ("market", "市场"),
            ("list_date", "上市"),
        ):
            value = profile.get(key)
            if value:
                parts.append(f"{label} {value}")
        if not parts:
            return ""
        return "; ".join(parts) + "; "


ai_market_context_service = AIMarketContextService()
