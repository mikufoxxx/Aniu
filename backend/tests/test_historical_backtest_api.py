from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import DailyBar
from app.main import create_app
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.services.aniu_service import aniu_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "historical.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    rate_limit_module._limiter.reset()
    aniu_service._account_overview_cache = None
    aniu_service._account_overview_cache_expires_at = None
    app = create_app()
    return TestClient(app)


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/aniu/login",
        json={"password": "release-pass"},
    )
    payload = response.json()
    return {"Authorization": f"Bearer {payload['token']}"}


def _reset_state() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_fetch_daily_rows_uses_tushare_http_protocol_with_timeout(monkeypatch) -> None:
    from app.services.historical_data_service import historical_data_service

    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setenv("TUSHARE_API_URL", "http://tushare-proxy.test")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "data": {
                    "fields": ["ts_code", "trade_date", "close", "amount"],
                    "items": [["600519.SH", "20260528", 1326.0, 10037388.288]],
                },
            }

    def fake_post(url, *, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.services.historical_data_service.httpx.post", fake_post)

    rows = historical_data_service.fetch_daily_rows("20260528")

    assert rows == [
        {
            "ts_code": "600519.SH",
            "trade_date": "20260528",
            "close": 1326.0,
            "amount": 10037388.288,
        }
    ]
    assert captured["url"] == "http://tushare-proxy.test"
    assert captured["timeout"] == 20.0
    assert captured["json"] == {
        "api_name": "daily",
        "token": "test-token",
        "params": {"trade_date": "20260528"},
        "fields": "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
    }

    _reset_state()


def test_refresh_daily_bars_stores_normalized_tushare_rows(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        assert trade_date == "20260528"
        assert symbols == ["600519.SH", "000001.SZ"]
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "open": 1270.0,
                "high": 1329.0,
                "low": 1270.0,
                "close": 1326.0,
                "pre_close": 1275.98,
                "pct_chg": 3.919,
                "vol": 76478,
                "amount": 10037388.288,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260528",
                "open": 10.65,
                "high": 10.93,
                "low": 10.62,
                "close": 10.93,
                "pre_close": 10.66,
                "pct_chg": 2.533,
                "vol": 1399367,
                "amount": 1515692.032,
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh",
            headers=headers,
            json={"trade_date": "20260528", "symbols": ["600519.SH", "000001.SZ"]},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["trade_date"] == "20260528"
        assert payload["stored_count"] == 2
        assert payload["source"] == "tushare"

        with session_scope() as db:
            bars = db.query(DailyBar).order_by(DailyBar.symbol).all()
            assert [bar.symbol for bar in bars] == ["000001.SZ", "600519.SH"]
            assert bars[0].close == 10.93
            assert bars[1].amount == 10037388.288

    _reset_state()


def test_refresh_daily_range_processes_each_date_and_summarizes_coverage(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    rows_by_date = {
        "20260526": [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260526",
                "open": 1200.0,
                "high": 1210.0,
                "low": 1190.0,
                "close": 1205.0,
                "pre_close": 1198.0,
                "pct_chg": 0.5843,
                "vol": 70000,
                "amount": 9000,
            }
        ],
        "20260527": [],
        "20260528": [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "open": 1270.0,
                "high": 1329.0,
                "low": 1270.0,
                "close": 1326.0,
                "pre_close": 1275.98,
                "pct_chg": 3.919,
                "vol": 76478,
                "amount": 10037388.288,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260528",
                "open": 10.65,
                "high": 10.93,
                "low": 10.62,
                "close": 10.93,
                "pre_close": 10.66,
                "pct_chg": 2.533,
                "vol": 1399367,
                "amount": 1515692.032,
            },
        ],
    }

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        assert symbols is None
        return rows_by_date[trade_date]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh-range",
            headers=headers,
            json={
                "start_date": "20260526",
                "end_date": "20260528",
                "symbols": None,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["start_date"] == "20260526"
        assert payload["end_date"] == "20260528"
        assert payload["processed_days"] == 3
        assert payload["stored_count"] == 3
        assert payload["unique_symbols"] == 2
        assert [item["stored_count"] for item in payload["daily_results"]] == [1, 0, 2]

        with session_scope() as db:
            bars = db.query(DailyBar).order_by(DailyBar.trade_date, DailyBar.symbol).all()
            assert [(bar.trade_date, bar.symbol) for bar in bars] == [
                ("20260526", "600519.SH"),
                ("20260528", "000001.SZ"),
                ("20260528", "600519.SH"),
            ]

    _reset_state()


def test_refresh_daily_range_skips_failed_dates(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        if trade_date == "20260527":
            raise RuntimeError("Tushare 日线请求超时")
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "close": 1200.0,
                "amount": 9000,
            }
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh-range",
            headers=headers,
            json={
                "start_date": "20260526",
                "end_date": "20260528",
                "symbols": None,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed_days"] == 3
    assert payload["stored_count"] == 2
    assert payload["skipped_count"] == 1
    assert [item["stored_count"] for item in payload["daily_results"]] == [1, 0, 1]
    assert "Tushare 日线请求超时" in payload["daily_results"][1]["error"]

    _reset_state()


def test_backtest_uses_stored_daily_bars_and_persists_metrics(monkeypatch, tmp_path) -> None:
    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260526", close=1200, amount=9000),
                    DailyBar(symbol="600519.SH", trade_date="20260527", close=1260, amount=9500),
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=1326, amount=10000),
                    DailyBar(symbol="000001.SZ", trade_date="20260526", close=10.0, amount=1000),
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=10.5, amount=1100),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10.2, amount=900),
                ]
            )

        response = client.post(
            "/api/aniu/quant/backtest",
            headers=headers,
            json={
                "symbols": ["600519.SH", "000001.SZ"],
                "start_date": "20260526",
                "end_date": "20260528",
                "initial_cash": 200000,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] > 0
    assert payload["strategy_name"] == "daily_momentum"
    assert payload["selected_symbol"] == "600519.SH"
    assert payload["trade_count"] == 2
    assert payload["final_assets"] > 200000
    assert payload["return_ratio"] > 0
    assert payload["metrics"]["bars_used"] == 6

    _reset_state()
