"""The digest is built from what the store says is still unsent, not from what this run found.

A run that stored articles and then failed to send leaves them in status NEW,
and the next run's digest picks them up. Rows are stamped only after a
successful send, so delivery is at-least-once without any retry bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from newswatch.models import SEVERITIES, Customer, Item, Status, Verdict
from newswatch.storage import Repository

CRITICAL_CATEGORIES = ("sanctions", "fraud")


class Emailer(Protocol):
    def send(self, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class DigestResult:
    sent: bool
    items: int = 0
    critical: int = 0


def is_critical(item: Item) -> bool:
    return item.category in CRITICAL_CATEGORIES and SEVERITIES.index(
        item.severity
    ) >= SEVERITIES.index("high")


def render(items: list[Item], customers: dict[str, Customer]) -> str:
    lines: list[str] = []
    for customer_id in dict.fromkeys(i.customer_id for i in items):
        name = customers[customer_id].name if customer_id in customers else customer_id
        lines += [name, "-" * len(name)]
        for item in (i for i in items if i.customer_id == customer_id):
            flags = "".join(
                [
                    " [to verify]" if item.verdict is Verdict.UNCERTAIN else "",
                    " [check content]" if item.flagged_injection else "",
                ]
            )
            lines += [
                f"[{item.severity}] {item.category}: {item.title}{flags}",
                f"  {item.domain}  {item.url}",
            ]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class Notifier:
    def __init__(self, repo: Repository, emailer: Emailer, customers: list[Customer]):
        self.repo = repo
        self.emailer = emailer
        self.customers = {c.id: c for c in customers}

    def notify(self, now: datetime) -> DigestResult:
        items = self.repo.with_status(Status.NEW)
        if not items:
            return DigestResult(sent=False)  # nothing new: no e-mail at all
        critical = [i for i in items if is_critical(i)]
        if critical:
            self.emailer.send(
                f"Critical alert: {len(critical)} item(s)", render(critical, self.customers)
            )
        self.emailer.send(f"News digest: {len(items)} new item(s)", render(items, self.customers))
        self.repo.mark_emailed([i.dedup_key for i in items], now)
        return DigestResult(True, len(items), len(critical))
