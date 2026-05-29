from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import DailyBar, IndexBar, SectorBar, SectorMember
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

    def fake_run(command, *, capture_output, text, timeout, check):
        captured["command"] = command
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        captured["check"] = check

        class Completed:
            returncode = 0
            stdout = (
                '{"code":0,"data":{"fields":["ts_code","trade_date","close","amount"],'
                '"items":[["600519.SH","20260528",1326.0,10037388.288]]}}'
            )
            stderr = ""

        return Completed()

    monkeypatch.setattr("app.services.historical_data_service.subprocess.run", fake_run)

    rows = historical_data_service.fetch_daily_rows("20260528")

    assert rows == [
        {
            "ts_code": "600519.SH",
            "trade_date": "20260528",
            "close": 1326.0,
            "amount": 10037388.288,
        }
    ]
    command = captured["command"]
    assert command[:8] == [
        "curl",
        "--silent",
        "--show-error",
        "--fail",
        "--max-time",
        "20",
        "--connect-timeout",
        "5",
    ]
    assert command[-1] == "http://tushare-proxy.test"
    assert captured["timeout"] == 25
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["check"] is False
    assert '"api_name": "daily"' in command[-2]
    assert '"token": "test-token"' in command[-2]
    assert '"trade_date": "20260528"' in command[-2]

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


def test_refresh_daily_bars_merges_tushare_daily_basic_rows(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        assert trade_date == "20260528"
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "close": 1326.0,
                "amount": 10037388.288,
            }
        ]

    def fake_fetch_daily_basic_rows(trade_date: str, symbols: list[str] | None = None):
        assert trade_date == "20260528"
        assert symbols is None
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "turnover_rate": 0.72,
                "volume_ratio": 1.34,
                "pe_ttm": 23.5,
                "pb": 7.8,
                "total_mv": 166500000.0,
                "circ_mv": 166500000.0,
            }
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(
        historical_data_service,
        "fetch_daily_basic_rows",
        fake_fetch_daily_basic_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh",
            headers=headers,
            json={"trade_date": "20260528", "symbols": None},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["stored_count"] == 1
        assert payload["daily_basic_count"] == 1

        with session_scope() as db:
            bar = db.query(DailyBar).one()
            assert bar.turnover_rate == 0.72
            assert bar.volume_ratio == 1.34
            assert bar.pe_ttm == 23.5
            assert bar.pb == 7.8
            assert bar.total_mv == 166500000.0
            assert bar.circ_mv == 166500000.0
            assert bar.raw_payload["daily_basic"]["volume_ratio"] == 1.34

    _reset_state()


def test_refresh_daily_bars_merges_tushare_moneyflow_rows(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        assert trade_date == "20260528"
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "close": 1326.0,
                "amount": 10037388.288,
            }
        ]

    def fake_fetch_daily_basic_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_moneyflow_rows(trade_date: str, symbols: list[str] | None = None):
        assert trade_date == "20260528"
        assert symbols is None
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "net_amount": 8123.4,
                "net_d5_amount": 15231.5,
                "buy_lg_amount": 5100.0,
                "buy_lg_amount_rate": 6.2,
                "buy_md_amount": 1800.0,
                "buy_md_amount_rate": 2.1,
                "buy_sm_amount": -900.0,
                "buy_sm_amount_rate": -1.1,
            }
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(
        historical_data_service,
        "fetch_daily_basic_rows",
        fake_fetch_daily_basic_rows,
    )
    monkeypatch.setattr(
        historical_data_service,
        "fetch_moneyflow_rows",
        fake_fetch_moneyflow_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh",
            headers=headers,
            json={"trade_date": "20260528", "symbols": None},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["stored_count"] == 1
        assert payload["moneyflow_count"] == 1

        with session_scope() as db:
            bar = db.query(DailyBar).one()
            assert bar.moneyflow_net_amount == 8123.4
            assert bar.moneyflow_net_d5_amount == 15231.5
            assert bar.moneyflow_buy_lg_amount == 5100.0
            assert bar.moneyflow_buy_lg_amount_rate == 6.2
            assert bar.moneyflow_buy_md_amount == 1800.0
            assert bar.moneyflow_buy_sm_amount == -900.0
            assert bar.raw_payload["moneyflow"]["net_amount"] == 8123.4

    _reset_state()


def test_refresh_daily_bars_stores_tushare_index_daily_rows(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "close": 1326.0,
                "amount": 10037388.288,
            }
        ]

    def fake_fetch_daily_basic_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_moneyflow_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_index_daily_rows(trade_date: str):
        assert trade_date == "20260528"
        return [
            {
                "ts_code": "000001.SH",
                "trade_date": "20260528",
                "close": 3351.23,
                "pct_chg": 0.78,
                "amount": 488888888.0,
            },
            {
                "ts_code": "399006.SZ",
                "trade_date": "20260528",
                "close": 2198.12,
                "pct_chg": -1.23,
                "amount": 288888888.0,
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", fake_fetch_daily_basic_rows)
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", fake_fetch_moneyflow_rows)
    monkeypatch.setattr(
        historical_data_service,
        "fetch_index_daily_rows",
        fake_fetch_index_daily_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh",
            headers=headers,
            json={"trade_date": "20260528", "symbols": None},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["stored_count"] == 1
        assert payload["index_count"] == 2

        with session_scope() as db:
            rows = db.query(IndexBar).order_by(IndexBar.symbol).all()
            assert [(row.symbol, row.close, row.pct_chg) for row in rows] == [
                ("000001.SH", 3351.23, 0.78),
                ("399006.SZ", 2198.12, -1.23),
            ]

    _reset_state()


def test_refresh_daily_bars_stores_tushare_sector_rows(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "close": 1326.0,
                "amount": 10037388.288,
            }
        ]

    def fake_fetch_daily_basic_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_moneyflow_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_index_daily_rows(trade_date: str):
        return []

    def fake_fetch_sector_index_rows():
        return [
            {"ts_code": "885001.TI", "name": "白酒概念", "type": "N"},
            {"ts_code": "885002.TI", "name": "食品饮料", "type": "N"},
            {"ts_code": "881155.TI", "name": "半导体", "type": "I"},
        ]

    def fake_fetch_sector_daily_rows(trade_date: str):
        assert trade_date == "20260528"
        return [
            {
                "ts_code": "885001.TI",
                "trade_date": "20260528",
                "close": 1332.1,
                "pct_change": 3.21,
                "turnover_rate": 2.4,
                "total_mv": 123456789.0,
                "float_mv": 98765432.0,
            },
            {
                "ts_code": "881155.TI",
                "trade_date": "20260528",
                "close": 998.7,
                "pct_change": -1.12,
                "turnover_rate": 1.6,
                "total_mv": 223456789.0,
                "float_mv": 187654321.0,
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", fake_fetch_daily_basic_rows)
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", fake_fetch_moneyflow_rows)
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", fake_fetch_index_daily_rows)
    monkeypatch.setattr(
        historical_data_service,
        "fetch_sector_index_rows",
        fake_fetch_sector_index_rows,
        raising=False,
    )
    monkeypatch.setattr(
        historical_data_service,
        "fetch_sector_daily_rows",
        fake_fetch_sector_daily_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh",
            headers=headers,
            json={"trade_date": "20260528", "symbols": None},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["stored_count"] == 1
        assert payload["sector_count"] == 2

        with session_scope() as db:
            rows = db.query(SectorBar).order_by(SectorBar.symbol).all()
            assert [(row.symbol, row.name, row.sector_type, row.pct_chg) for row in rows] == [
                ("881155.TI", "半导体", "I", -1.12),
                ("885001.TI", "白酒概念", "N", 3.21),
            ]

    _reset_state()


def test_refresh_daily_bars_stores_hot_sector_members(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": "20260528",
                "close": 1326.0,
                "amount": 10037388.288,
            }
        ]

    def fake_fetch_daily_basic_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_moneyflow_rows(trade_date: str, symbols: list[str] | None = None):
        return []

    def fake_fetch_index_daily_rows(trade_date: str):
        return []

    def fake_fetch_sector_index_rows():
        return [
            {"ts_code": "885001.TI", "name": "白酒概念", "type": "N"},
            {"ts_code": "881155.TI", "name": "半导体", "type": "I"},
        ]

    def fake_fetch_sector_daily_rows(trade_date: str):
        return [
            {"ts_code": "885001.TI", "trade_date": "20260528", "pct_change": 3.21},
            {"ts_code": "885002.TI", "trade_date": "20260528", "pct_change": 1.08},
            {"ts_code": "881155.TI", "trade_date": "20260528", "pct_change": -1.12},
        ]

    def fake_fetch_sector_member_rows(sector_symbols: list[str]):
        assert sector_symbols == ["885001.TI", "885002.TI"]
        return [
            {
                "ts_code": "885001.TI",
                "con_code": "600519.SH",
                "con_name": "贵州茅台",
                "is_new": "Y",
            },
            {
                "ts_code": "885001.TI",
                "con_code": "001282.HK",
                "con_name": "港股样本",
                "is_new": "N",
            }
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", fake_fetch_daily_basic_rows)
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", fake_fetch_moneyflow_rows)
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", fake_fetch_index_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_sector_index_rows", fake_fetch_sector_index_rows)
    monkeypatch.setattr(historical_data_service, "fetch_sector_daily_rows", fake_fetch_sector_daily_rows)
    monkeypatch.setattr(
        historical_data_service,
        "fetch_sector_member_rows",
        fake_fetch_sector_member_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh",
            headers=headers,
            json={"trade_date": "20260528", "symbols": None},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["sector_count"] == 3
        assert payload["sector_member_count"] == 1

        with session_scope() as db:
            member = db.query(SectorMember).one()
            assert member.sector_symbol == "885001.TI"
            assert member.sector_name == "白酒概念"
            assert member.stock_symbol == "600519.SH"
            assert member.stock_name == "贵州茅台"
            assert member.is_new == "Y"

    _reset_state()


def test_fetch_sector_member_rows_keeps_partial_success(monkeypatch) -> None:
    from app.services.historical_data_service import historical_data_service

    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    get_settings.cache_clear()

    def fake_request(params: dict[str, str]):
        if params["ts_code"] == "885002.TI":
            raise RuntimeError("Tushare ths_member 请求超时: {'ts_code': '885002.TI'}")
        return [{"ts_code": params["ts_code"], "con_code": "600519.SH"}]

    monkeypatch.setattr(
        historical_data_service,
        "_request_tushare_ths_member",
        fake_request,
    )

    rows = historical_data_service.fetch_sector_member_rows(
        ["885001.TI", "885002.TI", "885003.TI"]
    )

    assert len(rows) == 2
    assert "1 个板块成分拉取失败" in historical_data_service._last_sector_member_error

    _reset_state()


def test_refresh_stock_profiles_stores_tushare_stock_basic_rows(monkeypatch, tmp_path) -> None:
    from app.db.models import StockProfile
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_stock_basic_rows():
        return [
            {
                "ts_code": "600519.SH",
                "symbol": "600519",
                "name": "贵州茅台",
                "area": "贵州",
                "industry": "白酒",
                "market": "主板",
                "exchange": "SSE",
                "list_status": "L",
                "list_date": "20010827",
                "is_hs": "H",
            },
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "平安银行",
                "area": "深圳",
                "industry": "银行",
                "market": "主板",
                "exchange": "SZSE",
                "list_status": "L",
                "list_date": "19910403",
                "is_hs": "S",
            },
        ]

    monkeypatch.setattr(
        historical_data_service,
        "fetch_stock_basic_rows",
        fake_fetch_stock_basic_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_stock_profiles(db)
            profiles = db.query(StockProfile).order_by(StockProfile.symbol).all()

    assert result == {
        "source": "tushare_stock_basic",
        "stored_count": 2,
        "error": None,
    }
    assert [(item.symbol, item.name, item.industry, item.area) for item in profiles] == [
        ("000001.SZ", "平安银行", "银行", "深圳"),
        ("600519.SH", "贵州茅台", "白酒", "贵州"),
    ]

    _reset_state()


def test_refresh_financial_indicators_stores_tushare_fina_indicator_rows(monkeypatch, tmp_path) -> None:
    from app.db.models import FinancialIndicator
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_financial_rows(symbols):
        assert symbols == ["600519.SH", "000001.SZ"]
        return [
            {
                "ts_code": "600519.SH",
                "ann_date": "20260402",
                "end_date": "20251231",
                "roe": 31.2,
                "roe_dt": 30.1,
                "grossprofit_margin": 91.2,
                "netprofit_margin": 52.3,
                "netprofit_yoy": 18.5,
                "or_yoy": 15.6,
                "debt_to_assets": 18.0,
                "assets_turn": 0.48,
                "current_ratio": 4.2,
            },
            {
                "ts_code": "600519.SH",
                "ann_date": "20260402",
                "end_date": "20251231",
                "roe": 31.8,
                "grossprofit_margin": 91.6,
                "netprofit_yoy": 19.1,
                "debt_to_assets": 17.8,
            },
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260420",
                "end_date": "20251231",
                "roe": 12.1,
                "grossprofit_margin": None,
                "netprofit_yoy": 4.5,
                "debt_to_assets": 91.0,
            },
        ]

    monkeypatch.setattr(
        historical_data_service,
        "fetch_financial_indicator_rows",
        fake_fetch_financial_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_financial_indicators(
                db,
                ["600519.SH", "000001.SZ"],
            )
            rows = db.query(FinancialIndicator).order_by(FinancialIndicator.symbol).all()

    assert result == {
        "source": "tushare_fina_indicator",
        "stored_count": 2,
        "error": None,
        "requested_symbols": ["600519.SH", "000001.SZ"],
    }
    assert [row.symbol for row in rows] == ["000001.SZ", "600519.SH"]
    assert rows[1].ann_date == "20260402"
    assert rows[1].end_date == "20251231"
    assert rows[1].roe == 31.8
    assert rows[1].grossprofit_margin == 91.6
    assert rows[1].netprofit_yoy == 19.1
    assert rows[1].debt_to_assets == 17.8
    assert rows[1].source == "tushare_fina_indicator"
    assert rows[1].raw_payload["ts_code"] == "600519.SH"

    _reset_state()


def test_refresh_daily_bars_stores_tushare_limit_events(monkeypatch, tmp_path) -> None:
    from app.db.models import LimitEvent
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date, symbols=None):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "open": 100,
                "high": 110,
                "low": 99,
                "close": 110,
                "amount": 9000,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": trade_date,
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "amount": 1000,
            },
        ]

    def fake_fetch_limit_rows(trade_date):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "industry": "白酒",
                "name": "贵州茅台",
                "close": 110,
                "pct_chg": 10.0,
                "amount": 9000,
                "turnover_ratio": 3.2,
                "fd_amount": 120000000,
                "first_time": "093100",
                "last_time": "145700",
                "open_times": 1,
                "up_stat": "2/3",
                "limit_times": 2,
                "limit": "U",
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": trade_date,
                "industry": "银行",
                "name": "平安银行",
                "close": 9,
                "pct_chg": -10.0,
                "open_times": 3,
                "limit": "Z",
            },
            {
                "ts_code": "300750.SZ",
                "trade_date": trade_date,
                "name": "宁德时代",
                "close": 200,
                "pct_chg": -20.0,
                "limit": "D",
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_index_rows", lambda *args: [])
    monkeypatch.setattr(
        historical_data_service,
        "fetch_limit_list_rows",
        fake_fetch_limit_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_daily_bars(
                db,
                trade_date="20260528",
                symbols=["600519.SH", "000001.SZ"],
            )
            events = db.query(LimitEvent).order_by(LimitEvent.symbol).all()

    assert result["limit_event_count"] == 2
    assert result["limit_event_error"] is None
    assert [(item.symbol, item.limit_type) for item in events] == [
        ("000001.SZ", "Z"),
        ("600519.SH", "U"),
    ]
    assert events[1].name == "贵州茅台"
    assert events[1].industry == "白酒"
    assert events[1].limit_times == 2
    assert events[1].open_times == 1
    assert events[1].fd_amount == 120000000
    assert events[1].source == "tushare_limit_list_d"

    _reset_state()


def test_refresh_daily_bars_stores_tushare_margin_details(monkeypatch, tmp_path) -> None:
    from app.db.models import MarginDetail
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date, symbols=None):
        return [
            {"ts_code": "600519.SH", "trade_date": trade_date, "close": 100, "amount": 9000},
            {"ts_code": "000001.SZ", "trade_date": trade_date, "close": 10, "amount": 1000},
        ]

    def fake_fetch_margin_rows(trade_date):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "name": "贵州茅台",
                "rzye": 3_200_000_000,
                "rqye": 42_000_000,
                "rzmre": 180_000_000,
                "rqyl": 120_000,
                "rzche": 90_000_000,
                "rqchl": 15_000,
                "rqmcl": 22_000,
                "rzrqye": 3_242_000_000,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": trade_date,
                "name": "平安银行",
                "rzye": 1_100_000_000,
                "rqye": 12_000_000,
                "rzmre": 20_000_000,
                "rzche": 40_000_000,
                "rzrqye": 1_112_000_000,
            },
            {
                "ts_code": "300750.SZ",
                "trade_date": trade_date,
                "name": "宁德时代",
                "rzye": 5_000_000_000,
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_index_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_limit_list_rows", lambda *args: [])
    monkeypatch.setattr(
        historical_data_service,
        "fetch_margin_detail_rows",
        fake_fetch_margin_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_daily_bars(
                db,
                trade_date="20260528",
                symbols=["600519.SH", "000001.SZ"],
            )
            rows = db.query(MarginDetail).order_by(MarginDetail.symbol).all()

    assert result["margin_detail_count"] == 2
    assert result["margin_detail_error"] is None
    assert [(item.symbol, item.name) for item in rows] == [
        ("000001.SZ", "平安银行"),
        ("600519.SH", "贵州茅台"),
    ]
    assert rows[1].rzye == 3_200_000_000
    assert rows[1].rqye == 42_000_000
    assert rows[1].rzmre == 180_000_000
    assert rows[1].rzche == 90_000_000
    assert rows[1].rzrqye == 3_242_000_000
    assert rows[1].source == "tushare_margin_detail"

    _reset_state()


def test_refresh_daily_bars_stores_tushare_dragon_tiger_rows(monkeypatch, tmp_path) -> None:
    from app.db.models import DragonTigerInstitution, DragonTigerList
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date, symbols=None):
        return [
            {"ts_code": "600519.SH", "trade_date": trade_date, "close": 100, "amount": 9000},
            {"ts_code": "000001.SZ", "trade_date": trade_date, "close": 10, "amount": 1000},
        ]

    def fake_top_list_rows(trade_date):
        return [
            {
                "trade_date": trade_date,
                "ts_code": "600519.SH",
                "name": "贵州茅台",
                "close": 100,
                "pct_change": 7.1,
                "turnover_rate": 8.2,
                "amount": 2_000_000_000,
                "l_sell": 120_000_000,
                "l_buy": 210_000_000,
                "l_amount": 330_000_000,
                "net_amount": 90_000_000,
                "net_rate": 4.5,
                "amount_rate": 16.5,
                "float_values": 1_500_000_000_000,
                "reason": "涨幅偏离值达7%的证券",
            },
            {
                "trade_date": trade_date,
                "ts_code": "300750.SZ",
                "name": "宁德时代",
                "reason": "振幅值达15%的证券",
            },
        ]

    def fake_top_inst_rows(trade_date):
        return [
            {
                "trade_date": trade_date,
                "ts_code": "600519.SH",
                "exalter": "机构专用",
                "side": "0",
                "buy": 10_000_000,
                "sell": 2_000_000,
                "net_buy": 8_000_000,
                "reason": "涨幅偏离值达7%的证券",
            },
            {
                "trade_date": trade_date,
                "ts_code": "600519.SH",
                "exalter": "机构专用",
                "side": "0",
                "buy": 60_000_000,
                "buy_rate": 3.0,
                "sell": 10_000_000,
                "sell_rate": 0.5,
                "net_buy": 50_000_000,
                "reason": "涨幅偏离值达7%的证券",
            },
            {
                "trade_date": trade_date,
                "ts_code": "600519.SH",
                "exalter": "机构专用",
                "side": "1",
                "buy": 5_000_000,
                "sell": 20_000_000,
                "net_buy": -15_000_000,
                "reason": "涨幅偏离值达7%的证券",
            },
            {
                "trade_date": trade_date,
                "ts_code": "300750.SZ",
                "exalter": "机构专用",
                "side": "0",
                "buy": 100_000_000,
                "net_buy": 100_000_000,
                "reason": "振幅值达15%的证券",
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_index_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_limit_list_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_margin_detail_rows", lambda *args: [])
    monkeypatch.setattr(
        historical_data_service,
        "fetch_top_list_rows",
        fake_top_list_rows,
        raising=False,
    )
    monkeypatch.setattr(
        historical_data_service,
        "fetch_top_inst_rows",
        fake_top_inst_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_daily_bars(
                db,
                trade_date="20260528",
                symbols=["600519.SH", "000001.SZ"],
            )
            lists = db.query(DragonTigerList).all()
            institutions = db.query(DragonTigerInstitution).order_by(
                DragonTigerInstitution.net_buy.desc()
            ).all()

    assert result["dragon_tiger_count"] == 1
    assert result["dragon_tiger_inst_count"] == 2
    assert result["dragon_tiger_error"] is None
    assert result["dragon_tiger_inst_error"] is None
    assert lists[0].symbol == "600519.SH"
    assert lists[0].net_amount == 90_000_000
    assert lists[0].reason == "涨幅偏离值达7%的证券"
    assert institutions[0].exalter == "机构专用"
    assert institutions[0].buy == 70_000_000
    assert institutions[0].sell == 12_000_000
    assert institutions[0].net_buy == 58_000_000
    assert institutions[0].source == "tushare_top_inst"

    _reset_state()


def test_refresh_daily_bars_stores_tushare_block_trades(monkeypatch, tmp_path) -> None:
    from app.db.models import BlockTrade
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date, symbols=None):
        return [
            {"ts_code": "600519.SH", "trade_date": trade_date, "close": 100, "amount": 9000},
            {"ts_code": "000001.SZ", "trade_date": trade_date, "close": 10, "amount": 1000},
        ]

    def fake_block_trade_rows(trade_date):
        return [
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "price": 98.5,
                "vol": 120.0,
                "amount": 11820.0,
                "buyer": "机构专用",
                "seller": "中信证券总部",
            },
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "price": 98.5,
                "vol": 120.0,
                "amount": 11820.0,
                "buyer": "机构专用",
                "seller": "中信证券总部",
            },
            {
                "ts_code": "600519.SH",
                "trade_date": trade_date,
                "price": 101.5,
                "vol": 80.0,
                "amount": 8120.0,
                "buyer": "华泰证券上海营业部",
                "seller": "机构专用",
            },
            {
                "ts_code": "300750.SZ",
                "trade_date": trade_date,
                "price": 200.0,
                "vol": 20.0,
                "amount": 4000.0,
                "buyer": "机构专用",
                "seller": "机构专用",
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_index_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_limit_list_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_margin_detail_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_top_list_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_top_inst_rows", lambda *args: [])
    monkeypatch.setattr(
        historical_data_service,
        "fetch_block_trade_rows",
        fake_block_trade_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_daily_bars(
                db,
                trade_date="20260528",
                symbols=["600519.SH", "000001.SZ"],
            )
            rows = db.query(BlockTrade).order_by(BlockTrade.amount.desc()).all()

    assert result["block_trade_count"] == 2
    assert result["block_trade_error"] is None
    assert [(item.symbol, item.price, item.vol) for item in rows] == [
        ("600519.SH", 98.5, 120.0),
        ("600519.SH", 101.5, 80.0),
    ]
    assert rows[0].buyer == "机构专用"
    assert rows[0].seller == "中信证券总部"
    assert rows[0].source == "tushare_block_trade"

    _reset_state()


def test_refresh_daily_bars_stores_tushare_shareholder_rows(monkeypatch, tmp_path) -> None:
    from app.db.models import ShareholderNumber, ShareholderTrade
    from app.services.historical_data_service import historical_data_service

    def fake_fetch_daily_rows(trade_date, symbols=None):
        return [
            {"ts_code": "600519.SH", "trade_date": trade_date, "close": 100, "amount": 9000},
            {"ts_code": "000001.SZ", "trade_date": trade_date, "close": 10, "amount": 1000},
        ]

    def fake_holder_number_rows(ann_date):
        return [
            {
                "ts_code": "600519.SH",
                "ann_date": ann_date,
                "end_date": "20260331",
                "holder_num": 88000,
            },
            {
                "ts_code": "300750.SZ",
                "ann_date": ann_date,
                "end_date": "20260331",
                "holder_num": 110000,
            },
        ]

    def fake_holder_trade_rows(ann_date):
        return [
            {
                "ts_code": "600519.SH",
                "ann_date": ann_date,
                "holder_name": "贵州国资公司",
                "holder_type": "C",
                "in_de": "IN",
                "change_vol": 120.0,
                "change_ratio": 0.18,
                "after_share": 5000.0,
                "after_ratio": 4.1,
                "avg_price": 1320.0,
                "total_share": 5000.0,
                "begin_date": "20260501",
                "close_date": "20260528",
            },
            {
                "ts_code": "600519.SH",
                "ann_date": ann_date,
                "holder_name": "某高管",
                "holder_type": "G",
                "in_de": "DE",
                "change_vol": 20.0,
                "change_ratio": 0.03,
                "after_share": 80.0,
                "after_ratio": 0.06,
                "avg_price": 1330.0,
                "total_share": 80.0,
                "begin_date": "20260510",
                "close_date": "20260520",
            },
            {
                "ts_code": "300750.SZ",
                "ann_date": ann_date,
                "holder_name": "宁德股东",
                "holder_type": "C",
                "in_de": "IN",
                "change_vol": 50.0,
                "change_ratio": 0.1,
            },
        ]

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)
    monkeypatch.setattr(historical_data_service, "fetch_daily_basic_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_moneyflow_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_index_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_daily_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_sector_index_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_limit_list_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_margin_detail_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_top_list_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_top_inst_rows", lambda *args: [])
    monkeypatch.setattr(historical_data_service, "fetch_block_trade_rows", lambda *args: [])
    monkeypatch.setattr(
        historical_data_service,
        "fetch_shareholder_number_rows",
        fake_holder_number_rows,
        raising=False,
    )
    monkeypatch.setattr(
        historical_data_service,
        "fetch_shareholder_trade_rows",
        fake_holder_trade_rows,
        raising=False,
    )

    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            result = historical_data_service.refresh_daily_bars(
                db,
                trade_date="20260528",
                symbols=["600519.SH", "000001.SZ"],
            )
            numbers = db.query(ShareholderNumber).all()
            trades = db.query(ShareholderTrade).order_by(ShareholderTrade.change_vol.desc()).all()

    assert result["shareholder_number_count"] == 1
    assert result["shareholder_trade_count"] == 2
    assert result["shareholder_number_error"] is None
    assert result["shareholder_trade_error"] is None
    assert numbers[0].symbol == "600519.SH"
    assert numbers[0].holder_num == 88000
    assert numbers[0].source == "tushare_stk_holdernumber"
    assert trades[0].holder_name == "贵州国资公司"
    assert trades[0].in_de == "IN"
    assert trades[0].change_ratio == 0.18
    assert trades[0].source == "tushare_stk_holdertrade"

    _reset_state()


def test_refresh_daily_range_summarizes_enriched_data_sources(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    results_by_date = {
        "20260527": {
            "trade_date": "20260527",
            "source": "tushare",
            "stored_count": 2,
            "skipped_count": 0,
            "daily_basic_count": 2,
            "daily_basic_error": None,
            "moneyflow_count": 2,
            "moneyflow_error": None,
            "index_count": 5,
            "index_error": None,
            "sector_count": 1508,
            "sector_error": None,
            "sector_member_count": 120000,
            "sector_member_error": None,
            "requested_symbols": [],
        },
        "20260528": {
            "trade_date": "20260528",
            "source": "tushare",
            "stored_count": 3,
            "skipped_count": 0,
            "daily_basic_count": 3,
            "daily_basic_error": None,
            "moneyflow_count": 2,
            "moneyflow_error": "moneyflow partial",
            "index_count": 5,
            "index_error": None,
            "sector_count": 1508,
            "sector_error": None,
            "sector_member_count": 90000,
            "sector_member_error": "2 个板块成分拉取失败",
            "requested_symbols": [],
        },
    }

    def fake_refresh_daily_bars(db, *, trade_date: str, symbols=None):
        return results_by_date[trade_date]

    progress_updates: list[dict[str, object]] = []
    monkeypatch.setattr(historical_data_service, "refresh_daily_bars", fake_refresh_daily_bars)
    with create_test_client(monkeypatch, tmp_path):
        with session_scope() as db:
            payload = historical_data_service.refresh_daily_range(
                db,
                start_date="20260527",
                end_date="20260528",
                progress_callback=progress_updates.append,
            )

    assert payload["data_source_counts"] == {
        "tushare_daily": 5,
        "tushare_daily_basic": 5,
        "tushare_moneyflow_ths": 4,
        "tushare_index_daily": 10,
        "tushare_sector": 3016,
        "tushare_sector_member": 210000,
        "tushare_limit_list_d": 0,
        "tushare_margin_detail": 0,
        "tushare_top_list": 0,
        "tushare_top_inst": 0,
        "tushare_block_trade": 0,
        "tushare_stk_holdernumber": 0,
        "tushare_stk_holdertrade": 0,
    }
    assert payload["data_source_errors"] == {
        "tushare_moneyflow_ths": ["20260528: moneyflow partial"],
        "tushare_sector_member": ["20260528: 2 个板块成分拉取失败"],
    }
    assert progress_updates[-1]["data_source_counts"]["tushare_sector_member"] == 210000
    assert progress_updates[-1]["data_source_error_count"] == 2

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


def test_refresh_daily_range_allows_maximized_backfill_window(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    calls: list[str] = []

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        assert symbols is None
        calls.append(trade_date)
        return []

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh-range",
            headers=headers,
            json={
                "start_date": "20260101",
                "end_date": "20260501",
                "symbols": None,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed_days"] == 121
    assert payload["stored_count"] == 0
    assert len(calls) == 121

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


def test_refresh_daily_range_stops_after_consecutive_failures(monkeypatch, tmp_path) -> None:
    from app.services.historical_data_service import historical_data_service

    calls: list[str] = []

    def fake_fetch_daily_rows(trade_date: str, symbols: list[str] | None = None):
        calls.append(trade_date)
        raise RuntimeError("Tushare 日线请求超时")

    monkeypatch.setattr(historical_data_service, "fetch_daily_rows", fake_fetch_daily_rows)

    with create_test_client(monkeypatch, tmp_path) as client:
        headers = _auth_headers(client)
        response = client.post(
            "/api/aniu/market/daily/refresh-range",
            headers=headers,
            json={
                "start_date": "20260525",
                "end_date": "20260529",
                "symbols": None,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["20260525", "20260526", "20260527"]
    assert payload["processed_days"] == 5
    assert payload["stored_count"] == 0
    assert payload["skipped_count"] == 5
    assert [item["trade_date"] for item in payload["daily_results"]] == [
        "20260525",
        "20260526",
        "20260527",
        "20260528",
        "20260529",
    ]
    assert "连续 3 天刷新失败" in payload["daily_results"][3]["error"]

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
