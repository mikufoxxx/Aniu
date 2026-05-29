from pathlib import Path
import sys
from datetime import datetime

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import rate_limit as rate_limit_module
from app.core.config import get_settings
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import DailyBar, MarketReport
from app.main import create_app
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.services.aniu_service import aniu_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "market_reports.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    rate_limit_module._limiter.reset()
    aniu_service._account_overview_cache = None
    aniu_service._account_overview_cache_expires_at = None
    return TestClient(create_app())


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/aniu/login", json={"password": "release-pass"})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _reset_state() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_market_report_generation_persists_structured_morning_report(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 1326.0,
                "change_pct": 3.9,
                "amount": 10_037_388_288,
                "turnover": 0.61,
                "volume_ratio": 1.42,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 09:25:00",
            },
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.93,
                "change_pct": 2.53,
                "amount": 1_515_692_032,
                "turnover": 0.72,
                "volume_ratio": 1.67,
                "source": "tencent",
                "timestamp": "2026-05-29 09:25:00",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260526", close=1200, amount=9000),
                    DailyBar(symbol="600519.SH", trade_date="20260527", close=1260, amount=9500),
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=1326, amount=10000),
                    DailyBar(symbol="000001.SZ", trade_date="20260526", close=10.0, amount=1000),
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=10.1, amount=1100),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10.0, amount=900),
                ]
            )

        response = client.post(
            "/api/aniu/market/reports",
            headers=headers,
            json={
                "report_type": "morning",
                "symbols": ["600519.SH", "000001.SZ"],
                "limit": 2,
                "lookback_days": 3,
            },
        )
        list_response = client.get(
            "/api/aniu/market/reports?report_type=morning",
            headers=headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] > 0
    assert payload["report_type"] == "morning"
    assert payload["title"] == "早盘推荐"
    assert payload["coverage"]["daily_history_symbols"] == 2
    assert {"easy_tdx", "tencent", "tushare_daily"} <= set(payload["data_sources"])
    assert payload["recommendations"][0]["symbol"] == "600519.SH"
    assert payload["recommendations"][0]["action"] == "WATCH"
    assert "AI量化市场上下文" in payload["context"]

    assert list_response.status_code == 200
    reports = list_response.json()["items"]
    assert len(reports) == 1
    assert reports[0]["id"] == payload["id"]
    assert reports[0]["recommendations"][0]["symbol"] == "600519.SH"

    _reset_state()


def test_market_report_uses_stored_universe_when_symbols_are_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_service import market_data_service

    captured_calls: list[list[str]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        captured_calls.append(symbols)
        return [
            {
                "symbol": symbol,
                "name": symbol,
                "price": 10.0,
                "change_pct": 1.0,
                "amount": 1000.0,
                "turnover": 1.0,
                "volume_ratio": 1.0,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 09:25:00",
            }
            for symbol in symbols
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900),
                    DailyBar(symbol="688001.SH", trade_date="20260527", close=80, amount=3000),
                    DailyBar(symbol="002001.SZ", trade_date="20260527", close=20, amount=1000),
                ]
            )

        response = client.post(
            "/api/aniu/market/reports",
            headers=headers,
            json={
                "report_type": "morning",
                "limit": 2,
                "lookback_days": 3,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert captured_calls[0] == ["688001.SH", "002001.SZ"]
    assert payload["recommendations"][0]["symbol"] == "688001.SH"
    assert payload["coverage"]["daily_history_symbols"] == 2

    _reset_state()


def test_market_report_performance_evaluates_recommendation_forward_returns(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 100.0,
                "change_pct": 1.0,
                "amount": 10_000_000,
                "turnover": 0.5,
                "volume_ratio": 1.2,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 09:25:00",
            },
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 0.5,
                "amount": 8_000_000,
                "turnover": 0.4,
                "volume_ratio": 1.1,
                "source": "tencent",
                "timestamp": "2026-05-29 09:25:00",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260527", close=90, amount=9000),
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=95, amount=9500),
                    DailyBar(symbol="600519.SH", trade_date="20260529", close=100, amount=10000),
                    DailyBar(symbol="600519.SH", trade_date="20260601", close=110, amount=11000),
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=9.5, amount=900),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=9.8, amount=950),
                    DailyBar(symbol="000001.SZ", trade_date="20260529", close=10, amount=1000),
                    DailyBar(symbol="000001.SZ", trade_date="20260601", close=9, amount=1100),
                ]
            )

        response = client.post(
            "/api/aniu/market/reports",
            headers=headers,
            json={
                "report_type": "morning",
                "symbols": ["600519.SH", "000001.SZ"],
                "limit": 2,
                "lookback_days": 3,
            },
        )
        report_id = response.json()["id"]

        with session_scope() as db:
            report = db.get(MarketReport, report_id)
            assert report is not None
            report.created_at = datetime(2026, 5, 29, 8, 45, 0)

        performance_response = client.get(
            f"/api/aniu/market/reports/{report_id}/performance?horizon_days=1",
            headers=headers,
        )

    assert performance_response.status_code == 200
    payload = performance_response.json()
    assert payload["report_id"] == report_id
    assert payload["horizon_days"] == 1
    assert payload["evaluated_count"] == 2
    assert payload["average_return_pct"] == 0
    assert payload["items"][0]["symbol"] == "600519.SH"
    assert payload["items"][0]["return_pct"] == 10
    assert payload["items"][1]["symbol"] == "000001.SZ"
    assert payload["items"][1]["return_pct"] == -10

    _reset_state()
