from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import subprocess
import time
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    BacktestRun,
    BlockTrade,
    DailyBar,
    DragonTigerInstitution,
    DragonTigerList,
    FinancialIndicator,
    IndexBar,
    LimitEvent,
    MarginDetail,
    PledgeStat,
    SectorBar,
    SectorMember,
    ShareholderNumber,
    ShareholderTrade,
    StockProfile,
)
from app.services.market_data_service import normalize_symbol

_TUSHARE_DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
)
_TUSHARE_DAILY_BASIC_FIELDS = (
    "ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb,total_mv,circ_mv"
)
_TUSHARE_MONEYFLOW_THS_FIELDS = (
    "ts_code,trade_date,net_amount,net_d5_amount,buy_lg_amount,buy_lg_amount_rate,"
    "buy_md_amount,buy_md_amount_rate,buy_sm_amount,buy_sm_amount_rate"
)
_TUSHARE_INDEX_DAILY_FIELDS = "ts_code,trade_date,close,pct_chg,amount"
_TUSHARE_THS_INDEX_FIELDS = "ts_code,name,type"
_TUSHARE_THS_DAILY_FIELDS = (
    "ts_code,trade_date,close,pct_change,turnover_rate,total_mv,float_mv"
)
_TUSHARE_THS_MEMBER_FIELDS = "ts_code,con_code,con_name,is_new"
_TUSHARE_STOCK_BASIC_FIELDS = (
    "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,is_hs"
)
_TUSHARE_FINA_INDICATOR_FIELDS = (
    "ts_code,ann_date,end_date,roe,roe_dt,grossprofit_margin,netprofit_margin,"
    "netprofit_yoy,or_yoy,debt_to_assets,assets_turn,current_ratio"
)
_TUSHARE_LIMIT_LIST_D_FIELDS = (
    "trade_date,ts_code,industry,name,close,pct_chg,amount,limit_amount,float_mv,"
    "total_mv,turnover_ratio,fd_amount,first_time,last_time,open_times,up_stat,"
    "limit_times,limit"
)
_TUSHARE_MARGIN_DETAIL_FIELDS = (
    "trade_date,ts_code,name,rzye,rqye,rzmre,rqyl,rzche,rqchl,rqmcl,rzrqye"
)
_TUSHARE_TOP_LIST_FIELDS = (
    "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,"
    "l_amount,net_amount,net_rate,amount_rate,float_values,reason"
)
_TUSHARE_TOP_INST_FIELDS = (
    "trade_date,ts_code,exalter,side,buy,buy_rate,sell,sell_rate,net_buy,reason"
)
_TUSHARE_BLOCK_TRADE_FIELDS = "ts_code,trade_date,price,vol,amount,buyer,seller"
_TUSHARE_HOLDER_NUMBER_FIELDS = "ts_code,ann_date,end_date,holder_num"
_TUSHARE_HOLDER_TRADE_FIELDS = (
    "ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,"
    "after_share,after_ratio,avg_price,total_share,begin_date,close_date"
)
_TUSHARE_PLEDGE_STAT_FIELDS = (
    "ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio"
)
_DEFAULT_INDEX_SYMBOLS = ("000001.SH", "399001.SZ", "399006.SZ", "000300.SH", "000905.SH")
_TUSHARE_THS_MEMBER_WORKERS = 2
_TUSHARE_THS_MEMBER_RETRIES = 3
_TUSHARE_THS_MEMBER_RETRY_DELAY_SECONDS = 2.0
_TUSHARE_PLEDGE_STAT_LOOKBACK_DAYS = 10
_TUSHARE_HTTP_TIMEOUT_SECONDS = 20
_TUSHARE_CURL_TIMEOUT_SECONDS = _TUSHARE_HTTP_TIMEOUT_SECONDS + 5
_MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES = 3
_MAX_DAILY_REFRESH_RANGE_DAYS = 1825


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalize_trade_date(value: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"交易日期格式不正确: {value}")
    return text


class HistoricalDataService:
    def summarize_daily_coverage(self, db: Session) -> dict[str, Any]:
        total_rows = int(db.scalar(select(func.count(DailyBar.id))) or 0)
        unique_symbols = int(db.scalar(select(func.count(func.distinct(DailyBar.symbol)))) or 0)
        first_trade_date = db.scalar(select(func.min(DailyBar.trade_date)))
        latest_trade_date = db.scalar(select(func.max(DailyBar.trade_date)))
        trade_day_count = int(
            db.scalar(select(func.count(func.distinct(DailyBar.trade_date)))) or 0
        )

        recent_rows = db.execute(
            select(
                DailyBar.trade_date,
                func.count(func.distinct(DailyBar.symbol)).label("symbol_count"),
                func.count(DailyBar.id).label("row_count"),
            )
            .group_by(DailyBar.trade_date)
            .order_by(desc(DailyBar.trade_date))
            .limit(10)
        ).all()
        recent_trade_dates = [
            {
                "trade_date": trade_date,
                "symbol_count": int(symbol_count or 0),
                "row_count": int(row_count or 0),
            }
            for trade_date, symbol_count, row_count in recent_rows
        ]

        most_complete_row = db.execute(
            select(
                DailyBar.trade_date,
                func.count(func.distinct(DailyBar.symbol)).label("symbol_count"),
            )
            .group_by(DailyBar.trade_date)
            .order_by(desc("symbol_count"), desc(DailyBar.trade_date))
            .limit(1)
        ).first()
        most_complete_trade_date = most_complete_row[0] if most_complete_row else None
        most_complete_trade_date_symbols = (
            int(most_complete_row[1] or 0) if most_complete_row else 0
        )

        latest_trade_date_symbols = 0
        if latest_trade_date:
            latest_trade_date_symbols = int(
                db.scalar(
                    select(func.count(func.distinct(DailyBar.symbol))).where(
                        DailyBar.trade_date == latest_trade_date
                    )
                )
                or 0
            )

        source_rows = db.execute(
            select(
                DailyBar.source,
                func.count(DailyBar.id).label("row_count"),
            )
            .group_by(DailyBar.source)
            .order_by(desc("row_count"), DailyBar.source)
        ).all()
        source_counts = [
            {"source": source or "unknown", "row_count": int(row_count or 0)}
            for source, row_count in source_rows
        ]

        latest_day_complete = (
            most_complete_trade_date_symbols > 0
            and latest_trade_date_symbols >= int(most_complete_trade_date_symbols * 0.8)
        )
        completeness_threshold = max(1, int(most_complete_trade_date_symbols * 0.8))
        latest_complete_trade_date = None
        if most_complete_trade_date_symbols > 0:
            latest_complete_trade_date = db.scalar(
                select(DailyBar.trade_date)
                .group_by(DailyBar.trade_date)
                .having(func.count(func.distinct(DailyBar.symbol)) >= completeness_threshold)
                .order_by(desc(DailyBar.trade_date))
                .limit(1)
            )
        refresh_suggestion = self._refresh_suggestion(
            latest_trade_date=latest_trade_date,
            latest_day_complete=latest_day_complete,
            latest_complete_trade_date=latest_complete_trade_date,
        )
        readiness = {
            "has_daily_history": total_rows > 0,
            "has_broad_universe": unique_symbols >= 500,
            "latest_day_complete": latest_day_complete,
            "backtest_ready": unique_symbols > 0 and trade_day_count >= 2,
        }

        return {
            "total_rows": total_rows,
            "unique_symbols": unique_symbols,
            "first_trade_date": first_trade_date,
            "latest_trade_date": latest_trade_date,
            "latest_trade_date_symbols": latest_trade_date_symbols,
            "most_complete_trade_date": most_complete_trade_date,
            "most_complete_trade_date_symbols": most_complete_trade_date_symbols,
            "recent_trade_dates": recent_trade_dates,
            "source_counts": source_counts,
            "refresh_suggestion": refresh_suggestion,
            "readiness": readiness,
        }

    def _refresh_suggestion(
        self,
        *,
        latest_trade_date: str | None,
        latest_day_complete: bool,
        latest_complete_trade_date: str | None,
    ) -> dict[str, Any]:
        if not latest_trade_date:
            return {
                "needed": False,
                "start_date": None,
                "end_date": None,
                "reason": "暂无日线库存",
            }
        if latest_day_complete:
            return {
                "needed": False,
                "start_date": None,
                "end_date": None,
                "reason": "最新交易日覆盖充足",
            }
        start_date = latest_trade_date
        if latest_complete_trade_date:
            start_date = (
                datetime.strptime(latest_complete_trade_date, "%Y%m%d").date()
                + timedelta(days=1)
            ).strftime("%Y%m%d")
        return {
            "needed": True,
            "start_date": start_date,
            "end_date": latest_trade_date,
            "reason": "最新交易日覆盖不足",
        }

    def fetch_daily_rows(
        self,
        trade_date: str,
        symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新日频数据。")

        normalized_date = _normalize_trade_date(trade_date)
        if symbols:
            rows: list[dict[str, Any]] = []
            for symbol in symbols:
                rows.extend(
                    self._request_tushare_daily(
                        {
                            "ts_code": normalize_symbol(symbol),
                            "start_date": normalized_date,
                            "end_date": normalized_date,
                        }
                    )
                )
            return rows
        return self._request_tushare_daily({"trade_date": normalized_date})

    def fetch_daily_basic_rows(
        self,
        trade_date: str,
        symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare daily_basic 数据。")

        normalized_date = _normalize_trade_date(trade_date)
        if symbols:
            rows: list[dict[str, Any]] = []
            for symbol in symbols:
                rows.extend(
                    self._request_tushare_daily_basic(
                        {
                            "ts_code": normalize_symbol(symbol),
                            "trade_date": normalized_date,
                        }
                    )
                )
            return rows
        return self._request_tushare_daily_basic({"trade_date": normalized_date})

    def fetch_moneyflow_rows(
        self,
        trade_date: str,
        symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare moneyflow_ths 数据。")

        normalized_date = _normalize_trade_date(trade_date)
        if symbols:
            rows: list[dict[str, Any]] = []
            for symbol in symbols:
                rows.extend(
                    self._request_tushare_moneyflow_ths(
                        {
                            "ts_code": normalize_symbol(symbol),
                            "trade_date": normalized_date,
                        }
                    )
                )
            return rows
        return self._request_tushare_moneyflow_ths({"trade_date": normalized_date})

    def fetch_index_daily_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare index_daily 数据。")

        normalized_date = _normalize_trade_date(trade_date)
        rows: list[dict[str, Any]] = []
        for symbol in _DEFAULT_INDEX_SYMBOLS:
            rows.extend(
                self._request_tushare_index_daily(
                    {
                        "ts_code": symbol,
                        "start_date": normalized_date,
                        "end_date": normalized_date,
                    }
                )
            )
        return rows

    def fetch_sector_index_rows(self) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare ths_index 数据。")
        return self._request_tushare_ths_index({"exchange": "A"})

    def fetch_sector_daily_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare ths_daily 数据。")
        return self._request_tushare_ths_daily(
            {"trade_date": _normalize_trade_date(trade_date)}
        )

    def fetch_sector_member_rows(self, sector_symbols: list[str]) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare ths_member 数据。")
        self._last_sector_member_error = None
        if len(sector_symbols) <= 1:
            rows: list[dict[str, Any]] = []
            for symbol in sector_symbols:
                rows.extend(self._request_tushare_ths_member({"ts_code": symbol}))
            return rows

        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        worker_count = min(_TUSHARE_THS_MEMBER_WORKERS, len(sector_symbols))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(self._request_tushare_ths_member, {"ts_code": symbol})
                for symbol in sector_symbols
            ]
            for future in as_completed(futures):
                try:
                    rows.extend(future.result())
                except RuntimeError as exc:
                    errors.append(str(exc))
        if errors:
            self._last_sector_member_error = (
                f"{len(errors)} 个板块成分拉取失败，已保留其余成功数据；"
                f"首个错误: {errors[0]}"
            )
            if not rows:
                raise RuntimeError(self._last_sector_member_error)
        return rows

    def fetch_stock_basic_rows(self) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare stock_basic 数据。")
        return self._request_tushare_stock_basic({"list_status": "L"})

    def fetch_limit_list_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare limit_list_d 数据。")
        return self._request_tushare_limit_list_d(
            {"trade_date": _normalize_trade_date(trade_date)}
        )

    def fetch_margin_detail_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare margin_detail 数据。")
        return self._request_tushare_margin_detail(
            {"trade_date": _normalize_trade_date(trade_date)}
        )

    def fetch_top_list_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare top_list 数据。")
        return self._request_tushare_top_list({"trade_date": _normalize_trade_date(trade_date)})

    def fetch_top_inst_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare top_inst 数据。")
        return self._request_tushare_top_inst({"trade_date": _normalize_trade_date(trade_date)})

    def fetch_block_trade_rows(self, trade_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare block_trade 数据。")
        return self._request_tushare_block_trade(
            {"trade_date": _normalize_trade_date(trade_date)}
        )

    def fetch_shareholder_number_rows(self, ann_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare stk_holdernumber 数据。")
        return self._request_tushare_shareholder_number(
            {"ann_date": _normalize_trade_date(ann_date)}
        )

    def fetch_shareholder_trade_rows(self, ann_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare stk_holdertrade 数据。")
        return self._request_tushare_shareholder_trade(
            {"ann_date": _normalize_trade_date(ann_date)}
        )

    def fetch_pledge_stat_rows(self, end_date: str) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare pledge_stat 数据。")
        normalized_date = _normalize_trade_date(end_date)
        base_date = datetime.strptime(normalized_date, "%Y%m%d")
        for offset in range(_TUSHARE_PLEDGE_STAT_LOOKBACK_DAYS + 1):
            probe_date = (base_date - timedelta(days=offset)).strftime("%Y%m%d")
            rows = self._request_tushare_pledge_stat({"end_date": probe_date})
            if rows:
                return rows
        return []

    def fetch_financial_indicator_rows(self, symbols: list[str]) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新 Tushare fina_indicator 数据。")
        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            rows.extend(
                self._request_tushare_fina_indicator({"ts_code": normalize_symbol(symbol)})
            )
        return rows

    def _request_tushare_daily(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="daily",
            params=params,
            fields=_TUSHARE_DAILY_FIELDS,
            label="日线",
        )

    def _request_tushare_daily_basic(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="daily_basic",
            params=params,
            fields=_TUSHARE_DAILY_BASIC_FIELDS,
            label="daily_basic",
        )

    def _request_tushare_moneyflow_ths(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="moneyflow_ths",
            params=params,
            fields=_TUSHARE_MONEYFLOW_THS_FIELDS,
            label="moneyflow_ths",
        )

    def _request_tushare_index_daily(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="index_daily",
            params=params,
            fields=_TUSHARE_INDEX_DAILY_FIELDS,
            label="index_daily",
        )

    def _request_tushare_ths_index(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="ths_index",
            params=params,
            fields=_TUSHARE_THS_INDEX_FIELDS,
            label="ths_index",
        )

    def _request_tushare_ths_daily(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="ths_daily",
            params=params,
            fields=_TUSHARE_THS_DAILY_FIELDS,
            label="ths_daily",
        )

    def _request_tushare_ths_member(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        for attempt in range(_TUSHARE_THS_MEMBER_RETRIES + 1):
            try:
                return self._request_tushare_api(
                    api_name="ths_member",
                    params=params,
                    fields=_TUSHARE_THS_MEMBER_FIELDS,
                    label="ths_member",
                )
            except RuntimeError as exc:
                message = str(exc)
                if "429" not in message or attempt >= _TUSHARE_THS_MEMBER_RETRIES:
                    raise
                time.sleep(_TUSHARE_THS_MEMBER_RETRY_DELAY_SECONDS * (attempt + 1))
        return []

    def _request_tushare_stock_basic(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="stock_basic",
            params=params,
            fields=_TUSHARE_STOCK_BASIC_FIELDS,
            label="stock_basic",
        )

    def _request_tushare_fina_indicator(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="fina_indicator",
            params=params,
            fields=_TUSHARE_FINA_INDICATOR_FIELDS,
            label="fina_indicator",
        )

    def _request_tushare_limit_list_d(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="limit_list_d",
            params=params,
            fields=_TUSHARE_LIMIT_LIST_D_FIELDS,
            label="limit_list_d",
        )

    def _request_tushare_margin_detail(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="margin_detail",
            params=params,
            fields=_TUSHARE_MARGIN_DETAIL_FIELDS,
            label="margin_detail",
        )

    def _request_tushare_top_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="top_list",
            params=params,
            fields=_TUSHARE_TOP_LIST_FIELDS,
            label="top_list",
        )

    def _request_tushare_top_inst(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="top_inst",
            params=params,
            fields=_TUSHARE_TOP_INST_FIELDS,
            label="top_inst",
        )

    def _request_tushare_block_trade(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="block_trade",
            params=params,
            fields=_TUSHARE_BLOCK_TRADE_FIELDS,
            label="block_trade",
        )

    def _request_tushare_shareholder_number(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="stk_holdernumber",
            params=params,
            fields=_TUSHARE_HOLDER_NUMBER_FIELDS,
            label="stk_holdernumber",
        )

    def _request_tushare_shareholder_trade(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="stk_holdertrade",
            params=params,
            fields=_TUSHARE_HOLDER_TRADE_FIELDS,
            label="stk_holdertrade",
        )

    def _request_tushare_pledge_stat(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._request_tushare_api(
            api_name="pledge_stat",
            params=params,
            fields=_TUSHARE_PLEDGE_STAT_FIELDS,
            label="pledge_stat",
        )

    def _request_tushare_api(
        self,
        *,
        api_name: str,
        params: dict[str, Any],
        fields: str,
        label: str,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        url = settings.tushare_api_url or "http://api.tushare.pro"
        payload = {
            "api_name": api_name,
            "token": settings.tushare_token,
            "params": params,
            "fields": fields,
        }
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--max-time",
            str(_TUSHARE_HTTP_TIMEOUT_SECONDS),
            "--connect-timeout",
            "5",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_TUSHARE_CURL_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Tushare {label} 请求超时: {params}") from exc

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown error").strip()
            if completed.returncode == 28:
                raise RuntimeError(f"Tushare {label} 请求超时: {params}")
            raise RuntimeError(f"Tushare {label} 请求失败: {message}")

        try:
            body = json.loads(completed.stdout)
        except ValueError as exc:
            raise RuntimeError(f"Tushare {label} 响应不是合法 JSON。") from exc

        code = int(body.get("code") or 0)
        if code != 0:
            message = str(body.get("msg") or "unknown error")
            raise RuntimeError(f"Tushare {label} 接口错误: {message}")

        data = body.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields, list) or not isinstance(items, list):
            raise RuntimeError(f"Tushare {label} 响应结构不正确。")

        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, list):
                continue
            rows.append(dict(zip(fields, item, strict=False)))
        return rows

    def refresh_daily_bars(
        self,
        db: Session,
        *,
        trade_date: str,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        normalized_date = _normalize_trade_date(trade_date)
        rows = self.fetch_daily_rows(normalized_date, normalized_symbols)
        daily_basic_rows: list[dict[str, Any]] = []
        daily_basic_error = None
        try:
            daily_basic_rows = self.fetch_daily_basic_rows(normalized_date, normalized_symbols)
        except RuntimeError as exc:
            daily_basic_error = str(exc)
        daily_basic_by_symbol = {
            normalize_symbol(str(row.get("ts_code") or row.get("symbol") or "")): row
            for row in daily_basic_rows
            if _normalize_trade_date(str(row.get("trade_date") or normalized_date))
            == normalized_date
        }
        moneyflow_rows: list[dict[str, Any]] = []
        moneyflow_error = None
        try:
            moneyflow_rows = self.fetch_moneyflow_rows(normalized_date, normalized_symbols)
        except RuntimeError as exc:
            moneyflow_error = str(exc)
        moneyflow_by_symbol = {
            normalize_symbol(str(row.get("ts_code") or row.get("symbol") or "")): row
            for row in moneyflow_rows
            if _normalize_trade_date(str(row.get("trade_date") or normalized_date))
            == normalized_date
        }
        index_rows: list[dict[str, Any]] = []
        index_error = None
        try:
            index_rows = self.fetch_index_daily_rows(normalized_date)
        except RuntimeError as exc:
            index_error = str(exc)
        index_count = self._store_index_rows(db, normalized_date, index_rows)
        sector_error = None
        sector_count = 0
        sector_member_error = None
        sector_member_count = 0
        sector_daily_rows: list[dict[str, Any]] = []
        sector_index_rows: list[dict[str, Any]] = []
        try:
            sector_daily_rows = self.fetch_sector_daily_rows(normalized_date)
            sector_index_rows = self.fetch_sector_index_rows()
            sector_count = self._store_sector_rows(
                db,
                normalized_date,
                sector_daily_rows,
                sector_index_rows,
            )
        except RuntimeError as exc:
            sector_error = str(exc)
        if sector_daily_rows and sector_index_rows:
            try:
                hot_sector_symbols = self._hot_sector_symbols_for_member_refresh(
                    sector_daily_rows
                )
                sector_member_count = self._store_sector_member_rows(
                    db,
                    self.fetch_sector_member_rows(hot_sector_symbols),
                    sector_index_rows,
                )
                sector_member_error = getattr(self, "_last_sector_member_error", None)
            except RuntimeError as exc:
                sector_member_error = str(exc)

        limit_event_error = None
        limit_event_count = 0
        try:
            limit_event_count = self._store_limit_event_rows(
                db,
                normalized_date,
                self.fetch_limit_list_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            limit_event_error = str(exc)

        margin_detail_error = None
        margin_detail_count = 0
        try:
            margin_detail_count = self._store_margin_detail_rows(
                db,
                normalized_date,
                self.fetch_margin_detail_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            margin_detail_error = str(exc)

        dragon_tiger_error = None
        dragon_tiger_count = 0
        try:
            dragon_tiger_count = self._store_dragon_tiger_rows(
                db,
                normalized_date,
                self.fetch_top_list_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            dragon_tiger_error = str(exc)
        dragon_tiger_inst_error = None
        dragon_tiger_inst_count = 0
        try:
            dragon_tiger_inst_count = self._store_dragon_tiger_inst_rows(
                db,
                normalized_date,
                self.fetch_top_inst_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            dragon_tiger_inst_error = str(exc)

        block_trade_error = None
        block_trade_count = 0
        try:
            block_trade_count = self._store_block_trade_rows(
                db,
                normalized_date,
                self.fetch_block_trade_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            block_trade_error = str(exc)

        shareholder_number_error = None
        shareholder_number_count = 0
        try:
            shareholder_number_count = self._store_shareholder_number_rows(
                db,
                normalized_date,
                self.fetch_shareholder_number_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            shareholder_number_error = str(exc)

        shareholder_trade_error = None
        shareholder_trade_count = 0
        try:
            shareholder_trade_count = self._store_shareholder_trade_rows(
                db,
                normalized_date,
                self.fetch_shareholder_trade_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            shareholder_trade_error = str(exc)

        pledge_stat_error = None
        pledge_stat_count = 0
        try:
            pledge_stat_count = self._store_pledge_stat_rows(
                db,
                normalized_date,
                self.fetch_pledge_stat_rows(normalized_date),
                normalized_symbols,
            )
        except RuntimeError as exc:
            pledge_stat_error = str(exc)

        stored_count = 0
        skipped_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or normalized_date))
            if row_trade_date != normalized_date:
                skipped_count += 1
                continue
            existing = db.scalar(
                select(DailyBar).where(
                    DailyBar.symbol == symbol,
                    DailyBar.trade_date == row_trade_date,
                )
            )
            bar = existing or DailyBar(symbol=symbol, trade_date=row_trade_date)
            bar.open = _to_float(row.get("open"))
            bar.high = _to_float(row.get("high"))
            bar.low = _to_float(row.get("low"))
            bar.close = _to_float(row.get("close"))
            bar.pre_close = _to_float(row.get("pre_close"))
            bar.pct_chg = _to_float(row.get("pct_chg"))
            bar.vol = _to_float(row.get("vol"))
            bar.amount = _to_float(row.get("amount"))
            daily_basic = daily_basic_by_symbol.get(symbol) or {}
            if daily_basic:
                bar.turnover_rate = _to_float(daily_basic.get("turnover_rate"))
                bar.volume_ratio = _to_float(daily_basic.get("volume_ratio"))
                bar.pe_ttm = _to_float(daily_basic.get("pe_ttm"))
                bar.pb = _to_float(daily_basic.get("pb"))
                bar.total_mv = _to_float(daily_basic.get("total_mv"))
                bar.circ_mv = _to_float(daily_basic.get("circ_mv"))
            moneyflow = moneyflow_by_symbol.get(symbol) or {}
            if moneyflow:
                bar.moneyflow_net_amount = _to_float(moneyflow.get("net_amount"))
                bar.moneyflow_net_d5_amount = _to_float(moneyflow.get("net_d5_amount"))
                bar.moneyflow_buy_lg_amount = _to_float(moneyflow.get("buy_lg_amount"))
                bar.moneyflow_buy_lg_amount_rate = _to_float(
                    moneyflow.get("buy_lg_amount_rate")
                )
                bar.moneyflow_buy_md_amount = _to_float(moneyflow.get("buy_md_amount"))
                bar.moneyflow_buy_md_amount_rate = _to_float(
                    moneyflow.get("buy_md_amount_rate")
                )
                bar.moneyflow_buy_sm_amount = _to_float(moneyflow.get("buy_sm_amount"))
                bar.moneyflow_buy_sm_amount_rate = _to_float(
                    moneyflow.get("buy_sm_amount_rate")
                )
            bar.source = "tushare"
            bar.raw_payload = {
                **dict(row),
                **({"daily_basic": dict(daily_basic)} if daily_basic else {}),
                **({"moneyflow": dict(moneyflow)} if moneyflow else {}),
            }
            db.add(bar)
            stored_count += 1

        db.commit()
        return {
            "trade_date": normalized_date,
            "source": "tushare",
            "stored_count": stored_count,
            "skipped_count": skipped_count,
            "daily_basic_count": len(daily_basic_by_symbol),
            "daily_basic_error": daily_basic_error,
            "moneyflow_count": len(moneyflow_by_symbol),
            "moneyflow_error": moneyflow_error,
            "index_count": index_count,
            "index_error": index_error,
            "sector_count": sector_count,
            "sector_error": sector_error,
            "sector_member_count": sector_member_count,
            "sector_member_error": sector_member_error,
            "limit_event_count": limit_event_count,
            "limit_event_error": limit_event_error,
            "margin_detail_count": margin_detail_count,
            "margin_detail_error": margin_detail_error,
            "dragon_tiger_count": dragon_tiger_count,
            "dragon_tiger_error": dragon_tiger_error,
            "dragon_tiger_inst_count": dragon_tiger_inst_count,
            "dragon_tiger_inst_error": dragon_tiger_inst_error,
            "block_trade_count": block_trade_count,
            "block_trade_error": block_trade_error,
            "shareholder_number_count": shareholder_number_count,
            "shareholder_number_error": shareholder_number_error,
            "shareholder_trade_count": shareholder_trade_count,
            "shareholder_trade_error": shareholder_trade_error,
            "pledge_stat_count": pledge_stat_count,
            "pledge_stat_error": pledge_stat_error,
            "requested_symbols": normalized_symbols or [],
        }

    def refresh_stock_profiles(self, db: Session) -> dict[str, Any]:
        rows = self.fetch_stock_basic_rows()
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            existing = db.scalar(select(StockProfile).where(StockProfile.symbol == symbol))
            profile = existing or StockProfile(symbol=symbol)
            profile.name = str(row.get("name") or profile.name or symbol)
            profile.area = str(row.get("area") or "") or None
            profile.industry = str(row.get("industry") or "") or None
            profile.market = str(row.get("market") or "") or None
            profile.exchange = str(row.get("exchange") or "") or None
            profile.list_status = str(row.get("list_status") or "") or None
            profile.list_date = str(row.get("list_date") or "") or None
            profile.is_hs = str(row.get("is_hs") or "") or None
            profile.source = "tushare_stock_basic"
            profile.raw_payload = dict(row)
            db.add(profile)
            stored_count += 1
        db.commit()
        return {
            "source": "tushare_stock_basic",
            "stored_count": stored_count,
            "error": None,
        }

    def refresh_financial_indicators(
        self,
        db: Session,
        symbols: list[str],
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
        rows = self.fetch_financial_indicator_rows(normalized_symbols)
        rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            end_date = str(row.get("end_date") or "").strip().replace("-", "")
            if not symbol or len(end_date) != 8:
                continue
            key = (symbol, end_date)
            merged = rows_by_key.setdefault(key, {})
            for field, value in row.items():
                if value not in (None, ""):
                    merged[field] = value
        stored_count = 0
        for symbol, end_date in rows_by_key:
            row = rows_by_key[(symbol, end_date)]
            existing = db.scalar(
                select(FinancialIndicator).where(
                    FinancialIndicator.symbol == symbol,
                    FinancialIndicator.end_date == end_date,
                )
            )
            indicator = existing or FinancialIndicator(symbol=symbol, end_date=end_date)
            ann_date = str(row.get("ann_date") or "").strip().replace("-", "")
            indicator.ann_date = ann_date if len(ann_date) == 8 else None
            indicator.roe = _to_float(row.get("roe"))
            indicator.roe_dt = _to_float(row.get("roe_dt"))
            indicator.grossprofit_margin = _to_float(row.get("grossprofit_margin"))
            indicator.netprofit_margin = _to_float(row.get("netprofit_margin"))
            indicator.netprofit_yoy = _to_float(row.get("netprofit_yoy"))
            indicator.or_yoy = _to_float(row.get("or_yoy"))
            indicator.debt_to_assets = _to_float(row.get("debt_to_assets"))
            indicator.assets_turn = _to_float(row.get("assets_turn"))
            indicator.current_ratio = _to_float(row.get("current_ratio"))
            indicator.source = "tushare_fina_indicator"
            indicator.raw_payload = dict(row)
            db.add(indicator)
            stored_count += 1
        db.commit()
        return {
            "source": "tushare_fina_indicator",
            "stored_count": stored_count,
            "error": None,
            "requested_symbols": normalized_symbols,
        }

    def _store_index_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
    ) -> int:
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            existing = db.scalar(
                select(IndexBar).where(
                    IndexBar.symbol == symbol,
                    IndexBar.trade_date == row_trade_date,
                )
            )
            bar = existing or IndexBar(symbol=symbol, trade_date=row_trade_date)
            bar.close = _to_float(row.get("close"))
            bar.pct_chg = _to_float(row.get("pct_chg"))
            bar.amount = _to_float(row.get("amount"))
            bar.source = "tushare_index"
            bar.raw_payload = dict(row)
            db.add(bar)
            stored_count += 1
        return stored_count

    def _hot_sector_symbols_for_member_refresh(
        self,
        rows: list[dict[str, Any]],
    ) -> list[str]:
        ranked = sorted(
            rows,
            key=lambda row: _to_float(row.get("pct_change") or row.get("pct_chg")) or 0.0,
            reverse=True,
        )
        symbols: list[str] = []
        for row in ranked:
            pct_chg = _to_float(row.get("pct_change") or row.get("pct_chg")) or 0.0
            if pct_chg <= 0:
                continue
            symbol = str(row.get("ts_code") or row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            symbols.append(symbol)
        return symbols

    def _store_sector_member_rows(
        self,
        db: Session,
        rows: list[dict[str, Any]],
        index_rows: list[dict[str, Any]],
    ) -> int:
        metadata = {
            normalize_symbol(str(row.get("ts_code") or row.get("symbol") or "")): row
            for row in index_rows
        }
        stored_count = 0
        for row in rows:
            sector_symbol = normalize_symbol(str(row.get("ts_code") or ""))
            stock_symbol = normalize_symbol(str(row.get("con_code") or ""))
            if not self._is_a_share_symbol(stock_symbol):
                continue
            info = metadata.get(sector_symbol) or {}
            existing = db.scalar(
                select(SectorMember).where(
                    SectorMember.sector_symbol == sector_symbol,
                    SectorMember.stock_symbol == stock_symbol,
                )
            )
            member = existing or SectorMember(
                sector_symbol=sector_symbol,
                stock_symbol=stock_symbol,
            )
            member.sector_name = str(info.get("name") or row.get("name") or sector_symbol)
            member.sector_type = str(info.get("type") or row.get("type") or "") or None
            member.stock_name = str(row.get("con_name") or row.get("stock_name") or "")
            member.is_new = str(row.get("is_new") or "") or None
            member.source = "tushare_ths_member"
            member.raw_payload = {
                **dict(row),
                **({"index": dict(info)} if info else {}),
            }
            db.add(member)
            stored_count += 1
        return stored_count

    def _is_a_share_symbol(self, symbol: str) -> bool:
        return symbol.endswith((".SH", ".SZ", ".BJ"))

    def _store_sector_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
        index_rows: list[dict[str, Any]],
    ) -> int:
        metadata = {
            normalize_symbol(str(row.get("ts_code") or row.get("symbol") or "")): row
            for row in index_rows
        }
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            info = metadata.get(symbol) or {}
            existing = db.scalar(
                select(SectorBar).where(
                    SectorBar.symbol == symbol,
                    SectorBar.trade_date == row_trade_date,
                )
            )
            bar = existing or SectorBar(symbol=symbol, trade_date=row_trade_date)
            bar.name = str(info.get("name") or row.get("name") or symbol)
            bar.sector_type = str(info.get("type") or row.get("type") or "") or None
            bar.close = _to_float(row.get("close"))
            bar.pct_chg = _to_float(row.get("pct_change") or row.get("pct_chg"))
            bar.turnover_rate = _to_float(row.get("turnover_rate"))
            bar.total_mv = _to_float(row.get("total_mv"))
            bar.float_mv = _to_float(row.get("float_mv"))
            bar.source = "tushare_ths"
            bar.raw_payload = {
                **dict(row),
                **({"index": dict(info)} if info else {}),
            }
            db.add(bar)
            stored_count += 1
        return stored_count

    def _store_limit_event_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            limit_type = str(row.get("limit") or row.get("limit_type") or "").strip().upper()
            if not limit_type:
                continue
            existing = db.scalar(
                select(LimitEvent).where(
                    LimitEvent.symbol == symbol,
                    LimitEvent.trade_date == row_trade_date,
                    LimitEvent.limit_type == limit_type,
                )
            )
            event = existing or LimitEvent(
                symbol=symbol,
                trade_date=row_trade_date,
                limit_type=limit_type,
            )
            event.name = str(row.get("name") or event.name or symbol)
            event.industry = str(row.get("industry") or "") or None
            event.close = _to_float(row.get("close"))
            event.pct_chg = _to_float(row.get("pct_chg"))
            event.amount = _to_float(row.get("amount"))
            event.limit_amount = _to_float(row.get("limit_amount"))
            event.float_mv = _to_float(row.get("float_mv"))
            event.total_mv = _to_float(row.get("total_mv"))
            event.turnover_ratio = _to_float(row.get("turnover_ratio"))
            event.fd_amount = _to_float(row.get("fd_amount"))
            event.first_time = str(row.get("first_time") or "") or None
            event.last_time = str(row.get("last_time") or "") or None
            event.open_times = _to_int(row.get("open_times"))
            event.up_stat = str(row.get("up_stat") or "") or None
            event.limit_times = _to_int(row.get("limit_times"))
            event.source = "tushare_limit_list_d"
            event.raw_payload = dict(row)
            db.add(event)
            stored_count += 1
        return stored_count

    def _store_margin_detail_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            existing = db.scalar(
                select(MarginDetail).where(
                    MarginDetail.symbol == symbol,
                    MarginDetail.trade_date == row_trade_date,
                )
            )
            detail = existing or MarginDetail(symbol=symbol, trade_date=row_trade_date)
            detail.name = str(row.get("name") or detail.name or symbol)
            detail.rzye = _to_float(row.get("rzye"))
            detail.rqye = _to_float(row.get("rqye"))
            detail.rzmre = _to_float(row.get("rzmre"))
            detail.rqyl = _to_float(row.get("rqyl"))
            detail.rzche = _to_float(row.get("rzche"))
            detail.rqchl = _to_float(row.get("rqchl"))
            detail.rqmcl = _to_float(row.get("rqmcl"))
            detail.rzrqye = _to_float(row.get("rzrqye"))
            detail.source = "tushare_margin_detail"
            detail.raw_payload = dict(row)
            db.add(detail)
            stored_count += 1
        return stored_count

    def _store_dragon_tiger_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            reason = str(row.get("reason") or "").strip()
            existing = db.scalar(
                select(DragonTigerList).where(
                    DragonTigerList.symbol == symbol,
                    DragonTigerList.trade_date == row_trade_date,
                    DragonTigerList.reason == reason,
                )
            )
            item = existing or DragonTigerList(
                symbol=symbol,
                trade_date=row_trade_date,
                reason=reason,
            )
            item.name = str(row.get("name") or item.name or symbol)
            item.close = _to_float(row.get("close"))
            item.pct_change = _to_float(row.get("pct_change") or row.get("pct_chg"))
            item.turnover_rate = _to_float(row.get("turnover_rate"))
            item.amount = _to_float(row.get("amount"))
            item.l_sell = _to_float(row.get("l_sell"))
            item.l_buy = _to_float(row.get("l_buy"))
            item.l_amount = _to_float(row.get("l_amount"))
            item.net_amount = _to_float(row.get("net_amount"))
            item.net_rate = _to_float(row.get("net_rate"))
            item.amount_rate = _to_float(row.get("amount_rate"))
            item.float_values = _to_float(row.get("float_values"))
            item.source = "tushare_top_list"
            item.raw_payload = dict(row)
            db.add(item)
            stored_count += 1
        return stored_count

    def _store_dragon_tiger_inst_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        aggregated: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            exalter = str(row.get("exalter") or "").strip()
            side = str(row.get("side") or "").strip()
            reason = str(row.get("reason") or "").strip()
            key = (symbol, row_trade_date, exalter, side, reason)
            item = aggregated.setdefault(
                key,
                {
                    "symbol": symbol,
                    "trade_date": row_trade_date,
                    "exalter": exalter,
                    "side": side,
                    "reason": reason,
                    "buy": 0.0,
                    "sell": 0.0,
                    "net_buy": 0.0,
                    "buy_rate": None,
                    "sell_rate": None,
                    "rows": [],
                },
            )
            item["buy"] += _to_float(row.get("buy")) or 0.0
            item["sell"] += _to_float(row.get("sell")) or 0.0
            item["net_buy"] += _to_float(row.get("net_buy")) or 0.0
            item["buy_rate"] = _to_float(row.get("buy_rate"))
            item["sell_rate"] = _to_float(row.get("sell_rate"))
            item["rows"].append(dict(row))

        stored_count = 0
        for row in aggregated.values():
            existing = db.scalar(
                select(DragonTigerInstitution).where(
                    DragonTigerInstitution.symbol == row["symbol"],
                    DragonTigerInstitution.trade_date == row["trade_date"],
                    DragonTigerInstitution.exalter == row["exalter"],
                    DragonTigerInstitution.side == row["side"],
                    DragonTigerInstitution.reason == row["reason"],
                )
            )
            item = existing or DragonTigerInstitution(
                symbol=row["symbol"],
                trade_date=row["trade_date"],
                exalter=row["exalter"],
                side=row["side"],
                reason=row["reason"],
            )
            item.buy = row["buy"]
            item.buy_rate = row["buy_rate"]
            item.sell = row["sell"]
            item.sell_rate = row["sell_rate"]
            item.net_buy = row["net_buy"]
            item.source = "tushare_top_inst"
            raw_rows = row["rows"]
            item.raw_payload = raw_rows[0] if len(raw_rows) == 1 else {"rows": raw_rows}
            db.add(item)
            stored_count += 1
        return stored_count

    def _store_block_trade_rows(
        self,
        db: Session,
        trade_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        seen_keys: set[
            tuple[str, str, float | None, float | None, float | None, str, str]
        ] = set()
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or trade_date))
            if row_trade_date != trade_date:
                continue
            price = _to_float(row.get("price"))
            vol = _to_float(row.get("vol"))
            amount = _to_float(row.get("amount"))
            buyer = str(row.get("buyer") or "").strip()
            seller = str(row.get("seller") or "").strip()
            key = (symbol, row_trade_date, price, vol, amount, buyer, seller)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            existing = db.scalar(
                select(BlockTrade).where(
                    BlockTrade.symbol == symbol,
                    BlockTrade.trade_date == row_trade_date,
                    BlockTrade.price == price,
                    BlockTrade.vol == vol,
                    BlockTrade.amount == amount,
                    BlockTrade.buyer == buyer,
                    BlockTrade.seller == seller,
                )
            )
            item = existing or BlockTrade(
                symbol=symbol,
                trade_date=row_trade_date,
                price=price,
                vol=vol,
                amount=amount,
                buyer=buyer,
                seller=seller,
            )
            item.price = price
            item.vol = vol
            item.amount = amount
            item.buyer = buyer
            item.seller = seller
            item.source = "tushare_block_trade"
            item.raw_payload = dict(row)
            db.add(item)
            stored_count += 1
        return stored_count

    def _store_shareholder_number_rows(
        self,
        db: Session,
        ann_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_ann_date = _normalize_trade_date(str(row.get("ann_date") or ann_date))
            if row_ann_date != ann_date:
                continue
            end_date = _normalize_trade_date(str(row.get("end_date") or row.get("enddate")))
            existing = db.scalar(
                select(ShareholderNumber).where(
                    ShareholderNumber.symbol == symbol,
                    ShareholderNumber.ann_date == row_ann_date,
                    ShareholderNumber.end_date == end_date,
                )
            )
            item = existing or ShareholderNumber(
                symbol=symbol,
                ann_date=row_ann_date,
                end_date=end_date,
            )
            item.holder_num = _to_int(row.get("holder_num"))
            item.source = "tushare_stk_holdernumber"
            item.raw_payload = dict(row)
            db.add(item)
            stored_count += 1
        return stored_count

    def _store_shareholder_trade_rows(
        self,
        db: Session,
        ann_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        seen_keys: set[tuple[str, str, str, str, str, str, float | None]] = set()
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_ann_date = _normalize_trade_date(str(row.get("ann_date") or ann_date))
            if row_ann_date != ann_date:
                continue
            holder_name = str(row.get("holder_name") or "").strip()
            in_de = str(row.get("in_de") or row.get("trade_type") or "").strip().upper()
            begin_date = str(row.get("begin_date") or "").strip()
            close_date = str(row.get("close_date") or "").strip()
            change_vol = _to_float(row.get("change_vol"))
            key = (symbol, row_ann_date, holder_name, in_de, begin_date, close_date, change_vol)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            existing = db.scalar(
                select(ShareholderTrade).where(
                    ShareholderTrade.symbol == symbol,
                    ShareholderTrade.ann_date == row_ann_date,
                    ShareholderTrade.holder_name == holder_name,
                    ShareholderTrade.in_de == in_de,
                    ShareholderTrade.begin_date == begin_date,
                    ShareholderTrade.close_date == close_date,
                    ShareholderTrade.change_vol == change_vol,
                )
            )
            item = existing or ShareholderTrade(
                symbol=symbol,
                ann_date=row_ann_date,
                holder_name=holder_name,
                in_de=in_de,
                begin_date=begin_date,
                close_date=close_date,
                change_vol=change_vol,
            )
            item.holder_type = str(row.get("holder_type") or "") or None
            item.change_vol = change_vol
            item.change_ratio = _to_float(row.get("change_ratio"))
            item.after_share = _to_float(row.get("after_share"))
            item.after_ratio = _to_float(row.get("after_ratio"))
            item.avg_price = _to_float(row.get("avg_price"))
            item.total_share = _to_float(row.get("total_share"))
            item.source = "tushare_stk_holdertrade"
            item.raw_payload = dict(row)
            db.add(item)
            stored_count += 1
        return stored_count

    def _store_pledge_stat_rows(
        self,
        db: Session,
        end_date: str,
        rows: list[dict[str, Any]],
        symbols: list[str] | None,
    ) -> int:
        symbol_filter = set(symbols or [])
        stored_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            if symbol_filter and symbol not in symbol_filter:
                continue
            row_end_date = _normalize_trade_date(str(row.get("end_date") or end_date))
            existing = db.scalar(
                select(PledgeStat).where(
                    PledgeStat.symbol == symbol,
                    PledgeStat.end_date == row_end_date,
                )
            )
            item = existing or PledgeStat(symbol=symbol, end_date=row_end_date)
            item.pledge_count = _to_int(row.get("pledge_count"))
            item.unrest_pledge = _to_float(row.get("unrest_pledge"))
            item.rest_pledge = _to_float(row.get("rest_pledge"))
            item.total_share = _to_float(row.get("total_share"))
            item.pledge_ratio = _to_float(row.get("pledge_ratio"))
            item.source = "tushare_pledge_stat"
            item.raw_payload = dict(row)
            db.add(item)
            stored_count += 1
        return stored_count

    def refresh_daily_range(
        self,
        db: Session,
        *,
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        dates = self._date_range(start_date, end_date)
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        daily_results: list[dict[str, Any]] = []
        stored_symbols: set[str] = set()
        total_stored = 0
        total_skipped = 0
        consecutive_failures = 0
        total_days = len(dates)

        for index, trade_date in enumerate(dates):
            try:
                result = self.refresh_daily_bars(
                    db,
                    trade_date=trade_date,
                    symbols=normalized_symbols,
                )
                consecutive_failures = 0
            except RuntimeError as exc:
                consecutive_failures += 1
                result = {
                    "trade_date": trade_date,
                    "source": "tushare",
                    "stored_count": 0,
                    "skipped_count": 1,
                    "daily_basic_count": 0,
                    "daily_basic_error": None,
                    "moneyflow_count": 0,
                    "moneyflow_error": None,
                    "index_count": 0,
                    "index_error": None,
                    "sector_count": 0,
                    "sector_error": None,
                    "sector_member_count": 0,
                    "sector_member_error": None,
                    "limit_event_count": 0,
                    "limit_event_error": None,
                    "margin_detail_count": 0,
                    "margin_detail_error": None,
                    "dragon_tiger_count": 0,
                    "dragon_tiger_error": None,
                    "dragon_tiger_inst_count": 0,
                    "dragon_tiger_inst_error": None,
                    "block_trade_count": 0,
                    "block_trade_error": None,
                    "shareholder_number_count": 0,
                    "shareholder_number_error": None,
                    "shareholder_trade_count": 0,
                    "shareholder_trade_error": None,
                    "pledge_stat_count": 0,
                    "pledge_stat_error": None,
                    "requested_symbols": normalized_symbols or [],
                    "error": str(exc),
                }
            daily_results.append(result)
            total_stored += int(result["stored_count"])
            total_skipped += int(result["skipped_count"])
            if progress_callback:
                source_summary = self._data_source_summary(daily_results)
                progress_callback(
                    {
                        "phase": "refreshing_daily",
                        "total_days": total_days,
                        "processed_days": index + 1,
                        "current_trade_date": trade_date,
                        "stored_count": total_stored,
                        "skipped_count": total_skipped,
                        "error_count": sum(1 for item in daily_results if item.get("error")),
                        "data_source_counts": source_summary["data_source_counts"],
                        "data_source_error_count": sum(
                            len(items)
                            for items in source_summary["data_source_errors"].values()
                        ),
                    }
                )
            stored_symbols.update(
                db.scalars(
                    select(DailyBar.symbol)
                    .where(DailyBar.trade_date == trade_date)
                    .distinct()
                ).all()
            )
            if consecutive_failures >= _MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES:
                remaining_dates = dates[index + 1 :]
                for skipped_date in remaining_dates:
                    daily_results.append(
                        {
                            "trade_date": skipped_date,
                            "source": "tushare",
                            "stored_count": 0,
                            "skipped_count": 1,
                            "daily_basic_count": 0,
                            "daily_basic_error": None,
                            "moneyflow_count": 0,
                            "moneyflow_error": None,
                            "index_count": 0,
                            "index_error": None,
                            "sector_count": 0,
                            "sector_error": None,
                            "sector_member_count": 0,
                            "sector_member_error": None,
                            "limit_event_count": 0,
                            "limit_event_error": None,
                            "margin_detail_count": 0,
                            "margin_detail_error": None,
                            "dragon_tiger_count": 0,
                            "dragon_tiger_error": None,
                            "dragon_tiger_inst_count": 0,
                            "dragon_tiger_inst_error": None,
                            "block_trade_count": 0,
                            "block_trade_error": None,
                            "shareholder_number_count": 0,
                            "shareholder_number_error": None,
                            "shareholder_trade_count": 0,
                            "shareholder_trade_error": None,
                            "pledge_stat_count": 0,
                            "pledge_stat_error": None,
                            "requested_symbols": normalized_symbols or [],
                            "error": (
                                f"连续 {_MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES} 天刷新失败，"
                                "停止本轮剩余日期请求。"
                            ),
                        }
                    )
                total_skipped += len(remaining_dates)
                if progress_callback:
                    source_summary = self._data_source_summary(daily_results)
                    progress_callback(
                        {
                            "phase": "refreshing_daily",
                            "total_days": total_days,
                            "processed_days": total_days,
                            "current_trade_date": dates[-1],
                            "stored_count": total_stored,
                            "skipped_count": total_skipped,
                            "error_count": sum(
                                1 for item in daily_results if item.get("error")
                            ),
                            "data_source_counts": source_summary["data_source_counts"],
                            "data_source_error_count": sum(
                                len(items)
                                for items in source_summary["data_source_errors"].values()
                            ),
                        }
                    )
                break

        source_summary = self._data_source_summary(daily_results)
        return {
            "start_date": dates[0],
            "end_date": dates[-1],
            "source": "tushare",
            "processed_days": len(dates),
            "stored_count": total_stored,
            "skipped_count": total_skipped,
            "unique_symbols": len(stored_symbols),
            "requested_symbols": normalized_symbols or [],
            **source_summary,
            "daily_results": daily_results,
        }

    def _data_source_summary(self, daily_results: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {
            "tushare_daily": 0,
            "tushare_daily_basic": 0,
            "tushare_moneyflow_ths": 0,
            "tushare_index_daily": 0,
            "tushare_sector": 0,
            "tushare_sector_member": 0,
            "tushare_limit_list_d": 0,
            "tushare_margin_detail": 0,
            "tushare_top_list": 0,
            "tushare_top_inst": 0,
            "tushare_block_trade": 0,
            "tushare_stk_holdernumber": 0,
            "tushare_stk_holdertrade": 0,
            "tushare_pledge_stat": 0,
        }
        errors: dict[str, list[str]] = defaultdict(list)
        for item in daily_results:
            trade_date = str(item.get("trade_date") or "")
            counts["tushare_daily"] += int(item.get("stored_count") or 0)
            counts["tushare_daily_basic"] += int(item.get("daily_basic_count") or 0)
            counts["tushare_moneyflow_ths"] += int(item.get("moneyflow_count") or 0)
            counts["tushare_index_daily"] += int(item.get("index_count") or 0)
            counts["tushare_sector"] += int(item.get("sector_count") or 0)
            counts["tushare_sector_member"] += int(item.get("sector_member_count") or 0)
            counts["tushare_limit_list_d"] += int(item.get("limit_event_count") or 0)
            counts["tushare_margin_detail"] += int(item.get("margin_detail_count") or 0)
            counts["tushare_top_list"] += int(item.get("dragon_tiger_count") or 0)
            counts["tushare_top_inst"] += int(item.get("dragon_tiger_inst_count") or 0)
            counts["tushare_block_trade"] += int(item.get("block_trade_count") or 0)
            counts["tushare_stk_holdernumber"] += int(
                item.get("shareholder_number_count") or 0
            )
            counts["tushare_stk_holdertrade"] += int(item.get("shareholder_trade_count") or 0)
            counts["tushare_pledge_stat"] += int(item.get("pledge_stat_count") or 0)
            for key, source in (
                ("error", "tushare_daily"),
                ("daily_basic_error", "tushare_daily_basic"),
                ("moneyflow_error", "tushare_moneyflow_ths"),
                ("index_error", "tushare_index_daily"),
                ("sector_error", "tushare_sector"),
                ("sector_member_error", "tushare_sector_member"),
                ("limit_event_error", "tushare_limit_list_d"),
                ("margin_detail_error", "tushare_margin_detail"),
                ("dragon_tiger_error", "tushare_top_list"),
                ("dragon_tiger_inst_error", "tushare_top_inst"),
                ("block_trade_error", "tushare_block_trade"),
                ("shareholder_number_error", "tushare_stk_holdernumber"),
                ("shareholder_trade_error", "tushare_stk_holdertrade"),
                ("pledge_stat_error", "tushare_pledge_stat"),
            ):
                message = item.get(key)
                if message:
                    errors[source].append(f"{trade_date}: {message}")
        return {
            "data_source_counts": counts,
            "data_source_errors": dict(errors),
        }

    def run_daily_momentum_backtest(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_cash: float,
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
        start = _normalize_trade_date(start_date)
        end = _normalize_trade_date(end_date)
        bars = db.scalars(
            select(DailyBar)
            .where(
                DailyBar.symbol.in_(normalized_symbols),
                DailyBar.trade_date >= start,
                DailyBar.trade_date <= end,
            )
            .order_by(DailyBar.symbol, DailyBar.trade_date)
        ).all()
        by_symbol: dict[str, list[DailyBar]] = defaultdict(list)
        for bar in bars:
            if bar.close is not None:
                by_symbol[bar.symbol].append(bar)
        if not by_symbol:
            raise ValueError("没有找到可用于回测的日频数据，请先刷新历史数据。")

        ranked: list[tuple[float, str, list[DailyBar]]] = []
        for symbol, symbol_bars in by_symbol.items():
            if len(symbol_bars) < 2:
                continue
            first_close = float(symbol_bars[0].close or 0)
            last_close = float(symbol_bars[-1].close or 0)
            if first_close <= 0 or last_close <= 0:
                continue
            momentum = (last_close - first_close) / first_close
            ranked.append((momentum, symbol, symbol_bars))
        if not ranked:
            raise ValueError("可用日频数据不足，至少需要每只股票两根日线。")

        ranked.sort(reverse=True, key=lambda item: item[0])
        _, selected_symbol, selected_bars = ranked[0]
        buy_bar = selected_bars[0]
        sell_bar = selected_bars[-1]
        buy_price = float(buy_bar.close or 0)
        sell_price = float(sell_bar.close or 0)
        quantity = int(initial_cash // (buy_price * 100)) * 100
        buy_amount = quantity * buy_price
        cash_after_buy = initial_cash - buy_amount
        final_assets = cash_after_buy + quantity * sell_price
        return_ratio = (final_assets - initial_cash) / initial_cash
        equity_curve = [
            cash_after_buy + quantity * float(bar.close or 0)
            for bar in selected_bars
        ]
        max_drawdown = self._max_drawdown(equity_curve)
        trades = [
            {
                "action": "BUY",
                "symbol": selected_symbol,
                "trade_date": buy_bar.trade_date,
                "price": buy_price,
                "quantity": quantity,
                "amount": round(buy_amount, 2),
            },
            {
                "action": "SELL",
                "symbol": selected_symbol,
                "trade_date": sell_bar.trade_date,
                "price": sell_price,
                "quantity": quantity,
                "amount": round(quantity * sell_price, 2),
            },
        ]
        metrics = {
            "bars_used": len(bars),
            "candidate_symbols": len(by_symbol),
            "selected_momentum": round((sell_price - buy_price) / buy_price, 6),
            "quantity": quantity,
        }
        run = BacktestRun(
            strategy_name="daily_momentum",
            symbols_json=normalized_symbols,
            start_date=start,
            end_date=end,
            initial_cash=initial_cash,
            final_assets=round(final_assets, 2),
            return_ratio=round(return_ratio, 6),
            max_drawdown=round(max_drawdown, 6),
            trade_count=len(trades),
            selected_symbol=selected_symbol,
            metrics_payload=metrics,
            trades_payload=trades,
        )
        db.add(run)
        db.commit()
        return {
            "run_id": run.id,
            "strategy_name": run.strategy_name,
            "selected_symbol": selected_symbol,
            "start_date": start,
            "end_date": end,
            "initial_cash": initial_cash,
            "final_assets": run.final_assets,
            "return_ratio": run.return_ratio,
            "max_drawdown": run.max_drawdown,
            "trade_count": run.trade_count,
            "metrics": metrics,
            "trades": trades,
        }

    def _max_drawdown(self, equity_curve: list[float]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = min(max_drawdown, (value - peak) / peak)
        return max_drawdown

    def _date_range(self, start_date: str, end_date: str) -> list[str]:
        start = datetime.strptime(_normalize_trade_date(start_date), "%Y%m%d").date()
        end = datetime.strptime(_normalize_trade_date(end_date), "%Y%m%d").date()
        if start > end:
            raise ValueError("开始日期不能晚于结束日期。")
        days = (end - start).days + 1
        if days > _MAX_DAILY_REFRESH_RANGE_DAYS:
            raise ValueError(f"单次最多刷新 {_MAX_DAILY_REFRESH_RANGE_DAYS} 个自然日。")
        return [
            (start + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(days)
        ]


historical_data_service = HistoricalDataService()
