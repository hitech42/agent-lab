"""A derived SQLite database over the results files, for ad-hoc SQL. Never committed."""

import json
import os
import sqlite3
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from agent_lab.runner.records import AbortRecord, AttemptRecord, RunSummary
from agent_lab.runner.storage import ResultsStore

_PAIRS_VIEW = """
CREATE VIEW pairs AS
SELECT b.run_id, b.date, b.track, b.task_id, b.trial,
       b.config_hash AS baseline_hash, v.config_hash AS variant_hash,
       b.status AS baseline_status, v.status AS variant_status,
       b.passed AS baseline_passed, v.passed AS variant_passed,
       b.cost_usd AS baseline_cost_usd, v.cost_usd AS variant_cost_usd,
       b.turns AS baseline_turns, v.turns AS variant_turns,
       b.input_tokens AS baseline_input_tokens, v.input_tokens AS variant_input_tokens,
       b.output_tokens AS baseline_output_tokens, v.output_tokens AS variant_output_tokens,
       b.total_latency_s AS baseline_latency_s, v.total_latency_s AS variant_latency_s
FROM attempts b
JOIN attempts v
  ON v.run_id = b.run_id AND v.task_id = b.task_id AND v.trial = b.trial
 AND b.config_role = 'baseline' AND v.config_role = 'variant'
"""


@dataclass(frozen=True)
class RebuildReport:
    attempts: int
    runs: int
    aborts: int
    corrupt_lines: int


class ResultsDatabase:
    """Rebuilds results.db from every JSONL, run summary and abort file."""

    def rebuild(self, store: ResultsStore, db_path: Path) -> RebuildReport:
        """Build into a temp file and atomically replace db_path (a derived artifact)."""
        attempts, corrupt = store.read_all_attempts()
        runs = store.read_summaries()
        aborts = store.read_aborts()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(dir=db_path.parent, suffix=".tmp")
        os.close(fd)
        try:
            with sqlite3.connect(temp_name) as conn:
                self._load(conn, "attempts", AttemptRecord, attempts)
                self._load(conn, "runs", RunSummary, runs)
                self._load(conn, "aborts", AbortRecord, aborts)
                conn.execute(_PAIRS_VIEW)
            conn.close()
            os.replace(temp_name, db_path)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return RebuildReport(len(attempts), len(runs), len(aborts), corrupt)

    def _load(
        self, conn: sqlite3.Connection, table: str, model: type[BaseModel], rows: list[Any]
    ) -> None:
        columns = {name: self._sql_type(f.annotation) for name, f in model.model_fields.items()}
        definition = ", ".join(f'"{name}" {sql}' for name, sql in columns.items())
        conn.execute(f"CREATE TABLE {table} ({definition})")
        marks = ", ".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO {table} VALUES ({marks})",
            [[self._value(getattr(row, name)) for name in columns] for row in rows],
        )

    @classmethod
    def _sql_type(cls, annotation: Any) -> str:
        origin = get_origin(annotation)
        if origin in (Union, types.UnionType):
            inner = [a for a in get_args(annotation) if a is not type(None)]
            return cls._sql_type(inner[0]) if len(inner) == 1 else "TEXT"
        if annotation is bool or annotation is int:
            return "INTEGER"
        if annotation is float:
            return "REAL"
        return "TEXT"  # str, Literal, and JSON-encoded lists

    @staticmethod
    def _value(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        if isinstance(value, list):
            return json.dumps([v.model_dump() if isinstance(v, BaseModel) else v for v in value])
        return value
