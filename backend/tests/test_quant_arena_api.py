from pathlib import Path
import json
import sys

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import (
    AppSettings,
    ArenaAccount,
    ArenaAgentMemory,
    ArenaOrder,
    ArenaRun,
    ArenaPosition,
    DailyBar,
    SectorBar,
    SectorMember,
    StockProfile,
)
from app.main import create_app
from app.services.scheduler_service import scheduler_service
from app.services.trading_calendar_service import trading_calendar_service


def create_test_client(monkeypatch, tmp_path) -> TestClient:
    from app.services.aniu_service import aniu_service
    from app.services.arena_service import arena_service

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
    arena_service._order_forecast_cache.clear()
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
            from app.db.models import (
                BlockTrade,
                DragonTigerInstitution,
                DragonTigerList,
                FinancialIndicator,
                LimitEvent,
                MarginDetail,
                PledgeStat,
                ShareholderNumber,
                ShareholderTrade,
            )

            db.add_all(
                [
                    DailyBar(symbol="600519.SH", trade_date="20260526", close=1200, amount=9000),
                    DailyBar(symbol="600519.SH", trade_date="20260527", close=1260, amount=9500),
                    DailyBar(
                        symbol="600519.SH",
                        trade_date="20260528",
                        close=1326,
                        amount=10000,
                        turnover_rate=0.72,
                        volume_ratio=1.34,
                        pe_ttm=23.5,
                        pb=7.8,
                        total_mv=166500000.0,
                        circ_mv=166500000.0,
                        moneyflow_net_amount=8123.4,
                        moneyflow_net_d5_amount=15231.5,
                        moneyflow_buy_lg_amount=5100.0,
                        moneyflow_buy_lg_amount_rate=6.2,
                        moneyflow_buy_md_amount=1800.0,
                        moneyflow_buy_md_amount_rate=2.1,
                        moneyflow_buy_sm_amount=-900.0,
                        moneyflow_buy_sm_amount_rate=-1.1,
                    ),
                    DailyBar(symbol="000001.SZ", trade_date="20260526", close=10.0, amount=1000),
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=10.1, amount=1100),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=10.0, amount=900),
                    SectorBar(
                        symbol="885001.TI",
                        name="白酒概念",
                        sector_type="N",
                        trade_date="20260528",
                        pct_chg=3.21,
                        turnover_rate=2.4,
                    ),
                    SectorMember(
                        sector_symbol="885001.TI",
                        sector_name="白酒概念",
                        sector_type="N",
                        stock_symbol="600519.SH",
                        stock_name="贵州茅台",
                    ),
                    StockProfile(
                        symbol="600519.SH",
                        name="贵州茅台",
                        area="贵州",
                        industry="白酒",
                        market="主板",
                        exchange="SSE",
                        list_status="L",
                        list_date="20010827",
                    ),
                    FinancialIndicator(
                        symbol="600519.SH",
                        ann_date="20260402",
                        end_date="20251231",
                        roe=31.2,
                        grossprofit_margin=91.2,
                        netprofit_margin=52.3,
                        netprofit_yoy=18.5,
                        or_yoy=15.6,
                        debt_to_assets=18.0,
                        assets_turn=0.48,
                        current_ratio=4.2,
                    ),
                    LimitEvent(
                        symbol="600519.SH",
                        trade_date="20260528",
                        limit_type="U",
                        name="贵州茅台",
                        industry="白酒",
                        close=1326,
                        pct_chg=10.0,
                        open_times=1,
                        up_stat="2/3",
                        limit_times=2,
                        fd_amount=120000000,
                    ),
                    MarginDetail(
                        symbol="600519.SH",
                        trade_date="20260528",
                        name="贵州茅台",
                        rzye=3_200_000_000,
                        rqye=42_000_000,
                        rzmre=180_000_000,
                        rqyl=120_000,
                        rzche=90_000_000,
                        rqchl=15_000,
                        rqmcl=22_000,
                        rzrqye=3_242_000_000,
                    ),
                    DragonTigerList(
                        symbol="600519.SH",
                        trade_date="20260528",
                        name="贵州茅台",
                        close=1326,
                        pct_change=7.1,
                        turnover_rate=8.2,
                        amount=2_000_000_000,
                        l_sell=120_000_000,
                        l_buy=210_000_000,
                        l_amount=330_000_000,
                        net_amount=90_000_000,
                        net_rate=4.5,
                        amount_rate=16.5,
                        float_values=1_500_000_000_000,
                        reason="涨幅偏离值达7%的证券",
                    ),
                    DragonTigerInstitution(
                        symbol="600519.SH",
                        trade_date="20260528",
                        exalter="机构专用",
                        side="0",
                        buy=60_000_000,
                        sell=10_000_000,
                        net_buy=50_000_000,
                        reason="涨幅偏离值达7%的证券",
                    ),
                    BlockTrade(
                        symbol="600519.SH",
                        trade_date="20260528",
                        price=1320.0,
                        vol=140.0,
                        amount=184_800.0,
                        buyer="机构专用",
                        seller="中信证券总部",
                    ),
                    BlockTrade(
                        symbol="600519.SH",
                        trade_date="20260528",
                        price=1350.0,
                        vol=60.0,
                        amount=81_000.0,
                        buyer="华泰证券上海营业部",
                        seller="机构专用",
                    ),
                    ShareholderNumber(
                        symbol="600519.SH",
                        ann_date="20260528",
                        end_date="20260331",
                        holder_num=88000,
                    ),
                    ShareholderNumber(
                        symbol="600519.SH",
                        ann_date="20260428",
                        end_date="20251231",
                        holder_num=96000,
                    ),
                    ShareholderTrade(
                        symbol="600519.SH",
                        ann_date="20260528",
                        holder_name="贵州国资公司",
                        holder_type="C",
                        in_de="IN",
                        change_vol=120.0,
                        change_ratio=0.18,
                        after_share=5000.0,
                        after_ratio=4.1,
                        avg_price=1320.0,
                        total_share=5000.0,
                        begin_date="20260501",
                        close_date="20260528",
                    ),
                    ShareholderTrade(
                        symbol="600519.SH",
                        ann_date="20260528",
                        holder_name="某高管",
                        holder_type="G",
                        in_de="DE",
                        change_vol=20.0,
                        change_ratio=0.03,
                        after_share=80.0,
                        after_ratio=0.06,
                        avg_price=1330.0,
                        total_share=80.0,
                        begin_date="20260510",
                        close_date="20260520",
                    ),
                    PledgeStat(
                        symbol="600519.SH",
                        end_date="20260528",
                        pledge_count=12,
                        unrest_pledge=1200.0,
                        rest_pledge=300.0,
                        total_share=125619.78,
                        pledge_ratio=3.2,
                    ),
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
    assert {
        "easy_tdx",
        "tencent",
        "tushare_daily",
        "tushare_moneyflow",
        "tushare_sector_member",
        "tushare_stock_basic",
        "tushare_fina_indicator",
        "tushare_limit_list_d",
        "tushare_margin_detail",
        "tushare_top_list",
        "tushare_top_inst",
        "tushare_block_trade",
        "tushare_stk_holdernumber",
        "tushare_stk_holdertrade",
        "tushare_pledge_stat",
    } <= set(payload["data_sources"])
    assert payload["items"][0]["symbol"] == "600519.SH"
    assert payload["items"][0]["daily_factors"]["latest_trade_date"] == "20260528"
    assert payload["items"][0]["daily_factors"]["bars_used"] == 3
    assert payload["items"][0]["daily_factors"]["momentum_pct"] > 10
    assert payload["items"][0]["daily_factors"]["turnover_rate"] == 0.72
    assert payload["items"][0]["daily_factors"]["volume_ratio"] == 1.34
    assert payload["items"][0]["daily_factors"]["pe_ttm"] == 23.5
    assert payload["items"][0]["daily_factors"]["pb"] == 7.8
    assert payload["items"][0]["daily_factors"]["total_mv"] == 166500000.0
    assert payload["items"][0]["daily_factors"]["moneyflow_net_amount"] == 8123.4
    assert payload["items"][0]["daily_factors"]["moneyflow_buy_lg_amount_rate"] == 6.2
    assert payload["items"][0]["daily_factors"]["sector_heat"][0]["name"] == "白酒概念"
    assert payload["items"][0]["daily_factors"]["sector_heat"][0]["pct_chg"] == 3.21
    assert payload["items"][0]["daily_factors"]["limit_event"]["limit_type"] == "U"
    assert payload["items"][0]["daily_factors"]["limit_event"]["limit_times"] == 2
    assert payload["items"][0]["daily_factors"]["margin_detail"]["rzmre"] == 180000000.0
    assert payload["items"][0]["daily_factors"]["margin_detail"]["rzche"] == 90000000.0
    assert payload["items"][0]["daily_factors"]["margin_detail"]["net_financing_buy"] == 90000000.0
    assert payload["items"][0]["daily_factors"]["dragon_tiger"]["net_amount"] == 90000000.0
    assert payload["items"][0]["daily_factors"]["dragon_tiger"]["institution_net_buy"] == 50000000.0
    assert payload["items"][0]["daily_factors"]["block_trade"]["trade_count"] == 2
    assert payload["items"][0]["daily_factors"]["block_trade"]["total_amount"] == 265800.0
    assert payload["items"][0]["daily_factors"]["block_trade"]["price_vs_close_pct"] > 0
    assert payload["items"][0]["daily_factors"]["shareholder_number"]["holder_num"] == 88000
    assert payload["items"][0]["daily_factors"]["shareholder_number"]["holder_num_change_pct"] < 0
    assert payload["items"][0]["daily_factors"]["shareholder_trade"]["net_change_vol"] == 100.0
    assert payload["items"][0]["daily_factors"]["shareholder_trade"]["net_change_ratio"] == 0.15
    assert payload["items"][0]["daily_factors"]["pledge_stat"]["pledge_ratio"] == 3.2
    assert payload["items"][0]["profile"]["industry"] == "白酒"
    assert payload["items"][0]["profile"]["area"] == "贵州"
    assert payload["coverage"]["profile_symbols"] == 1
    assert payload["coverage"]["financial_symbols"] == 1
    assert payload["coverage"]["limit_event_symbols"] == 1
    assert payload["coverage"]["margin_detail_symbols"] == 1
    assert payload["coverage"]["dragon_tiger_symbols"] == 1
    assert payload["coverage"]["block_trade_symbols"] == 1
    assert payload["coverage"]["shareholder_number_symbols"] == 1
    assert payload["coverage"]["shareholder_trade_symbols"] == 1
    assert payload["coverage"]["pledge_stat_symbols"] == 1
    assert payload["items"][0]["financial_factors"]["roe"] == 31.2
    assert payload["items"][0]["financial_factors"]["grossprofit_margin"] == 91.2
    assert payload["items"][0]["financial_factors"]["netprofit_yoy"] == 18.5
    assert payload["items"][0]["financial_factors"]["debt_to_assets"] == 18.0
    assert "daily_momentum" in payload["items"][0]["factor_scores"]
    assert "financial_quality" in payload["items"][0]["factor_scores"]
    assert "daily_turnover" in payload["items"][0]["factor_scores"]
    assert "valuation_sanity" in payload["items"][0]["factor_scores"]
    assert "moneyflow_net" in payload["items"][0]["factor_scores"]
    assert "moneyflow_large" in payload["items"][0]["factor_scores"]
    assert "sector_heat" in payload["items"][0]["factor_scores"]
    assert "limit_sentiment" in payload["items"][0]["factor_scores"]
    assert "margin_financing" in payload["items"][0]["factor_scores"]
    assert "dragon_tiger_flow" in payload["items"][0]["factor_scores"]
    assert "block_trade_flow" in payload["items"][0]["factor_scores"]
    assert "shareholder_structure" in payload["items"][0]["factor_scores"]
    assert "shareholder_trade" in payload["items"][0]["factor_scores"]
    assert "pledge_risk" in payload["items"][0]["factor_scores"]

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


def test_ai_stock_picker_reranks_with_temporal_retail_risk_context(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        quotes = {
            "000001.SZ": {
                "symbol": "000001.SZ",
                "name": "追高风险股",
                "price": 12.0,
                "change_pct": 8.0,
                "amount": 1_500_000_000,
                "turnover": 3.0,
                "volume_ratio": 2.4,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:30:03",
            },
            "600519.SH": {
                "symbol": "600519.SH",
                "name": "趋势稳健股",
                "price": 38.0,
                "change_pct": 1.6,
                "amount": 900_000_000,
                "turnover": 1.2,
                "volume_ratio": 1.4,
                "source": "tencent",
                "timestamp": "2026-05-29 10:30:03",
            },
        }
        return [quotes[symbol] for symbol in symbols]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            for index in range(60):
                trade_date = f"202604{index + 1:02d}" if index < 30 else f"202605{index - 29:02d}"
                db.add(
                    DailyBar(
                        symbol="000001.SZ",
                        trade_date=trade_date,
                        close=20 - index * 0.12,
                        amount=900,
                        turnover_rate=0.4,
                        volume_ratio=0.8,
                        moneyflow_net_amount=-2000,
                    )
                )
                db.add(
                    DailyBar(
                        symbol="600519.SH",
                        trade_date=trade_date,
                        close=28 + index * 0.16,
                        amount=1200,
                        turnover_rate=1.2,
                        volume_ratio=1.2,
                        pe_ttm=24,
                        pb=4,
                        moneyflow_net_amount=3000,
                        moneyflow_buy_lg_amount_rate=4,
                    )
                )

        response = client.post(
            "/api/aniu/ai/picks",
            headers=headers,
            json={
                "symbols": ["000001.SZ", "600519.SH"],
                "limit": 2,
                "lookback_days": 60,
                "prefer_realtime": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["symbol"] for item in payload["recommendations"]] == [
        "600519.SH",
        "000001.SZ",
    ]
    first = payload["recommendations"][0]
    second = payload["recommendations"][1]
    assert first["ai_selection"]["score"] > second["ai_selection"]["score"]
    assert first["ai_selection"]["temporal_profile"]["above_ma60"] is True
    assert second["ai_selection"]["temporal_profile"]["above_ma60"] is False
    assert "below_ma60" in second["ai_selection"]["risk_flags"]
    assert first["retail_analysis"]["decision"] in {"buy", "watch", "hold"}
    assert "ai_selection" in payload["context"]

    _reset_state()


def test_ai_stock_picker_uses_agent_specific_selection_plan(monkeypatch, tmp_path) -> None:
    from app.services.ai_stock_picker_service import ai_stock_picker_service
    from app.services.quant_service import quant_service

    def candidate(
        symbol: str,
        *,
        score: float,
        change_pct: float,
        recent_momentum: float,
        range_position: float,
        volatility: float,
    ) -> dict[str, object]:
        return {
            "symbol": symbol,
            "name": symbol,
            "price": 20.0,
            "change_pct": change_pct,
            "amount": 900_000_000,
            "turnover": 1.5,
            "volume_ratio": 1.8,
            "source": "easy_tdx",
            "timestamp": "2026-05-29 10:30:03",
            "score": score,
            "factor_scores": {},
            "profile": {},
            "financial_factors": {},
            "daily_factors": {
                "latest_trade_date": "20260528",
                "bars_used": 90,
                "latest_close": 20.0,
                "momentum_pct": recent_momentum * 2,
                "recent_momentum_pct": recent_momentum,
                "ma20": 18.0,
                "ma60": 16.0,
                "above_ma20": True,
                "above_ma60": True,
                "range_position_pct": range_position,
                "max_drawdown_pct": -6.0,
                "volatility_pct": volatility,
                "moneyflow_net_amount": 3000,
                "moneyflow_buy_lg_amount_rate": 4,
            },
            "rationale": "测试候选",
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=1825):
        items = [
            candidate(
                "000001.SZ",
                score=82,
                change_pct=8.0,
                recent_momentum=12.0,
                range_position=96.0,
                volatility=7.0,
            ),
            candidate(
                "600519.SH",
                score=74,
                change_pct=1.4,
                recent_momentum=3.0,
                range_position=72.0,
                volatility=2.0,
            ),
        ]
        return {
            "universe_size": len(items),
            "item_count": len(items),
            "lookback_days": lookback_days,
            "data_sources": ["easy_tdx", "tushare_daily", "tushare_moneyflow"],
            "coverage": {"daily_history_symbols": len(items)},
            "items": items,
        }

    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            momentum = ai_stock_picker_service.build_snapshot(
                db,
                agent={"id": "m1", "name": "动量", "style": "momentum"},
                limit=2,
                lookback_days=1825,
            )
            risk_control = ai_stock_picker_service.build_snapshot(
                db,
                agent={"id": "r1", "name": "风控", "style": "risk_control"},
                limit=2,
                lookback_days=1825,
            )

    assert momentum["selection_plan"]["strategy"] == "momentum"
    assert risk_control["selection_plan"]["strategy"] == "risk_control"
    assert momentum["recommendations"][0]["symbol"] == "000001.SZ"
    assert risk_control["recommendations"][0]["symbol"] == "600519.SH"
    assert "quote" in momentum["selection_plan"]["dimensions"]
    assert "pledge_risk" in risk_control["selection_plan"]["risk_checks"]
    assert momentum["recommendations"][0]["ai_selection"]["strategy"] == "momentum"
    assert risk_control["recommendations"][0]["ai_selection"]["strategy"] == "risk_control"
    momentum_request = momentum["recommendations"][0]["ai_selection"]["data_request"]
    risk_request = risk_control["recommendations"][0]["ai_selection"]["data_request"]
    assert "sector_heat" in momentum_request["requested_dimensions"]
    assert "daily_history" in momentum_request["available_dimensions"]
    assert "sector_heat" in momentum_request["missing_dimensions"]
    assert "pledge_stat" in risk_request["requested_dimensions"]
    assert "pledge_stat" in risk_request["missing_dimensions"]
    assert risk_request["checks"]["daily_history"]["status"] == "available"
    momentum_actions = {
        item["dimension"]: item
        for item in momentum_request["actions"]
    }
    risk_actions = {
        item["dimension"]: item
        for item in risk_request["actions"]
    }
    assert momentum_actions["sector_heat"]["source"] == "tushare_sector_member"
    assert momentum_actions["sector_heat"]["status"] == "missing"
    assert momentum_actions["sector_heat"]["refresh_endpoint"] == "/api/aniu/market/maintenance/run"
    assert momentum_actions["daily_history"]["temporal_window"]["lookback_days"] == 1825
    assert risk_actions["pledge_stat"]["source"] == "tushare_pledge_stat"
    assert risk_actions["pledge_stat"]["tables"] == ["pledge_stats"]

    _reset_state()


def test_ai_data_request_endpoint_refreshes_requested_dimensions(monkeypatch, tmp_path) -> None:
    from app.services.market_data_maintenance_service import market_data_maintenance_service
    from app.services.quant_service import quant_service

    captured: dict[str, object] = {}

    def fake_run_now(db, *, end_date=None, lookback_days=None, symbols=None, dataset_limit=None, report_type=None):
        captured["maintenance"] = {
            "end_date": end_date,
            "lookback_days": lookback_days,
            "symbols": symbols,
            "dataset_limit": dataset_limit,
            "report_type": report_type,
        }
        return {
            "status": "completed",
            "refresh": {"stored_count": 3},
            "dataset": {"item_count": 1},
        }

    def fake_build_dataset(db, *, symbols=None, limit=50, prefer_realtime=True, lookback_days=1825):
        captured["dataset"] = {
            "symbols": symbols,
            "limit": limit,
            "prefer_realtime": prefer_realtime,
            "lookback_days": lookback_days,
        }
        return {
            "universe_size": 1,
            "item_count": 1,
            "lookback_days": lookback_days,
            "data_sources": ["easy_tdx", "tushare_daily", "tushare_moneyflow"],
            "coverage": {"daily_history_symbols": 1},
            "items": [
                {
                    "symbol": "000001.SZ",
                    "name": "平安银行",
                    "price": 11.2,
                    "change_pct": 1.5,
                    "amount": 120000000,
                    "turnover": 0.8,
                    "volume_ratio": 1.1,
                    "source": "easy_tdx",
                    "timestamp": "2026-05-29 10:30:03",
                    "score": 70,
                    "factor_scores": {},
                    "profile": {},
                    "financial_factors": {},
                    "daily_factors": {"bars_used": 30, "moneyflow_net_amount": 1200},
                    "rationale": "测试数据",
                }
            ],
        }

    monkeypatch.setattr(market_data_maintenance_service, "run_now", fake_run_now)
    monkeypatch.setattr(quant_service, "build_dataset", fake_build_dataset)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/ai-data-request",
            headers=headers,
            json={
                "symbols": ["000001.SZ"],
                "dimensions": ["daily_history", "moneyflow"],
                "lookback_days": 30,
                "limit": 1,
                "refresh": True,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_symbols"] == ["000001.SZ"]
    assert payload["requested_dimensions"] == ["daily_history", "moneyflow"]
    assert payload["refresh"]["status"] == "completed"
    assert captured["maintenance"] == {
        "end_date": None,
        "lookback_days": 30,
        "symbols": ["000001.SZ"],
        "dataset_limit": 1,
        "report_type": None,
    }
    assert captured["dataset"] == {
        "symbols": ["000001.SZ"],
        "limit": 1,
        "prefer_realtime": True,
        "lookback_days": 30,
    }
    action_by_dimension = {item["dimension"]: item for item in payload["actions"]}
    assert action_by_dimension["daily_history"]["refresh_endpoint"] == "/api/aniu/market/daily/refresh-range"
    assert action_by_dimension["moneyflow"]["source"] == "tushare_moneyflow"
    assert action_by_dimension["moneyflow"]["temporal_window"]["lookback_days"] == 30
    assert payload["dataset"]["items"][0]["name"] == "平安银行"
    assert payload["context_length"] > 0

    _reset_state()


def test_quant_research_compares_strategies_for_ai_learning(monkeypatch, tmp_path) -> None:
    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="000001.SZ", trade_date="20260101", close=10, amount=1000),
                    DailyBar(symbol="000001.SZ", trade_date="20260102", close=11, amount=1100),
                    DailyBar(symbol="000001.SZ", trade_date="20260103", close=12, amount=1200),
                    DailyBar(symbol="600519.SH", trade_date="20260101", close=100, amount=2000),
                    DailyBar(symbol="600519.SH", trade_date="20260102", close=99, amount=2000),
                    DailyBar(symbol="600519.SH", trade_date="20260103", close=105, amount=2100),
                    DailyBar(symbol="300750.SZ", trade_date="20260101", close=80, amount=1500),
                    DailyBar(symbol="300750.SZ", trade_date="20260102", close=70, amount=1500),
                    DailyBar(symbol="300750.SZ", trade_date="20260103", close=76, amount=1500),
                ]
            )
        response = client.post(
            "/api/aniu/quant/research",
            headers=headers,
            json={
                "symbols": ["000001.SZ", "600519.SH", "300750.SZ"],
                "start_date": "20260101",
                "end_date": "20260103",
                "initial_cash": 200000,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol_count"] == 3
    assert payload["bar_count"] == 9
    assert [item["strategy_name"] for item in payload["strategies"]] == [
        "daily_momentum",
        "equal_weight",
        "low_drawdown",
    ]
    assert payload["best_strategy"]["strategy_name"] == "daily_momentum"
    assert payload["best_strategy"]["selected_symbols"] == ["000001.SZ"]
    assert payload["best_strategy"]["return_ratio"] > payload["strategies"][-1]["return_ratio"]
    assert payload["comparison_chart"] == [
        {
            "strategy_name": "daily_momentum",
            "display_name": "日线动量",
            "return_ratio": payload["strategies"][0]["return_ratio"],
            "max_drawdown": payload["strategies"][0]["max_drawdown"],
            "score": payload["strategies"][0]["score"],
        },
        {
            "strategy_name": "equal_weight",
            "display_name": "等权分散",
            "return_ratio": payload["strategies"][1]["return_ratio"],
            "max_drawdown": payload["strategies"][1]["max_drawdown"],
            "score": payload["strategies"][1]["score"],
        },
        {
            "strategy_name": "low_drawdown",
            "display_name": "低波动回撤",
            "return_ratio": payload["strategies"][2]["return_ratio"],
            "max_drawdown": payload["strategies"][2]["max_drawdown"],
            "score": payload["strategies"][2]["score"],
        },
    ]
    charts = payload["best_strategy"]["charts"]
    assert [point["close"] for point in charts["price_series"]] == [10.0, 11.0, 12.0]
    assert charts["trade_markers"] == [
        {"action": "BUY", "symbol": "000001.SZ", "trade_date": "20260101", "price": 10.0, "quantity": 20000},
        {"action": "SELL", "symbol": "000001.SZ", "trade_date": "20260103", "price": 12.0, "quantity": 20000},
    ]
    assert [point["trade_date"] for point in charts["equity_curve"]] == [
        "20260101",
        "20260102",
        "20260103",
    ]
    assert charts["equity_curve"][-1]["value"] == payload["best_strategy"]["final_assets"]
    assert charts["drawdown_curve"][0]["drawdown"] == 0.0
    assert len(charts["drawdown_curve"]) == 3
    assert set(charts["risk_metrics"]) >= {
        "total_return",
        "annual_return",
        "volatility",
        "sharpe",
        "sortino",
        "omega",
        "win_rate",
        "max_drawdown",
        "calmar",
    }
    assert charts["return_distribution"]
    assert {"daily", "weekly", "monthly", "hourly"} <= set(charts["interval_series"])
    assert charts["interval_series"]["daily"] == charts["price_series"]
    assert charts["interval_series"]["monthly"][-1]["close"] == 12.0
    assert charts["interval_series"]["hourly"]
    assert len(charts["forecast_series"]) >= 5
    assert charts["forecast_series"][0]["trade_date"] > charts["price_series"][-1]["trade_date"]
    assert {"macd", "macd_signal", "macd_hist", "rsi14"} <= set(charts["price_series"][-1])
    assert len(payload["benchmark_curve"]) == 3
    assert payload["benchmark_curve"][0]["return_ratio"] == 0.0
    assert payload["benchmark_curve"][-1]["return_ratio"] > 0
    assert len(payload["alpha_curve"]) == 3
    assert payload["alpha_curve"][-1]["alpha"] > 0
    assert len(payload["strategy_equity_curves"]) == 3
    assert payload["strategy_equity_curves"][0]["strategy_name"] == "daily_momentum"
    assert "daily_momentum" in payload["ai_learning_context"]
    assert "最大回撤" in payload["ai_learning_context"]

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

    captured_calls: list[list[str]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        captured_calls.append(symbols)
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
    assert payload["stock_pick_snapshot"] is None
    assert captured_calls[:2] == [
        ["600519.SH", "000001.SZ", "300750.SZ"],
        ["600519.SH", "000001.SZ", "300750.SZ"],
    ]
    assert captured_calls[2:] == [
        ["600519.SH", "000001.SZ", "300750.SZ"],
        ["600519.SH", "000001.SZ", "300750.SZ"],
    ]
    assert payload["candidate_count"] == 3
    for order in payload["orders"]:
        context = order["decision_context"]
        assert context["stock_pick_snapshot_id"].startswith("ai-picks-")
        assert context["selected_candidate"]["symbol"] in {
            item["symbol"] for item in payload["candidates"]
        }

    _reset_state()


def test_arena_run_requests_maximized_stock_pick_snapshot(monkeypatch, tmp_path) -> None:
    from app.services.ai_stock_picker_service import ai_stock_picker_service

    captured_requests: list[tuple[object, object, object, object, object]] = []

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
        agent=None,
    ):
        captured_requests.append((symbols, limit, prefer_realtime, lookback_days, (agent or {}).get("style")))
        call_index = len(captured_requests)
        items = [candidate("600519.SH", 80), candidate("000001.SZ", 70)]
        return {
            "snapshot_id": f"ai-picks-test-{call_index}",
            "selection_mode": "auto_universe",
            "data_sources": ["tencent", "tushare_daily"],
            "coverage": {},
            "selection_plan": {"strategy": (agent or {}).get("style") or "balanced"},
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
    assert captured_requests == [
        (None, 1000, True, 1825, "momentum"),
        (None, 1000, True, 1825, "balanced"),
    ]
    payload = response.json()
    assert payload["stock_pick_snapshot"] is None
    assert {order["decision_context"]["stock_pick_snapshot_id"] for order in payload["orders"]} == {
        "ai-picks-test-1",
        "ai-picks-test-2",
    }

    _reset_state()


def test_arena_run_ignores_user_symbols_and_builds_independent_snapshot_per_agent(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.ai_stock_picker_service import ai_stock_picker_service

    captured_requests: list[tuple[object, object, object, object, object]] = []

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
            "rationale": "独立候选",
        }

    def fake_build_snapshot(
        *,
        db,
        symbols=None,
        limit=50,
        prefer_realtime=True,
        lookback_days=120,
        agent=None,
    ):
        captured_requests.append((symbols, limit, prefer_realtime, lookback_days, (agent or {}).get("style")))
        call_index = len(captured_requests)
        items = [candidate(f"60000{call_index}.SH", 80 - call_index)]
        return {
            "snapshot_id": f"ai-picks-agent-{call_index}",
            "selection_mode": "auto_universe" if symbols is None else "custom_symbols",
            "data_sources": ["tencent", "tushare_daily"],
            "coverage": {},
            "selection_plan": {"strategy": (agent or {}).get("style") or "balanced"},
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
                "symbols": ["用户自选.SZ"],
                "agents": [
                    {"id": "deepseek", "name": "DeepSeek", "style": "momentum"},
                    {"id": "gpt", "name": "GPT", "style": "balanced"},
                ],
            },
        )

    assert response.status_code == 200
    assert captured_requests == [
        (None, 1000, True, 1825, "momentum"),
        (None, 1000, True, 1825, "balanced"),
    ]
    payload = response.json()
    assert payload["candidate_pool_scope"] == {
        "mode": "agent_independent_auto_universe",
        "user_candidate_pool": "stock_analysis_only",
        "arena_user_symbols_effect": "ignored",
        "agent_pool_count": 2,
    }
    assert payload["agent_candidate_pools"] == [
        {
            "agent_id": "deepseek",
            "selection_mode": "auto_universe",
            "snapshot_id": "ai-picks-agent-1",
            "candidate_count": 1,
            "symbols": ["600001.SH"],
        },
        {
            "agent_id": "gpt",
            "selection_mode": "auto_universe",
            "snapshot_id": "ai-picks-agent-2",
            "candidate_count": 1,
            "symbols": ["600002.SH"],
        },
    ]
    order_contexts = [order["decision_context"] for order in payload["orders"]]
    assert {context["stock_pick_snapshot_id"] for context in order_contexts} == {
        "ai-picks-agent-1",
        "ai-picks-agent-2",
    }
    assert {context["selected_candidate"]["symbol"] for context in order_contexts} == {
        "600001.SH",
        "600002.SH",
    }
    assert payload["stock_pick_snapshot"] is None

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


def test_arena_orders_record_decision_timing_for_latency_audit(
    monkeypatch,
    tmp_path,
) -> None:
    from datetime import datetime, timedelta

    from app.services.arena_service import arena_service
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
                "timestamp": "2026-05-29 10:20:00",
            }
        ]

    base = datetime(2026, 5, 29, 10, 20, 0)
    ticks = iter(
        [
            base,
            base + timedelta(milliseconds=180),
            base + timedelta(milliseconds=240),
        ]
    )
    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr(arena_service, "_utcnow", lambda: next(ticks), raising=False)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add(DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900))
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "symbols": ["600519.SH"],
                "initial_cash": 200000,
                "agents": [{"id": "timely_ai", "name": "及时 AI", "style": "momentum"}],
            },
        )

    assert response.status_code == 200
    order = response.json()["orders"][0]
    assert order["decision_started_at"] == "2026-05-29T10:20:00"
    assert order["decision_generated_at"] == "2026-05-29T10:20:00.180000"
    assert order["decision_recorded_at"] == "2026-05-29T10:20:00.240000"
    assert order["decision_latency_ms"] == 180
    assert order["record_latency_ms"] == 60
    assert order["decision_context"]["timing"]["decision_latency_ms"] == 180
    assert order["decision_context"]["timing"]["record_latency_ms"] == 60

    _reset_state()


def test_arena_morning_phase_records_agent_recommendations_without_orders(
    monkeypatch,
    tmp_path,
) -> None:
    from app.db.models import ArenaRun
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
                "timestamp": "2026-05-29 08:20:00",
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
                "timestamp": "2026-05-29 08:20:00",
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
                "phase": "morning_recommendation",
                "symbols": ["600519.SH", "000001.SZ"],
                "initial_cash": 200000,
                "agents": [
                    {"id": "momentum_ai", "name": "动量 AI", "style": "momentum"},
                    {"id": "risk_ai", "name": "风控 AI", "style": "risk_control"},
                ],
            },
        )
        with session_scope() as db:
            stored_run = db.get(ArenaRun, response.json()["run_id"])

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "morning_recommendation"
    assert payload["orders"] == []
    assert [item["agent_id"] for item in payload["agent_recommendations"]] == [
        "momentum_ai",
        "risk_ai",
    ]
    assert payload["agent_recommendations"][0]["action"] == "WATCH"
    assert 1 <= len(payload["agent_recommendations"][0]["picks"]) <= 5
    assert len(payload["agent_recommendations"][0]["picks"]) < 2
    assert payload["agent_recommendations"][0]["playbook"]["mode"] in {
        "short_swing",
        "quant_rotation",
        "long_defensive",
    }
    assert payload["agent_recommendations"][0]["decision_context"]["action"] == "WATCH"
    assert stored_run.phase == "morning_recommendation"
    assert stored_run.candidate_payload["agent_recommendations"][0]["agent_id"] == "momentum_ai"

    _reset_state()


def test_arena_closing_phase_records_agent_memory_and_reuses_it_in_prompt(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    calls: list[dict[str, object]] = []
    quote_count = {"value": 0}

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        quote_count["value"] += 1
        price = 10.0 if quote_count["value"] == 1 else 12.0
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": price,
                "change_pct": 3.0 if price == 10.0 else 5.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 15:00:03",
            }
        ]

    def fake_call_llm(*, base_url, api_key, payload, timeout_seconds):
        calls.append({"payload": payload})
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "BUY",
                                "symbol": "000001.SZ",
                                "allocation_ratio": 0.1,
                                "reason": "读取记忆后继续小仓位跟踪。",
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
            settings.llm_model = "gpt-4o-mini"
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500))
        run_payload = {
            "symbols": ["000001.SZ"],
            "initial_cash": 200000,
            "agents": [
                {
                    "id": "memory_ai",
                    "name": "记忆 AI",
                    "style": "momentum",
                    "provider": "openai-compatible",
                    "model": "gpt-4o-mini",
                    "prompt": "复盘后改进仓位。",
                }
            ],
        }
        first_run = client.post("/api/aniu/arena/run", headers=headers, json=run_payload)
        closing = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={**run_payload, "phase": "closing_review"},
        )
        memories = client.get(
            "/api/aniu/arena/agents/memory_ai/memories",
            headers=headers,
        )
        second_run = client.post("/api/aniu/arena/run", headers=headers, json=run_payload)
        with session_scope() as db:
            stored_memory = db.query(ArenaAgentMemory).filter_by(agent_id="memory_ai").first()

    assert first_run.status_code == 200
    assert closing.status_code == 200
    closing_payload = closing.json()
    assert closing_payload["phase"] == "closing_review"
    assert closing_payload["orders"] == []
    assert closing_payload["agent_reviews"][0]["agent_id"] == "memory_ai"
    assert closing_payload["agent_reviews"][0]["memory_type"] == "closing_review"
    assert "收益" in closing_payload["agent_reviews"][0]["summary"]
    assert memories.status_code == 200
    assert memories.json()["memories"][0]["summary"] == closing_payload["agent_reviews"][0]["summary"]
    assert second_run.status_code == 200
    assert stored_memory is not None
    last_prompt = json.loads(calls[-1]["payload"]["messages"][1]["content"])
    assert last_prompt["recent_memories"][0]["summary"] == stored_memory.summary

    _reset_state()


def test_arena_nightly_phase_records_backtest_learning_memory(
    monkeypatch,
    tmp_path,
) -> None:
    from app.db.models import ArenaAgentMemory
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 12.0,
                "change_pct": 2.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 23:30:00",
            },
            {
                "symbol": "600519.SH",
                "name": "贵州茅台",
                "price": 98.0,
                "change_pct": -1.0,
                "amount": 500_000_000,
                "turnover": 0.3,
                "volume_ratio": 0.9,
                "source": "tencent",
                "timestamp": "2026-05-29 23:30:00",
            },
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="000001.SZ", trade_date="20260527", close=10, amount=500),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", close=12, amount=600),
                    DailyBar(symbol="600519.SH", trade_date="20260527", close=100, amount=900),
                    DailyBar(symbol="600519.SH", trade_date="20260528", close=98, amount=800),
                ]
            )
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "phase": "nightly_learning",
                "symbols": ["000001.SZ", "600519.SH"],
                "initial_cash": 200000,
                "agents": [
                    {
                        "id": "night_ai",
                        "name": "夜间学习 AI",
                        "style": "balanced",
                    }
                ],
            },
        )
        with session_scope() as db:
            stored_memory = db.query(ArenaAgentMemory).filter_by(agent_id="night_ai").first()

    assert response.status_code == 200
    payload = response.json()
    assert payload["phase"] == "nightly_learning"
    assert payload["orders"] == []
    assert payload["agent_reviews"][0]["memory_type"] == "nightly_learning"
    assert "回测" in payload["agent_reviews"][0]["summary"]
    assert payload["agent_reviews"][0]["metrics"]["backtest"]["selected_symbol"] == "000001.SZ"
    assert payload["agent_reviews"][0]["metrics"]["backtest"]["return_ratio"] > 0
    assert stored_memory is not None
    assert stored_memory.memory_type == "nightly_learning"

    _reset_state()


def test_stock_analysis_returns_purchase_advice_from_quant_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 12.0,
                "change_pct": 4.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.8,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:20:00",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260526", open=9.8, high=10.3, low=9.7, close=10, amount=500))
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260527", open=10.0, high=11.1, low=9.9, close=11, amount=800))
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", open=11.0, high=12.2, low=10.8, close=12, amount=1200))
        response = client.post(
            "/api/aniu/stocks/analyze",
            headers=headers,
            json={"symbol": "000001.SZ", "initial_cash": 200000},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "000001.SZ"
    assert payload["name"] == "平安银行"
    assert payload["action"] in {"BUY", "HOLD", "SELL"}
    assert payload["price"] == 12.0
    assert payload["score"] > 0
    assert payload["decision"]["target_allocation_ratio"] >= 0
    assert payload["llm_decision"]["used"] is False
    assert payload["retail_analysis"]["decision"] in {"buy", "watch", "hold", "avoid"}
    assert payload["retail_analysis"]["sell_plan"]["rule"]
    assert {item["key"] for item in payload["retail_analysis"]["dimension_scores"]} >= {
        "technical",
        "liquidity",
    }
    assert "easy_tdx" in payload["data_sources"]
    assert [point["trade_date"] for point in payload["charts"]["price_series"]] == [
        "20260526",
        "20260527",
        "20260528",
    ]
    assert payload["charts"]["signal_markers"][0]["action"] == payload["action"]
    assert payload["charts"]["signal_markers"][0]["symbol"] == "000001.SZ"
    assert payload["charts"]["factor_radar"]
    assert payload["charts"]["support_resistance"]["support"] == 9.7
    assert payload["charts"]["support_resistance"]["resistance"] == 12.2
    assert payload["charts"]["return_distribution"]
    assert payload["charts"]["volume_profile"]
    assert {"daily", "weekly", "monthly", "hourly"} <= set(payload["charts"]["interval_series"])
    assert payload["charts"]["interval_series"]["daily"] == payload["charts"]["price_series"]
    assert payload["charts"]["interval_series"]["weekly"][-1]["close"] == 12.0
    assert payload["charts"]["interval_series"]["hourly"]
    assert len(payload["charts"]["forecast_series"]) >= 5
    assert payload["charts"]["forecast_series"][0]["trade_date"] > payload["charts"]["price_series"][-1]["trade_date"]
    assert payload["charts"]["forecast_series"][0]["source"] == "quant_regime_projection"
    assert "confidence" in payload["charts"]["forecast_series"][0]
    assert {"macd", "macd_signal", "macd_hist", "rsi14"} <= set(payload["charts"]["price_series"][-1])

    _reset_state()


def test_stock_analysis_uses_selected_forecast_model_and_skill(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    monkeypatch.setenv("FORECAST_AI_BASE_URL", "https://ai.example/v1")
    monkeypatch.setenv("FORECAST_AI_API_KEY", "forecast-secret-key")
    monkeypatch.setenv("FORECAST_AI_MODELS", "deepseek-v4-pro,minimax-m2.7")

    calls: list[dict[str, object]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 12.0,
                "change_pct": 4.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.8,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:20:00",
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
                                "rating": "深度观察",
                                "target_allocation_ratio": 0.18,
                                "reason": "结合 UZI 深度分析框架后维持观察买入。",
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
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260526", open=9.8, high=10.3, low=9.7, close=10, amount=500))
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260527", open=10.0, high=11.1, low=9.9, close=11, amount=800))
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", open=11.0, high=12.2, low=10.8, close=12, amount=1200))
        response = client.post(
            "/api/aniu/stocks/analyze",
            headers=headers,
            json={
                "symbol": "000001.SZ",
                "initial_cash": 200000,
                "model": "deepseek-v4-pro",
                "skill_id": "uzi_deep_analysis",
            },
        )

    assert response.status_code == 200
    assert calls
    call = calls[0]
    assert call["base_url"] == "https://ai.example/v1"
    assert call["api_key"] == "forecast-secret-key"
    assert call["payload"]["model"] == "deepseek-v4-pro"
    prompt = call["payload"]["messages"][1]["content"]
    assert "uzi_deep_analysis" in prompt
    assert "UZI 深度分析" in prompt
    payload = response.json()
    assert payload["rating"] == "深度观察"
    assert payload["llm_decision"]["used"] is True
    assert payload["llm_decision"]["model"] == "deepseek-v4-pro"
    assert payload["analysis_config"]["selected_model"] == "deepseek-v4-pro"
    assert payload["analysis_config"]["selected_skill"]["id"] == "uzi_deep_analysis"
    assert payload["analysis_config"]["ai_config"]["api_key_configured"] is True
    assert "forecast-secret-key" not in response.text

    _reset_state()


def test_stock_analysis_uses_multiple_skills_and_persists_report(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    monkeypatch.setenv("FORECAST_AI_BASE_URL", "https://ai.example/v1")
    monkeypatch.setenv("FORECAST_AI_API_KEY", "forecast-secret-key")
    monkeypatch.setenv("FORECAST_AI_MODELS", "deepseek-v4-pro,minimax-m2.7")

    calls: list[dict[str, object]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 12.0,
                "change_pct": 4.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.8,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:20:00",
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
                                "rating": "多技能观察",
                                "target_allocation_ratio": 0.16,
                                "reason": "深度研究和风险检测汇总后建议轻仓观察。",
                                "report_summary": "多技能汇总显示趋势可跟踪，但需排除异常推广风险。",
                                "skill_opinions": [
                                    {
                                        "skill_id": "uzi_deep_analysis",
                                        "stance": "bullish",
                                        "summary": "基本面和趋势结构可继续观察。",
                                    },
                                    {
                                        "skill_id": "uzi_trap_detector",
                                        "stance": "neutral",
                                        "summary": "暂未确认高危推广信号，但需要持续跟踪。",
                                    },
                                ],
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
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260526", open=9.8, high=10.3, low=9.7, close=10, amount=500))
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260527", open=10.0, high=11.1, low=9.9, close=11, amount=800))
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", open=11.0, high=12.2, low=10.8, close=12, amount=1200))
        response = client.post(
            "/api/aniu/stocks/analyze",
            headers=headers,
            json={
                "symbol": "000001.SZ",
                "initial_cash": 200000,
                "model": "deepseek-v4-pro",
                "skill_ids": ["uzi_deep_analysis", "uzi_trap_detector"],
            },
        )

    assert response.status_code == 200
    assert calls
    prompt = calls[0]["payload"]["messages"][1]["content"]
    assert "uzi_deep_analysis" in prompt
    assert "uzi_trap_detector" in prompt
    assert "UZI 深度分析" in prompt
    assert "UZI 杀猪盘检测" in prompt
    payload = response.json()
    assert [item["id"] for item in payload["analysis_config"]["selected_skills"]] == [
        "uzi_deep_analysis",
        "uzi_trap_detector",
    ]
    report = payload["analysis_report"]
    assert report["id"] > 0
    assert report["symbol"] == "000001.SZ"
    assert report["model"] == "deepseek-v4-pro"
    assert [item["id"] for item in report["selected_skills"]] == [
        "uzi_deep_analysis",
        "uzi_trap_detector",
    ]
    assert "多技能汇总" in report["summary"]
    assert len(report["sections"]) >= 3

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        detail_response = client.get(
            f"/api/aniu/stocks/analysis-reports/{report['id']}",
            headers=headers,
        )

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["id"] == report["id"]
    assert detail_payload["summary"] == report["summary"]
    assert "forecast-secret-key" not in detail_response.text

    _reset_state()


def test_arena_order_context_includes_retail_a_share_analysis(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 12.0,
                "change_pct": 4.0,
                "amount": 1_000_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.8,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:20:00",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500))
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "initial_cash": 200000,
                "agents": [
                    {"id": "retail_ai", "name": "散户分析 AI", "style": "balanced"},
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    context = payload["orders"][0]["decision_context"]
    assert context["retail_analysis"]["decision"] in {"buy", "watch", "hold", "avoid"}
    assert context["retail_analysis"]["sell_plan"]["rule"]
    assert context["retail_analysis"]["dimension_scores"]
    assert context["selected_candidate"]["ai_selection"]["score"] > 0
    assert context["selected_candidate"]["ai_selection"]["strategy"] == "balanced"
    assert context["selected_candidate"]["ai_selection"]["temporal_profile"]
    assert context["selected_candidate"]["ai_selection"]["data_request"]["requested_dimensions"]
    assert context["selection_plan"]["strategy"] == "balanced"

    _reset_state()


def test_arena_decision_uses_agent_on_demand_data_request(monkeypatch, tmp_path) -> None:
    from app.services.ai_data_request_service import ai_data_request_service
    from app.services.market_data_service import market_data_service

    requests: list[dict[str, object]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 3.2,
                "amount": 900_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:20:00",
            }
        ]

    def fake_execute(db, *, symbols, dimensions, limit, lookback_days, prefer_realtime, refresh, end_date=None):
        requests.append(
            {
                "symbols": symbols,
                "dimensions": dimensions,
                "limit": limit,
                "lookback_days": lookback_days,
                "prefer_realtime": prefer_realtime,
                "refresh": refresh,
                "end_date": end_date,
            }
        )
        return {
            "requested_symbols": symbols,
            "requested_dimensions": dimensions,
            "actions": [
                {
                    "dimension": "moneyflow",
                    "source": "tushare_moneyflow",
                    "temporal_window": {"lookback_days": lookback_days, "frequency": "daily"},
                }
            ],
            "refresh": None,
            "dataset": {"item_count": 1, "items": [{"symbol": "000001.SZ", "name": "平安银行"}]},
            "context": "按需补数上下文: 资金流与日线已就绪",
            "context_length": 18,
        }

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr(ai_data_request_service, "execute", fake_execute)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500))
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "initial_cash": 200000,
                "agents": [
                    {"id": "data_ai", "name": "补数 AI", "style": "momentum"},
                ],
            },
        )

    assert response.status_code == 200
    assert requests
    assert requests[0]["symbols"] == ["000001.SZ"]
    assert requests[0]["dimensions"] == [
        "quote",
        "daily_history",
        "moneyflow",
        "sector_heat",
        "limit_event",
    ]
    assert requests[0]["refresh"] is False
    context = response.json()["orders"][0]["decision_context"]
    assert context["ai_data_request"]["requested_symbols"] == ["000001.SZ"]
    assert context["ai_data_request"]["context_length"] == 18
    assert context["ai_data_request"]["actions"][0]["source"] == "tushare_moneyflow"

    _reset_state()


def test_arena_llm_can_request_extra_data_dimensions_before_decision(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.ai_data_request_service import ai_data_request_service
    from app.services.llm_service import llm_service
    from app.services.market_data_service import market_data_service

    data_requests: list[dict[str, object]] = []
    llm_payloads: list[dict[str, object]] = []

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "price": 10.0,
                "change_pct": 3.2,
                "amount": 900_000_000,
                "turnover": 1.0,
                "volume_ratio": 1.5,
                "source": "easy_tdx",
                "timestamp": "2026-05-29 10:20:00",
            }
        ]

    def fake_data_request(db, *, symbols, dimensions, limit, lookback_days, prefer_realtime, refresh, end_date=None):
        data_requests.append(
            {
                "symbols": symbols,
                "dimensions": dimensions,
                "limit": limit,
                "lookback_days": lookback_days,
                "prefer_realtime": prefer_realtime,
                "refresh": refresh,
                "end_date": end_date,
            }
        )
        return {
            "requested_symbols": symbols,
            "requested_dimensions": dimensions,
            "actions": [
                {
                    "dimension": dimension,
                    "source": f"source:{dimension}",
                    "temporal_window": {"lookback_days": lookback_days, "frequency": "daily"},
                }
                for dimension in dimensions
            ],
            "refresh": None,
            "dataset": {"item_count": 1, "data_sources": ["easy_tdx"], "coverage": {}},
            "context": "LLM 自主请求后的补数上下文",
            "context_length": 15,
        }

    def fake_call_llm(*, base_url, api_key, payload, timeout_seconds):
        llm_payloads.append(payload)
        prompt = json.loads(payload["messages"][1]["content"])
        if "allowed_dimensions" in prompt:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "dimensions": ["financial_indicator", "pledge_stat"],
                                    "reason": "需要财务质量和质押风险确认。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        assert "financial_indicator" in prompt["ai_data_request"]["requested_dimensions"]
        assert "pledge_stat" in prompt["ai_data_request"]["requested_dimensions"]
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "action": "BUY",
                                "symbol": "000001.SZ",
                                "allocation_ratio": 0.1,
                                "reason": "补齐财务和质押数据后买入。",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr(ai_data_request_service, "execute", fake_data_request)
    monkeypatch.setattr(llm_service, "_call_llm", fake_call_llm)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            settings = db.query(AppSettings).first()
            assert settings is not None
            settings.provider_name = "openai-compatible"
            settings.llm_base_url = "https://llm.example/v1"
            settings.llm_api_key = "llm-key"
            settings.llm_model = "model-a"
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500))
        response = client.post(
            "/api/aniu/arena/run",
            headers=headers,
            json={
                "initial_cash": 200000,
                "agents": [
                    {
                        "id": "llm_data_ai",
                        "name": "LLM 补数 AI",
                        "style": "risk_control",
                        "provider": "openai-compatible",
                        "model": "model-a",
                    },
                ],
            },
        )

    assert response.status_code == 200
    assert len(llm_payloads) == 2
    assert data_requests[-1]["dimensions"] == [
        "quote",
        "daily_history",
        "financial_indicator",
        "valuation",
        "pledge_stat",
    ]
    context = response.json()["orders"][0]["decision_context"]
    assert context["llm_decision"]["data_dimension_request"]["dimensions"] == [
        "financial_indicator",
        "pledge_stat",
    ]
    assert "financial_indicator" in context["ai_data_request"]["requested_dimensions"]
    assert "pledge_stat" in context["ai_data_request"]["requested_dimensions"]

    _reset_state()


def test_arena_agent_dashboard_groups_four_phase_details(monkeypatch, tmp_path) -> None:
    from app.services.market_data_service import market_data_service

    quotes = [
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "price": 12.0,
            "change_pct": 4.0,
            "amount": 1_000_000_000,
            "turnover": 1.0,
            "volume_ratio": 1.8,
            "source": "easy_tdx",
            "timestamp": "2026-05-29 10:20:00",
        },
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "price": 100.0,
            "change_pct": 1.0,
            "amount": 800_000_000,
            "turnover": 0.4,
            "volume_ratio": 1.1,
            "source": "tencent",
            "timestamp": "2026-05-29 10:20:00",
        },
        {
            "symbol": "300750.SZ",
            "name": "宁德时代",
            "price": 80.0,
            "change_pct": 2.2,
            "amount": 600_000_000,
            "turnover": 0.8,
            "volume_ratio": 1.3,
            "source": "easy_tdx",
            "timestamp": "2026-05-29 10:20:00",
        },
        {
            "symbol": "601318.SH",
            "name": "中国平安",
            "price": 40.0,
            "change_pct": 0.8,
            "amount": 500_000_000,
            "turnover": 0.5,
            "volume_ratio": 1.0,
            "source": "tencent",
            "timestamp": "2026-05-29 10:20:00",
        },
        {
            "symbol": "000858.SZ",
            "name": "五粮液",
            "price": 90.0,
            "change_pct": 1.6,
            "amount": 450_000_000,
            "turnover": 0.6,
            "volume_ratio": 1.2,
            "source": "easy_tdx",
            "timestamp": "2026-05-29 10:20:00",
        },
        {
            "symbol": "601899.SH",
            "name": "紫金矿业",
            "price": 18.0,
            "change_pct": 1.4,
            "amount": 420_000_000,
            "turnover": 0.7,
            "volume_ratio": 1.1,
            "source": "tencent",
            "timestamp": "2026-05-29 10:20:00",
        },
    ]

    def fake_quotes(symbols: list[str], prefer_realtime: bool = True):
        requested = set(symbols)
        return [item for item in quotes if item["symbol"] in requested]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    symbols = ["000001.SZ", "600519.SH", "300750.SZ", "601318.SH", "000858.SZ", "601899.SH"]
    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            for symbol, close in (
                ("000001.SZ", 10),
                ("600519.SH", 100),
                ("300750.SZ", 78),
                ("601318.SH", 39),
                ("000858.SZ", 90),
                ("601899.SH", 18),
            ):
                db.add(DailyBar(symbol=symbol, trade_date="20260527", close=close, amount=500))
                db.add(DailyBar(symbol=symbol, trade_date="20260528", close=close + 1, amount=600))
            for symbol, name in (
                ("000001.SZ", "平安银行"),
                ("600519.SH", "贵州茅台"),
                ("300750.SZ", "宁德时代"),
                ("601318.SH", "中国平安"),
                ("000858.SZ", "五粮液"),
                ("601899.SH", "紫金矿业"),
            ):
                db.add(StockProfile(symbol=symbol, name=name, industry="A股"))
        agent = {
            "id": "detail_ai",
            "name": "详情 AI",
            "style": "momentum",
            "provider": "openai-compatible",
            "model": "",
            "enabled": True,
            "prompt": "偏短线动量。",
        }
        assert client.post("/api/aniu/arena/agents", headers=headers, json=agent).status_code == 200
        for phase in (
            "morning_recommendation",
            "intraday_trade",
            "closing_review",
            "nightly_learning",
        ):
            response = client.post(
                "/api/aniu/arena/run",
                headers=headers,
                json={"phase": phase, "symbols": symbols, "initial_cash": 200000},
            )
            assert response.status_code == 200
        with session_scope() as db:
            stored_run = db.scalar(select(ArenaRun).where(ArenaRun.phase == "morning_recommendation"))
            assert stored_run is not None
            payload = dict(stored_run.candidate_payload or {})
            for item in payload.get("agent_recommendations") or []:
                for pick in item.get("picks") or []:
                    pick["name"] = pick["symbol"]
                item["name"] = item["symbol"]
            stored_run.candidate_payload = payload
            flag_modified(stored_run, "candidate_payload")
        dashboard_response = client.get(
            "/api/aniu/arena/agents/detail_ai/dashboard",
            headers=headers,
        )

    assert dashboard_response.status_code == 200
    payload = dashboard_response.json()
    assert payload["agent"]["id"] == "detail_ai"
    assert payload["summary"]["playbook"]["mode"] == "short_swing"
    assert payload["summary"]["total_assets"] > 0
    assert len(payload["morning"]["recommendations"]) == 1
    morning = payload["morning"]["recommendations"][0]
    assert len(morning["picks"]) == 5
    assert len(morning["picks"]) < 6
    assert morning["playbook"]["mode"] == "short_swing"
    assert morning["name"] != morning["symbol"]
    assert all(pick["name"] != pick["symbol"] for pick in morning["picks"])
    assert all(pick["ai_selection_score"] > 0 for pick in morning["picks"])
    assert all("risk_flags" in pick for pick in morning["picks"])
    assert len(payload["intraday"]["orders"]) >= 1
    order_chart = payload["intraday"]["orders"][0]["charts"]
    assert order_chart["trade_markers"][0]["action"] in {"BUY", "SELL"}
    assert order_chart["price_series"]
    assert payload["summary"]["charts"]["action_distribution"]
    assert payload["summary"]["charts"]["symbol_exposure"]
    assert payload["closing"]["reviews"][0]["memory_type"] == "closing_review"
    assert payload["learning"]["reviews"][0]["memory_type"] == "nightly_learning"

    _reset_state()


def test_arena_ai_config_and_default_agents_reflect_forecast_models(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("FORECAST_AI_BASE_URL", "https://ai.example/v1")
    monkeypatch.setenv("FORECAST_AI_API_KEY", "forecast-secret-key")
    monkeypatch.setenv("FORECAST_AI_MODELS", "deepseek-v4-pro,minimax-m2.7")

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        config_response = client.get("/api/aniu/arena/ai-config", headers=headers)
        settings_response = client.get("/api/aniu/settings", headers=headers)
        agents_response = client.get("/api/aniu/arena/agents", headers=headers)

    assert config_response.status_code == 200
    assert settings_response.status_code == 200
    assert agents_response.status_code == 200

    config_payload = config_response.json()
    assert config_payload["base_url"] == "https://ai.example/v1"
    assert config_payload["api_key_configured"] is True
    assert config_payload["api_key_masked"] != "forecast-secret-key"
    assert config_payload["models"] == ["deepseek-v4-pro", "minimax-m2.7"]

    settings_payload = settings_response.json()
    assert settings_payload["forecast_ai_config"]["base_url"] == "https://ai.example/v1"
    assert settings_payload["forecast_ai_config"]["models"] == [
        "deepseek-v4-pro",
        "minimax-m2.7",
    ]
    assert "forecast-secret-key" not in settings_response.text

    agents = agents_response.json()["agents"]
    assert [item["model"] for item in agents] == ["deepseek-v4-pro", "minimax-m2.7"]
    assert [item["provider"] for item in agents] == ["forecast-ai", "forecast-ai"]
    assert [item["id"] for item in agents] == [
        "model_deepseek_v4_pro",
        "model_minimax_m2_7",
    ]

    _reset_state()


def test_arena_order_forecast_returns_multi_model_analysis(monkeypatch, tmp_path) -> None:
    from app.services.llm_service import llm_service

    monkeypatch.setenv("FORECAST_AI_BASE_URL", "https://ai.example/v1")
    monkeypatch.setenv("FORECAST_AI_API_KEY", "forecast-key")
    monkeypatch.setenv("FORECAST_AI_MODELS", "model-a,model-b")
    calls: list[str] = []

    def fake_call_llm(*, base_url: str, api_key: str, payload: dict, timeout_seconds: int):
        calls.append(str(payload["model"]))
        assert base_url == "https://ai.example/v1"
        assert api_key == "forecast-key"
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "direction": "bullish",
                                "confidence": 82,
                                "target_price": 12.8,
                                "stop_loss": 10.6,
                                "support": 10.8,
                                "resistance": 12.2,
                                "buy_zone": [10.9, 11.2],
                                "sell_zone": [12.5, 12.9],
                                "key_points": ["MA5上穿MA20", "成交额放大", "MACD柱改善"],
                                "risk_points": ["跌破10.6失效", "压力位需放量突破"],
                                "analysis": "短线趋势向上，价格站回短均线后量能改善，若能突破前高压力，风险收益比尚可。",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(llm_service, "_call_llm", fake_call_llm)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        with session_scope() as db:
            db.add_all(
                [
                    DailyBar(symbol="000001.SZ", trade_date="20260524", open=10.0, high=10.4, low=9.9, close=10.2, amount=500),
                    DailyBar(symbol="000001.SZ", trade_date="20260525", open=10.2, high=10.7, low=10.1, close=10.6, amount=650),
                    DailyBar(symbol="000001.SZ", trade_date="20260526", open=10.6, high=11.0, low=10.5, close=10.9, amount=800),
                    DailyBar(symbol="000001.SZ", trade_date="20260527", open=10.9, high=11.4, low=10.8, close=11.2, amount=1100),
                    DailyBar(symbol="000001.SZ", trade_date="20260528", open=11.2, high=11.8, low=11.0, close=11.6, amount=1500),
                ]
            )
            run = ArenaRun(status="completed", phase="intraday_trade", initial_cash=200000)
            db.add(run)
            db.flush()
            order = ArenaOrder(
                arena_run_id=run.id,
                agent_id="forecast_ai",
                agent_name="预测AI",
                style="momentum",
                action="BUY",
                symbol="000001.SZ",
                name="平安银行",
                quantity=1000,
                price=11.6,
                amount=11600,
                remaining_cash=188400,
                reason="测试预测",
            )
            db.add(order)
            db.flush()
            order_id = order.id

        response = client.get(
            f"/api/aniu/arena/orders/{order_id}/forecast",
            headers=headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "000001.SZ"
    assert payload["ai_config"]["base_url"] == "https://ai.example/v1"
    assert payload["ai_config"]["api_key_configured"] is True
    assert payload["ai_config"]["api_key_masked"] == "foreca...st-key"
    assert payload["ai_config"]["models"] == ["model-a", "model-b"]
    assert "forecast-key" not in json.dumps(payload, ensure_ascii=False)
    assert payload["technical_context"]["direction"] in {"bullish", "neutral", "bearish"}
    assert payload["methodology"]
    assert [item["model"] for item in payload["model_forecasts"]] == ["model-a", "model-b"]
    assert {item["status"] for item in payload["model_forecasts"]} == {"live_ai"}
    assert payload["model_forecasts"][0]["analysis"]
    assert sorted(calls) == ["model-a", "model-b"]

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
        "deepseek-chat",
        "gpt-4o-mini",
        "gpt-4o-mini",
    ]
    assert all(call["base_url"] == "https://llm.example/v1" for call in calls)
    assert all(call["api_key"] == "llm-key" for call in calls)
    decision_calls = [
        call
        for call in calls
        if "allowed_dimensions"
        not in json.loads(call["payload"]["messages"][1]["content"])
    ]
    user_prompts = [call["payload"]["messages"][1]["content"] for call in decision_calls]
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
        ("https://deepseek.example/v1", "deepseek-key", "deepseek-chat"),
        ("https://openai.example/v1", "openai-key", "gpt-4o-mini"),
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
        second["orders"][0]["decision_context"]["stock_pick_snapshot_id"].startswith(
            "ai-picks-"
        )
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


def test_arena_agents_support_individual_crud(monkeypatch, tmp_path) -> None:
    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        create_response = client.post(
            "/api/aniu/arena/agents",
            headers=headers,
            json={
                "id": "claude_ai",
                "name": "Claude 稳健",
                "style": "risk_control",
                "provider": "anthropic-compatible",
                "model": "claude-sonnet",
                "enabled": True,
                "prompt": "偏低回撤和交易纪律。",
            },
        )
        detail_response = client.get(
            "/api/aniu/arena/agents/claude_ai",
            headers=headers,
        )
        update_response = client.put(
            "/api/aniu/arena/agents/claude_ai",
            headers=headers,
            json={
                "id": "ignored_id",
                "name": "Claude 趋势",
                "style": "momentum",
                "provider": "anthropic-compatible",
                "model": "claude-opus",
                "enabled": False,
                "prompt": "偏趋势突破，但禁用。",
            },
        )
        list_after_update = client.get("/api/aniu/arena/agents", headers=headers)
        delete_response = client.delete(
            "/api/aniu/arena/agents/claude_ai",
            headers=headers,
        )
        detail_after_delete = client.get(
            "/api/aniu/arena/agents/claude_ai",
            headers=headers,
        )

    assert create_response.status_code == 200
    assert create_response.json()["id"] == "claude_ai"
    assert detail_response.status_code == 200
    assert detail_response.json()["prompt"] == "偏低回撤和交易纪律。"
    assert update_response.status_code == 200
    assert update_response.json()["id"] == "claude_ai"
    assert update_response.json()["name"] == "Claude 趋势"
    assert update_response.json()["style"] == "momentum"
    assert update_response.json()["enabled"] is False
    assert [item["id"] for item in list_after_update.json()["agents"]] == ["claude_ai"]
    assert delete_response.status_code == 200
    assert delete_response.json()["agents"] == []
    assert detail_after_delete.status_code == 404

    _reset_state()
