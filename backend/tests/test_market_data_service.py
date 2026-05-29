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
