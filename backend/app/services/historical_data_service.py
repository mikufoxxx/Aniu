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
from app.db.models import BacktestRun, DailyBar, IndexBar, SectorBar, SectorMember
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
_DEFAULT_INDEX_SYMBOLS = ("000001.SH", "399001.SZ", "399006.SZ", "000300.SH", "000905.SH")
_TUSHARE_THS_MEMBER_WORKERS = 2
_TUSHARE_THS_MEMBER_RETRIES = 3
_TUSHARE_THS_MEMBER_RETRY_DELAY_SECONDS = 2.0
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
        if len(sector_symbols) <= 1:
            rows: list[dict[str, Any]] = []
            for symbol in sector_symbols:
                rows.extend(self._request_tushare_ths_member({"ts_code": symbol}))
            return rows

        rows: list[dict[str, Any]] = []
        worker_count = min(_TUSHARE_THS_MEMBER_WORKERS, len(sector_symbols))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(self._request_tushare_ths_member, {"ts_code": symbol})
                for symbol in sector_symbols
            ]
            for future in as_completed(futures):
                rows.extend(future.result())
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
            except RuntimeError as exc:
                sector_member_error = str(exc)

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
            "requested_symbols": normalized_symbols or [],
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
                    "requested_symbols": normalized_symbols or [],
                    "error": str(exc),
                }
            daily_results.append(result)
            total_stored += int(result["stored_count"])
            total_skipped += int(result["skipped_count"])
            if progress_callback:
                progress_callback(
                    {
                        "phase": "refreshing_daily",
                        "total_days": total_days,
                        "processed_days": index + 1,
                        "current_trade_date": trade_date,
                        "stored_count": total_stored,
                        "skipped_count": total_skipped,
                        "error_count": sum(1 for item in daily_results if item.get("error")),
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
                            "requested_symbols": normalized_symbols or [],
                            "error": (
                                f"连续 {_MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES} 天刷新失败，"
                                "停止本轮剩余日期请求。"
                            ),
                        }
                    )
                total_skipped += len(remaining_dates)
                if progress_callback:
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
                        }
                    )
                break

        return {
            "start_date": dates[0],
            "end_date": dates[-1],
            "source": "tushare",
            "processed_days": len(dates),
            "stored_count": total_stored,
            "skipped_count": total_skipped,
            "unique_symbols": len(stored_symbols),
            "requested_symbols": normalized_symbols or [],
            "daily_results": daily_results,
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
