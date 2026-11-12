from datetime import datetime, timedelta, timezone

import pytest

from newswatch import PipelineRunner
from newswatch.digest import Notifier
from newswatch.models import Status, Verdict
from newswatch.storage import Repository
from tests.fakes import ACME, OTHER, FakeClassifier, FakeEmailer, FakeMatcher, FakeSource, article

T0 = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)


def runner(repo, articles, **kwargs):
    kwargs.setdefault("matcher", FakeMatcher())
    kwargs.setdefault("classifier", FakeClassifier())
    return PipelineRunner([ACME], [FakeSource({"c1": articles})], repo, **kwargs)


def test_second_run_over_the_same_articles_stores_nothing():
    repo = Repository()
    first = runner(repo, [article(1), article(2)]).run(T0)
    second = runner(repo, [article(1), article(2)]).run(T0 + timedelta(hours=1))
    assert first.counters == {"fetched": 2, "evaluated": 2, "new": 2}
    assert second.counters == {"fetched": 2, "duplicate": 2}
    assert len(repo.with_status(Status.NEW)) == 2


def test_syndicated_copy_is_caught_by_title_within_the_window():
    repo = Repository()
    copy = article(
        title="Acme Metals opens plant number 1 | Wire Copy", url="https://wire.example/other-url"
    )
    report = runner(repo, [article(1), copy]).run(T0)
    assert report.counters["duplicate"] == 1
    later = runner(repo, [copy]).run(T0 + timedelta(days=20))
    assert later.counters.get("new") == 1  # outside the 14-day title window it is a new story


def test_rejected_articles_are_stored_so_the_model_is_not_asked_twice():
    repo, matcher = Repository(), FakeMatcher(Verdict.NO_MATCH, "")
    runner(repo, [article(1)], matcher=matcher).run(T0)
    runner(repo, [article(1)], matcher=matcher).run(T0 + timedelta(hours=1))
    assert matcher.calls == 1
    assert [i.status for i in repo.with_status(Status.BELOW_THRESHOLD)] == [Status.BELOW_THRESHOLD]
    assert repo.with_status(Status.NEW) == []


def test_uncertain_articles_are_delivered_with_a_flag():
    repo, mail = Repository(), FakeEmailer()
    runner(repo, [article(1)], matcher=FakeMatcher(Verdict.UNCERTAIN)).run(T0)
    Notifier(repo, mail, [ACME]).notify(T0)
    assert "[to verify]" in mail.sent[0][1]


def test_evaluation_cap_defers_the_rest_to_a_later_run():
    repo = Repository()
    articles = [article(n) for n in range(1, 6)]
    first = runner(repo, articles, max_evaluations=3).run(T0)
    second = runner(repo, articles, max_evaluations=3).run(T0 + timedelta(hours=1))
    assert (first.counters["evaluated"], first.counters["deferred"]) == (3, 2)
    assert (second.counters["evaluated"], second.counters["duplicate"]) == (2, 3)


def test_a_failing_source_is_recorded_and_the_others_still_count():
    repo = Repository()
    pipeline = PipelineRunner(
        [ACME],
        [FakeSource(fail=True), FakeSource({"c1": [article(1)]})],
        repo,
        FakeMatcher(),
        FakeClassifier(),
    )
    report = pipeline.run(T0)
    assert report.status == "succeeded_with_errors" and "TimeoutError" in report.errors[0]
    assert report.counters["new"] == 1
    assert repo.runs()[0].status == "succeeded_with_errors"


def test_without_the_dedup_sets_nothing_is_stored_for_that_customer():
    class BrokenRepo(Repository):
        def known_keys(self, customer_id):
            raise ConnectionError("store unavailable")

    repo, source = BrokenRepo(), FakeSource({"c1": [article(1)]})
    report = PipelineRunner([ACME], [source], repo, FakeMatcher(), FakeClassifier()).run(T0)
    assert source.calls == 0 and "ConnectionError" in report.errors[0]
    assert repo.with_status(Status.NEW) == []


def test_customer_order_rotates_by_the_hour():
    pipeline = PipelineRunner([ACME, OTHER], [], Repository())
    assert [c.id for c in pipeline._rotated(T0)] != [
        c.id for c in pipeline._rotated(T0 + timedelta(hours=1))
    ]


def test_the_store_rejects_a_duplicate_key_by_itself():
    repo = Repository()
    runner(repo, [article(1)]).run(T0)
    [stored] = repo.with_status(Status.NEW)
    assert repo.insert(stored) is False


def test_no_new_items_means_no_email():
    repo, mail = Repository(), FakeEmailer()
    assert Notifier(repo, mail, [ACME]).notify(T0).sent is False
    assert mail.sent == []


def test_a_failed_send_is_delivered_by_the_next_run():
    repo = Repository()
    runner(repo, [article(1)]).run(T0)
    with pytest.raises(ConnectionError):
        Notifier(repo, FakeEmailer(fail=True), [ACME]).notify(T0)
    assert len(repo.with_status(Status.NEW)) == 1  # not stamped, so not lost

    runner(repo, [article(1), article(2)]).run(T0 + timedelta(hours=1))
    mail = FakeEmailer()
    result = Notifier(repo, mail, [ACME]).notify(T0 + timedelta(hours=1))
    assert result.items == 2 and len(mail.sent) == 1
    assert repo.with_status(Status.NEW) == [] and len(repo.with_status(Status.EMAILED)) == 2
    assert Notifier(repo, mail, [ACME]).notify(T0 + timedelta(hours=2)).sent is False


def test_critical_items_get_their_own_alert_first():
    repo, mail = Repository(), FakeEmailer()
    runner(repo, [article(1)], classifier=FakeClassifier("fraud", "critical")).run(T0)
    result = Notifier(repo, mail, [ACME]).notify(T0)
    assert result.critical == 1
    assert [subject.split(":")[0] for subject, _ in mail.sent] == ["Critical alert", "News digest"]
