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
            {"ts_code": "881155.TI", "trade_date": "20260528", "pct_change": -1.12},
        ]

    def fake_fetch_sector_member_rows(sector_symbols: list[str]):
        assert sector_symbols == ["885001.TI"]
        return [
            {
                "ts_code": "885001.TI",
                "con_code": "600519.SH",
                "con_name": "贵州茅台",
                "is_new": "Y",
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
        assert payload["sector_count"] == 2
        assert payload["sector_member_count"] == 1

        with session_scope() as db:
            member = db.query(SectorMember).one()
            assert member.sector_symbol == "885001.TI"
            assert member.sector_name == "白酒概念"
            assert member.stock_symbol == "600519.SH"
            assert member.stock_name == "贵州茅台"
            assert member.is_new == "Y"

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
