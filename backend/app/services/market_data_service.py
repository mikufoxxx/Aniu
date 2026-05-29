from __future__ import annotations

import importlib.util
import math
import shutil
import subprocess
import time
from datetime import datetime
from typing import Any

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


class MarketDataService:
    def __init__(self) -> None:
        self._quote_cache: dict[str, Any] | None = None
        self._quote_cache_key = ""
        self._quote_cache_expires_at = 0.0

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
                "cadence": "30-60 秒快照/备用交叉校验",
                "risk": "免费接口，需要限频和缓存。",
            },
            {
                "id": "easy_tdx",
                "name": "easy-tdx",
                "tier": "quasi_high_frequency",
                "status": "available" if easy_tdx_available else "optional_missing",
                "cadence": "候选池 1-5 秒轮询，支持 quote/分时/逐笔",
                "risk": "通达信公开服务器，禁止全市场高压轮询。",
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

    def get_quotes(
        self,
        symbols: list[str],
        prefer_realtime: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
        cache_key = ",".join(normalized_symbols)
        now = time.time()
        if (
            self._quote_cache is not None
            and self._quote_cache_key == cache_key
            and self._quote_cache_expires_at > now
        ):
            return [dict(item) for item in self._quote_cache]

        quotes = self._get_easy_tdx_quotes(normalized_symbols) if prefer_realtime else []
        if not quotes:
            quotes = self._get_tencent_quotes(normalized_symbols)

        self._quote_cache = [dict(item) for item in quotes]
        self._quote_cache_key = cache_key
        self._quote_cache_expires_at = now + max(
            1, int(get_settings().realtime_quote_cache_ttl_seconds)
        )
        return quotes

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
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        return results

    def _get_tencent_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for start in range(0, len(symbols), _TENCENT_QUOTE_BATCH_SIZE):
            batch = symbols[start : start + _TENCENT_QUOTE_BATCH_SIZE]
            batch_results = self._request_tencent_quote_batch(batch)
            results.extend(batch_results or self._fallback_quotes(batch))
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
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            for symbol in symbols
        ]


market_data_service = MarketDataService()
