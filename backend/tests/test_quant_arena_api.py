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
    assert captured_calls == [
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

    captured_requests: list[tuple[object, object, object, object]] = []

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
        captured_requests.append((symbols, limit, prefer_realtime, lookback_days))
        call_index = len(captured_requests)
        items = [candidate("600519.SH", 80), candidate("000001.SZ", 70)]
        return {
            "snapshot_id": f"ai-picks-test-{call_index}",
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
    assert captured_requests == [
        (None, 1000, True, 1825),
        (None, 1000, True, 1825),
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

    captured_requests: list[tuple[object, object, object, object]] = []

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
    ):
        captured_requests.append((symbols, limit, prefer_realtime, lookback_days))
        call_index = len(captured_requests)
        items = [candidate(f"60000{call_index}.SH", 80 - call_index)]
        return {
            "snapshot_id": f"ai-picks-agent-{call_index}",
            "selection_mode": "auto_universe" if symbols is None else "custom_symbols",
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
                "symbols": ["用户自选.SZ"],
                "agents": [
                    {"id": "deepseek", "name": "DeepSeek", "style": "momentum"},
                    {"id": "gpt", "name": "GPT", "style": "balanced"},
                ],
            },
        )

    assert response.status_code == 200
    assert captured_requests == [
        (None, 1000, True, 1825),
        (None, 1000, True, 1825),
    ]
    payload = response.json()
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
            db.add(DailyBar(symbol="000001.SZ", trade_date="20260528", close=10, amount=500))
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
    assert context["selected_candidate"]["ai_selection"]["temporal_profile"]

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
    assert payload["closing"]["reviews"][0]["memory_type"] == "closing_review"
    assert payload["learning"]["reviews"][0]["memory_type"] == "nightly_learning"

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
