from __future__ import annotations

import math
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BlockTrade,
    DailyBar,
    DragonTigerInstitution,
    DragonTigerList,
    FinancialIndicator,
    LimitEvent,
    MarginDetail,
    PledgeStat,
    SectorBar,
    SectorMember,
    ShareholderNumber,
    ShareholderTrade,
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
    _AUTO_REALTIME_UNIVERSE_LIMIT = 120
    _AUTO_REALTIME_REFINE_MAX = 80

    def generate_candidates(
        self,
        *,
        db: Session | None = None,
        symbols: list[str] | None = None,
        limit: int = 20,
        prefer_realtime: bool = True,
        lookback_days: int = 20,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        universe = self._resolve_universe(db, symbols, limit=limit, end_date=end_date)
        daily_factors = self._daily_factors_by_symbol(
            db,
            symbols=universe,
            lookback_days=lookback_days,
            end_date=end_date,
        )
        profiles = self._profiles_by_symbol(db, universe)
        financials = self._financial_indicators_by_symbol(db, universe)
        quotes = self._candidate_quotes(
            db,
            universe=universe,
            prefer_realtime=prefer_realtime,
            end_date=end_date,
            daily_factors=daily_factors,
            profiles=profiles,
            financials=financials,
            limit=limit,
        )
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
        data_source_names: set[str] = set()
        for item in selected:
            if item.get("source"):
                data_source_names.add(str(item["source"]))
            data_source_names.update(
                str(source)
                for source in item.get("source_candidates", [])
                if source
            )
        data_sources = sorted(data_source_names)
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
        if any(item.get("daily_factors", {}).get("margin_detail") for item in selected):
            data_sources.append("tushare_margin_detail")
        if any(item.get("daily_factors", {}).get("dragon_tiger") for item in selected):
            data_sources.extend(["tushare_top_list", "tushare_top_inst"])
        if any(item.get("daily_factors", {}).get("block_trade") for item in selected):
            data_sources.append("tushare_block_trade")
        if any(item.get("daily_factors", {}).get("shareholder_number") for item in selected):
            data_sources.append("tushare_stk_holdernumber")
        if any(item.get("daily_factors", {}).get("shareholder_trade") for item in selected):
            data_sources.append("tushare_stk_holdertrade")
        if any(item.get("daily_factors", {}).get("pledge_stat") for item in selected):
            data_sources.append("tushare_pledge_stat")
        return {
            "universe_size": len(universe),
            "candidate_count": len(selected),
            "data_sources": data_sources,
            "candidates": selected,
        }

    def _candidate_quotes(
        self,
        db: Session | None,
        *,
        universe: list[str],
        prefer_realtime: bool,
        end_date: str | None,
        daily_factors: dict[str, Any],
        profiles: dict[str, Any],
        financials: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        if (
            prefer_realtime
            and db is not None
            and len(universe) > self._AUTO_REALTIME_UNIVERSE_LIMIT
        ):
            daily_quotes = self._daily_quotes_by_symbol(db, universe, end_date=end_date)
            if daily_quotes:
                seed_candidates = [
                    self._score_quote(
                        item,
                        daily_factors.get(normalize_symbol(str(item.get("symbol") or ""))),
                        profiles.get(normalize_symbol(str(item.get("symbol") or ""))),
                        financials.get(normalize_symbol(str(item.get("symbol") or ""))),
                    )
                    for item in daily_quotes
                ]
                seed_candidates.sort(key=lambda item: item["score"], reverse=True)
                realtime_limit = min(
                    self._AUTO_REALTIME_REFINE_MAX,
                    max(20, max(1, int(limit or 20)) * 8),
                )
                realtime_symbols = [
                    str(item["symbol"])
                    for item in seed_candidates[:realtime_limit]
                    if item.get("symbol")
                ]
                realtime_quotes = market_data_service.get_quotes(
                    realtime_symbols,
                    prefer_realtime=True,
                ) if realtime_symbols else []
                realtime_by_symbol = {
                    normalize_symbol(str(item.get("symbol") or "")): item
                    for item in realtime_quotes
                    if item.get("symbol")
                }
                daily_by_symbol = {
                    normalize_symbol(str(item.get("symbol") or "")): item
                    for item in daily_quotes
                    if item.get("symbol")
                }
                return [
                    realtime_by_symbol.get(symbol) or daily_by_symbol[symbol]
                    for symbol in realtime_symbols
                    if symbol in daily_by_symbol
                ]

        quotes = (
            self._daily_quotes_by_symbol(db, universe, end_date=end_date)
            if not prefer_realtime and db is not None
            else []
        )
        if not quotes:
            quotes = market_data_service.get_quotes(universe, prefer_realtime=prefer_realtime)
        return quotes

    def _resolve_universe(
        self,
        db: Session | None,
        symbols: list[str] | None,
        *,
        limit: int,
        end_date: str | None = None,
    ) -> list[str]:
        if symbols:
            return [normalize_symbol(symbol) for symbol in symbols]
        stored = self._stored_universe(
            db,
            limit=max(limit, self._AUTO_UNIVERSE_LIMIT),
            end_date=end_date,
        )
        return stored or [normalize_symbol(symbol) for symbol in DEFAULT_UNIVERSE]

    def _stored_universe(
        self,
        db: Session | None,
        *,
        limit: int,
        end_date: str | None = None,
    ) -> list[str]:
        if db is None:
            return []
        trade_date_query = select(DailyBar.trade_date).group_by(DailyBar.trade_date)
        if end_date:
            trade_date_query = trade_date_query.where(DailyBar.trade_date <= end_date)
        trade_date = db.scalar(
            trade_date_query
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
        end_date: str | None = None,
    ) -> dict[str, Any]:
        payload = self.generate_candidates(
            db=db,
            symbols=symbols,
            limit=limit,
            prefer_realtime=prefer_realtime,
            lookback_days=lookback_days,
            end_date=end_date,
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
            "margin_detail_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("margin_detail")
            ),
            "dragon_tiger_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("dragon_tiger")
            ),
            "block_trade_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("block_trade")
            ),
            "shareholder_number_symbols": sum(
                1
                for item in items
                if item.get("daily_factors", {}).get("shareholder_number")
            ),
            "shareholder_trade_symbols": sum(
                1
                for item in items
                if item.get("daily_factors", {}).get("shareholder_trade")
            ),
            "pledge_stat_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("pledge_stat")
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

    def _daily_quotes_by_symbol(
        self,
        db: Session,
        symbols: list[str],
        *,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        if not symbols:
            return []
        latest_dates = (
            select(
                DailyBar.symbol.label("symbol"),
                func.max(DailyBar.trade_date).label("trade_date"),
            )
            .where(DailyBar.symbol.in_(symbols), DailyBar.close.is_not(None))
            .group_by(DailyBar.symbol)
        )
        if end_date:
            latest_dates = latest_dates.where(DailyBar.trade_date <= end_date)
        latest_dates_subquery = latest_dates.subquery()
        rows = db.scalars(
            select(DailyBar)
            .join(
                latest_dates_subquery,
                and_(
                    DailyBar.symbol == latest_dates_subquery.c.symbol,
                    DailyBar.trade_date == latest_dates_subquery.c.trade_date,
                ),
            )
            .order_by(DailyBar.symbol)
        ).all()
        rows_by_symbol = {row.symbol: row for row in rows}
        quotes: list[dict[str, Any]] = []
        for symbol in symbols:
            row = rows_by_symbol.get(symbol)
            if row is None or row.close is None:
                continue
            change_pct = row.pct_chg
            if change_pct is None and row.pre_close not in (None, 0):
                change_pct = (
                    (float(row.close) - float(row.pre_close))
                    / float(row.pre_close)
                    * 100
                )
            quotes.append(
                {
                    "symbol": row.symbol,
                    "name": row.symbol,
                    "price": float(row.close),
                    "change_pct": _number(change_pct),
                    "amount": _number(row.amount),
                    "turnover": _number(row.turnover_rate),
                    "volume_ratio": _number(row.volume_ratio, 1.0) or 1.0,
                    "source": "tushare_daily_snapshot",
                    "timestamp": row.trade_date,
                    "trade_date": row.trade_date,
                    "is_realtime": False,
                }
            )
        return quotes

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
            "margin_financing": self._margin_financing_score(daily),
            "dragon_tiger_flow": self._dragon_tiger_flow_score(daily),
            "block_trade_flow": self._block_trade_flow_score(daily),
            "shareholder_structure": self._shareholder_structure_score(daily),
            "shareholder_trade": self._shareholder_trade_score(daily),
            "pledge_risk": self._pledge_risk_score(daily),
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
            "source_candidates": list(quote.get("source_candidates") or []),
            "source_snapshot": list(quote.get("source_snapshot") or []),
            "source_agreement": dict(quote.get("source_agreement") or {}),
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
                + factor_scores["margin_financing"] * 3
                + factor_scores["dragon_tiger_flow"] * 4
                + factor_scores["block_trade_flow"] * 3
                + factor_scores["shareholder_structure"] * 3
                + factor_scores["shareholder_trade"] * 3
                + factor_scores["pledge_risk"] * 3
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

    def _margin_financing_score(self, daily: dict[str, Any]) -> float:
        margin = daily.get("margin_detail") or {}
        if not isinstance(margin, dict):
            return 0.0
        net_buy = _number(margin.get("net_financing_buy"))
        balance = _number(margin.get("rzrqye"))
        score = max(min(net_buy / 100000000.0, 1.0), -1.0)
        if balance > 0:
            score += min(math.log10(balance + 1) / 20.0, 0.5)
        return max(min(score, 1.0), -1.0)

    def _dragon_tiger_flow_score(self, daily: dict[str, Any]) -> float:
        item = daily.get("dragon_tiger") or {}
        if not isinstance(item, dict):
            return 0.0
        net_amount = _number(item.get("net_amount"))
        inst_net_buy = _number(item.get("institution_net_buy"))
        return max(min((net_amount + inst_net_buy) / 200000000.0, 1.0), -1.0)

    def _block_trade_flow_score(self, daily: dict[str, Any]) -> float:
        item = daily.get("block_trade") or {}
        if not isinstance(item, dict):
            return 0.0
        amount_score = min(
            math.log10(max(_number(item.get("total_amount")), 0.0) + 1) / 6.0,
            1.0,
        )
        premium_score = max(min(_number(item.get("price_vs_close_pct")) / 10.0, 0.35), -0.35)
        return max(min(amount_score + premium_score, 1.0), -1.0)

    def _shareholder_structure_score(self, daily: dict[str, Any]) -> float:
        item = daily.get("shareholder_number") or {}
        if not isinstance(item, dict):
            return 0.0
        change_pct = _number(item.get("holder_num_change_pct"))
        return max(min(-change_pct / 20.0, 1.0), -1.0)

    def _shareholder_trade_score(self, daily: dict[str, Any]) -> float:
        item = daily.get("shareholder_trade") or {}
        if not isinstance(item, dict):
            return 0.0
        return max(min(_number(item.get("net_change_ratio")) / 1.0, 1.0), -1.0)

    def _pledge_risk_score(self, daily: dict[str, Any]) -> float:
        item = daily.get("pledge_stat") or {}
        if not isinstance(item, dict):
            return 0.0
        return -max(min(_number(item.get("pledge_ratio")) / 50.0, 1.0), 0.0)

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
        end_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        if db is None:
            return {}
        query = select(DailyBar).where(DailyBar.symbol.in_(symbols))
        if end_date:
            query = query.where(DailyBar.trade_date <= end_date)
        rows = db.scalars(
            query.order_by(DailyBar.symbol, DailyBar.trade_date)
        ).all()
        by_symbol: dict[str, list[DailyBar]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)
        factors = {
            symbol: self._daily_factors(bars[-max(1, lookback_days):])
            for symbol, bars in by_symbol.items()
        }
        sector_heat = self._sector_heat_by_symbol(db, symbols, end_date=end_date)
        for symbol, sectors in sector_heat.items():
            factors.setdefault(symbol, self._empty_daily_factors())["sector_heat"] = sectors
        limit_events = self._limit_events_by_symbol(db, symbols, end_date=end_date)
        for symbol, event in limit_events.items():
            factors.setdefault(symbol, self._empty_daily_factors())["limit_event"] = event
        margin_details = self._margin_details_by_symbol(db, symbols, end_date=end_date)
        for symbol, detail in margin_details.items():
            factors.setdefault(symbol, self._empty_daily_factors())["margin_detail"] = detail
        dragon_tiger = self._dragon_tiger_by_symbol(db, symbols, end_date=end_date)
        for symbol, item in dragon_tiger.items():
            factors.setdefault(symbol, self._empty_daily_factors())["dragon_tiger"] = item
        block_trades = self._block_trades_by_symbol(db, symbols, end_date=end_date)
        for symbol, item in block_trades.items():
            factors.setdefault(symbol, self._empty_daily_factors())["block_trade"] = item
        shareholder_numbers = self._shareholder_numbers_by_symbol(db, symbols)
        for symbol, item in shareholder_numbers.items():
            factors.setdefault(symbol, self._empty_daily_factors())["shareholder_number"] = item
        shareholder_trades = self._shareholder_trades_by_symbol(db, symbols)
        for symbol, item in shareholder_trades.items():
            factors.setdefault(symbol, self._empty_daily_factors())["shareholder_trade"] = item
        pledge_stats = self._pledge_stats_by_symbol(db, symbols)
        for symbol, item in pledge_stats.items():
            factors.setdefault(symbol, self._empty_daily_factors())["pledge_stat"] = item
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
        end_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        query = select(LimitEvent).where(LimitEvent.symbol.in_(symbols))
        if end_date:
            query = query.where(LimitEvent.trade_date <= end_date)
        rows = db.scalars(
            query.order_by(
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

    def _margin_details_by_symbol(
        self,
        db: Session,
        symbols: list[str],
        end_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        query = select(MarginDetail).where(MarginDetail.symbol.in_(symbols))
        if end_date:
            query = query.where(MarginDetail.trade_date <= end_date)
        rows = db.scalars(
            query.order_by(MarginDetail.symbol, desc(MarginDetail.trade_date))
        ).all()
        latest: dict[str, MarginDetail] = {}
        for row in rows:
            latest.setdefault(row.symbol, row)
        return {
            symbol: {
                "trade_date": row.trade_date,
                "name": row.name,
                "rzye": _number(row.rzye),
                "rqye": _number(row.rqye),
                "rzmre": _number(row.rzmre),
                "rqyl": _number(row.rqyl),
                "rzche": _number(row.rzche),
                "rqchl": _number(row.rqchl),
                "rqmcl": _number(row.rqmcl),
                "rzrqye": _number(row.rzrqye),
                "net_financing_buy": _number(row.rzmre) - _number(row.rzche),
            }
            for symbol, row in latest.items()
        }

    def _dragon_tiger_by_symbol(
        self,
        db: Session,
        symbols: list[str],
        end_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        query = select(DragonTigerList).where(DragonTigerList.symbol.in_(symbols))
        if end_date:
            query = query.where(DragonTigerList.trade_date <= end_date)
        rows = db.scalars(
            query.order_by(
                DragonTigerList.symbol,
                desc(DragonTigerList.trade_date),
                desc(DragonTigerList.net_amount),
            )
        ).all()
        latest: dict[str, DragonTigerList] = {}
        for row in rows:
            latest.setdefault(row.symbol, row)
        if not latest:
            return {}
        dates_by_symbol = {symbol: row.trade_date for symbol, row in latest.items()}
        inst_rows = db.scalars(
            select(DragonTigerInstitution).where(
                DragonTigerInstitution.symbol.in_(list(dates_by_symbol))
            )
        ).all()
        inst_summary: dict[str, dict[str, Any]] = {}
        for row in inst_rows:
            if row.trade_date != dates_by_symbol.get(row.symbol):
                continue
            summary = inst_summary.setdefault(
                row.symbol,
                {"institution_net_buy": 0.0, "institution_count": 0},
            )
            summary["institution_net_buy"] += _number(row.net_buy)
            summary["institution_count"] += 1
        return {
            symbol: {
                "trade_date": row.trade_date,
                "reason": row.reason,
                "net_amount": _number(row.net_amount),
                "l_buy": _number(row.l_buy),
                "l_sell": _number(row.l_sell),
                "net_rate": _number(row.net_rate),
                "amount_rate": _number(row.amount_rate),
                **inst_summary.get(
                    symbol,
                    {"institution_net_buy": 0.0, "institution_count": 0},
                ),
            }
            for symbol, row in latest.items()
        }

    def _block_trades_by_symbol(
        self,
        db: Session,
        symbols: list[str],
        end_date: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        query = select(BlockTrade).where(BlockTrade.symbol.in_(symbols))
        if end_date:
            query = query.where(BlockTrade.trade_date <= end_date)
        rows = db.scalars(
            query.order_by(BlockTrade.symbol, desc(BlockTrade.trade_date))
        ).all()
        latest_dates: dict[str, str] = {}
        for row in rows:
            latest_dates.setdefault(row.symbol, row.trade_date)
        grouped: dict[str, list[BlockTrade]] = {}
        for row in rows:
            if row.trade_date == latest_dates.get(row.symbol):
                grouped.setdefault(row.symbol, []).append(row)
        result: dict[str, dict[str, Any]] = {}
        for symbol, items in grouped.items():
            total_amount = sum(_number(item.amount) for item in items)
            total_vol = sum(_number(item.vol) for item in items)
            weighted_price = 0.0
            if total_vol > 0:
                weighted_price = (
                    sum(_number(item.price) * _number(item.vol) for item in items) / total_vol
                )
            close = db.scalar(
                select(DailyBar.close).where(
                    DailyBar.symbol == symbol,
                    DailyBar.trade_date == latest_dates[symbol],
                )
            )
            price_vs_close_pct = 0.0
            if close and weighted_price > 0:
                price_vs_close_pct = (
                    (weighted_price - _number(close)) / _number(close) * 100.0
                )
            result[symbol] = {
                "trade_date": latest_dates[symbol],
                "trade_count": len(items),
                "total_amount": total_amount,
                "total_vol": total_vol,
                "avg_price": weighted_price,
                "price_vs_close_pct": price_vs_close_pct,
                "top_buyer": items[0].buyer,
                "top_seller": items[0].seller,
            }
        return result

    def _shareholder_numbers_by_symbol(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        rows = db.scalars(
            select(ShareholderNumber)
            .where(ShareholderNumber.symbol.in_(symbols))
            .order_by(
                ShareholderNumber.symbol,
                desc(ShareholderNumber.end_date),
                desc(ShareholderNumber.ann_date),
            )
        ).all()
        grouped: dict[str, list[ShareholderNumber]] = {}
        for row in rows:
            grouped.setdefault(row.symbol, []).append(row)
        result: dict[str, dict[str, Any]] = {}
        for symbol, items in grouped.items():
            latest = items[0]
            previous = items[1] if len(items) > 1 else None
            holder_num = int(latest.holder_num or 0)
            previous_holder_num = int(previous.holder_num or 0) if previous else 0
            change_pct = 0.0
            if previous_holder_num > 0:
                change_pct = (holder_num - previous_holder_num) / previous_holder_num * 100.0
            result[symbol] = {
                "ann_date": latest.ann_date,
                "end_date": latest.end_date,
                "holder_num": holder_num,
                "previous_holder_num": previous_holder_num,
                "holder_num_change_pct": change_pct,
            }
        return result

    def _shareholder_trades_by_symbol(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        rows = db.scalars(
            select(ShareholderTrade)
            .where(ShareholderTrade.symbol.in_(symbols))
            .order_by(ShareholderTrade.symbol, desc(ShareholderTrade.ann_date))
        ).all()
        latest_dates: dict[str, str] = {}
        for row in rows:
            latest_dates.setdefault(row.symbol, row.ann_date)
        grouped: dict[str, list[ShareholderTrade]] = {}
        for row in rows:
            if row.ann_date == latest_dates.get(row.symbol):
                grouped.setdefault(row.symbol, []).append(row)
        result: dict[str, dict[str, Any]] = {}
        for symbol, items in grouped.items():
            net_change_vol = 0.0
            net_change_ratio = 0.0
            increase_count = 0
            decrease_count = 0
            for item in items:
                sign = 1.0 if str(item.in_de or "").upper() == "IN" else -1.0
                if sign > 0:
                    increase_count += 1
                else:
                    decrease_count += 1
                net_change_vol += sign * _number(item.change_vol)
                net_change_ratio += sign * _number(item.change_ratio)
            result[symbol] = {
                "ann_date": latest_dates[symbol],
                "trade_count": len(items),
                "increase_count": increase_count,
                "decrease_count": decrease_count,
                "net_change_vol": net_change_vol,
                "net_change_ratio": net_change_ratio,
                "top_holder": items[0].holder_name,
            }
        return result

    def _pledge_stats_by_symbol(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, dict[str, Any]]:
        rows = db.scalars(
            select(PledgeStat)
            .where(PledgeStat.symbol.in_(symbols))
            .order_by(PledgeStat.symbol, desc(PledgeStat.end_date))
        ).all()
        latest: dict[str, PledgeStat] = {}
        for row in rows:
            latest.setdefault(row.symbol, row)
        return {
            symbol: {
                "end_date": row.end_date,
                "pledge_count": int(row.pledge_count or 0),
                "unrest_pledge": _number(row.unrest_pledge),
                "rest_pledge": _number(row.rest_pledge),
                "total_share": _number(row.total_share),
                "pledge_ratio": _number(row.pledge_ratio),
            }
            for symbol, row in latest.items()
        }

    def _sector_heat_by_symbol(
        self,
        db: Session,
        symbols: list[str],
        end_date: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        trade_date_query = select(SectorBar.trade_date)
        if end_date:
            trade_date_query = trade_date_query.where(SectorBar.trade_date <= end_date)
        trade_date = db.scalar(trade_date_query.order_by(desc(SectorBar.trade_date)).limit(1))
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
        closes = [float(bar.close or 0) for bar in usable if float(bar.close or 0) > 0]
        ma20 = self._mean(closes[-20:])
        ma60 = self._mean(closes[-60:])
        high_60 = max(closes[-60:]) if closes else 0.0
        low_60 = min(closes[-60:]) if closes else 0.0
        momentum_pct = (
            (latest_close - first_close) / first_close * 100
            if first_close > 0
            else 0.0
        )
        recent_base = closes[-6] if len(closes) >= 6 else first_close
        recent_momentum_pct = (
            (latest_close - recent_base) / recent_base * 100
            if recent_base > 0
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
        max_drawdown_pct = self._max_drawdown_pct(closes)
        range_position_pct = (
            (latest_close - low_60) / (high_60 - low_60) * 100
            if high_60 > low_60
            else 50.0
        )
        return {
            "latest_trade_date": latest.trade_date,
            "bars_used": len(usable),
            "latest_close": round(latest_close, 4),
            "momentum_pct": round(momentum_pct, 4),
            "recent_momentum_pct": round(recent_momentum_pct, 4),
            "ma20": round(ma20, 4) if ma20 else None,
            "ma60": round(ma60, 4) if ma60 else None,
            "above_ma20": bool(ma20 and latest_close >= ma20),
            "above_ma60": bool(ma60 and latest_close >= ma60),
            "distance_to_ma20_pct": round((latest_close - ma20) / ma20 * 100, 4) if ma20 else 0.0,
            "distance_to_ma60_pct": round((latest_close - ma60) / ma60 * 100, 4) if ma60 else 0.0,
            "high_60": round(high_60, 4) if high_60 else None,
            "low_60": round(low_60, 4) if low_60 else None,
            "range_position_pct": round(range_position_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
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
            "margin_detail": None,
            "dragon_tiger": None,
            "block_trade": None,
            "shareholder_number": None,
            "shareholder_trade": None,
            "pledge_stat": None,
        }

    def _empty_daily_factors(self) -> dict[str, Any]:
        return {
            "latest_trade_date": None,
            "bars_used": 0,
            "latest_close": None,
            "momentum_pct": 0.0,
            "recent_momentum_pct": 0.0,
            "ma20": None,
            "ma60": None,
            "above_ma20": False,
            "above_ma60": False,
            "distance_to_ma20_pct": 0.0,
            "distance_to_ma60_pct": 0.0,
            "high_60": None,
            "low_60": None,
            "range_position_pct": 50.0,
            "max_drawdown_pct": 0.0,
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
            "margin_detail": None,
            "dragon_tiger": None,
            "block_trade": None,
            "shareholder_number": None,
            "shareholder_trade": None,
            "pledge_stat": None,
        }

    def _stddev(self, values: list[float]) -> float:
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        return math.sqrt(variance)

    def _mean(self, values: list[float]) -> float:
        clean = [value for value in values if value > 0]
        return sum(clean) / len(clean) if clean else 0.0

    def _max_drawdown_pct(self, closes: list[float]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for close in closes:
            if close <= 0:
                continue
            peak = max(peak, close)
            if peak > 0:
                max_drawdown = min(max_drawdown, (close - peak) / peak * 100)
        return max_drawdown

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
