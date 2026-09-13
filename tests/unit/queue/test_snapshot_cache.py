"""The queue snapshot cache's failure behaviour, without a server (Issue 36).

A cache that is down must cost nothing but a miss: one failed call opens a short circuit, reads in the
window do not touch Redis at all, and anything unreadable in the store is a miss rather than an error.
The real-server behaviour is in ``tests/integration/queue/test_queue_snapshot.py``.
"""

from datetime import datetime

from src.commons.time import APP_TIMEZONE
from src.modules.queue.snapshot import RETRY_AFTER_SECONDS, RedisSnapshotCache, Snapshot


class DownRedis:
    """A client whose every call fails, counting how often it is tried."""

    def __init__(self) -> None:
        self.calls = 0

    def mget(self, keys: list[str]) -> list[str]:
        self.calls += 1
        raise ConnectionError("connection refused")

    def pipeline(self, transaction: bool = False) -> None:
        self.calls += 1
        raise ConnectionError("connection refused")


def test_one_failure_opens_the_circuit_so_an_outage_costs_one_timeout_not_one_per_page() -> (
    None
):
    """After a failure, reads are immediate misses until the retry window passes."""
    now = [1000.0]
    client = DownRedis()
    cache = RedisSnapshotCache(client, clock=lambda: now[0])

    assert cache.get_many(["q1"]) == {}
    assert client.calls == 1
    for _ in range(50):
        assert cache.get_many(["q1", "q2"]) == {}
    cache.set_many([Snapshot("q1", "s1", 3, None, datetime.now(APP_TIMEZONE))], 15)
    assert client.calls == 1, "nothing touched Redis while the circuit was open"

    now[0] += RETRY_AFTER_SECONDS + 1
    assert cache.get_many(["q1"]) == {}
    assert client.calls == 2, "tried again once the window passed"


def test_an_unreadable_stored_value_is_a_miss() -> None:
    """Garbage, a missing field or a bad timestamp in Redis never becomes an error page."""
    assert Snapshot.decode("q1", "not json") is None
    assert Snapshot.decode("q1", '{"s": "s1"}') is None
    assert Snapshot.decode("q1", '{"s":"s1","w":1,"a":null,"t":"yesterday"}') is None
    good = Snapshot("q1", "s1", 4, None, datetime(2026, 9, 15, 10, tzinfo=APP_TIMEZONE))
    assert Snapshot.decode("q1", good.encode()) == good
