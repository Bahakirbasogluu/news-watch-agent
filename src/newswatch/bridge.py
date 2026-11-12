"""The same engine offered to other orchestrators (a low-code agent, a scheduled flow) as tools.

The caller stays thin: list the watch-list, call run_watchlist once, deliver
what came back, call confirm_delivery. Deduplication, evidence and delivery
history stay on this side, so they cannot be skipped by a prompt.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from newswatch import gate
from newswatch.canonical import dedup_key, is_dedup_signal, normalized_title
from newswatch.models import Customer
from newswatch.sources import NewsSource
from newswatch.storage import Repository


def _customer(raw: dict[str, Any]) -> Customer:
    return Customer(
        raw["id"],
        raw["name"],
        tuple(raw.get("aliases", ())),
        tuple(raw.get("negative_keywords", ())),
    )


class Bridge:
    def __init__(
        self,
        sources: list[NewsSource],
        repo: Repository,
        allow_list: frozenset[str] | None = None,
        clock=None,
    ):
        self.sources = sources
        self.repo = repo
        self.allow_list = allow_list
        self._now = clock or (lambda: datetime.now(timezone.utc))

    def search_news(
        self, customer: dict[str, Any], known_keys: set[str] | None = None
    ) -> dict[str, Any]:
        """Stateless search: gated, deduplicated candidates with the evidence for each. No model."""
        c, seen, titles = _customer(customer), set(known_keys or ()), set()
        items, dropped, warnings = [], {}, []
        for source in self.sources:
            try:
                candidates = source.fetch(c)
            except Exception as exc:  # noqa: BLE001 - reported to the caller as a warning
                warnings.append(f"{source.name}: {type(exc).__name__}")
                continue
            for candidate in candidates:
                verdict = gate.check(candidate, c, self.allow_list)
                key, title = dedup_key(c.id, candidate.url), normalized_title(candidate.title)
                reason = (
                    verdict.reason
                    if not verdict.passed
                    else (
                        "duplicate"
                        if key in seen or (is_dedup_signal(title) and title in titles)
                        else ""
                    )
                )
                if reason:
                    dropped[reason] = dropped.get(reason, 0) + 1
                    continue
                seen.add(key)
                titles.add(title)
                items.append({
                    "dedup_key": key, "customer_id": c.id, "title": candidate.title, "url": candidate.url,
                    "domain": candidate.domain, "snippet": candidate.snippet, "evidence": verdict.evidence,
                    "flagged_injection": verdict.flagged_injection,
                })  # fmt: skip
        return {"items": items, "dropped": dropped, "warnings": warnings}

    def run_watchlist(self, consumer: str, customers: list[dict[str, Any]]) -> dict[str, Any]:
        """Everything this consumer has not confirmed yet, held as a pending run until it confirms."""
        self.repo.purge_pending(self._now())
        delivered = self.repo.delivered_keys(consumer)
        items, dropped, warnings = [], {}, []
        for customer in customers:
            result = self.search_news(customer, delivered)
            items += result["items"]
            warnings += result["warnings"]
            for reason, n in result["dropped"].items():
                dropped[reason] = dropped.get(reason, 0) + n
        run_id = uuid.uuid4().hex
        if items:
            self.repo.save_pending(run_id, consumer, [i["dedup_key"] for i in items], self._now())
        names = {c["id"]: c["name"] for c in customers}
        digest = "\n".join(f"{names[i['customer_id']]}: {i['title']}\n  {i['url']}" for i in items)
        return {"run_id": run_id if items else None, "items": items, "dropped": dropped, "warnings": warnings,
                "digest_subject": f"News digest: {len(items)} new item(s)" if items else None, "digest_text": digest}  # fmt: skip

    def confirm_delivery(self, run_id: str) -> dict[str, Any]:
        """Call after the digest was delivered. An unconfirmed run's articles come back next time."""
        count = self.repo.confirm_pending(run_id)
        return {"confirmed": count is not None, "items": count or 0}


def build_server(bridge: Bridge):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(
        "newswatch",
        instructions=(
            "Call run_watchlist once with the whole watch-list, deliver the digest only if items is not empty, "
            "then call confirm_delivery with the run_id. Do not search the web yourself and do not rewrite titles or URLs."
        ),
    )
    read = ToolAnnotations(read_only_hint=True)

    @server.tool(annotations=read)
    def compute_dedup_key(customer_id: str, url: str) -> str:
        """The identity of an article for a customer; equal for every link form of the same article."""
        return dedup_key(customer_id, url)

    @server.tool(annotations=read)
    def search_news(customer: dict[str, Any]) -> dict[str, Any]:
        """News that names one company ({id, name, aliases}), with the quoted evidence for each item."""
        return bridge.search_news(customer)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=False))
    def run_watchlist(consumer: str, customers: list[dict[str, Any]]) -> dict[str, Any]:
        """New items for the whole watch-list that this consumer has not confirmed, plus a ready digest."""
        return bridge.run_watchlist(consumer, customers)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True))
    def confirm_delivery(run_id: str) -> dict[str, Any]:
        """Confirm that the digest of a run was delivered, so its items are not returned again."""
        return bridge.confirm_delivery(run_id)

    return server
