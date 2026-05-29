from pathlib import Path
import json
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import (
    AppSettings,
    ArenaAccount,
    ArenaPosition,
    DailyBar,
)
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


def test_ai_stock_picker_builds_autonomous_snapshot_from_stored_universe(
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
                "price": 10.0 + index,
                "change_pct": 3.0 - index,
                "amount": 10_000_000 - index * 100_000,
                "turnover": 0.5 + index * 0.1,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:30:03",
            }
            for index, symbol in enumerate(symbols)
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=3000),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=2000),
                    DailyBar(symbol="300750.SZ", trade_date="20260528", close=200, amount=1000),
                ]
            )
        response = client.post(
            "/api/aniu/ai/picks",
            headers=headers,
            json={"limit": 3, "lookback_days": 20, "prefer_realtime": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot_id"].startswith("ai-picks-")
    assert payload["selection_mode"] == "auto_universe"
    assert captured["symbols"] == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert payload["dataset"]["universe_size"] == 3
    assert payload["dataset"]["item_count"] == 3
    assert len(payload["recommendations"]) == 3
    assert {"easy_tdx", "tushare_daily"} <= set(payload["data_sources"])
    assert payload["coverage"]["latest_trade_date"] == "20260528"
    assert payload["coverage"]["latest_trade_date_symbols"] == 3
    assert "AI量化市场上下文" in payload["context"]
    assert "600519.SH" in payload["context"]

    _reset_state()


def test_quant_dataset_uses_most_complete_stored_trade_date(monkeypatch, tmp_path) -> None:
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
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900),
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=10, amount=500),
                    DailyBar(symbol="300750.SZ", trade_date="20260527", close=50, amount=1200),
                ]
            )

        response = client.post(
            "/api/aniu/quant/dataset",
            headers=headers,
            json={"limit": 2, "lookback_days": 3, "prefer_realtime": True},
        )

    assert response.status_code == 200
    assert captured["symbols"] == ["300750.SZ", "000001.SZ"]

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


def test_arena_run_reuses_autonomous_stock_pick_snapshot(
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
                "price": 10.0 + index,
                "change_pct": 4.0 - index,
                "amount": 10_000_000 - index * 100_000,
                "turnover": 0.6,
                "volume_ratio": 1.4,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
            for index, symbol in enumerate(symbols)
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=3000),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=2000),
                    DailyBar(symbol="300750.SZ", trade_date="20260528", close=200, amount=1000),
                ]
            )
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "initial_cash": 200000,
                "agents": [
                    {"id": "deepseek", "name": "DeepSeek", "style": "momentum"},
                    {"id": "gpt", "name": "GPT", "style": "balanced"},
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    snapshot = payload["stock_pick_snapshot"]
    assert snapshot["snapshot_id"].startswith("ai-picks-")
    assert snapshot["selection_mode"] == "auto_universe"
    assert captured["symbols"] == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert payload["candidate_count"] == snapshot["dataset"]["item_count"]
    assert [item["symbol"] for item in payload["candidates"]] == [
        item["symbol"] for item in snapshot["recommendations"]
    ]
    for order in payload["orders"]:
        context = order["decision_context"]
        assert context["stock_pick_snapshot_id"] == snapshot["snapshot_id"]
        assert context["selected_candidate"]["symbol"] in {
            item["symbol"] for item in snapshot["recommendations"]
        }

    _reset_state()


def test_arena_run_requests_maximized_stock_pick_snapshot(monkeypatch, tmp_path) -> None:
    from app.services.ai_stock_picker_service import ai_stock_picker_service

    captured: dict[str, object] = {}

    def candidate(symbol: str, score: float) -> dict[str, object]:
        return {
            "symbol": symbol,
            "name": symbol,
            "price": 10.0,
            "change_pct": 1.5,
            "amount": 10_000_000,
            "turnover": 0.8,
            "volume_ratio": 1.2,
            "source": "tencent",
            "timestamp": "2026-05-29 15:00:03",
            "score": score,
            "factor_scores": {},
            "daily_factors": {"bars_used": 120},
            "rationale": "测试候选",
        }

    def fake_build_snapshot(
        *,
        db,
        symbols=None,
        limit=50,
        prefer_realtime=True,
        lookback_days=120,
    ):
        captured["request"] = (symbols, limit, prefer_realtime, lookback_days)
        items = [candidate("600519.SH", 80), candidate("000001.SZ", 70)]
        return {
            "snapshot_id": "ai-picks-test",
            "selection_mode": "auto_universe",
            "data_sources": ["tencent", "tushare_daily"],
            "coverage": {},
            "dataset": {
                "universe_size": 1000,
                "item_count": len(items),
                "lookback_days": lookback_days,
                "data_sources": ["tencent", "tushare_daily"],
                "coverage": {"daily_history_symbols": len(items)},
                "items": items,
            },
            "recommendations": items,
            "context": "context",
            "context_length": 7,
        }

    monkeypatch.setattr(ai_stock_picker_service, "build_snapshot", fake_build_snapshot)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "initial_cash": 200000,
                "agents": [
                    {"id": "deepseek", "name": "DeepSeek", "style": "momentum"},
                    {"id": "gpt", "name": "GPT", "style": "balanced"},
                ],
            },
        )

    assert response.status_code == 200
    assert captured["request"] == (None, 1000, True, 1825)
    payload = response.json()
    assert payload["stock_pick_snapshot"]["dataset"]["lookback_days"] == 1825

    _reset_state()


def test_arena_orders_store_shared_snapshot_and_agent_decision_context(
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
                "change_pct": 3.0,
                "amount": 10_000_000,
                "turnover": 0.6,
                "volume_ratio": 1.4,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            },
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 1.0,
                "amount": 5_000_000,
                "turnover": 0.4,
                "volume_ratio": 1.1,
                "source": "tencent",
                "timestamp": "2026-05-29 15:00:03",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500),
                ]
            )
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "symbols": ["600519.SH", "000001.SZ"],
                "initial_cash": 200000,
                "agents": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "style": "momentum",
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "prompt": "偏动量突破",
                    },
                    {
                        "id": "gpt",
                        "name": "GPT",
                        "style": "risk_control",
                        "provider": "openai-compatible",
                        "model": "gpt-4o-mini",
                        "prompt": "偏回撤控制",
                    },
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orders"]) == 2
    snapshot_ids = {order["decision_context"]["snapshot_id"] for order in payload["orders"]}
    assert snapshot_ids == {f"arena-{payload['run_id']}"}
    for order in payload["orders"]:
        context = order["decision_context"]
        assert context["agent"]["id"] == order["agent_id"]
        assert context["agent"]["prompt"]
        assert context["data_sources"] == payload["data_sources"]
        assert context["selected_candidate"]["symbol"] == order["symbol"]
        assert "factor_scores" in context["selected_candidate"]
        assert "daily_factors" in context["selected_candidate"]

    _reset_state()


def test_arena_run_uses_llm_decision_for_each_agent_when_configured(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    calls: list[dict[str, object]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 100.0,
                "change_pct": 1.2,
                "amount": 8_000_000,
                "turnover": 0.4,
                "volume_ratio": 1.1,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            },
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 4.0,
                "amount": 20_000_000,
                "turnover": 1.2,
                "volume_ratio": 1.8,
                "source": "tencent",
                "timestamp": "2026-05-29 15:00:03",
            },
        ]

    def fake_call_llm(*, base_url, api_key, payload, timeout_seconds):
        calls.append(
            {
                "base_url": base_url,
                "api_key": api_key,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        symbol = "000001.SZ" if payload["model"] == "deepseek-chat" else "600519.SH"
        reason = "LLM 选择高弹性标的" if symbol == "000001.SZ" else "LLM 选择低波动核心资产"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "BUY",
                                "symbol": symbol,
                                "allocation_ratio": 0.1,
                                "reason": reason,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr(llm_service, "_call_llm", fake_call_llm)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            settings = db.query(AppSettings).first()
            assert settings is not None
            settings.provider_name = "openai-compatible"
            settings.llm_base_url = "https://llm.example/v1"
            settings.llm_api_key = "llm-key"
            settings.llm_model = "fallback-model"
            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500),
                ]
            )
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "symbols": ["600519.SH", "000001.SZ"],
                "initial_cash": 200000,
                "agents": [
                    {
                        "id": "deepseek",
                        "name": "DeepSeek",
                        "style": "momentum",
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "prompt": "偏动量突破",
                    },
                    {
                        "id": "gpt",
                        "name": "GPT",
                        "style": "risk_control",
                        "provider": "openai-compatible",
                        "model": "gpt-4o-mini",
                        "prompt": "偏回撤控制",
                    },
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [call["payload"]["model"] for call in calls] == [
        "deepseek-chat",
        "gpt-4o-mini",
    ]
    assert all(call["base_url"] == "https://llm.example/v1" for call in calls)
    assert all(call["api_key"] == "llm-key" for call in calls)
    user_prompts = [call["payload"]["messages"][1]["content"] for call in calls]
    assert all("arena-" in prompt for prompt in user_prompts)
    assert all("600519.SH" in prompt and "000001.SZ" in prompt for prompt in user_prompts)
    orders_by_agent = {order["agent_id"]: order for order in payload["orders"]}
    assert orders_by_agent["deepseek"]["symbol"] == "000001.SZ"
    assert orders_by_agent["gpt"]["symbol"] == "600519.SH"
    assert orders_by_agent["deepseek"]["quantity"] == 2000
    assert orders_by_agent["gpt"]["quantity"] == 200
    for order in payload["orders"]:
        context = order["decision_context"]
        assert context["llm_decision"]["used"] is True
        assert context["llm_decision"]["model"] == order["decision_context"]["agent"]["model"]
        assert context["llm_decision"]["raw_decision"]["action"] == "BUY"

    _reset_state()


def test_arena_run_resolves_llm_credentials_by_agent_provider(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    calls: list[dict[str, object]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 100.0,
                "change_pct": 2.0,
                "amount": 8_000_000,
                "turnover": 0.4,
                "volume_ratio": 1.1,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
        ]

    def fake_call_llm(*, base_url, api_key, payload, timeout_seconds):
        calls.append(
            {
                "base_url": base_url,
                "api_key": api_key,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "BUY",
                                "symbol": "600519.SH",
                                "allocation_ratio": 0.1,
                                "reason": f"{payload['model']} 独立判断买入。",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr(llm_service, "_call_llm", fake_call_llm)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            settings = db.query(AppSettings).first()
            assert settings is not None
            settings.llm_base_url = "https://global.example/v1"
            settings.llm_api_key = "global-key"
            settings.llm_model = "global-model"
            settings.llm_provider_configs = {
                "deepseek": {
                    "base_url": "https://deepseek.example/v1",
                    "api_key": "deepseek-key",
                    "default_model": "deepseek-chat",
                },
                "openai-compatible": {
                    "base_url": "https://openai.example/v1",
                    "api_key": "openai-key",
                    "default_model": "gpt-4o-mini",
                },
            }
            db.add(DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900))
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "symbols": ["600519.SH"],
                "initial_cash": 200000,
                "agents": [
                    {
                        "id": "deepseek_ai",
                        "name": "DeepSeek",
                        "style": "momentum",
                        "provider": "deepseek",
                        "model": "",
                        "prompt": "偏动量突破",
                    },
                    {
                        "id": "openai_ai",
                        "name": "OpenAI",
                        "style": "balanced",
                        "provider": "openai-compatible",
                        "model": "",
                        "prompt": "偏均衡",
                    },
                ],
            },
        )

    assert response.status_code == 200
    assert [
        (
            call["base_url"],
            call["api_key"],
            call["payload"]["model"],
        )
        for call in calls
    ] == [
        ("https://deepseek.example/v1", "deepseek-key", "deepseek-chat"),
        ("https://openai.example/v1", "openai-key", "gpt-4o-mini"),
    ]
    payload = response.json()
    contexts = [order["decision_context"] for order in payload["orders"]]
    assert [context["llm_decision"]["provider"] for context in contexts] == [
        "deepseek",
        "openai-compatible",
    ]
    assert [context["llm_decision"]["model"] for context in contexts] == [
        "deepseek-chat",
        "gpt-4o-mini",
    ]

    _reset_state()


def test_arena_run_uses_llm_sell_decision_for_existing_position(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": -1.0,
                "amount": 5_000_000,
                "turnover": 0.5,
                "volume_ratio": 0.9,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
        ]

    def fake_call_llm(*, base_url, api_key, payload, timeout_seconds):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "SELL",
                                "symbol": "000001.SZ",
                                "sell_ratio": 0.5,
                                "reason": "LLM 判断动能衰减，先卖出一半。",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr(llm_service, "_call_llm", fake_call_llm)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            settings = db.query(AppSettings).first()
            assert settings is not None
            settings.llm_base_url = "https://llm.example/v1"
            settings.llm_api_key = "llm-key"
            settings.llm_model = "fallback-model"
            account = ArenaAccount(
                agent_id="seller",
                agent_name="卖出 AI",
                style="risk_control",
                initial_cash=200000,
                cash=100000,
            )
            account.positions.append(
                ArenaPosition(
                    symbol="000001.SZ",
                    name="平安银行",
                    quantity=1000,
                    avg_cost=9.0,
                    last_price=10.0,
                )
            )
            db.add(account)
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500))
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "symbols": ["000001.SZ"],
                "initial_cash": 200000,
                "agents": [
                    {
                        "id": "seller",
                        "name": "卖出 AI",
                        "style": "risk_control",
                        "provider": "openai-compatible",
                        "model": "gpt-4o-mini",
                        "prompt": "弱势时主动减仓",
                    }
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["orders"]) == 1
    order = payload["orders"][0]
    assert order["action"] == "SELL"
    assert order["symbol"] == "000001.SZ"
    assert order["quantity"] == 500
    assert order["remaining_cash"] == 105000
    context = order["decision_context"]
    assert context["llm_decision"]["used"] is True
    assert context["llm_decision"]["raw_decision"]["action"] == "SELL"
    assert context["selected_candidate"]["symbol"] == "000001.SZ"

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
    assert (
        second["orders"][0]["decision_context"]["stock_pick_snapshot_id"]
        == second["stock_pick_snapshot"]["snapshot_id"]
    )
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
