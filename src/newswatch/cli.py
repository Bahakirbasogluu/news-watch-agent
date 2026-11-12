from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from newswatch.digest import Notifier
from newswatch.models import Customer
from newswatch.pipeline import PipelineRunner
from newswatch.sources import RssSource, folder_source, google_news_locator
from newswatch.storage import Repository


def load_watchlist(path: str | Path) -> list[Customer]:
    return [
        Customer(
            c["id"], c["name"], tuple(c.get("aliases", ())), tuple(c.get("negative_keywords", ()))
        )
        for c in json.loads(Path(path).read_text(encoding="utf-8"))
    ]


class ConsoleEmailer:
    def send(self, subject: str, body: str) -> None:
        print(f"\n  E-MAIL  {subject}\n" + "\n".join(f"  | {line}" for line in body.splitlines()))


def _analysis(backend: str):
    if backend == "rules":
        return None, None
    from newswatch.analysis import AzureOpenAIModel, LLMClassifier, LLMMatcher

    model = AzureOpenAIModel()
    return LLMMatcher(model), LLMClassifier(model)


def _run(runner: PipelineRunner, notifier: Notifier, now: datetime, label: str) -> None:
    report = runner.run(now)
    counters = ", ".join(f"{k}={v}" for k, v in report.counters.items())
    print(f"\n{label}  run {report.run_id}: {report.status}  ({counters})")
    for error in report.errors:
        print(f"  error: {error}")
    result = notifier.notify(now)
    if not result.sent:
        print("  nothing new: no e-mail sent")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="newswatch")
    parser.add_argument("--watchlist", type=Path, default=Path("samples/watchlist.json"))
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--backend", choices=("rules", "azure"), default="rules")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="three hourly runs over the sample feeds")
    live = sub.add_parser("run", help="one run against Google News RSS")
    live.add_argument("--lookback-days", type=int, default=3)
    args = parser.parse_args(argv)

    customers, repo = load_watchlist(args.watchlist), Repository(args.db)
    matcher, classifier = _analysis(args.backend)
    notifier = Notifier(repo, ConsoleEmailer(), customers)
    if args.command == "demo":
        feeds = args.watchlist.parent / "feeds"
        for hour, folder in ((8, "hour1"), (9, "hour2"), (10, "hour2")):
            runner = PipelineRunner(
                customers, [folder_source("sample", feeds / folder)], repo, matcher, classifier
            )
            _run(
                runner,
                notifier,
                datetime(2026, 3, 9, hour, 15, tzinfo=timezone.utc),
                f"{hour:02d}:15",
            )
    else:
        source = RssSource("google-news", google_news_locator(args.lookback_days))
        _run(
            PipelineRunner(customers, [source], repo, matcher, classifier),
            notifier,
            datetime.now(timezone.utc),
            "now",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
