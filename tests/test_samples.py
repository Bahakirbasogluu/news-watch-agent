from datetime import datetime, timezone
from pathlib import Path

from newswatch import PipelineRunner
from newswatch.cli import load_watchlist
from newswatch.digest import Notifier
from newswatch.sources import folder_source
from newswatch.storage import Repository
from tests.fakes import FakeEmailer

SAMPLES = Path(__file__).parent.parent / "samples"


def test_three_hours_over_the_sample_feeds():
    customers, repo, mail = load_watchlist(SAMPLES / "watchlist.json"), Repository(), FakeEmailer()
    notifier, reports, digests = Notifier(repo, mail, customers), [], []
    for hour, folder in ((8, "hour1"), (9, "hour2"), (10, "hour2")):
        now = datetime(2026, 3, 9, hour, 15, tzinfo=timezone.utc)
        reports.append(
            PipelineRunner(customers, [folder_source("sample", SAMPLES / "feeds" / folder)], repo)
            .run(now)
            .counters
        )
        digests.append(notifier.notify(now))
    assert reports[0] == {
        "fetched": 15,
        "evaluated": 7,
        "new": 7,
        "duplicate": 2,
        "not_an_article": 1,
        "negative_keyword": 3,
        "no_name": 2,
    }
    assert (reports[1]["new"], reports[1]["duplicate"]) == (2, 9)
    assert "new" not in reports[2] and reports[2]["duplicate"] == 11
    assert [(d.sent, d.items, d.critical) for d in digests] == [
        (True, 7, 1),
        (True, 2, 0),
        (False, 0, 0),
    ]
    assert len(mail.sent) == 3  # one critical alert and two digests; the third hour sends nothing
