from pathlib import Path
import sys
from datetime import datetime

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core import rate_limit as rate_limit_module
from app.core.config import get_settings
from app.db import database as database_module
from app.db.database import init_db, session_scope
from app.db.models import (
    AppSettings,
    DailyBar,
    FinancialIndicator,
    IndexBar,
    LimitEvent,
    MarginDetail,
    MarketDataMaintenanceRun,
    MarketReport,
    SectorBar,
    SectorMember,
    StockProfile,
)
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
                DailyBar(
                    symbol="600519.SH",
                    trade_date="20260528",
                    close=1326,
                    amount=10000,
                    turnover_rate=0.72,
                    volume_ratio=1.34,
                    pe_ttm=23.5,
                    pb=7.8,
                    moneyflow_net_amount=-8123.4,
                    moneyflow_buy_lg_amount_rate=-6.2,
                ),
                DailyBar(symbol="000001.SZ", trade_date="20260526", close=10.0, amount=1000),
                DailyBar(symbol="000001.SZ", trade_date="20260527", close=10.1, amount=1100),
                DailyBar(symbol="000001.SZ", trade_date="20260528", close=10.0, amount=900),
                IndexBar(
                    symbol="000001.SH",
                    trade_date="20260528",
                    close=3351.23,
                    pct_chg=0.78,
                    amount=488888888.0,
                ),
                IndexBar(
                    symbol="399006.SZ",
                    trade_date="20260528",
                    close=2198.12,
                    pct_chg=-1.23,
                    amount=288888888.0,
                ),
                SectorBar(
                    symbol="885001.TI",
                    name="白酒概念",
                    sector_type="N",
                    trade_date="20260528",
                    close=1332.1,
                    pct_chg=3.21,
                    turnover_rate=2.4,
                ),
                SectorBar(
                    symbol="881155.TI",
                    name="半导体",
                    sector_type="I",
                    trade_date="20260528",
                    close=998.7,
                    pct_chg=-1.12,
                    turnover_rate=1.6,
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
    assert (
        "数据源: easy_tdx, tencent, tushare_daily, tushare_moneyflow, "
        "tushare_sector_member, tushare_stock_basic, tushare_fina_indicator, "
        "tushare_limit_list_d, tushare_margin_detail, tushare_index, tushare_sector"
        in context
    )
    assert "覆盖: 实时 2/2, 日线 2/2" in context
    assert "600519.SH 贵州茅台" in context
    assert "日线动量 +10.50%" in context
    assert "换手 0.72%" in context
    assert "量比 1.34" in context
    assert "ROE 31.20%" in context
    assert "毛利 91.20%" in context
    assert "净利同比 +18.50%" in context
    assert "负债率 18.00%" in context
    assert "涨停 2连板" in context
    assert "开板 1次" in context
    assert "融资净买 9000.00万" in context
    assert "两融余额 324200.00万" in context
    assert "PE 23.50" in context
    assert "PB 7.80" in context
    assert "行业 白酒" in context
    assert "地域 贵州" in context
    assert "市场 主板" in context
    assert "净流入 -8123.40万" in context
    assert "大单 -6.20%" in context
    assert "热板块 白酒概念 +3.21%" in context
    assert "指数环境:" in context
    assert "上证指数 3351.23 (+0.78%)" in context
    assert "创业板指 2198.12 (-1.23%)" in context
    assert "板块热度:" in context
    assert "白酒概念 +3.21%" in context
    assert "半导体 -1.12%" in context
    assert "000001.SZ 平安银行" in context

    _reset_state()


def test_ai_market_context_uses_stored_universe_when_symbols_are_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.ai_market_context_service import ai_market_context_service
    from app.services.market_data_service import market_data_service

    _use_temp_db(monkeypatch, tmp_path)
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

    with session_scope() as db:
        db.add_all(
            [
                DailyBar(symbol="600519.SH", trade_date="20260528", close=100, amount=900),
                DailyBar(symbol="688001.SH", trade_date="20260527", close=80, amount=3000),
                DailyBar(symbol="002001.SZ", trade_date="20260527", close=20, amount=1000),
            ]
        )
        db.flush()
        context = ai_market_context_service.build_context(
            db,
            symbols=None,
            limit=2,
            lookback_days=3,
        )

    assert captured["symbols"] == ["688001.SH", "002001.SZ"]
    assert "688001.SH" in context
    assert "002001.SZ" in context

    _reset_state()


def test_ai_market_context_includes_recent_report_performance(monkeypatch, tmp_path) -> None:
    from app.services.ai_market_context_service import ai_market_context_service
    from app.services.market_data_service import market_data_service

    _use_temp_db(monkeypatch, tmp_path)

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
                "timestamp": "2026-05-29 10:30:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with session_scope() as db:
        db.add_all(
            [
                DailyBar(symbol="600519.SH", trade_date="20260528", close=95, amount=9500),
                DailyBar(symbol="600519.SH", trade_date="20260529", close=100, amount=10000),
                DailyBar(symbol="600519.SH", trade_date="20260601", close=110, amount=11000),
                MarketReport(
                    report_type="morning",
                    title="早盘推荐",
                    symbols_json=["600519.SH"],
                    lookback_days=3,
                    data_sources_json=["easy_tdx", "tushare_daily"],
                    coverage_payload={"realtime_symbols": 1, "daily_history_symbols": 1},
                    recommendations_payload=[
                        {
                            "symbol": "600519.SH",
                            "name": "贵州茅台",
                            "action": "WATCH",
                            "score": 88.0,
                        }
                    ],
                    dataset_payload={},
                    context_text="",
                    summary="早盘推荐已生成。",
                    created_at=datetime(2026, 5, 29, 8, 45, 0),
                ),
            ]
        )
        db.flush()
        context = ai_market_context_service.build_context(
            db,
            symbols=["600519.SH"],
            limit=1,
            lookback_days=3,
        )

    assert "历史推荐表现:" in context
    assert "早盘推荐" in context
    assert "1日均值 +10.00%" in context
    assert "600519.SH +10.00%" in context

    _reset_state()


def test_ai_market_context_includes_latest_data_quality_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.ai_market_context_service import ai_market_context_service
    from app.services.market_data_service import market_data_service

    _use_temp_db(monkeypatch, tmp_path)

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
                "timestamp": "2026-05-29 10:30:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)

    with session_scope() as db:
        db.add_all(
            [
                DailyBar(symbol="600519.SH", trade_date="20260528", close=95, amount=9500),
                MarketDataMaintenanceRun(
                    status="completed",
                    refresh_start_date="20260520",
                    refresh_end_date="20260528",
                    processed_days=9,
                    stored_count=49500,
                    skipped_count=0,
                    refresh_unique_symbols=5500,
                    dataset_universe_size=500,
                    dataset_item_count=50,
                    latest_trade_date="20260528",
                    latest_trade_date_symbols=5506,
                    most_complete_trade_date="20260528",
                    most_complete_trade_date_symbols=5506,
                    refresh_needed=False,
                    refresh_reason="最新交易日覆盖充足",
                    coverage_payload={
                        "readiness": {
                            "latest_day_complete": True,
                            "backtest_ready": True,
                        }
                    },
                    result_payload={},
                    created_at=datetime(2026, 5, 29, 8, 50, 0),
                ),
            ]
        )
        db.flush()
        context = ai_market_context_service.build_context(
            db,
            symbols=["600519.SH"],
            limit=1,
            lookback_days=3,
        )

    assert "数据质量:" in context
    assert "最近维护 20260529 08:50" in context
    assert "刷新 20260520-20260528" in context
    assert "入库 49500 条" in context
    assert "最新日 20260528 覆盖 5506 只" in context
    assert "补数状态 覆盖充足" in context

    _reset_state()


def test_ai_market_context_includes_miaoxiang_supplement_when_configured(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.ai_market_context_service import ai_market_context_service
    from app.services.market_data_service import market_data_service

    _use_temp_db(monkeypatch, tmp_path)
    calls: list[tuple[str, str]] = []

    class DummyMXClient:
        def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
            assert api_key == "mx-key"

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def screen_stocks(self, query: str):
            calls.append(("screen", query))
            return {"items": [{"code": "600519", "name": "贵州茅台", "reason": "资金关注"}]}

        def search_news(self, query: str):
            calls.append(("news", query))
            return {"items": [{"title": "白酒板块走强", "summary": "机构关注龙头"}]}

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
                "source": "tencent",
                "timestamp": "2026-05-29 10:30:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr("app.services.ai_market_context_service.MXClient", DummyMXClient)

    with session_scope() as db:
        db.add(
            AppSettings(
                mx_api_key="mx-key",
                screener_query="A股今天值得关注的强势股",
                news_query="今天A股市场热点新闻",
            )
        )
        db.add(DailyBar(symbol="600519.SH", trade_date="20260528", close=95, amount=9500))
        db.flush()
        context = ai_market_context_service.build_context(
            db,
            symbols=["600519.SH"],
            limit=1,
            lookback_days=3,
        )

    assert calls == [
        ("screen", "A股今天值得关注的强势股"),
        ("news", "今天A股市场热点新闻"),
    ]
    assert "妙想补充信号:" in context
    assert "自然语言选股" in context
    assert "600519" in context
    assert "资讯检索" in context
    assert "白酒板块走强" in context

    _reset_state()


def test_ai_market_context_keeps_quant_context_when_miaoxiang_fails(
    monkeypatch,
    tmp_path,
) -> None:
    from app.services.ai_market_context_service import ai_market_context_service
    from app.services.market_data_service import market_data_service

    _use_temp_db(monkeypatch, tmp_path)

    class FailingMXClient:
        def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def screen_stocks(self, query: str):
            raise RuntimeError("今日调用次数已达上限")

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
                "source": "tencent",
                "timestamp": "2026-05-29 10:30:03",
            }
        ]

    monkeypatch.setattr(market_data_service, "get_quotes", fake_quotes)
    monkeypatch.setattr("app.services.ai_market_context_service.MXClient", FailingMXClient)

    with session_scope() as db:
        db.add(AppSettings(mx_api_key="mx-key"))
        db.add(DailyBar(symbol="600519.SH", trade_date="20260528", close=95, amount=9500))
        db.flush()
        context = ai_market_context_service.build_context(
            db,
            symbols=["600519.SH"],
            limit=1,
            lookback_days=3,
        )

    assert "AI量化市场上下文" in context
    assert "600519.SH 贵州茅台" in context
    assert "妙想补充信号:" in context
    assert "获取失败: 今日调用次数已达上限" in context

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
