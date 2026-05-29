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
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "quant_arena.db"))
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


def test_market_source_health_exposes_all_data_tiers(monkeypatch, tmp_path) -> None:
    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.get("/api/aniu/market/sources/health", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    source_ids = {item["id"] for item in payload["sources"]}
    assert {"tushare", "tencent", "eastmoney", "easy_tdx", "miaoxiang"} <= source_ids
    tiers = {item["tier"] for item in payload["sources"]}
    assert {"daily", "low_frequency", "quasi_high_frequency", "supplemental"} <= tiers
    assert payload["recommended_usage"]["daily"] == "Tushare"
    assert payload["recommended_usage"]["quasi_high_frequency"] == "easy-tdx"

    _reset_state()


def test_quant_candidates_rank_symbols_from_shared_snapshots(monkeypatch, tmp_path) -> None:
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
                "timestamp": "2026-05-29 15:00:03",
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
                "timestamp": "2026-05-29 15:00:03",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/quant/candidates",
            headers=headers,
            json={"symbols": ["600519.SH", "000001.SZ"], "limit": 2},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["universe_size"] == 2
    assert [item["symbol"] for item in payload["candidates"]] == [
        "600519.SH",
        "000001.SZ",
    ]
    assert payload["candidates"][0]["score"] > payload["candidates"][1]["score"]
    assert "amount" in payload["candidates"][0]["factor_scores"]
    assert payload["data_sources"] == ["easy_tdx", "tencent"]

    _reset_state()


def test_quant_dataset_combines_realtime_quotes_and_daily_history(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        assert prefer_realtime is True
        return [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 1326.0,
                "change_pct": 1.2,
                "amount": 10_037_388_288,
                "turnover": 0.61,
                "volume_ratio": 1.12,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:30:03",
            },
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.93,
                "change_pct": 2.8,
                "amount": 1_515_692_032,
                "turnover": 0.72,
                "volume_ratio": 1.67,
                "source": "tencent",
                "timestamp": "2026-05-29 10:30:03",
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
            "/api/aniu/quant/dataset",
            headers=headers,
            json={
                "symbols": ["600519.SH", "000001.SZ"],
                "limit": 2,
                "lookback_days": 3,
                "prefer_realtime": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["universe_size"] == 2
    assert payload["coverage"]["realtime_symbols"] == 2
    assert payload["coverage"]["daily_history_symbols"] == 2
    assert {"easy_tdx", "tencent", "tushare_daily"} <= set(payload["data_sources"])
    assert payload["items"][0]["symbol"] == "600519.SH"
    assert payload["items"][0]["daily_factors"]["latest_trade_date"] == "20260528"
    assert payload["items"][0]["daily_factors"]["bars_used"] == 3
    assert payload["items"][0]["daily_factors"]["momentum_pct"] > 10
    assert "daily_momentum" in payload["items"][0]["factor_scores"]

    _reset_state()


def test_quant_dataset_uses_stored_market_universe_when_symbols_are_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_service import market_data_service

    captured: dict[str, list[str]] = {}

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        captured["symbols"] = symbols
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
                "timestamp": "2026-05-29 10:30:03",
            }
            for symbol in symbols
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=300),
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900),
                    DailyBar(symbol="300750.SZ", trade_date="20260528", close=50, amount=600),
                    DailyBar(symbol="002594.SZ", trade_date="20260527", close=20, amount=5000),
                ]
            )

        response = client.post(
            "/api/aniu/quant/dataset",
            headers=headers,
            json={
                "limit": 3,
                "lookback_days": 3,
                "prefer_realtime": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["universe_size"] == 3
    assert captured["symbols"] == ["600519.SH", "300750.SZ", "000001.SZ"]
    assert [item["symbol"] for item in payload["items"]] == [
        "600519.SH",
        "300750.SZ",
        "000001.SZ",
    ]

    _reset_state()


def test_arena_run_keeps_each_ai_account_independent(monkeypatch, tmp_path) -> None:
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
                "timestamp": "2026-05-29 15:00:03",
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
                "timestamp": "2026-05-29 15:00:03",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "symbols": ["600519.SH", "000001.SZ"],
                "initial_cash": 200000,
                "agents": [
                    {"id": "deepseek", "name": "DeepSeek", "style": "momentum"},
                    {"id": "gpt", "name": "GPT", "style": "balanced"},
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] > 0
    assert payload["candidate_count"] == 2
    assert {item["agent_id"] for item in payload["leaderboard"]} == {"deepseek", "gpt"}
    assert all(item["total_assets"] > 0 for item in payload["leaderboard"])
    assert len(payload["orders"]) >= 2
    assert {item["agent_id"] for item in payload["orders"]} == {"deepseek", "gpt"}
    assert payload["orders"][0]["agent_id"] != payload["orders"][1]["agent_id"]
    assert payload["orders"][0]["remaining_cash"] != payload["orders"][1]["remaining_cash"]

    _reset_state()


def test_arena_accounts_persist_cash_and_positions_across_runs(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    call_count = {"value": 0}

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        call_count["value"] += 1
        price = 10.0 if call_count["value"] == 1 else 12.0
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": price,
                "change_pct": 3.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        payload = {
            "symbols": ["000001.SZ"],
            "initial_cash": 200000,
            "agents": [
                {"id": "deepseek", "name": "DeepSeek", "style": "momentum"},
            ],
        }
        first = client.post("/api/aniu/arena/run", headers=headers, json=payload).json()
        second = client.post("/api/aniu/arena/run", headers=headers, json=payload).json()
        leaderboard_response = client.get("/api/aniu/arena/leaderboard", headers=headers)

    assert first["leaderboard"][0]["cash"] == 110000
    assert second["leaderboard"][0]["cash"] < first["leaderboard"][0]["cash"]
    assert second["leaderboard"][0]["position_value"] > first["leaderboard"][0]["position_value"]
    assert leaderboard_response.status_code == 200
    leaderboard = leaderboard_response.json()
    assert leaderboard["items"][0]["agent_id"] == "deepseek"
    assert leaderboard["items"][0]["order_count"] == 2
    assert leaderboard["items"][0]["positions"][0]["symbol"] == "000001.SZ"
    assert leaderboard["items"][0]["positions"][0]["quantity"] > 9000

    _reset_state()


def test_arena_stop_loss_sells_position_and_records_realized_pnl(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    call_count = {"value": 0}

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        call_count["value"] += 1
        price = 10.0 if call_count["value"] == 1 else 9.0
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": price,
                "change_pct": -5.0 if price < 10 else 3.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        payload = {
            "symbols": ["000001.SZ"],
            "initial_cash": 200000,
            "agents": [
                {"id": "risk_agent", "name": "风控AI", "style": "risk_control"},
            ],
        }
        first = client.post("/api/aniu/arena/run", headers=headers, json=payload).json()
        second = client.post("/api/aniu/arena/run", headers=headers, json=payload).json()
        leaderboard = client.get("/api/aniu/arena/leaderboard", headers=headers).json()

    assert first["orders"][0]["action"] == "BUY"
    assert second["orders"][0]["action"] == "SELL"
    item = [row for row in leaderboard["items"] if row["agent_id"] == "risk_agent"][0]
    assert item["order_count"] == 2
    assert item["positions"] == []
    assert item["realized_pnl"] < 0
    assert item["cash"] < 200000

    _reset_state()


def test_arena_agents_can_be_saved_and_used_as_default_runner(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 3.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        save_response = client.put(
            "/api/aniu/arena/agents",
            headers=headers,
            json={
                "agents": [
                    {
                        "id": "deepseek_ai",
                        "name": "DeepSeek 量化",
                        "style": "momentum",
                        "provider": "openai-compatible",
                        "model": "deepseek-chat",
                        "enabled": True,
                        "prompt": "偏动量和量价确认。",
                    },
                    {
                        "id": "gpt_ai",
                        "name": "GPT 风控",
                        "style": "risk_control",
                        "provider": "openai-compatible",
                        "model": "gpt-4o-mini",
                        "enabled": False,
                        "prompt": "偏风险控制。",
                    },
                ]
            },
        )
        list_response = client.get("/api/aniu/arena/agents", headers=headers)
        run_response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={"symbols": ["000001.SZ"], "initial_cash": 200000},
        )

    assert save_response.status_code == 200
    assert [item["id"] for item in save_response.json()["agents"]] == [
        "deepseek_ai",
        "gpt_ai",
    ]
    assert list_response.status_code == 200
    assert list_response.json()["agents"][0]["model"] == "deepseek-chat"
    assert run_response.status_code == 200
    payload = run_response.json()
    assert [item["agent_id"] for item in payload["leaderboard"]] == ["deepseek_ai"]
    assert payload["orders"][0]["agent_name"] == "DeepSeek 量化"

    _reset_state()
