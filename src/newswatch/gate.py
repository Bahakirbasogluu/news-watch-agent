"""Deterministic checks every candidate passes before a model sees it."""

from __future__ import annotations

import re
from dataclasses import dataclass

from newswatch.canonical import fold
from newswatch.models import Candidate, Customer

_HUB_TITLE = re.compile(
    r"\b(stock price|share price|quote|hisse fiyat|hisse senedi|company profile)\b"
)
_HUB_PATH = re.compile(r"/(quote|quotes|symbol|tag|topic|company)/")
_INJECTION = re.compile(
    r"ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(system|previous)|"
    r"you are now|system prompt|önceki talimatları (yok say|unut)"
)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str = ""
    evidence: str = ""
    matched_name: str = ""
    flagged_injection: bool = False


def find_name(text: str, names: tuple[str, ...]) -> tuple[str, str] | None:
    """Returns (name, verbatim text) for the first name that occurs in the text as whole words."""
    folded = fold(text)
    for name in names:
        m = re.search(rf"(?<!\w){re.escape(fold(name))}(?!\w)", folded)
        if m:
            return name, text[m.start() : m.end()]
    return None


def check(
    candidate: Candidate, customer: Customer, allow_list: frozenset[str] | None = None
) -> GateResult:
    """Allow-list, article page, name evidence, negative keywords; in that order.

    Text that looks like a prompt injection is flagged, not dropped: a person
    should still see the article, and the flag travels with it.
    """
    if allow_list is not None and candidate.domain not in allow_list:
        return GateResult(False, "off_allow_list")
    text = f"{candidate.title}\n{candidate.snippet}"
    folded = fold(text)
    if _HUB_TITLE.search(fold(candidate.title)) or _HUB_PATH.search(candidate.url.lower()):
        return GateResult(False, "not_an_article")
    found = find_name(text, customer.names)
    if found is None:
        return GateResult(False, "no_name")
    if any(
        re.search(rf"(?<!\w){re.escape(fold(k))}(?!\w)", folded) for k in customer.negative_keywords
    ):
        return GateResult(False, "negative_keyword")
    return GateResult(
        True,
        evidence=found[1],
        matched_name=found[0],
        flagged_injection=bool(_INJECTION.search(folded)),
    )
