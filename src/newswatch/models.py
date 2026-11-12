from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    NEW = "new"
    EMAILED = "emailed"
    BELOW_THRESHOLD = "below_threshold"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"


class Verdict(str, Enum):
    MATCH = "match"
    UNCERTAIN = "uncertain"
    NO_MATCH = "no_match"


SEVERITIES = ("low", "medium", "high", "critical")
CATEGORIES = (
    "sanctions", "fraud", "litigation", "regulatory", "financial_distress",
    "ownership_change", "management_change", "operations", "other",
)  # fmt: skip


@dataclass(frozen=True)
class Customer:
    """A company on the watch-list."""

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    negative_keywords: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass(frozen=True)
class Candidate:
    """An article as a source returned it, before any judgement."""

    url: str
    title: str
    snippet: str
    source: str
    domain: str
    published: datetime | None = None


@dataclass
class Item:
    """A candidate that reached the store, with everything decided about it."""

    dedup_key: str
    customer_id: str
    url: str
    title: str
    normalized_title: str
    snippet: str
    source: str
    domain: str
    status: Status
    verdict: Verdict
    evidence: str = ""
    confidence: float = 0.0
    category: str = "other"
    severity: str = "low"
    summary: str = ""
    flagged_injection: bool = False
    published: datetime | None = None
    created_at: datetime | None = None
    emailed_at: datetime | None = None


@dataclass
class RunReport:
    run_id: int
    started_at: datetime
    status: str = "running"
    counters: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def count(self, name: str, by: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + by
