from datetime import datetime, timedelta, timezone

import pytest
from mcp import Client

from newswatch.bridge import Bridge, build_server
from newswatch.storage import Repository
from tests.fakes import FakeSource, article

pytestmark = pytest.mark.anyio
WATCHLIST = [{"id": "c1", "name": "Acme Metals", "aliases": ["Acme"]}]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 3, 9, 8, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def setup():
    source, clock = (
        FakeSource(
            {"c1": [article(1), article(2), article(title="Metals outlook", snippet="No company.")]}
        ),
        Clock(),
    )
    return Bridge([source], Repository(), clock=clock), source, clock


def test_search_returns_evidence_and_drop_counters(setup):
    bridge, _, _ = setup
    result = bridge.search_news(WATCHLIST[0])
    assert [i["evidence"] for i in result["items"]] == ["Acme Metals", "Acme Metals"]
    assert result["dropped"] == {"no_name": 1}


def test_unconfirmed_run_resurfaces_and_confirmed_run_does_not(setup):
    bridge, source, _ = setup
    first = bridge.run_watchlist("flow-a", WATCHLIST)
    again = bridge.run_watchlist("flow-a", WATCHLIST)
    assert len(first["items"]) == len(again["items"]) == 2  # delivery was never confirmed

    assert bridge.confirm_delivery(again["run_id"]) == {"confirmed": True, "items": 2}
    after = bridge.run_watchlist("flow-a", WATCHLIST)
    assert after["items"] == [] and after["run_id"] is None and after["digest_subject"] is None

    source.by_customer["c1"].append(article(3))
    assert [i["title"] for i in bridge.run_watchlist("flow-a", WATCHLIST)["items"]] == [
        "Acme Metals opens plant number 3"
    ]


def test_delivery_history_is_kept_per_consumer(setup):
    bridge, _, _ = setup
    bridge.confirm_delivery(bridge.run_watchlist("flow-a", WATCHLIST)["run_id"])
    assert len(bridge.run_watchlist("agent-b", WATCHLIST)["items"]) == 2


def test_stale_pending_runs_are_purged_and_unknown_runs_are_not_confirmed(setup):
    bridge, _, clock = setup
    run_id = bridge.run_watchlist("flow-a", WATCHLIST)["run_id"]
    clock.now += timedelta(days=4)
    bridge.run_watchlist("flow-a", WATCHLIST)
    assert bridge.confirm_delivery(run_id) == {"confirmed": False, "items": 0}


def test_a_failing_source_becomes_a_warning_and_an_empty_list_is_a_valid_answer():
    bridge = Bridge([FakeSource(fail=True)], Repository())
    result = bridge.run_watchlist("flow-a", WATCHLIST)
    assert result["items"] == [] and result["warnings"] == ["fake: TimeoutError"]


async def test_tools_over_the_protocol(setup):
    bridge, _, _ = setup
    async with Client(build_server(bridge)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"compute_dedup_key", "search_news", "run_watchlist", "confirm_delivery"}
        run = (
            await client.call_tool("run_watchlist", {"consumer": "agent", "customers": WATCHLIST})
        ).structured_content
        assert len(run["items"]) == 2 and run["digest_subject"] == "News digest: 2 new item(s)"
        done = (
            await client.call_tool("confirm_delivery", {"run_id": run["run_id"]})
        ).structured_content
        assert done["confirmed"] is True
        empty = (
            await client.call_tool("run_watchlist", {"consumer": "agent", "customers": WATCHLIST})
        ).structured_content
        assert empty["items"] == []
