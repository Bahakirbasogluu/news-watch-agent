from __future__ import annotations

from newswatch.analysis import Classification, Match
from newswatch.models import Candidate, Customer, Verdict

ACME = Customer("c1", "Acme Metals", ("Acme",), ("Acme Corporation",))
OTHER = Customer("c2", "Borealis Foods")


def article(
    n: int = 1,
    title: str | None = None,
    snippet: str = "Acme Metals said so.",
    url: str | None = None,
    domain: str = "news.example",
) -> Candidate:
    return Candidate(
        url or f"https://{domain}/story-{n}",
        title or f"Acme Metals opens plant number {n}",
        snippet,
        "fake",
        domain,
    )


class FakeSource:
    name = "fake"

    def __init__(self, by_customer: dict[str, list[Candidate]] | None = None, fail: bool = False):
        self.by_customer = by_customer or {}
        self.fail = fail
        self.calls = 0

    def fetch(self, customer: Customer) -> list[Candidate]:
        self.calls += 1
        if self.fail:
            raise TimeoutError("source timed out")
        return list(self.by_customer.get(customer.id, []))


class FakeMatcher:
    def __init__(self, verdict: Verdict = Verdict.MATCH, evidence: str = "Acme Metals"):
        self.verdict, self.evidence, self.calls = verdict, evidence, 0

    def match(self, candidate: Candidate, customer: Customer) -> Match:
        self.calls += 1
        return Match(self.verdict, self.evidence, customer.name, 0.8)


class FakeClassifier:
    def __init__(self, category: str = "operations", severity: str = "low"):
        self.category, self.severity = category, severity

    def classify(self, candidate: Candidate, customer: Customer) -> Classification:
        return Classification(self.category, self.severity, candidate.title)


class FakeEmailer:
    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, subject: str, body: str) -> None:
        if self.fail:
            raise ConnectionError("mail service unavailable")
        self.sent.append((subject, body))
