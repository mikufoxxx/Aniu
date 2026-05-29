from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import rate_limit as rate_limit_module
from app.core.config import get_settings
from app.db import database as database_module
from app.db.database import init_db, session_scope
from app.db.models import DailyBar
from app.services.automation_session_service import automation_session_service
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def _use_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "ai_context.db"))
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    rate_limit_module._limiter.reset()
    init_db()


def _reset_state() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.main import create_app
    from app.services.aniu_service import aniu_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "ai_context_api.db"))
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


def test_ai_market_context_summarizes_unified_dataset(monkeypatch, tmp_path) -> None:
    from app.services.ai_market_context_service import ai_market_context_service
    from app.services.market_data_service import market_data_service

    _use_temp_db(monkeypatch, tmp_path)

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
                "timestamp": "2026-05-29 10:30:03",
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
                "timestamp": "2026-05-29 10:30:03",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

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
        db.flush()
        context = ai_market_context_service.build_context(
            db,
            symbols=["600519.SH", "000001.SZ"],
            limit=2,
            lookback_days=3,
        )

    assert "AI量化市场上下文" in context
    assert "数据源: easy_tdx, tencent, tushare_daily" in context
    assert "覆盖: 实时 2/2, 日线 2/2" in context
    assert "600519.SH 贵州茅台" in context
    assert "日线动量 +10.50%" in context
    assert "000001.SZ 平安银行" in context

    _reset_state()


def test_persistent_session_user_content_includes_prefetched_market_context() -> None:
    automation_session_service.configure_hooks(now_shanghai=lambda: __import__("datetime").datetime(2026, 5, 29, 9, 0, 0))
    content = automation_session_service.build_persistent_session_user_content(
        settings=object(),
        trigger_source="schedule",
        schedule_id=1,
        schedule_name="盘前分析",
        run_type="analysis",
        task_prompt="请给出早盘推荐。",
        prefetched_context="AI量化市场上下文\n- 600519.SH 贵州茅台",
    )

    assert "本轮任务:" in content
    assert "请给出早盘推荐。" in content
    assert "预取市场上下文:" in content
    assert "600519.SH 贵州茅台" in content


def test_ai_market_context_preview_endpoint(monkeypatch, tmp_path) -> None:
    from app.services.ai_market_context_service import ai_market_context_service

    monkeypatch.setattr(
        ai_market_context_service,
        "build_context",
        lambda db, **kwargs: "AI量化市场上下文\n- 000001.SZ 平安银行",
    )

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/ai-context",
            headers=headers,
            json={
                "symbols": ["000001.SZ"],
                "limit": 1,
                "lookback_days": 5,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"].startswith("AI量化市场上下文")
    assert payload["context_length"] == len(payload["context"])

    _reset_state()
