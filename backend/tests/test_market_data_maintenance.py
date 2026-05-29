from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import rate_limit as rate_limit_module
from app.core.config import get_settings
from app.db import database as database_module
from app.main import create_app
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.services.aniu_service import aniu_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "maintenance.db"))
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
    response = client.post("/api/aniu/login", json={"password": "release-pass"})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _reset_state() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_market_data_maintenance_endpoint_refreshes_recent_range_and_dataset(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.historical_data_service import historical_data_service
    from app.services.market_report_service import market_report_service
    from app.services.quant_service import quant_service

    captured: dict[str, object] = {}

    def fake_refresh_range(db, *, start_date: str, end_date: str, symbols=None):
        captured["range"] = (start_date, end_date, symbols)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "source": "tushare",
            "processed_days": 3,
            "stored_count": 300,
            "skipped_count": 0,
            "unique_symbols": 100,
            "requested_symbols": symbols or [],
            "daily_results": [],
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=20):
        captured["dataset"] = (symbols, limit, prefer_realtime, lookback_days)
        return {
            "universe_size": 3,
            "item_count": 3,
            "lookback_days": lookback_days,
            "data_sources": ["easy_tdx", "tushare_daily"],
            "coverage": {
                "realtime_symbols": 3,
                "daily_history_symbols": 3,
                "symbols_with_price": 3,
            },
            "items": [],
        }

    def fake_generate_report(db, *, report_type: str, symbols=None, limit=10, lookback_days=20):
        captured["report"] = (report_type, symbols, limit, lookback_days)
        return {
            "id": 7,
            "report_type": report_type,
            "title": "收盘分析",
            "symbols": symbols or [],
            "lookback_days": lookback_days,
            "data_sources": ["easy_tdx", "tushare_daily"],
            "coverage": {"realtime_symbols": 3, "daily_history_symbols": 3},
            "recommendations": [],
            "dataset": {},
            "context": "context",
            "summary": "summary",
            "created_at": "2026-05-29T15:30:00",
        }

    monkeypatch.setattr(historical_data_service, "refresh_daily_range", fake_refresh_range)
    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)
    monkeypatch.setattr(market_report_service, "generate_report", fake_generate_report)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/maintenance/run",
            headers=headers,
            json={
                "end_date": "20260528",
                "lookback_days": 3,
                "symbols": ["000001.SZ", "600519.SH"],
                "dataset_limit": 20,
                "report_type": "closing",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refresh"]["start_date"] == "20260526"
    assert payload["refresh"]["stored_count"] == 300
    assert payload["dataset"]["coverage"]["daily_history_symbols"] == 3
    assert payload["report"]["report_type"] == "closing"
    assert captured["range"] == ("20260526", "20260528", ["000001.SZ", "600519.SH"])
    assert captured["dataset"] == (["000001.SZ", "600519.SH"], 20, True, 3)
    assert captured["report"] == ("closing", ["000001.SZ", "600519.SH"], 20, 3)

    _reset_state()


def test_market_data_maintenance_process_due_jobs_runs_once_per_slot(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_maintenance_service import market_data_maintenance_service

    monkeypatch.setenv("MARKET_DATA_MAINTENANCE_ENABLED", "true")
    monkeypatch.setenv("MARKET_DATA_MAINTENANCE_TIMES", "08:45,15:30")
    monkeypatch.setenv("MARKET_DATA_MAINTENANCE_LOOKBACK_DAYS", "5")
    monkeypatch.setenv("MARKET_DATA_MAINTENANCE_DATASET_LIMIT", "30")
    get_settings.cache_clear()

    calls: list[dict[str, object]] = []

    def fake_run(db, **kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(market_data_maintenance_service, "run_now", fake_run)
    market_data_maintenance_service.reset_runtime_state()
    now = datetime(2026, 5, 29, 15, 31, tzinfo=ZoneInfo("Asia/Shanghai"))

    with create_test_client(monkeypatch, tmp_path):
        first = market_data_maintenance_service.process_due_jobs(now=now)
        second = market_data_maintenance_service.process_due_jobs(now=now)

    assert first["status"] == "completed"
    assert second["status"] == "skipped"
    assert len(calls) == 1
    assert calls[0]["lookback_days"] == 5
    assert calls[0]["dataset_limit"] == 30
    assert calls[0]["report_type"] == "closing"

    market_data_maintenance_service.reset_runtime_state()
    _reset_state()
