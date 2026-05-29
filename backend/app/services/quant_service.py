from __future__ import annotations

import math
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DailyBar,
    FinancialIndicator,
    LimitEvent,
    SectorBar,
    SectorMember,
    StockProfile,
)
from app.services.market_data_service import DEFAULT_UNIVERSE, market_data_service, normalize_symbol


def _number(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric):
        return default
    return numeric


class QuantService:
    _AUTO_UNIVERSE_LIMIT = 1000

    def generate_candidates(
        self,
        *,
        db: Session | None = None,
        symbols: list[str] | None = None,
        limit: int = 20,
        prefer_realtime: bool = True,
        lookback_days: int = 20,
    ) -> dict[str, Any]:
        universe = self._resolve_universe(db, symbols, limit=limit)
        quotes = market_data_service.get_quotes(universe, prefer_realtime=prefer_realtime)
        daily_factors = self._daily_factors_by_symbol(
            db,
            symbols=universe,
            lookback_days=lookback_days,
        )
        profiles = self._profiles_by_symbol(db, universe)
        financials = self._financial_indicators_by_symbol(db, universe)
        candidates = [
            self._score_quote(
                item,
                daily_factors.get(normalize_symbol(str(item.get("symbol") or ""))),
                profiles.get(normalize_symbol(str(item.get("symbol") or ""))),
                financials.get(normalize_symbol(str(item.get("symbol") or ""))),
            )
            for item in quotes
        ]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        selected = candidates[: max(1, min(limit, len(candidates)))]
        data_sources = sorted(
            {str(item.get("source")) for item in selected if item.get("source")}
        )
        if any(item.get("daily_factors", {}).get("bars_used", 0) > 0 for item in selected):
            data_sources.append("tushare_daily")
        if any(
            item.get("daily_factors", {}).get("moneyflow_net_amount", 0) != 0
            for item in selected
        ):
            data_sources.append("tushare_moneyflow")
        if any(item.get("daily_factors", {}).get("sector_heat") for item in selected):
            data_sources.append("tushare_sector_member")
        if any(item.get("profile") for item in selected):
            data_sources.append("tushare_stock_basic")
        if any(item.get("financial_factors") for item in selected):
            data_sources.append("tushare_fina_indicator")
        if any(item.get("daily_factors", {}).get("limit_event") for item in selected):
            data_sources.append("tushare_limit_list_d")
        return {
            "universe_size": len(universe),
            "candidate_count": len(selected),
            "data_sources": data_sources,
            "candidates": selected,
        }

    def _resolve_universe(
        self,
        db: Session | None,
        symbols: list[str] | None,
        *,
        limit: int,
    ) -> list[str]:
        if symbols:
            return [normalize_symbol(symbol) for symbol in symbols]
        stored = self._stored_universe(db, limit=max(limit, self._AUTO_UNIVERSE_LIMIT))
        return stored or [normalize_symbol(symbol) for symbol in DEFAULT_UNIVERSE]

    def _stored_universe(self, db: Session | None, *, limit: int) -> list[str]:
        if db is None:
            return []
        trade_date = db.scalar(
            select(DailyBar.trade_date)
            .group_by(DailyBar.trade_date)
            .order_by(func.count(DailyBar.symbol).desc(), DailyBar.trade_date.desc())
            .limit(1)
        )
        if not trade_date:
            return []
        return list(
            db.scalars(
                select(DailyBar.symbol)
                .where(DailyBar.trade_date == trade_date)
                .order_by(DailyBar.amount.desc(), DailyBar.symbol)
                .limit(max(1, min(self._AUTO_UNIVERSE_LIMIT, int(limit))))
            ).all()
        )

    def build_dataset(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        limit: int = 50,
        prefer_realtime: bool = True,
        lookback_days: int = 20,
    ) -> dict[str, Any]:
        payload = self.generate_candidates(
            db=db,
            symbols=symbols,
            limit=limit,
            prefer_realtime=prefer_realtime,
            lookback_days=lookback_days,
        )
        items = payload["candidates"]
        coverage = {
            "realtime_symbols": sum(1 for item in items if item.get("source") != "fallback"),
            "daily_history_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("bars_used", 0) > 0
            ),
            "symbols_with_price": sum(1 for item in items if item.get("price") is not None),
            "profile_symbols": sum(1 for item in items if item.get("profile")),
            "financial_symbols": sum(1 for item in items if item.get("financial_factors")),
            "limit_event_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("limit_event")
            ),
        }
        return {
            "universe_size": payload["universe_size"],
            "item_count": len(items),
            "lookback_days": lookback_days,
            "data_sources": payload["data_sources"],
            "coverage": coverage,
            "items": items,
        }

    def _score_quote(
        self,
        quote: dict[str, Any],
        daily_factors: dict[str, Any] | None = None,
        profile: dict[str, Any] | None = None,
        financial_factors: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        change_pct = _number(quote.get("change_pct"))
        amount = max(_number(quote.get("amount")), 0.0)
        turnover = max(_number(quote.get("turnover")), 0.0)
        volume_ratio = max(_number(quote.get("volume_ratio"), 1.0), 0.0)
        daily = daily_factors or self._empty_daily_factors()
        profile_payload = profile or {}
        financial_payload = financial_factors or {}
        display_name = str(quote.get("name") or profile_payload.get("name") or "")

        factor_scores = {
            "momentum": max(min(change_pct / 10.0, 1.0), -1.0),
            "amount": min(math.log10(amount + 1) / 10.0, 1.0),
            "turnover": min(turnover / 8.0, 1.0),
            "volume_ratio": min(volume_ratio / 3.0, 1.0),
            "daily_momentum": max(
                min(_number(daily.get("momentum_pct")) / 15.0, 1.0),
                -1.0,
            ),
            "daily_liquidity": min(math.log10(max(_number(daily.get("avg_amount")), 0.0) + 1) / 8.0, 1.0),
            "daily_turnover": min(max(_number(daily.get("turnover_rate")), 0.0) / 5.0, 1.0),
            "daily_volume_ratio": min(max(_number(daily.get("volume_ratio"), 1.0), 0.0) / 3.0, 1.0),
            "valuation_sanity": self._valuation_sanity_score(daily),
            "market_cap": min(math.log10(max(_number(daily.get("total_mv")), 0.0) + 1) / 9.0, 1.0),
            "moneyflow_net": max(
                min(_number(daily.get("moneyflow_net_amount")) / 10000.0, 1.0),
                -1.0,
            ),
            "moneyflow_large": max(
                min(_number(daily.get("moneyflow_buy_lg_amount_rate")) / 10.0, 1.0),
                -1.0,
            ),
            "sector_heat": self._sector_heat_score(daily),
            "limit_sentiment": self._limit_sentiment_score(daily),
            "financial_quality": self._financial_quality_score(financial_payload),
        }
        score = self._weighted_score(factor_scores, daily["bars_used"])
        return {
            "symbol": normalize_symbol(str(quote.get("symbol") or "")),
            "name": display_name,
            "price": quote.get("price"),
            "change_pct": change_pct,
            "amount": amount,
            "turnover": turnover,
            "volume_ratio": volume_ratio,
            "source": quote.get("source"),
            "timestamp": quote.get("timestamp"),
            "score": round(score, 4),
            "factor_scores": {
                key: round(value, 4) for key, value in factor_scores.items()
            },
            "profile": profile_payload,
            "financial_factors": financial_payload,
            "daily_factors": daily,
            "rationale": self._rationale(change_pct, amount, volume_ratio),
        }

    def _weighted_score(self, factor_scores: dict[str, float], daily_bars_used: int) -> float:
        if daily_bars_used <= 0:
            score = (
                factor_scores["momentum"] * 40
                + factor_scores["amount"] * 30
                + factor_scores["turnover"] * 15
                + factor_scores["volume_ratio"] * 15
            )
        else:
            score = (
                factor_scores["momentum"] * 25
                + factor_scores["amount"] * 20
                + factor_scores["turnover"] * 8
                + factor_scores["volume_ratio"] * 8
                + factor_scores["daily_momentum"] * 18
                + factor_scores["daily_liquidity"] * 5
                + factor_scores["daily_turnover"] * 6
                + factor_scores["daily_volume_ratio"] * 5
                + factor_scores["valuation_sanity"] * 3
                + factor_scores["market_cap"] * 2
                + factor_scores["moneyflow_net"] * 4
                + factor_scores["moneyflow_large"] * 2
                + factor_scores["sector_heat"] * 4
                + factor_scores["limit_sentiment"] * 4
                + factor_scores["financial_quality"] * 4
            )
        return round(score, 4)

    def _valuation_sanity_score(self, daily: dict[str, Any]) -> float:
        pe_ttm = _number(daily.get("pe_ttm"))
        pb = _number(daily.get("pb"))
        scores: list[float] = []
        if pe_ttm > 0:
            scores.append(max(0.0, min(1.0, 1.0 - abs(pe_ttm - 25.0) / 50.0)))
        if pb > 0:
            scores.append(max(0.0, min(1.0, 1.0 - pb / 20.0)))
        if not scores:
            return 0.0
        return sum(scores) / len(scores)

    def _sector_heat_score(self, daily: dict[str, Any]) -> float:
        sectors = daily.get("sector_heat") or []
        if not sectors:
            return 0.0
        top = max(_number(item.get("pct_chg")) for item in sectors if isinstance(item, dict))
        return max(min(top / 5.0, 1.0), -1.0)

    def _limit_sentiment_score(self, daily: dict[str, Any]) -> float:
        event = daily.get("limit_event") or {}
        if not isinstance(event, dict):
            return 0.0
        limit_type = str(event.get("limit_type") or "").upper()
        limit_times = max(_number(event.get("limit_times")), 0.0)
        open_times = max(_number(event.get("open_times")), 0.0)
        if limit_type == "U":
            return max(min(0.55 + limit_times * 0.15 - open_times * 0.05, 1.0), 0.0)
        if limit_type == "Z":
            return max(min(0.1 - open_times * 0.05, 0.0), -0.6)
        if limit_type == "D":
            return -1.0
        return 0.0

    def _financial_quality_score(self, financial: dict[str, Any]) -> float:
        if not financial:
            return 0.0
        scores: list[float] = []
        roe = _number(financial.get("roe"))
        gross_margin = _number(financial.get("grossprofit_margin"))
        netprofit_yoy = _number(financial.get("netprofit_yoy"))
        debt_to_assets = _number(financial.get("debt_to_assets"))
        if roe:
            scores.append(max(min(roe / 30.0, 1.0), -1.0))
        if gross_margin:
            scores.append(max(min(gross_margin / 60.0, 1.0), -1.0))
        if netprofit_yoy:
            scores.append(max(min(netprofit_yoy / 40.0, 1.0), -1.0))
        if debt_to_assets:
            scores.append(max(min((80.0 - debt_to_assets) / 80.0, 1.0), -1.0))
        return sum(scores) / len(scores) if scores else 0.0

    def _daily_factors_by_symbol(
        self,
        db: Session | None,
        *,
        symbols: list[str],
        lookback_days: int,
    ) -> dict[str, dict[str, Any]]:
        if db is None:
            return {}
        rows = db.scalars(
            select(DailyBar)
            .where(DailyBar.symbol.in_(symbols))
            .order_by(DailyBar.symbol, DailyBar.trade_date)
        ).all()
        by_symbol: dict[str, list[DailyBar]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)
        factors = {
            symbol: self._daily_factors(bars[-max(1, lookback_days):])
            for symbol, bars in by_symbol.items()
        }
        sector_heat = self._sector_heat_by_symbol(db, symbols)
        for symbol, sectors in sector_heat.items():
            factors.setdefault(symbol, self._empty_daily_factors())["sector_heat"] = sectors
        limit_events = self._limit_events_by_symbol(db, symbols)
        for symbol, event in limit_events.items():
            factors.setdefault(symbol, self._empty_daily_factors())["limit_event"] = event
        return factors

    def _profiles_by_symbol(
        self,
        db: Session | None,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        if db is None:
            return {}
        rows = db.scalars(
            select(StockProfile).where(StockProfile.symbol.in_(symbols))
        ).all()
        return {
            row.symbol: {
                "name": row.name,
                "area": row.area,
                "industry": row.industry,
                "market": row.market,
                "exchange": row.exchange,
                "list_status": row.list_status,
                "list_date": row.list_date,
                "is_hs": row.is_hs,
            }
            for row in rows
        }

    def _financial_indicators_by_symbol(
        self,
        db: Session | None,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        if db is None:
            return {}
        rows = db.scalars(
            select(FinancialIndicator)
            .where(FinancialIndicator.symbol.in_(symbols))
            .order_by(
                FinancialIndicator.symbol,
                desc(FinancialIndicator.end_date),
                desc(FinancialIndicator.ann_date),
            )
        ).all()
        latest: dict[str, FinancialIndicator] = {}
        for row in rows:
            latest.setdefault(row.symbol, row)
        return {
            symbol: {
                "ann_date": row.ann_date,
                "end_date": row.end_date,
                "roe": _number(row.roe),
                "roe_dt": _number(row.roe_dt),
                "grossprofit_margin": _number(row.grossprofit_margin),
                "netprofit_margin": _number(row.netprofit_margin),
                "netprofit_yoy": _number(row.netprofit_yoy),
                "or_yoy": _number(row.or_yoy),
                "debt_to_assets": _number(row.debt_to_assets),
                "assets_turn": _number(row.assets_turn),
                "current_ratio": _number(row.current_ratio),
            }
            for symbol, row in latest.items()
        }

    def _limit_events_by_symbol(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        rows = db.scalars(
            select(LimitEvent)
            .where(LimitEvent.symbol.in_(symbols))
            .order_by(
                LimitEvent.symbol,
                desc(LimitEvent.trade_date),
                LimitEvent.limit_type,
            )
        ).all()
        latest: dict[str, LimitEvent] = {}
        priority = {"U": 0, "Z": 1, "D": 2}
        for row in rows:
            current = latest.get(row.symbol)
            if current is None:
                latest[row.symbol] = row
                continue
            if row.trade_date != current.trade_date:
                continue
            if priority.get(row.limit_type, 99) < priority.get(current.limit_type, 99):
                latest[row.symbol] = row
        return {
            symbol: {
                "trade_date": row.trade_date,
                "limit_type": row.limit_type,
                "name": row.name,
                "industry": row.industry,
                "pct_chg": _number(row.pct_chg),
                "open_times": int(row.open_times or 0),
                "up_stat": row.up_stat,
                "limit_times": int(row.limit_times or 0),
                "fd_amount": _number(row.fd_amount),
                "turnover_ratio": _number(row.turnover_ratio),
            }
            for symbol, row in latest.items()
        }

    def _sector_heat_by_symbol(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        trade_date = db.scalar(
            select(SectorBar.trade_date).order_by(desc(SectorBar.trade_date)).limit(1)
        )
        if not trade_date:
            return {}
        sector_rows = db.scalars(
            select(SectorBar).where(SectorBar.trade_date == trade_date)
        ).all()
        sectors_by_symbol = {row.symbol: row for row in sector_rows}
        members = db.scalars(
            select(SectorMember).where(SectorMember.stock_symbol.in_(symbols))
        ).all()
        result: dict[str, list[dict[str, Any]]] = {}
        for member in members:
            sector = sectors_by_symbol.get(member.sector_symbol)
            if sector is None or sector.pct_chg is None:
                continue
            result.setdefault(member.stock_symbol, []).append(
                {
                    "symbol": member.sector_symbol,
                    "name": member.sector_name or sector.name or member.sector_symbol,
                    "pct_chg": round(float(sector.pct_chg or 0), 4),
                    "turnover_rate": round(float(sector.turnover_rate or 0), 4),
                }
            )
        for items in result.values():
            items.sort(key=lambda item: item["pct_chg"], reverse=True)
            del items[3:]
        return result

    def _daily_factors(self, bars: list[DailyBar]) -> dict[str, Any]:
        usable = [bar for bar in bars if bar.close is not None]
        if not usable:
            return self._empty_daily_factors()
        first_close = float(usable[0].close or 0)
        latest = usable[-1]
        latest_close = float(latest.close or 0)
        momentum_pct = (
            (latest_close - first_close) / first_close * 100
            if first_close > 0
            else 0.0
        )
        returns: list[float] = []
        for previous, current in zip(usable, usable[1:]):
            previous_close = float(previous.close or 0)
            current_close = float(current.close or 0)
            if previous_close > 0:
                returns.append((current_close - previous_close) / previous_close * 100)
        avg_amount = sum(_number(bar.amount) for bar in usable) / len(usable)
        volatility_pct = self._stddev(returns)
        return {
            "latest_trade_date": latest.trade_date,
            "bars_used": len(usable),
            "latest_close": round(latest_close, 4),
            "momentum_pct": round(momentum_pct, 4),
            "avg_amount": round(avg_amount, 4),
            "volatility_pct": round(volatility_pct, 4),
            "turnover_rate": _number(latest.turnover_rate),
            "volume_ratio": _number(latest.volume_ratio),
            "pe_ttm": _number(latest.pe_ttm),
            "pb": _number(latest.pb),
            "total_mv": _number(latest.total_mv),
            "circ_mv": _number(latest.circ_mv),
            "moneyflow_net_amount": _number(latest.moneyflow_net_amount),
            "moneyflow_net_d5_amount": _number(latest.moneyflow_net_d5_amount),
            "moneyflow_buy_lg_amount": _number(latest.moneyflow_buy_lg_amount),
            "moneyflow_buy_lg_amount_rate": _number(
                latest.moneyflow_buy_lg_amount_rate
            ),
            "moneyflow_buy_md_amount": _number(latest.moneyflow_buy_md_amount),
            "moneyflow_buy_md_amount_rate": _number(
                latest.moneyflow_buy_md_amount_rate
            ),
            "moneyflow_buy_sm_amount": _number(latest.moneyflow_buy_sm_amount),
            "moneyflow_buy_sm_amount_rate": _number(
                latest.moneyflow_buy_sm_amount_rate
            ),
            "sector_heat": [],
            "limit_event": None,
        }

    def _empty_daily_factors(self) -> dict[str, Any]:
        return {
            "latest_trade_date": None,
            "bars_used": 0,
            "latest_close": None,
            "momentum_pct": 0.0,
            "avg_amount": 0.0,
            "volatility_pct": 0.0,
            "turnover_rate": 0.0,
            "volume_ratio": 0.0,
            "pe_ttm": 0.0,
            "pb": 0.0,
            "total_mv": 0.0,
            "circ_mv": 0.0,
            "moneyflow_net_amount": 0.0,
            "moneyflow_net_d5_amount": 0.0,
            "moneyflow_buy_lg_amount": 0.0,
            "moneyflow_buy_lg_amount_rate": 0.0,
            "moneyflow_buy_md_amount": 0.0,
            "moneyflow_buy_md_amount_rate": 0.0,
            "moneyflow_buy_sm_amount": 0.0,
            "moneyflow_buy_sm_amount_rate": 0.0,
            "sector_heat": [],
            "limit_event": None,
        }

    def _stddev(self, values: list[float]) -> float:
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        return math.sqrt(variance)

    def _rationale(self, change_pct: float, amount: float, volume_ratio: float) -> str:
        parts: list[str] = []
        if change_pct > 0:
            parts.append(f"涨幅 {change_pct:.2f}%")
        if amount > 0:
            parts.append(f"成交额约 {amount / 100000000:.1f} 亿")
        if volume_ratio > 1:
            parts.append(f"量比 {volume_ratio:.2f}")
        return "，".join(parts) or "基础行情完整，等待更多确认信号。"


quant_service = QuantService()
