from pathlib import Path
from datetime import datetime, timezone
import json
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))


def test_easy_tdx_quote_timestamp_uses_shanghai_time(monkeypatch) -> None:
    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    class FakeDateTime:
        @staticmethod
        def now(tz=None):
            value = datetime(2026, 6, 1, 4, 30, 32, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

        @staticmethod
        def fromtimestamp(value, tz=None):
            result = datetime.fromtimestamp(value, tz=timezone.utc)
            return result.astimezone(tz) if tz is not None else result.replace(tzinfo=None)

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps(
            [
                {
                    "code": "000001",
                    "market": 0,
                    "name": "平安银行",
                    "close": 10.91,
                    "pre_close": 10.93,
                    "amount": 645638272,
                    "turnover": 1.0,
                    "vol_ratio": 1.1,
                }
            ]
        )

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/local/bin/easy-tdx")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: FakeCompleted())
    monkeypatch.setattr(module, "datetime", FakeDateTime)

    quote = market_data_service._get_easy_tdx_quotes(["000001.SZ"])[0]

    assert quote["timestamp"] == "2026-06-01 12:30:32"


def test_eastmoney_intraday_bars_are_parsed_as_real_hourly_data(monkeypatch) -> None:
    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "klines": [
                        "2026-06-01 10:30,10.90,10.93,10.95,10.81,428850,466594440.00,1.28,0.00,0.00,0.22",
                        "2026-06-01 11:30,10.93,10.91,10.96,10.91,163695,179043839.00,0.46,-0.18,-0.02,0.08",
                    ]
                }
            }

    def fake_get(url: str, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return FakeResponse()

    monkeypatch.setattr(module.httpx, "get", fake_get)
    market_data_service._intraday_cache = {}
    market_data_service._intraday_cache_expires_at = {}

    bars = market_data_service.get_intraday_bars("000001.SZ", interval="hourly", limit=2)

    assert captured["params"]["secid"] == "0.000001"
    assert captured["params"]["klt"] == "60"
    assert bars == [
        {
            "trade_date": "2026-06-01 10:30",
            "open": 10.9,
            "high": 10.95,
            "low": 10.81,
            "close": 10.93,
            "volume": 428850.0,
            "amount": 466594440.0,
            "source": "eastmoney_intraday",
            "timestamp": "2026-06-01 10:30",
            "is_realtime": False,
        },
        {
            "trade_date": "2026-06-01 11:30",
            "open": 10.93,
            "high": 10.96,
            "low": 10.91,
            "close": 10.91,
            "volume": 163695.0,
            "amount": 179043839.0,
            "source": "eastmoney_intraday",
            "timestamp": "2026-06-01 11:30",
            "is_realtime": False,
        },
    ]


def test_eastmoney_intraday_current_bar_time_is_not_displayed_as_future(monkeypatch) -> None:
    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "klines": [
                        "2026-06-01 14:00,10.90,10.92,10.93,10.90,17435,19029042.00,0.20,0.00,0.00,0.01",
                    ]
                }
            }

    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        module,
        "_market_now",
        lambda: datetime(2026, 6, 1, 13, 6, tzinfo=module.MARKET_TIMEZONE),
    )
    market_data_service._intraday_cache = {}
    market_data_service._intraday_cache_expires_at = {}

    bars = market_data_service.get_intraday_bars("000001.SZ", interval="hourly", limit=1)

    assert bars[0]["trade_date"] == "2026-06-01 13:06"
    assert bars[0]["timestamp"] == "2026-06-01 13:06"


def test_intraday_bars_fall_back_to_tencent_minutes_when_eastmoney_fails(monkeypatch) -> None:
    import httpx

    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    class FakeTencentResponse:
        text = (
            'm5_today={"code":0,"msg":"","data":{"sz000001":{"m5":['
            '["202606011305","10.91","10.91","10.93","10.90","15460.00",{},"0.80"],'
            '["202606011310","10.91","10.91","10.92","10.90","6324.00",{},"0.33"],'
            '["202606011315","10.91","10.92","10.93","10.91","17435.00",{},"0.90"]'
            ']}}}'
        )

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs):
        if "push2his.eastmoney.com" in url:
            raise httpx.RemoteProtocolError("disconnect")
        assert url == "http://ifzq.gtimg.cn/appstock/app/kline/mkline"
        assert kwargs["params"]["param"] == "sz000001,m5,,24"
        return FakeTencentResponse()

    monkeypatch.setattr(module.httpx, "get", fake_get)
    market_data_service._intraday_cache = {}
    market_data_service._intraday_cache_expires_at = {}

    bars = market_data_service.get_intraday_bars("000001.SZ", interval="hourly", limit=2)

    assert bars == [
        {
            "trade_date": "2026-06-01 13:15",
            "open": 10.91,
            "high": 10.93,
            "low": 10.9,
            "close": 10.92,
            "volume": 39219.0,
            "amount": 0.0,
            "source": "tencent_m5_aggregated",
            "timestamp": "2026-06-01 13:15",
            "is_realtime": False,
        }
    ]


def test_tencent_quotes_are_requested_in_batches(monkeypatch) -> None:
    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    calls: list[list[str]] = []

    class FakeResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs):
        query = url.split("q=", 1)[1].split(",")
        calls.append(query)
        lines: list[str] = []
        for raw_symbol in query:
            code = raw_symbol[-6:]
            fields = [""] * 39
            fields[1] = code
            fields[2] = code
            fields[3] = "10.00"
            fields[4] = "9.80"
            fields[30] = "2026-05-29 15:00:00"
            fields[32] = "2.04"
            fields[37] = "1000"
            fields[38] = "1.1"
            lines.append(f'v_{raw_symbol}="' + "~".join(fields) + '";')
        return FakeResponse("".join(lines).encode("gbk"))

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.httpx, "get", fake_get)
    market_data_service._quote_cache = None

    symbols = [f"{index:06d}.SZ" for index in range(1, 122)]
    quotes = market_data_service.get_quotes(symbols)

    assert len(quotes) == 121
    assert {item["source"] for item in quotes} == {"tencent"}
    assert len(calls) == 3
    assert all(len(batch) <= 60 for batch in calls)


def test_realtime_quotes_fill_easy_tdx_gaps_with_tencent(monkeypatch) -> None:
    from app.services.market_data_service import market_data_service

    captured: dict[str, list[str]] = {}

    def fake_easy_tdx(symbols: list[str]):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 1.0,
                "amount": 1000,
                "turnover": 1.0,
                "volume_ratio": 1.0,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:00",
            }
        ]

    def fake_tencent(symbols: list[str]):
        captured["missing"] = symbols
        return [
            {
                "symbol": symbol,
                "name": symbol,
                "price": 9.0,
                "change_pct": 0.5,
                "amount": 900,
                "turnover": 0.8,
                "volume_ratio": 1.0,
                "source": "tencent",
                "timestamp": "2026-05-29 15:00:00",
            }
            for symbol in symbols
        ]

    monkeypatch.setattr(market_data_service, "_get_easy_tdx_quotes", fake_easy_tdx)
    monkeypatch.setattr(market_data_service, "_get_tencent_quotes", fake_tencent)
    market_data_service._quote_cache = None

    quotes = market_data_service.get_quotes(["000001.SZ", "000002.SZ", "000003.SZ"])

    assert [item["symbol"] for item in quotes] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert [item["source"] for item in quotes] == ["easy_tdx", "tencent", "tencent"]
    assert captured["missing"] == ["000002.SZ", "000003.SZ"]


def test_quote_cache_separates_realtime_and_low_frequency_modes(monkeypatch) -> None:
    from app.services.market_data_service import market_data_service

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_easy_tdx(symbols: list[str]):
        calls.append(("easy_tdx", tuple(symbols)))
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 1.0,
                "amount": 1000,
                "turnover": 1.0,
                "volume_ratio": 1.0,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:00",
            }
        ]

    def fake_tencent(symbols: list[str]):
        calls.append(("tencent", tuple(symbols)))
        return [
            {
                "symbol": symbol,
                "name": symbol,
                "price": 9.0,
                "change_pct": 0.5,
                "amount": 900,
                "turnover": 0.8,
                "volume_ratio": 1.0,
                "source": "tencent",
                "timestamp": "2026-05-29 15:00:00",
            }
            for symbol in symbols
        ]

    monkeypatch.setattr(market_data_service, "_get_easy_tdx_quotes", fake_easy_tdx)
    monkeypatch.setattr(market_data_service, "_get_tencent_quotes", fake_tencent)
    market_data_service._quote_cache = None

    realtime = market_data_service.get_quotes(["000001.SZ"], prefer_realtime=True)
    low_frequency = market_data_service.get_quotes(["000001.SZ"], prefer_realtime=False)

    assert realtime[0]["source"] == "easy_tdx"
    assert low_frequency[0]["source"] == "tencent"
    assert calls == [
        ("easy_tdx", ("000001.SZ",)),
        ("tencent", ("000001.SZ",)),
    ]


def test_tencent_quotes_fill_partial_batch_gaps_with_fallback(monkeypatch) -> None:
    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    class FakeResponse:
        content = (
            'v_sz000001="~平安银行~000001~10.00~9.80~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
            '2026-05-29 15:00:00~~2.04~~~~1000~1.1";'
        ).encode("gbk")

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: FakeResponse())
    market_data_service._quote_cache = None

    quotes = market_data_service.get_quotes(["000001.SZ", "000002.SZ", "000003.SZ"])

    assert [item["symbol"] for item in quotes] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert [item["source"] for item in quotes] == ["tencent", "fallback", "fallback"]


def test_realtime_quotes_fill_tencent_gaps_with_eastmoney_before_fallback(monkeypatch) -> None:
    from app.services.market_data_service import market_data_service

    captured: dict[str, list[str]] = {}

    def fake_easy_tdx(symbols: list[str]):
        return []

    def fake_tencent(symbols: list[str]):
        captured["tencent"] = symbols
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 1.0,
                "amount": 1000,
                "turnover": 1.0,
                "volume_ratio": 1.0,
                "source": "tencent",
                "timestamp": "2026-05-29 15:00:00",
            }
        ]

    def fake_eastmoney(symbols: list[str]):
        captured["eastmoney"] = symbols
        return [
            {
                "symbol": "000002.SZ",
                "name": "万科A",
                "price": 9.5,
                "change_pct": 0.8,
                "amount": 900,
                "turnover": 0.7,
                "volume_ratio": None,
                "source": "eastmoney",
                "timestamp": "2026-05-29 15:00:00",
            }
        ]

    monkeypatch.setattr(market_data_service, "_get_easy_tdx_quotes", fake_easy_tdx)
    monkeypatch.setattr(market_data_service, "_get_tencent_quotes", fake_tencent)
    monkeypatch.setattr(market_data_service, "_get_eastmoney_quotes", fake_eastmoney)
    monkeypatch.setattr(market_data_service, "_get_sina_quotes", lambda symbols: [])
    market_data_service._quote_cache = None

    quotes = market_data_service.get_quotes(["000001.SZ", "000002.SZ", "000003.SZ"])

    assert [item["symbol"] for item in quotes] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert [item["source"] for item in quotes] == ["tencent", "eastmoney", "fallback"]
    assert captured["tencent"] == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert captured["eastmoney"] == ["000002.SZ", "000003.SZ"]


def test_eastmoney_quotes_parse_push2_batches(monkeypatch) -> None:
    from app.services import market_data_service as module
    from app.services.market_data_service import market_data_service

    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    def fake_get(url: str, **kwargs):
        assert kwargs["follow_redirects"] is True
        secids = kwargs["params"]["secids"]
        calls.append(secids)
        diff = []
        for secid in secids.split(","):
            market, code = secid.split(".", 1)
            diff.append(
                {
                    "f12": code,
                    "f13": int(market),
                    "f14": code,
                    "f2": 10.5,
                    "f3": 1.23,
                    "f6": 123456789,
                    "f8": 0.88,
                    "f10": 1.15,
                }
            )
        return FakeResponse({"data": {"diff": diff}})

    monkeypatch.setattr(module.httpx, "get", fake_get)

    symbols = [f"{index:06d}.SZ" for index in range(1, 83)]
    quotes = market_data_service._get_eastmoney_quotes(symbols)

    assert len(quotes) == 82
    assert quotes[0]["symbol"] == "000001.SZ"
    assert quotes[0]["price"] == 10.5
    assert quotes[0]["source"] == "eastmoney"
    assert len(calls) == 2
    assert all(len(item.split(",")) <= 80 for item in calls)
