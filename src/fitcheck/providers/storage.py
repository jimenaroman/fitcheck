"""Where the budget guard and the cache keep their records: files (the CLI on my laptop) or the
database (the API, so a Railway deploy can't wipe them). Each pair behaves the same; tests run both.

A usage record is {"time": datetime, "model", "input_tokens", "output_tokens", "cost_usd", "ok"}.
A cache entry is the saved answer: {"output": ..., "log": ...}.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from fitcheck.db import CacheRow, UsageRow


class UsageLog(Protocol):
    def since(self, start: datetime) -> list[dict]: ...
    def append(self, record: dict) -> None: ...


class AnswerCache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, entry: dict) -> None: ...


class FileUsageLog:
    """One JSON line per call attempt (.fitcheck/usage.jsonl)."""

    def __init__(self, path: Path):
        self.path = path

    def since(self, start: datetime) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                record["time"] = datetime.fromisoformat(record["time"])
                if record["time"] >= start:
                    records.append(record)
        return records

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record | {"time": record["time"].isoformat(timespec="seconds")}) + "\n")


class SqlUsageLog:
    """The same records as rows in the `usage` table."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def since(self, start: datetime) -> list[dict]:
        with Session(self.engine) as session:
            rows = session.scalars(select(UsageRow).where(UsageRow.time >= start))
            return [{"time": r.time, "model": r.model, "input_tokens": r.input_tokens,
                     "output_tokens": r.output_tokens, "cost_usd": r.cost_usd, "ok": r.ok} for r in rows]

    def append(self, record: dict) -> None:
        with Session(self.engine) as session:
            session.add(UsageRow(**record))
            session.commit()


class FileCache:
    """One JSON file per answer (.fitcheck/cache/<key>.json)."""

    def __init__(self, directory: Path):
        self.directory = directory

    def get(self, key: str) -> dict | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None  # unreadable entry: a miss, overwritten by the next answer

    def put(self, key: str, entry: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{key}.json").write_text(json.dumps(entry))


class SqlCache:
    """The same entries as rows in the `cache` table."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def get(self, key: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(CacheRow, key)
            return row.entry if row else None

    def put(self, key: str, entry: dict) -> None:
        with Session(self.engine) as session:
            session.merge(CacheRow(key=key, entry=entry))  # insert, or replace an unreadable entry
            session.commit()
