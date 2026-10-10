"""Durable status for one manual or scheduled evaluation queue."""

from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4


class EvaluationStatusStore(Protocol):
    def save_evaluation_status(self, status: dict[str, object]) -> None:
        """Persist the latest queue state."""

    def get_evaluation_status(self) -> dict[str, object] | None:
        """Return the latest queue state."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationProgress:
    """Small persistence-backed state machine shared by API and worker processes."""

    def __init__(self, store: EvaluationStatusStore) -> None:
        self._store = store

    def start(self, symbols: tuple[str, ...]) -> str:
        run_id = f"evaluation-{uuid4().hex}"
        self._save(
            {
                "run_id": run_id,
                "status": "RUNNING",
                "started_at": utc_now_iso(),
                "finished_at": None,
                "total_count": len(symbols),
                "completed_count": 0,
                "current_index": 0,
                "current_symbol": symbols[0] if symbols else None,
                "symbols": list(symbols),
                "result_summary": [],
                "error": None,
            }
        )
        return run_id

    def mark_symbol(self, run_id: str, index: int, symbol: str, completed: bool) -> None:
        status = self._current(run_id)
        status.update(
            {
                "current_index": index,
                "current_symbol": symbol,
                "completed_count": index if completed else max(index - 1, 0),
            }
        )
        self._save(status)

    def finish(self, run_id: str, result_summary: list[dict[str, object]]) -> None:
        status = self._current(run_id)
        status.update(
            {
                "status": "COMPLETED",
                "finished_at": utc_now_iso(),
                "completed_count": status.get("total_count", 0),
                "current_index": status.get("total_count", 0),
                "current_symbol": None,
                "result_summary": result_summary,
                "error": None,
            }
        )
        self._save(status)

    def fail(self, run_id: str, error: str) -> None:
        status = self._current(run_id)
        status.update({"status": "FAILED", "finished_at": utc_now_iso(), "error": error})
        self._save(status)

    def current(self) -> dict[str, object]:
        stored = self._store.get_evaluation_status()
        if stored is not None:
            return stored
        return {
            "run_id": None,
            "status": "IDLE",
            "started_at": None,
            "finished_at": None,
            "total_count": 0,
            "completed_count": 0,
            "current_index": 0,
            "current_symbol": None,
            "symbols": [],
            "result_summary": [],
            "error": None,
        }

    def _current(self, run_id: str) -> dict[str, object]:
        status = self.current()
        if status.get("run_id") != run_id:
            return status
        return status

    def _save(self, status: dict[str, object]) -> None:
        self._store.save_evaluation_status(status)
