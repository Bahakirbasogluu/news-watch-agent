"""Where candidates come from. Each source is an adapter behind one small interface."""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote_plus

from newswatch.canonical import domain_of
from newswatch.models import Candidate, Customer


class NewsSource(Protocol):
    name: str

    def fetch(self, customer: Customer) -> list[Candidate]: ...


def _plain(text: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def parse_rss(xml_text: str, source: str) -> list[Candidate]:
    """Reads RSS 2.0 items. The publisher's domain comes from <source url>, else from the link."""
    out = []
    for item in ET.fromstring(xml_text).iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        origin = item.find("source")
        domain = (
            domain_of(origin.get("url", ""))
            if origin is not None and origin.get("url")
            else domain_of(link)
        )
        published = None
        if raw := item.findtext("pubDate"):
            try:
                published = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                published = None
        out.append(
            Candidate(
                link,
                _plain(item.findtext("title")),
                _plain(item.findtext("description")),
                source,
                domain,
                published,
            )
        )
    return out


def http_get(url: str, timeout: float = 15.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "newswatch/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


class RssSource:
    """An RSS source: `locate` says which feeds to read for a customer, `get` reads one."""

    def __init__(
        self,
        name: str,
        locate: Callable[[Customer], list[str]],
        get: Callable[[str], str] = http_get,
    ):
        self.name = name
        self._locate = locate
        self._get = get

    def fetch(self, customer: Customer) -> list[Candidate]:
        return [
            c
            for location in self._locate(customer)
            for c in parse_rss(self._get(location), self.name)
        ]


def google_news_locator(
    lookback_days: int = 3, editions: tuple[tuple[str, str], ...] = (("en", "US"), ("tr", "TR"))
):
    """One Google News search feed per language edition: any of the customer's names, recent only."""

    def locate(customer: Customer) -> list[str]:
        names = " OR ".join(f'"{name}"' for name in customer.names)
        query = quote_plus(f"{names} when:{lookback_days}d")
        return [
            f"https://news.google.com/rss/search?q={query}&hl={lang}&gl={country}&ceid={country}:{lang}"
            for lang, country in editions
        ]

    return locate


def folder_source(name: str, folder: str | Path) -> RssSource:
    """Reads `<folder>/<customer id>.xml`; a customer without a file has no news. Used by the samples."""
    root = Path(folder)

    def locate(customer: Customer) -> list[str]:
        path = root / f"{customer.id}.xml"
        return [str(path)] if path.exists() else []

    return RssSource(name, locate, lambda p: Path(p).read_text(encoding="utf-8"))
