"""One run of the watch-list."""

from __future__ import annotations

from datetime import datetime, timedelta

from newswatch import gate
from newswatch.analysis import Classifier, KeywordClassifier, Matcher, NameMatcher
from newswatch.canonical import dedup_key, is_dedup_signal, normalized_title
from newswatch.models import Customer, Item, RunReport, Status, Verdict
from newswatch.sources import NewsSource
from newswatch.storage import Repository


class PipelineRunner:
    """fetch -> allow-list -> name gate -> dedup -> match -> classify -> store.

    The cheap deterministic steps run first and in a fixed order, so the model
    is asked only about articles that name the customer and have not been seen.
    What it rejects is stored as well, so it is never asked twice.
    """

    def __init__(
        self,
        customers: list[Customer],
        sources: list[NewsSource],
        repo: Repository,
        matcher: Matcher | None = None,
        classifier: Classifier | None = None,
        *,
        allow_list: frozenset[str] | None = None,
        max_evaluations: int = 200,
        title_window: timedelta = timedelta(days=14),
    ):
        self.customers = customers
        self.sources = sources
        self.repo = repo
        self.matcher = matcher or NameMatcher()
        self.classifier = classifier or KeywordClassifier()
        self.allow_list = allow_list
        self.max_evaluations = max_evaluations
        self.title_window = title_window

    def run(self, now: datetime) -> RunReport:
        report = RunReport(self.repo.start_run(now), now)  # the run exists before any work is done
        for customer in self._rotated(now):
            try:
                self._process(customer, now, report)
            except Exception as exc:  # noqa: BLE001 - one customer's failure must not end the run
                report.errors.append(f"{customer.id}: {type(exc).__name__}: {exc}")
        report.status = "succeeded_with_errors" if report.errors else "succeeded"
        self.repo.finish_run(report, now)
        return report

    def _rotated(self, now: datetime) -> list[Customer]:
        """Start at a different customer each hour, so those cut off by the cap go first later."""
        if not self.customers:
            return []
        shift = (now.toordinal() * 24 + now.hour) % len(self.customers)
        return self.customers[shift:] + self.customers[:shift]

    def _process(self, customer: Customer, now: datetime, report: RunReport) -> None:
        # Loaded before fetching: without the dedup sets nothing may be stored for this customer.
        known = self.repo.known_keys(customer.id)
        titles = self.repo.recent_titles(customer.id, now - self.title_window)
        candidates = []
        for source in self.sources:
            try:
                candidates += source.fetch(customer)
            except Exception as exc:  # noqa: BLE001 - a failing source must not hide the others
                report.errors.append(f"{customer.id}/{source.name}: {type(exc).__name__}: {exc}")
        report.count("fetched", len(candidates))

        for candidate in candidates:
            verdict = gate.check(candidate, customer, self.allow_list)
            if not verdict.passed:
                report.count(verdict.reason)
                continue
            key = dedup_key(customer.id, candidate.url)
            title = normalized_title(candidate.title)
            if key in known or (is_dedup_signal(title) and title in titles):
                report.count("duplicate")
                continue
            if report.counters.get("evaluated", 0) >= self.max_evaluations:
                report.count("deferred")
                continue
            known.add(key)
            titles.add(title)
            report.count("evaluated")

            match = self.matcher.match(candidate, customer)
            item = Item(
                dedup_key=key, customer_id=customer.id, url=candidate.url, title=candidate.title,
                normalized_title=title, snippet=candidate.snippet, source=candidate.source, domain=candidate.domain,
                status=Status.BELOW_THRESHOLD, verdict=match.verdict, evidence=match.evidence,
                confidence=match.confidence, flagged_injection=verdict.flagged_injection,
                published=candidate.published, created_at=now,
            )  # fmt: skip
            if match.verdict is not Verdict.NO_MATCH:
                kind = self.classifier.classify(candidate, customer)
                item.status, item.category, item.severity, item.summary = (
                    Status.NEW,
                    kind.category,
                    kind.severity,
                    kind.summary,
                )
            if self.repo.insert(item):
                report.count("new" if item.status is Status.NEW else "below_threshold")
            else:
                report.count("duplicate")  # another run stored it first
