from __future__ import annotations

from datetime import datetime, timedelta
import threading
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import session_scope
from app.db.models import MarketDataMaintenanceRun
from app.services.historical_data_service import historical_data_service
from app.services.market_data_service import normalize_symbol
from app.services.market_report_service import market_report_service
from app.services.quant_service import quant_service


class MarketDataMaintenanceService:
    def __init__(self) -> None:
        self._completed_slots: set[str] = set()
        self._job_lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def reset_runtime_state(self) -> None:
        self._completed_slots.clear()
        with self._job_lock:
            self._jobs.clear()

    def process_due_jobs(self, *, now: datetime | None = None) -> dict[str, Any]:
        settings = get_settings()
        if not settings.market_data_maintenance_enabled:
            return {"status": "disabled"}

        current = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
            ZoneInfo("Asia/Shanghai")
        )
        due_time = self._due_time(current, settings.market_data_maintenance_times)
        if due_time is None:
            return {"status": "skipped", "reason": "not_due"}

        slot_key = f"{current.strftime('%Y%m%d')}:{due_time}"
        if slot_key in self._completed_slots:
            return {"status": "skipped", "reason": "already_completed", "slot": slot_key}

        with session_scope() as db:
            result = self.run_now(
                db,
                end_date=current.strftime("%Y%m%d"),
                lookback_days=settings.market_data_maintenance_lookback_days,
                symbols=None,
                dataset_limit=settings.market_data_maintenance_dataset_limit,
                report_type=self._report_type_for_time(due_time),
            )
        self._completed_slots.add(slot_key)
        return {"status": "completed", "slot": slot_key, "result": result}

    def run_now(
        self,
        db: Session,
        *,
        end_date: str | None = None,
        lookback_days: int | None = None,
        symbols: list[str] | None = None,
        dataset_limit: int | None = None,
        report_type: str | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        settings = get_settings()
        normalized_lookback = max(
            1,
            min(120, int(lookback_days or settings.market_data_maintenance_lookback_days)),
        )
        normalized_limit = max(
            1,
            min(500, int(dataset_limit or settings.market_data_maintenance_dataset_limit)),
        )
        normalized_end = self._normalize_end_date(end_date)
        start_date = (
            datetime.strptime(normalized_end, "%Y%m%d").date()
            - timedelta(days=normalized_lookback - 1)
        ).strftime("%Y%m%d")
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        if normalized_symbols is None:
            coverage = historical_data_service.summarize_daily_coverage(db)
            suggestion = coverage.get("refresh_suggestion") or {}
            if suggestion.get("needed") and suggestion.get("start_date") and suggestion.get("end_date"):
                start_date = str(suggestion["start_date"])
                normalized_end = str(suggestion["end_date"])
        refresh = historical_data_service.refresh_daily_range(
            db,
            start_date=start_date,
            end_date=normalized_end,
            symbols=normalized_symbols,
            progress_callback=progress_callback,
        )
        if progress_callback:
            progress_callback(
                {
                    "phase": "building_dataset",
                    "total_days": refresh.get("processed_days"),
                    "processed_days": refresh.get("processed_days"),
                    "current_trade_date": refresh.get("end_date"),
                    "stored_count": refresh.get("stored_count"),
                    "skipped_count": refresh.get("skipped_count"),
                    "error_count": sum(
                        1 for item in refresh.get("daily_results", []) if item.get("error")
                    ),
                }
            )
        dataset = quant_service.build_dataset(
            db,
            symbols=normalized_symbols,
            limit=normalized_limit,
            prefer_realtime=True,
            lookback_days=normalized_lookback,
        )
        result: dict[str, Any] = {
            "status": "completed",
            "refresh": refresh,
            "dataset": dataset,
        }
        if report_type:
            result["report"] = market_report_service.generate_report(
                db,
                report_type=report_type,
                symbols=normalized_symbols,
                limit=min(50, normalized_limit),
                lookback_days=normalized_lookback,
            )
        coverage_after = historical_data_service.summarize_daily_coverage(db)
        self._record_run(db, result=result, coverage=coverage_after)
        return result

    def list_runs(self, db: Session, *, limit: int = 20) -> dict[str, Any]:
        rows = db.scalars(
            select(MarketDataMaintenanceRun)
            .order_by(MarketDataMaintenanceRun.id.desc())
            .limit(max(1, min(100, int(limit))))
        ).all()
        return {"items": [self._run_payload(row) for row in rows]}

    def start_job(
        self,
        *,
        end_date: str | None = None,
        lookback_days: int | None = None,
        symbols: list[str] | None = None,
        dataset_limit: int | None = None,
        report_type: str | None = None,
    ) -> dict[str, Any]:
        with self._job_lock:
            running = self._running_job_locked()
            if running is not None:
                return dict(running)

            job_id = uuid4().hex
            job = {
                "job_id": job_id,
                "status": "queued",
                "submitted_at": datetime.now(ZoneInfo("Asia/Shanghai")),
                "completed_at": None,
                "progress": None,
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            kwargs={
                "job_id": job_id,
                "end_date": end_date,
                "lookback_days": lookback_days,
                "symbols": list(symbols) if symbols else None,
                "dataset_limit": dataset_limit,
                "report_type": report_type,
            },
            daemon=True,
        )
        thread.start()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._job_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise LookupError(f"维护任务不存在: {job_id}")
            return dict(job)

    def _running_job_locked(self) -> dict[str, Any] | None:
        for job in self._jobs.values():
            if job.get("status") in {"queued", "running"}:
                return job
        return None

    def _run_job(
        self,
        *,
        job_id: str,
        end_date: str | None,
        lookback_days: int | None,
        symbols: list[str] | None,
        dataset_limit: int | None,
        report_type: str | None,
    ) -> None:
        with self._job_lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = "running"
        try:
            with session_scope() as db:
                result = self.run_now(
                    db,
                    end_date=end_date,
                    lookback_days=lookback_days,
                    symbols=symbols,
                    dataset_limit=dataset_limit,
                    report_type=report_type,
                    progress_callback=lambda progress: self._update_job_progress(
                        job_id,
                        progress,
                    ),
                )
            with self._job_lock:
                job = self._jobs[job_id]
                job["status"] = "completed"
                job["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai"))
                job["progress"] = self._completed_progress(result)
                job["result"] = result
        except Exception as exc:
            with self._job_lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["completed_at"] = datetime.now(ZoneInfo("Asia/Shanghai"))
                job["error"] = str(exc)

    def _update_job_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._job_lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["progress"] = dict(progress)

    def _completed_progress(self, result: dict[str, Any]) -> dict[str, Any] | None:
        refresh = result.get("refresh") or {}
        if not refresh:
            return None
        daily_results = refresh.get("daily_results") or []
        return {
            "phase": "completed",
            "total_days": refresh.get("processed_days"),
            "processed_days": refresh.get("processed_days"),
            "current_trade_date": refresh.get("end_date"),
            "stored_count": refresh.get("stored_count"),
            "skipped_count": refresh.get("skipped_count"),
            "error_count": sum(1 for item in daily_results if item.get("error")),
        }

    def _record_run(
        self,
        db: Session,
        *,
        result: dict[str, Any],
        coverage: dict[str, Any],
    ) -> None:
        refresh = result.get("refresh") or {}
        dataset = result.get("dataset") or {}
        suggestion = coverage.get("refresh_suggestion") or {}
        record = MarketDataMaintenanceRun(
            status=str(result.get("status") or "completed"),
            refresh_start_date=refresh.get("start_date"),
            refresh_end_date=refresh.get("end_date"),
            processed_days=int(refresh.get("processed_days") or 0),
            stored_count=int(refresh.get("stored_count") or 0),
            skipped_count=int(refresh.get("skipped_count") or 0),
            refresh_unique_symbols=int(refresh.get("unique_symbols") or 0),
            dataset_universe_size=int(dataset.get("universe_size") or 0),
            dataset_item_count=int(dataset.get("item_count") or 0),
            latest_trade_date=coverage.get("latest_trade_date"),
            latest_trade_date_symbols=int(coverage.get("latest_trade_date_symbols") or 0),
            most_complete_trade_date=coverage.get("most_complete_trade_date"),
            most_complete_trade_date_symbols=int(
                coverage.get("most_complete_trade_date_symbols") or 0
            ),
            refresh_needed=bool(suggestion.get("needed")),
            refresh_reason=str(suggestion.get("reason") or ""),
            coverage_payload=coverage,
            result_payload=result,
        )
        db.add(record)
        db.commit()

    def _run_payload(self, row: MarketDataMaintenanceRun) -> dict[str, Any]:
        return {
            "id": row.id,
            "status": row.status,
            "refresh_start_date": row.refresh_start_date,
            "refresh_end_date": row.refresh_end_date,
            "processed_days": row.processed_days,
            "stored_count": row.stored_count,
            "skipped_count": row.skipped_count,
            "refresh_unique_symbols": row.refresh_unique_symbols,
            "dataset_universe_size": row.dataset_universe_size,
            "dataset_item_count": row.dataset_item_count,
            "latest_trade_date": row.latest_trade_date,
            "latest_trade_date_symbols": row.latest_trade_date_symbols,
            "most_complete_trade_date": row.most_complete_trade_date,
            "most_complete_trade_date_symbols": row.most_complete_trade_date_symbols,
            "refresh_needed": row.refresh_needed,
            "refresh_reason": row.refresh_reason,
            "coverage": row.coverage_payload or {},
            "result": row.result_payload or {},
            "created_at": row.created_at,
        }

    def _normalize_end_date(self, end_date: str | None) -> str:
        if not end_date:
            return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
        text = str(end_date).strip().replace("-", "")
        if len(text) != 8 or not text.isdigit():
            raise ValueError(f"交易日期格式不正确: {end_date}")
        return text

    def _due_time(self, now: datetime, configured_times: str) -> str | None:
        current_minutes = now.hour * 60 + now.minute
        due: list[str] = []
        for item in configured_times.split(","):
            text = item.strip()
            if not text:
                continue
            hour_text, _, minute_text = text.partition(":")
            if not hour_text.isdigit() or not minute_text.isdigit():
                continue
            target_minutes = int(hour_text) * 60 + int(minute_text)
            if 0 <= current_minutes - target_minutes < 10:
                due.append(f"{int(hour_text):02d}:{int(minute_text):02d}")
        return due[-1] if due else None

    def _report_type_for_time(self, time_text: str) -> str:
        hour_text, _, minute_text = time_text.partition(":")
        minutes = int(hour_text) * 60 + int(minute_text)
        return "morning" if minutes < 12 * 60 else "closing"


market_data_maintenance_service = MarketDataMaintenanceService()
