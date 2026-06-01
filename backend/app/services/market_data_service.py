from __future__ import annotations

import importlib.util
import json
import math
import shutil
import subprocess
import time
from datetime import datetime
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings


DEFAULT_UNIVERSE = [
    "600519.SH",
    "000001.SZ",
    "300750.SZ",
    "601318.SH",
    "000858.SZ",
    "300059.SZ",
    "600036.SH",
    "600887.SH",
    "002594.SZ",
    "601012.SH",
]

_TENCENT_QUOTE_BATCH_SIZE = 60
_EASTMONEY_QUOTE_BATCH_SIZE = 80
_SINA_QUOTE_BATCH_SIZE = 220
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        raise ValueError("股票代码不能为空。")
    if "." in text:
        code, suffix = text.split(".", 1)
        return f"{code.zfill(6)}.{suffix}"
    code = text[-6:]
    if not code.isdigit():
        raise ValueError(f"股票代码格式不正确: {symbol}")
    suffix = "SH" if code.startswith(("5", "6", "9")) else "SZ"
    return f"{code}.{suffix}"


def symbol_to_tencent(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    code, suffix = normalized.split(".", 1)
    return f"{suffix.lower()}{code}"


def symbol_to_easy_tdx(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    code, suffix = normalized.split(".", 1)
    return f"{suffix} {code}"


def symbol_to_eastmoney_secid(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    code, suffix = normalized.split(".", 1)
    market = "1" if suffix == "SH" else "0"
    return f"{market}.{code}"


def symbol_to_sina(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    code, suffix = normalized.split(".", 1)
    return f"{suffix.lower()}{code}"


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _market_now() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def _market_now_text() -> str:
    return _market_now().strftime("%Y-%m-%d %H:%M:%S")


def _market_timestamp_text(value: float | int) -> str:
    return datetime.fromtimestamp(value, tz=MARKET_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _display_intraday_bar_time(value: str) -> str:
    text = str(value or "").strip()
    try:
        bar_time = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=MARKET_TIMEZONE)
    except ValueError:
        return text
    now = _market_now()
    if bar_time.date() == now.date() and bar_time > now:
        return now.strftime("%Y-%m-%d %H:%M")
    return text


class MarketDataService:
    def __init__(self) -> None:
        self._quote_cache: dict[str, list[dict[str, Any]]] = {}
        self._quote_cache_expires_at: dict[str, float] = {}
        self._quote_cache_lock = RLock()
        self._intraday_cache: dict[str, list[dict[str, Any]]] = {}
        self._intraday_cache_expires_at: dict[str, float] = {}

    def source_health(self) -> dict[str, Any]:
        settings = get_settings()
        easy_tdx_available = bool(shutil.which("easy-tdx")) or (
            importlib.util.find_spec("easy_tdx") is not None
        )
        tushare_available = bool(settings.tushare_token)

        sources = [
            {
                "id": "tushare",
                "name": "Tushare",
                "tier": "daily",
                "status": "available" if tushare_available else "not_configured",
                "cadence": "T+1 日频/回测数据",
                "risk": "积分按月消耗，不适合盘中实时。",
            },
            {
                "id": "tencent",
                "name": "腾讯行情",
                "tier": "low_frequency",
                "status": "available",
                "cadence": "30-60 秒快照",
                "risk": "免费接口，需要限频和缓存。",
            },
            {
                "id": "eastmoney",
                "name": "东方财富 push2",
                "tier": "low_frequency",
                "status": "available",
                "cadence": "30-60 秒快照/60 分钟 K 线",
                "risk": "免费接口，需要限频和缓存；小时线会用腾讯分钟线兜底。",
            },
            {
                "id": "easy_tdx",
                "name": "easy-tdx",
                "tier": "quasi_high_frequency",
                "status": "available" if easy_tdx_available else "optional_missing",
                "cadence": "候选池 1-5 秒 quote 轮询",
                "risk": "通达信公开服务器，分时/逐笔待独立接入，禁止全市场高压轮询。",
            },
            {
                "id": "miaoxiang",
                "name": "妙想",
                "tier": "supplemental",
                "status": "available" if settings.mx_apikey else "not_configured",
                "cadence": "资讯、自然语言选股、模拟交易确认",
                "risk": "有限额，不作为全市场行情主源。",
            },
        ]
        return {
            "sources": sources,
            "recommended_usage": {
                "daily": "Tushare",
                "low_frequency": "腾讯行情主用，东方财富备用",
                "quasi_high_frequency": "easy-tdx",
                "supplemental": "妙想",
            },
        }

    def cache_policy(self) -> dict[str, Any]:
        ttl = max(1, int(get_settings().realtime_quote_cache_ttl_seconds))
        return {
            "quote_cache_ttl_seconds": ttl,
            "intraday_cache_ttl_seconds": ttl,
            "live_quote_primary": "tencent",
            "live_quote_fallbacks": ["easy_tdx", "eastmoney", "sina", "local_fallback"],
            "intraday_primary": "eastmoney",
            "intraday_fallback": "tencent_m5",
            "handoff_policy": {
                "08:00-09:15": "早盘推荐只读 Tushare 历史日频、资金、财务、公告和新闻，不依赖盘中实时价。",
                "09:15-15:35": "盘中图表和竞技场收益用腾讯低频快照缓存，5-30 秒粒度由后端统一拉取。",
                "15:35-20:30": "收盘复盘冻结盘中缓存和当日操作，等待日频数据补齐。",
                "20:30之后": "夜间学习、回测和历史图表优先使用 Tushare 固化后的日频数据。",
            },
        }

    def get_quotes(
        self,
        symbols: list[str],
        prefer_realtime: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
        cache_prefix = "realtime" if prefer_realtime else "low_frequency"
        cache_key = f"{cache_prefix}:" + ",".join(normalized_symbols)
        now = time.time()
        with self._quote_cache_lock:
            if not isinstance(self._quote_cache, dict):
                self._quote_cache = {}
                self._quote_cache_expires_at = {}
            if self._quote_cache_expires_at.get(cache_key, 0) > now:
                return [dict(item) for item in self._quote_cache.get(cache_key, [])]

        quote_by_symbol: dict[str, dict[str, Any]] = {}
        for quote in self._get_tencent_quotes(normalized_symbols):
            symbol = normalize_symbol(str(quote.get("symbol") or ""))
            quote_by_symbol[symbol] = quote
        missing_symbols = [
            symbol for symbol in normalized_symbols if symbol not in quote_by_symbol
        ]
        if prefer_realtime and missing_symbols:
            for quote in self._get_easy_tdx_quotes(missing_symbols):
                symbol = normalize_symbol(str(quote.get("symbol") or ""))
                quote_by_symbol[symbol] = quote
        missing_symbols = [
            symbol for symbol in normalized_symbols if symbol not in quote_by_symbol
        ]
        if missing_symbols:
            for quote in self._get_eastmoney_quotes(missing_symbols):
                symbol = normalize_symbol(str(quote.get("symbol") or ""))
                quote_by_symbol[symbol] = quote

        missing_symbols = [
            symbol for symbol in normalized_symbols if symbol not in quote_by_symbol
        ]
        if missing_symbols:
            for quote in self._get_sina_quotes(missing_symbols):
                symbol = normalize_symbol(str(quote.get("symbol") or ""))
                quote_by_symbol[symbol] = quote

        missing_symbols = [
            symbol for symbol in normalized_symbols if symbol not in quote_by_symbol
        ]
        for quote in self._fallback_quotes(missing_symbols):
            quote_by_symbol[quote["symbol"]] = quote

        quotes = [
            quote_by_symbol[symbol]
            for symbol in normalized_symbols
            if symbol in quote_by_symbol
        ]

        with self._quote_cache_lock:
            self._quote_cache[cache_key] = [dict(item) for item in quotes]
            self._quote_cache_expires_at[cache_key] = time.time() + max(
                1, int(get_settings().realtime_quote_cache_ttl_seconds)
            )
        return quotes

    def get_intraday_bars(
        self,
        symbol: str,
        *,
        interval: str = "60m",
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        normalized_symbol = normalize_symbol(symbol)
        klt = self._normalize_intraday_interval(interval)
        normalized_limit = max(1, min(int(limit or 120), 300))
        cache_key = f"{normalized_symbol}:{klt}:{normalized_limit}"
        now = time.time()
        if self._intraday_cache_expires_at.get(cache_key, 0) > now:
            return [dict(item) for item in self._intraday_cache.get(cache_key, [])]

        results = self._get_eastmoney_intraday_bars(
            normalized_symbol,
            klt=klt,
            limit=normalized_limit,
        )
        if not results:
            results = self._get_tencent_intraday_bars(
                normalized_symbol,
                klt=klt,
                limit=normalized_limit,
            )
        self._intraday_cache[cache_key] = [dict(item) for item in results]
        self._intraday_cache_expires_at[cache_key] = now + max(
            1,
            int(get_settings().realtime_quote_cache_ttl_seconds),
        )
        return results

    def _normalize_intraday_interval(self, interval: str) -> str:
        value = str(interval or "60m").strip().lower()
        mapping = {
            "1": "1",
            "1m": "1",
            "5": "5",
            "5m": "5",
            "15": "15",
            "15m": "15",
            "30": "30",
            "30m": "30",
            "60": "60",
            "60m": "60",
            "hour": "60",
            "hourly": "60",
        }
        if value not in mapping:
            raise ValueError("分时 K 线周期必须是 1m、5m、15m、30m 或 60m。")
        return mapping[value]

    def _get_eastmoney_intraday_bars(
        self,
        symbol: str,
        *,
        klt: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        params = {
            "secid": symbol_to_eastmoney_secid(symbol),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt,
            "fqt": "1",
            "end": "20500101",
            "lmt": str(limit),
        }
        try:
            response = httpx.get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                params=params,
                timeout=8.0,
                follow_redirects=True,
                headers={"User-Agent": "Aniu/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []

        data = payload.get("data") if isinstance(payload, dict) else None
        klines = data.get("klines") if isinstance(data, dict) else None
        if not isinstance(klines, list):
            return []

        rows: list[dict[str, Any]] = []
        for line in klines:
            fields = str(line or "").split(",")
            if len(fields) < 7:
                continue
            trade_time = _display_intraday_bar_time(fields[0])
            open_price = _parse_float(fields[1])
            close_price = _parse_float(fields[2])
            high_price = _parse_float(fields[3])
            low_price = _parse_float(fields[4])
            if close_price is None:
                continue
            rows.append(
                {
                    "trade_date": trade_time,
                    "open": round(open_price if open_price is not None else close_price, 4),
                    "high": round(high_price if high_price is not None else close_price, 4),
                    "low": round(low_price if low_price is not None else close_price, 4),
                    "close": round(close_price, 4),
                    "volume": float(_parse_float(fields[5]) or 0),
                    "amount": float(_parse_float(fields[6]) or 0),
                    "source": "eastmoney_intraday",
                    "timestamp": trade_time,
                    "is_realtime": False,
                }
            )
        return rows

    def _get_tencent_intraday_bars(
        self,
        symbol: str,
        *,
        klt: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if klt == "60":
            minute_type = "m5"
            raw_limit = min(max(limit * 12, 12), 640)
        elif klt in {"1", "5", "15", "30"}:
            minute_type = f"m{klt}"
            raw_limit = min(max(limit, 1), 640)
        else:
            return []
        tencent_symbol = symbol_to_tencent(symbol)
        params = {
            "param": f"{tencent_symbol},{minute_type},,{raw_limit}",
            "_var": f"{minute_type}_today",
            "r": str(time.time()),
        }
        try:
            response = httpx.get(
                "http://ifzq.gtimg.cn/appstock/app/kline/mkline",
                params=params,
                timeout=8.0,
                follow_redirects=True,
                headers={"User-Agent": "Aniu/1.0"},
            )
            response.raise_for_status()
            text = response.text
            payload = json.loads(text.split("=", 1)[1] if "=" in text else text)
        except Exception:
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        symbol_data = data.get(tencent_symbol) if isinstance(data, dict) else None
        rows = symbol_data.get(minute_type) if isinstance(symbol_data, dict) else None
        if not isinstance(rows, list):
            return []
        minute_rows = self._parse_tencent_minute_rows(rows)
        if klt == "60":
            return self._aggregate_tencent_minutes_to_hourly(minute_rows)[-limit:]
        return minute_rows[-limit:]

    def _parse_tencent_minute_rows(self, rows: list[Any]) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            try:
                trade_time = datetime.strptime(str(row[0]), "%Y%m%d%H%M")
            except ValueError:
                continue
            open_price = _parse_float(row[1])
            close_price = _parse_float(row[2])
            high_price = _parse_float(row[3])
            low_price = _parse_float(row[4])
            if close_price is None:
                continue
            display_time = _display_intraday_bar_time(
                trade_time.strftime("%Y-%m-%d %H:%M")
            )
            parsed.append(
                {
                    "trade_date": display_time,
                    "open": round(open_price if open_price is not None else close_price, 4),
                    "high": round(high_price if high_price is not None else close_price, 4),
                    "low": round(low_price if low_price is not None else close_price, 4),
                    "close": round(close_price, 4),
                    "volume": float(_parse_float(row[5]) or 0),
                    "amount": 0.0,
                    "source": "tencent_minute",
                    "timestamp": display_time,
                    "is_realtime": False,
                }
            )
        return parsed

    def _aggregate_tencent_minutes_to_hourly(
        self,
        minute_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for row in minute_rows:
            trade_date = str(row.get("trade_date") or "")
            bucket_key = trade_date[:13]
            current = buckets.get(bucket_key)
            if current is None:
                buckets[bucket_key] = {
                    "trade_date": trade_date,
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "volume": row["volume"],
                    "amount": row["amount"],
                    "source": "tencent_m5_aggregated",
                    "timestamp": trade_date,
                    "is_realtime": False,
                }
                continue
            current["trade_date"] = trade_date
            current["high"] = max(float(current["high"]), float(row["high"]))
            current["low"] = min(float(current["low"]), float(row["low"]))
            current["close"] = row["close"]
            current["volume"] = float(current["volume"]) + float(row["volume"])
            current["amount"] = float(current["amount"]) + float(row["amount"])
            current["timestamp"] = trade_date
        return list(buckets.values())

    def _get_easy_tdx_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not shutil.which("easy-tdx"):
            return []
        command = [
            "easy-tdx",
            "quote",
            ",".join(symbol_to_easy_tdx(symbol) for symbol in symbols),
            "--output",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if completed.returncode != 0:
            return []
        try:
            import json

            payload = json.loads(completed.stdout)
        except (ValueError, TypeError):
            return []
        if not isinstance(payload, list):
            return []

        results: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").zfill(6)
            market = int(_parse_float(item.get("market")) or 0)
            suffix = "SH" if market == 1 else "SZ"
            price = _parse_float(item.get("close"))
            pre_close = _parse_float(item.get("pre_close"))
            change_pct = None
            if price is not None and pre_close not in (None, 0):
                change_pct = (price - pre_close) / pre_close * 100
            results.append(
                {
                    "symbol": f"{code}.{suffix}",
                    "name": str(item.get("name") or code),
                    "price": price,
                    "change_pct": change_pct,
                    "amount": _parse_float(item.get("amount")),
                    "turnover": _parse_float(item.get("turnover")),
                    "volume_ratio": _parse_float(item.get("vol_ratio")),
                    "source": "easy_tdx",
                    "timestamp": _market_now_text(),
                }
            )
        return results

    def _get_tencent_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for start in range(0, len(symbols), _TENCENT_QUOTE_BATCH_SIZE):
            batch = symbols[start : start + _TENCENT_QUOTE_BATCH_SIZE]
            batch_results = self._request_tencent_quote_batch(batch)
            batch_by_symbol = {
                normalize_symbol(str(item.get("symbol") or "")): item
                for item in batch_results
                if item.get("symbol")
            }
            fallback_by_symbol = {
                item["symbol"]: item for item in self._fallback_quotes(batch)
            }
            for symbol in batch:
                normalized = normalize_symbol(symbol)
                results.append(batch_by_symbol.get(normalized) or fallback_by_symbol[normalized])
        return results

    def _request_tencent_quote_batch(self, symbols: list[str]) -> list[dict[str, Any]]:
        query = ",".join(symbol_to_tencent(symbol) for symbol in symbols)
        try:
            response = httpx.get(
                "https://qt.gtimg.cn/q=" + query,
                timeout=8.0,
                headers={"User-Agent": "Aniu/1.0"},
            )
            response.raise_for_status()
        except Exception:
            return []

        text = response.content.decode("gbk", errors="ignore")
        results: list[dict[str, Any]] = []
        for line in text.split(";"):
            if "~" not in line:
                continue
            raw_symbol = line.split("=", 1)[0].replace("v_", "").strip()
            fields = line.split('"', 1)[-1].rsplit('"', 1)[0].split("~")
            if len(fields) < 33:
                continue
            code = fields[2].zfill(6)
            suffix = "SH" if raw_symbol.startswith("sh") else "SZ"
            price = _parse_float(fields[3])
            prev_close = _parse_float(fields[4])
            change_pct = _parse_float(fields[32])
            if change_pct is None and price is not None and prev_close not in (None, 0):
                change_pct = (price - prev_close) / prev_close * 100
            amount_wan = _parse_float(fields[37] if len(fields) > 37 else None)
            results.append(
                {
                    "symbol": f"{code}.{suffix}",
                    "name": fields[1] or code,
                    "price": price,
                    "change_pct": change_pct,
                    "amount": amount_wan * 10000 if amount_wan is not None else None,
                    "turnover": _parse_float(fields[38] if len(fields) > 38 else None),
                    "volume_ratio": None,
                    "source": "tencent",
                    "timestamp": fields[30] if len(fields) > 30 else None,
                }
            )
        return results

    def _get_eastmoney_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for start in range(0, len(symbols), _EASTMONEY_QUOTE_BATCH_SIZE):
            batch = symbols[start : start + _EASTMONEY_QUOTE_BATCH_SIZE]
            results.extend(self._request_eastmoney_quote_batch(batch))
        return results

    def _request_eastmoney_quote_batch(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not symbols:
            return []
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f12,f13,f14,f2,f3,f4,f5,f6,f8,f10,f17,f18,f20,f21,f24,f25,f109,f110,f124,f127,f160",
            "secids": ",".join(symbol_to_eastmoney_secid(symbol) for symbol in symbols),
        }
        try:
            response = httpx.get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params=params,
                timeout=8.0,
                follow_redirects=True,
                headers={"User-Agent": "Aniu/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []

        data = payload.get("data") if isinstance(payload, dict) else None
        diff = data.get("diff") if isinstance(data, dict) else None
        if not isinstance(diff, list):
            return []

        results: list[dict[str, Any]] = []
        for item in diff:
            if not isinstance(item, dict):
                continue
            code = str(item.get("f12") or "").zfill(6)
            market = int(_parse_float(item.get("f13")) or 0)
            suffix = "SH" if market == 1 else "SZ"
            price = _parse_float(item.get("f2"))
            change_pct = _parse_float(item.get("f3"))
            amount = _parse_float(item.get("f6"))
            turnover = _parse_float(item.get("f8"))
            timestamp_value = _parse_float(item.get("f124"))
            timestamp = (
                _market_timestamp_text(timestamp_value)
                if timestamp_value
                else _market_now_text()
            )
            results.append(
                {
                    "symbol": f"{code}.{suffix}",
                    "name": str(item.get("f14") or code),
                    "price": price,
                    "change_pct": change_pct,
                    "amount": amount,
                    "turnover": turnover,
                    "volume_ratio": _parse_float(item.get("f10")),
                    "source": "eastmoney",
                    "timestamp": timestamp,
                }
            )
        return results

    def _get_sina_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for start in range(0, len(symbols), _SINA_QUOTE_BATCH_SIZE):
            batch = symbols[start : start + _SINA_QUOTE_BATCH_SIZE]
            results.extend(self._request_sina_quote_batch(batch))
        return results

    def _request_sina_quote_batch(self, symbols: list[str]) -> list[dict[str, Any]]:
        if not symbols:
            return []
        query = ",".join(symbol_to_sina(symbol) for symbol in symbols)
        try:
            response = httpx.get(
                "https://hq.sinajs.cn/list=" + query,
                timeout=8.0,
                headers={
                    "User-Agent": "Aniu/1.0",
                    "Referer": "https://finance.sina.com.cn/",
                },
            )
            response.raise_for_status()
        except Exception:
            return []

        text = response.content.decode("gbk", errors="ignore")
        results: list[dict[str, Any]] = []
        for line in text.split(";"):
            if "=" not in line:
                continue
            raw_symbol = line.split("=", 1)[0].replace("var hq_str_", "").strip()
            fields = line.split('"', 1)[-1].rsplit('"', 1)[0].split(",")
            if len(fields) < 32 or not fields[0]:
                continue
            code = raw_symbol[-6:].zfill(6)
            suffix = "SH" if raw_symbol.startswith("sh") else "SZ"
            price = _parse_float(fields[3])
            prev_close = _parse_float(fields[2])
            change_pct = None
            if price is not None and prev_close not in (None, 0):
                change_pct = (price - prev_close) / prev_close * 100
            date_text = fields[30] if len(fields) > 30 else ""
            time_text = fields[31] if len(fields) > 31 else ""
            results.append(
                {
                    "symbol": f"{code}.{suffix}",
                    "name": fields[0] or code,
                    "price": price,
                    "change_pct": change_pct,
                    "amount": _parse_float(fields[9] if len(fields) > 9 else None),
                    "turnover": None,
                    "volume_ratio": None,
                    "source": "sina",
                    "timestamp": f"{date_text} {time_text}".strip() or _market_now_text(),
                }
            )
        return results

    def _fallback_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "symbol": normalize_symbol(symbol),
                "name": normalize_symbol(symbol).split(".", 1)[0],
                "price": 10.0,
                "change_pct": 0.0,
                "amount": 0.0,
                "turnover": 0.0,
                "volume_ratio": 1.0,
                "source": "fallback",
                "timestamp": _market_now_text(),
            }
            for symbol in symbols
        ]


market_data_service = MarketDataService()
