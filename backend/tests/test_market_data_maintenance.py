from datetime import datetime, timedelta
from pathlib import Path
import sys
import threading
import time
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import rate_limit as rate_limit_module
from app.core.config import get_settings
from app.db.database import session_scope
from app.db.models import DailyBar
from app.db import database as database_module
from app.main import create_app
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.services.aniu_service import aniu_service
    from app.services.market_data_maintenance_service import market_data_maintenance_service

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
    market_data_maintenance_service.reset_runtime_state()
    app = create_app()
    return TestClient(app)


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/aniu/login", json={"password": "release-pass"})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _reset_state() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_market_data_defaults_maximize_monthly_allowance(monkeypatch) -> None:
    monkeypatch.delenv("MARKET_DATA_MAINTENANCE_LOOKBACK_DAYS", raising=False)
    monkeypatch.delenv("MARKET_DATA_MAINTENANCE_DATASET_LIMIT", raising=False)
    monkeypatch.delenv("AI_MARKET_CONTEXT_LIMIT", raising=False)
    monkeypatch.delenv("AI_MARKET_CONTEXT_LOOKBACK_DAYS", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.market_data_maintenance_lookback_days == 1825
    assert settings.market_data_maintenance_dataset_limit == 1000
    assert settings.ai_market_context_limit == 100
    assert settings.ai_market_context_lookback_days == 1825

    _reset_state()


def test_market_data_maintenance_initial_backfill_uses_full_configured_window(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.historical_data_service import historical_data_service
    from app.services.quant_service import quant_service

    captured: dict[str, object] = {}

    def fake_coverage(db):
        return {
            "latest_trade_date": None,
            "readiness": {"has_daily_history": False},
            "refresh_suggestion": {
                "needed": False,
                "start_date": None,
                "end_date": None,
                "reason": "暂无日线库存",
            },
        }

    def fake_refresh_range(
        db,
        *,
        start_date: str,
        end_date: str,
        symbols=None,
        progress_callback=None,
    ):
        captured["range"] = (start_date, end_date, symbols)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "source": "tushare",
            "processed_days": 1825,
            "stored_count": 200000,
            "skipped_count": 0,
            "unique_symbols": 5500,
            "requested_symbols": symbols or [],
            "daily_results": [],
        }

    def fake_refresh_profiles(db):
        captured["profiles"] = True
        return {
            "source": "tushare_stock_basic",
            "stored_count": 5300,
            "error": None,
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=20):
        captured["dataset"] = (symbols, limit, prefer_realtime, lookback_days)
        return {
            "universe_size": 5500,
            "item_count": 1000,
            "lookback_days": lookback_days,
            "data_sources": ["tushare_daily"],
            "coverage": {"daily_history_symbols": 1000},
            "items": [],
        }

    monkeypatch.setattr(historical_data_service, "summarize_daily_coverage", fake_coverage)
    monkeypatch.setattr(historical_data_service, "refresh_daily_range", fake_refresh_range)
    monkeypatch.setattr(
        historical_data_service,
        "refresh_stock_profiles",
        fake_refresh_profiles,
        raising=False,
    )
    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/maintenance/run",
            headers=headers,
            json={
                "end_date": "20260528",
                "lookback_days": 1825,
                "dataset_limit": 1000,
            },
        )

    expected_start = (
        datetime.strptime("20260528", "%Y%m%d").date() - timedelta(days=1824)
    ).strftime("%Y%m%d")
    assert response.status_code == 200
    assert captured["range"] == (expected_start, "20260528", None)
    assert captured["dataset"] == (None, 1000, True, 1825)

    _reset_state()


def test_market_data_maintenance_catches_up_after_latest_complete_date(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.historical_data_service import historical_data_service
    from app.services.quant_service import quant_service

    captured: dict[str, object] = {}

    def fake_coverage(db):
        return {
            "latest_trade_date": "20260526",
            "readiness": {"has_daily_history": True},
            "refresh_suggestion": {
                "needed": False,
                "start_date": None,
                "end_date": None,
                "reason": "最新交易日覆盖充足",
            },
        }

    def fake_refresh_range(
        db,
        *,
        start_date: str,
        end_date: str,
        symbols=None,
        progress_callback=None,
    ):
        captured["range"] = (start_date, end_date, symbols)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "source": "tushare",
            "processed_days": 2,
            "stored_count": 11000,
            "skipped_count": 0,
            "unique_symbols": 5500,
            "requested_symbols": symbols or [],
            "daily_results": [],
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=20):
        captured["dataset"] = (symbols, limit, prefer_realtime, lookback_days)
        return {
            "universe_size": 5500,
            "item_count": 1000,
            "lookback_days": lookback_days,
            "data_sources": ["tushare_daily"],
            "coverage": {"daily_history_symbols": 1000},
            "items": [],
        }

    monkeypatch.setattr(historical_data_service, "summarize_daily_coverage", fake_coverage)
    monkeypatch.setattr(historical_data_service, "refresh_daily_range", fake_refresh_range)
    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/maintenance/run",
            headers=headers,
            json={
                "end_date": "20260528",
                "lookback_days": 1825,
                "dataset_limit": 1000,
            },
        )

    assert response.status_code == 200
    assert captured["range"] == ("20260527", "20260528", None)
    assert captured["dataset"] == (None, 1000, True, 1825)

    _reset_state()


def test_market_data_maintenance_endpoint_refreshes_recent_range_and_dataset(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.historical_data_service import historical_data_service
    from app.services.market_report_service import market_report_service
    from app.services.quant_service import quant_service

    captured: dict[str, object] = {}

    def fake_refresh_range(
        db,
        *,
        start_date: str,
        end_date: str,
        symbols=None,
        progress_callback=None,
    ):
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
            "data_source_counts": {
                "tushare_daily": 300,
                "tushare_sector_member": 210000,
            },
            "data_source_errors": {
                "tushare_sector_member": ["20260528: 2 个板块成分拉取失败"],
            },
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

    def fake_refresh_profiles(db):
        captured["profiles"] = True
        return {
            "source": "tushare_stock_basic",
            "stored_count": 5300,
            "error": None,
        }

    def fake_refresh_financial_indicators(db, symbols):
        captured["financials"] = symbols
        return {
            "source": "tushare_fina_indicator",
            "stored_count": 2,
            "error": None,
            "requested_symbols": symbols,
        }

    monkeypatch.setattr(historical_data_service, "refresh_daily_range", fake_refresh_range)
    monkeypatch.setattr(
        historical_data_service,
        "refresh_stock_profiles",
        fake_refresh_profiles,
        raising=False,
    )
    monkeypatch.setattr(
        historical_data_service,
        "refresh_financial_indicators",
        fake_refresh_financial_indicators,
        raising=False,
    )
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
    assert payload["refresh"]["data_source_counts"]["tushare_sector_member"] == 210000
    assert payload["refresh"]["data_source_errors"] == {
        "tushare_sector_member": ["20260528: 2 个板块成分拉取失败"],
    }
    assert payload["profile_refresh"]["stored_count"] == 5300
    assert payload["financial_refresh"]["stored_count"] == 2
    assert payload["dataset"]["coverage"]["daily_history_symbols"] == 3
    assert payload["report"]["report_type"] == "closing"
    assert captured["range"] == ("20260526", "20260528", ["000001.SZ", "600519.SH"])
    assert captured["dataset"] == (["000001.SZ", "600519.SH"], 20, True, 3)
    assert captured["report"] == ("closing", ["000001.SZ", "600519.SH"], 20, 3)
    assert captured["profiles"] is True
    assert captured["financials"] == ["000001.SZ", "600519.SH"]

    _reset_state()


def test_market_data_maintenance_uses_coverage_gap_when_symbols_are_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    from app.db.database import session_scope
    from app.db.models import StockProfile
    from app.services.historical_data_service import historical_data_service
    from app.services.market_report_service import market_report_service
    from app.services.quant_service import quant_service

    captured: dict[str, object] = {}

    def fake_coverage(db):
        return {
            "refresh_suggestion": {
                "needed": True,
                "start_date": "20260520",
                "end_date": "20260528",
                "reason": "最新交易日覆盖不足",
            }
        }

    def fake_refresh_range(
        db,
        *,
        start_date: str,
        end_date: str,
        symbols=None,
        progress_callback=None,
    ):
        captured["range"] = (start_date, end_date, symbols)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "source": "tushare",
            "processed_days": 9,
            "stored_count": 900,
            "skipped_count": 0,
            "unique_symbols": 300,
            "requested_symbols": symbols or [],
            "daily_results": [],
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=20):
        captured["dataset"] = (symbols, limit, prefer_realtime, lookback_days)
        return {
            "universe_size": 500,
            "item_count": 50,
            "lookback_days": lookback_days,
            "data_sources": ["tushare_daily"],
            "coverage": {
                "realtime_symbols": 50,
                "daily_history_symbols": 50,
                "symbols_with_price": 50,
            },
            "items": [],
        }

    def fake_generate_report(db, *, report_type: str, symbols=None, limit=10, lookback_days=20):
        captured["report"] = (report_type, symbols, limit, lookback_days)
        return {
            "id": 8,
            "report_type": report_type,
            "title": "早盘推荐",
            "symbols": symbols or [],
            "lookback_days": lookback_days,
            "data_sources": ["tushare_daily"],
            "coverage": {"daily_history_symbols": 50},
            "recommendations": [],
            "dataset": {},
            "context": "context",
            "summary": "summary",
            "created_at": "2026-05-29T08:45:00",
        }

    def fake_refresh_financial_indicators(db, symbols):
        captured["financials"] = symbols
        return {
            "source": "tushare_fina_indicator",
            "stored_count": len(symbols),
            "error": None,
            "requested_symbols": symbols,
        }

    monkeypatch.setattr(historical_data_service, "summarize_daily_coverage", fake_coverage)
    monkeypatch.setattr(historical_data_service, "refresh_daily_range", fake_refresh_range)
    monkeypatch.setattr(
        historical_data_service,
        "refresh_financial_indicators",
        fake_refresh_financial_indicators,
        raising=False,
    )
    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)
    monkeypatch.setattr(market_report_service, "generate_report", fake_generate_report)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    StockProfile(symbol="000001.SZ", name="平安银行"),
                    StockProfile(symbol="600519.SH", name="贵州茅台"),
                ]
            )
        response = client.post(
            "/api/aniu/market/maintenance/run",
            headers=headers,
            json={
                "end_date": "20260528",
                "lookback_days": 120,
                "dataset_limit": 50,
                "report_type": "morning",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["refresh"]["start_date"] == "20260520"
    assert payload["financial_refresh"]["stored_count"] == 2
    assert captured["range"] == ("20260520", "20260528", None)
    assert captured["financials"] == ["000001.SZ", "600519.SH"]
    assert captured["dataset"] == (None, 50, True, 120)
    assert captured["report"] == ("morning", None, 50, 120)

    _reset_state()


def test_market_data_maintenance_history_records_quality_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.historical_data_service import historical_data_service
    from app.services.quant_service import quant_service

    def fake_refresh_range(
        db,
        *,
        start_date: str,
        end_date: str,
        symbols=None,
        progress_callback=None,
    ):
        return {
            "start_date": start_date,
            "end_date": end_date,
            "source": "tushare",
            "processed_days": 2,
            "stored_count": 20,
            "skipped_count": 1,
            "unique_symbols": 10,
            "requested_symbols": symbols or [],
            "daily_results": [],
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=20):
        return {
            "universe_size": 10,
            "item_count": 5,
            "lookback_days": lookback_days,
            "data_sources": ["tencent", "tushare_daily"],
            "coverage": {
                "realtime_symbols": 5,
                "daily_history_symbols": 5,
                "symbols_with_price": 5,
            },
            "items": [],
        }

    def fake_coverage(db):
        return {
            "total_rows": 200,
            "unique_symbols": 10,
            "first_trade_date": "20260520",
            "latest_trade_date": "20260528",
            "latest_trade_date_symbols": 10,
            "most_complete_trade_date": "20260528",
            "most_complete_trade_date_symbols": 10,
            "recent_trade_dates": [],
            "source_counts": [{"source": "tushare", "row_count": 200}],
            "refresh_suggestion": {
                "needed": False,
                "start_date": None,
                "end_date": None,
                "reason": "最新交易日覆盖充足",
            },
            "readiness": {
                "has_daily_history": True,
                "has_broad_universe": False,
                "latest_day_complete": True,
                "backtest_ready": True,
            },
        }

    monkeypatch.setattr(historical_data_service, "refresh_daily_range", fake_refresh_range)
    monkeypatch.setattr(historical_data_service, "summarize_daily_coverage", fake_coverage)
    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        run_response = client.post(
            "/api/aniu/market/maintenance/run",
            headers=headers,
            json={
                "end_date": "20260528",
                "lookback_days": 2,
                "symbols": ["000001.SZ"],
                "dataset_limit": 5,
            },
        )
        history_response = client.get(
            "/api/aniu/market/maintenance/runs?limit=5",
            headers=headers,
        )

    assert run_response.status_code == 200
    assert history_response.status_code == 200
    items = history_response.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "completed"
    assert items[0]["refresh_start_date"] == "20260527"
    assert items[0]["refresh_end_date"] == "20260528"
    assert items[0]["stored_count"] == 20
    assert items[0]["dataset_item_count"] == 5
    assert items[0]["latest_trade_date"] == "20260528"
    assert items[0]["latest_trade_date_symbols"] == 10
    assert items[0]["refresh_needed"] is False
    assert items[0]["coverage"]["total_rows"] == 200

    _reset_state()


def test_market_data_maintenance_job_starts_and_can_be_polled(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_maintenance_service import market_data_maintenance_service

    started = threading.Event()
    release = threading.Event()

    def fake_run(db, **kwargs):
        started.set()
        release.wait(timeout=2)
        return {
            "status": "completed",
            "refresh": {
                "start_date": "20260526",
                "end_date": "20260528",
                "source": "tushare",
                "processed_days": 3,
                "stored_count": 300,
                "skipped_count": 0,
                "unique_symbols": 100,
                "requested_symbols": [],
                "daily_results": [],
            },
            "dataset": {
                "universe_size": 500,
                "item_count": 200,
                "lookback_days": 120,
                "data_sources": ["tushare_daily"],
                "coverage": {"daily_history_symbols": 200, "realtime_symbols": 200},
                "items": [],
            },
            "report": None,
        }

    monkeypatch.setattr(market_data_maintenance_service, "run_now", fake_run)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        created = client.post(
            "/api/aniu/market/maintenance/jobs",
            headers=headers,
            json={"end_date": "20260528", "lookback_days": 3, "dataset_limit": 20},
        )

        assert created.status_code == 200
        payload = created.json()
        assert payload["status"] in {"queued", "running"}
        assert payload["job_id"]
        assert started.wait(timeout=2)

        running = client.get(
            f"/api/aniu/market/maintenance/jobs/{payload['job_id']}",
            headers=headers,
        )
        assert running.status_code == 200
        assert running.json()["status"] == "running"

        release.set()
        deadline = time.time() + 2
        completed_payload = None
        while time.time() < deadline:
            completed = client.get(
                f"/api/aniu/market/maintenance/jobs/{payload['job_id']}",
                headers=headers,
            )
            completed_payload = completed.json()
            if completed_payload["status"] == "completed":
                break
            time.sleep(0.05)

    assert completed_payload is not None
    assert completed_payload["status"] == "completed"
    assert completed_payload["result"]["refresh"]["stored_count"] == 300

    _reset_state()


def test_market_data_maintenance_job_reuses_running_job(monkeypatch, tmp_path) -> None:
    from app.services.market_data_maintenance_service import market_data_maintenance_service

    release = threading.Event()
    calls = 0

    def fake_run(db, **kwargs):
        nonlocal calls
        calls += 1
        release.wait(timeout=2)
        return {"status": "completed"}

    monkeypatch.setattr(market_data_maintenance_service, "run_now", fake_run)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        first = client.post(
            "/api/aniu/market/maintenance/jobs",
            headers=headers,
            json={"end_date": "20260528"},
        )
        second = client.post(
            "/api/aniu/market/maintenance/jobs",
            headers=headers,
            json={"end_date": "20260528"},
        )
        release.set()

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["status"] in {"queued", "running"}
    assert calls == 1

    _reset_state()


def test_market_data_maintenance_job_exposes_progress_updates(monkeypatch, tmp_path) -> None:
    from app.services.market_data_maintenance_service import market_data_maintenance_service

    progress_sent = threading.Event()
    release = threading.Event()

    def fake_run(db, **kwargs):
        kwargs["progress_callback"](
            {
                "phase": "refreshing_daily",
                "total_days": 3,
                "processed_days": 1,
                "current_trade_date": "20260526",
                "stored_count": 100,
                "skipped_count": 0,
                "error_count": 0,
            }
        )
        progress_sent.set()
        release.wait(timeout=2)
        return {"status": "completed"}

    monkeypatch.setattr(market_data_maintenance_service, "run_now", fake_run)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        created = client.post(
            "/api/aniu/market/maintenance/jobs",
            headers=headers,
            json={"end_date": "20260528"},
        )
        assert created.status_code == 200
        assert progress_sent.wait(timeout=2)

        running = client.get(
            f"/api/aniu/market/maintenance/jobs/{created.json()['job_id']}",
            headers=headers,
        )
        release.set()

    assert running.status_code == 200
    payload = running.json()
    assert payload["status"] == "running"
    assert payload["progress"] == {
        "phase": "refreshing_daily",
        "total_days": 3,
        "processed_days": 1,
        "current_trade_date": "20260526",
        "stored_count": 100,
        "skipped_count": 0,
        "error_count": 0,
    }

    _reset_state()


def test_market_data_coverage_endpoint_summarizes_daily_inventory(monkeypatch, tmp_path) -> None:
    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=10, source="tushare"),
                    DailyBar(symbol="600519.SH", trade_date="20260527", close=1000, source="tushare"),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=11, source="tushare"),
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=1005, source="tushare"),
                    DailyBar(symbol="300750.SZ", trade_date="20260528", close=210, source="tushare"),
                    DailyBar(symbol="000001.SZ", trade_date="20260529", close=12, source="tushare"),
                ]
            )

        response = client.get("/api/aniu/market/data/coverage", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 6
    assert payload["unique_symbols"] == 3
    assert payload["first_trade_date"] == "20260527"
    assert payload["latest_trade_date"] == "20260529"
    assert payload["latest_trade_date_symbols"] == 1
    assert payload["most_complete_trade_date"] == "20260528"
    assert payload["most_complete_trade_date_symbols"] == 3
    assert payload["recent_trade_dates"] == [
        {"trade_date": "20260529", "symbol_count": 1, "row_count": 1},
        {"trade_date": "20260528", "symbol_count": 3, "row_count": 3},
        {"trade_date": "20260527", "symbol_count": 2, "row_count": 2},
    ]
    assert payload["source_counts"] == [{"source": "tushare", "row_count": 6}]
    assert payload["refresh_suggestion"] == {
        "needed": True,
        "start_date": "20260529",
        "end_date": "20260529",
        "reason": "最新交易日覆盖不足",
    }
    assert payload["readiness"] == {
        "has_daily_history": True,
        "has_broad_universe": False,
        "latest_day_complete": False,
        "backtest_ready": True,
    }

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
