"""SQLite store. Article identity is a primary key, so the database rejects a duplicate
even when two runs race; no caller has to remember to check first."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from newswatch.models import Item, RunReport, Status, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    dedup_key TEXT PRIMARY KEY, customer_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL,
    normalized_title TEXT NOT NULL, snippet TEXT NOT NULL, source TEXT NOT NULL, domain TEXT NOT NULL,
    status TEXT NOT NULL, verdict TEXT NOT NULL, evidence TEXT NOT NULL, confidence REAL NOT NULL,
    category TEXT NOT NULL, severity TEXT NOT NULL, summary TEXT NOT NULL, flagged_injection INTEGER NOT NULL,
    published TEXT, created_at TEXT NOT NULL, emailed_at TEXT
);
CREATE INDEX IF NOT EXISTS items_by_customer ON items (customer_id, created_at);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, counters TEXT NOT NULL DEFAULT '{}', errors TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS delivered (consumer TEXT NOT NULL, dedup_key TEXT NOT NULL, PRIMARY KEY (consumer, dedup_key));
CREATE TABLE IF NOT EXISTS pending (
    run_id TEXT PRIMARY KEY, consumer TEXT NOT NULL, dedup_keys TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
_COLUMNS = (
    "dedup_key", "customer_id", "url", "title", "normalized_title", "snippet", "source", "domain", "status",
    "verdict", "evidence", "confidence", "category", "severity", "summary", "flagged_injection", "published",
    "created_at", "emailed_at",
)  # fmt: skip


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Repository:
    def __init__(self, path: str = ":memory:"):
        # Tool calls may arrive on worker threads; SQLite serialises access to the connection.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    # items -----------------------------------------------------------------
    def insert(self, item: Item) -> bool:
        """Create-only. Returns False when the article is already stored."""
        values = (
            item.dedup_key, item.customer_id, item.url, item.title, item.normalized_title, item.snippet,
            item.source, item.domain, item.status.value, item.verdict.value, item.evidence, item.confidence,
            item.category, item.severity, item.summary, int(item.flagged_injection), _iso(item.published),
            _iso(item.created_at), _iso(item.emailed_at),
        )  # fmt: skip
        with self._db:
            cursor = self._db.execute(
                f"INSERT OR IGNORE INTO items ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                values,
            )
        return cursor.rowcount == 1

    def known_keys(self, customer_id: str) -> set[str]:
        rows = self._db.execute("SELECT dedup_key FROM items WHERE customer_id = ?", (customer_id,))
        return {r["dedup_key"] for r in rows}

    def recent_titles(self, customer_id: str, since: datetime) -> set[str]:
        rows = self._db.execute(
            "SELECT normalized_title FROM items WHERE customer_id = ? AND created_at >= ?",
            (customer_id, since.isoformat()),
        )
        return {r["normalized_title"] for r in rows}

    def with_status(self, status: Status) -> list[Item]:
        rows = self._db.execute(
            "SELECT * FROM items WHERE status = ? ORDER BY customer_id, created_at, dedup_key",
            (status.value,),
        )
        return [self._item(r) for r in rows]

    def mark_emailed(self, keys: list[str], at: datetime) -> None:
        with self._db:
            self._db.executemany(
                "UPDATE items SET status = ?, emailed_at = ? WHERE dedup_key = ? AND status = ?",
                [(Status.EMAILED.value, at.isoformat(), key, Status.NEW.value) for key in keys],
            )

    @staticmethod
    def _item(r: sqlite3.Row) -> Item:
        return Item(
            dedup_key=r["dedup_key"], customer_id=r["customer_id"], url=r["url"], title=r["title"],
            normalized_title=r["normalized_title"], snippet=r["snippet"], source=r["source"], domain=r["domain"],
            status=Status(r["status"]), verdict=Verdict(r["verdict"]), evidence=r["evidence"],
            confidence=r["confidence"], category=r["category"], severity=r["severity"], summary=r["summary"],
            flagged_injection=bool(r["flagged_injection"]), published=_dt(r["published"]),
            created_at=_dt(r["created_at"]), emailed_at=_dt(r["emailed_at"]),
        )  # fmt: skip

    # runs ------------------------------------------------------------------
    def start_run(self, at: datetime) -> int:
        with self._db:
            return self._db.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (at.isoformat(),)
            ).lastrowid

    def finish_run(self, report: RunReport, at: datetime) -> None:
        with self._db:
            self._db.execute(
                "UPDATE runs SET finished_at = ?, status = ?, counters = ?, errors = ? WHERE id = ?",
                (
                    at.isoformat(),
                    report.status,
                    json.dumps(report.counters),
                    json.dumps(report.errors),
                    report.run_id,
                ),
            )

    def runs(self) -> list[RunReport]:
        return [
            RunReport(
                r["id"],
                datetime.fromisoformat(r["started_at"]),
                r["status"],
                json.loads(r["counters"]),
                json.loads(r["errors"]),
            )
            for r in self._db.execute("SELECT * FROM runs ORDER BY id")
        ]

    # delivery history for bridge consumers ----------------------------------
    def delivered_keys(self, consumer: str) -> set[str]:
        return {
            r["dedup_key"]
            for r in self._db.execute(
                "SELECT dedup_key FROM delivered WHERE consumer = ?", (consumer,)
            )
        }

    def save_pending(self, run_id: str, consumer: str, keys: list[str], at: datetime) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO pending VALUES (?, ?, ?, ?)",
                (run_id, consumer, json.dumps(keys), at.isoformat()),
            )

    def confirm_pending(self, run_id: str) -> int | None:
        """Moves a pending run's articles into the consumer's delivery history. None if unknown."""
        row = self._db.execute(
            "SELECT consumer, dedup_keys FROM pending WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        keys = json.loads(row["dedup_keys"])
        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO delivered VALUES (?, ?)",
                [(row["consumer"], k) for k in keys],
            )
            self._db.execute("DELETE FROM pending WHERE run_id = ?", (run_id,))
        return len(keys)

    def purge_pending(self, now: datetime, max_age: timedelta = timedelta(days=3)) -> int:
        with self._db:
            return self._db.execute(
                "DELETE FROM pending WHERE created_at < ?", ((now - max_age).isoformat(),)
            ).rowcount
