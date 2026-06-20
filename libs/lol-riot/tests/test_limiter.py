"""Tests for the token-bucket limiter (the one real, implemented piece)."""

from __future__ import annotations

import pytest

from lol_riot import ContinentalRoute, RiotAccountClient, TokenBucketLimiter


class FakeClock:
    """Manually advanced monotonic clock for deterministic limiter tests."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_starts_full_and_spends() -> None:
    clock = FakeClock()
    b = TokenBucketLimiter(rate=10.0, capacity=5.0, time_fn=clock)
    assert b.available == pytest.approx(5.0)
    assert b.try_acquire(5.0) is True
    assert b.try_acquire(1.0) is False


def test_refills_over_time() -> None:
    clock = FakeClock()
    b = TokenBucketLimiter(rate=2.0, capacity=4.0, time_fn=clock)
    assert b.try_acquire(4.0) is True
    assert b.try_acquire(1.0) is False
    clock.advance(1.0)  # +2 tokens
    assert b.available == pytest.approx(2.0)
    assert b.try_acquire(2.0) is True


def test_refill_caps_at_capacity() -> None:
    clock = FakeClock()
    b = TokenBucketLimiter(rate=100.0, capacity=3.0, time_fn=clock)
    clock.advance(10.0)
    assert b.available == pytest.approx(3.0)


def test_invalid_construction() -> None:
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate=0.0, capacity=1.0)
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate=1.0, capacity=0.0)


async def test_acquire_over_capacity_raises() -> None:
    b = TokenBucketLimiter(rate=1.0, capacity=2.0)
    with pytest.raises(ValueError):
        await b.acquire(3.0)


async def test_acquire_blocks_then_proceeds() -> None:
    clock = FakeClock()
    b = TokenBucketLimiter(rate=1000.0, capacity=1.0, time_fn=clock)
    await b.acquire(1.0)  # drains the bucket
    # try_acquire fails immediately...
    assert b.try_acquire(1.0) is False
    # ...but advancing the clock refills enough for a non-blocking spend.
    clock.advance(1.0)
    assert b.try_acquire(1.0) is True


async def test_async_context_manager_spends_one() -> None:
    clock = FakeClock()
    b = TokenBucketLimiter(rate=1.0, capacity=2.0, time_fn=clock)
    async with b:
        pass
    assert b.available == pytest.approx(1.0)


def test_account_client_route_is_stored() -> None:
    client = RiotAccountClient("fake-key", ContinentalRoute.EUROPE)
    assert client.route is ContinentalRoute.EUROPE


def test_continental_route_base_url() -> None:
    assert ContinentalRoute.AMERICAS.base_url == "https://americas.api.riotgames.com"
    assert {r.value for r in ContinentalRoute} == {"americas", "asia", "europe"}
