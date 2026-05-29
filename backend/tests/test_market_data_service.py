from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))


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
