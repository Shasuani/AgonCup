"""``GET /desktop/version`` + the feed/version helpers (offline; fake fetcher).

Pins: version ordering (pre-releases, missing parts, junk), the tiny
latest*.yml scanner, the cache's hit/miss TTLs, and the endpoint's answers —
update available / up to date / unknown feed / required below the minimum /
the dev override / per-platform files / rate limiting.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.desktop import router as desktop_router
from app.desktop.service import (
    FeedCache,
    compare_versions,
    describe_release,
    is_newer,
    parse_feed,
    parse_version,
)
from app.main import app

WIN_FEED = """version: 0.2.0
files:
  - url: LeagueOfLeagues-Setup-0.2.0.exe
    sha512: winhash==
    size: 87654321
path: LeagueOfLeagues-Setup-0.2.0.exe
sha512: winhash==
releaseDate: '2026-09-08T10:00:00.000Z'
"""

MAC_FEED = """version: 0.2.0
files:
  - url: LeagueOfLeagues-0.2.0-universal.zip
    sha512: machash==
    size: 123456789
path: LeagueOfLeagues-0.2.0-universal.zip
sha512: machash==
releaseDate: '2026-09-08T10:00:00.000Z'
"""


# --- versions -----------------------------------------------------------------


def test_parse_version_accepts_v_prefix_and_prerelease() -> None:
    v = parse_version("v1.2.3-beta.1+build.5")
    assert v is not None
    assert v.core == (1, 2, 3)
    assert v.pre == ("beta", "1")
    assert parse_version("") is None
    assert parse_version("latest") is None
    assert parse_version(None) is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("0.1.0", "0.2.0", -1),
        ("0.2.0", "0.2.0", 0),
        ("0.10.0", "0.9.9", 1),
        ("1.0.0", "1.0", 0),
        ("1.0.0-rc.1", "1.0.0", -1),
        ("1.0.0-rc.2", "1.0.0-rc.10", -1),
        ("junk", "0.0.1", -1),
    ],
)
def test_compare_versions(a: str, b: str, expected: int) -> None:
    assert compare_versions(a, b) == expected


def test_is_newer_treats_unknown_current_as_outdated() -> None:
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.2.0", "0.2.0")
    assert is_newer("0.2.0", None)
    assert not is_newer(None, "0.1.0")
    assert not is_newer("nope", "0.1.0")


# --- feed ---------------------------------------------------------------------


def test_parse_feed_reads_the_electron_builder_scalars() -> None:
    feed = parse_feed(MAC_FEED)
    assert feed is not None
    assert feed.version == "0.2.0"
    assert feed.path == "LeagueOfLeagues-0.2.0-universal.zip"
    assert feed.sha512 == "machash=="
    assert feed.size == 123456789
    assert feed.release_date == "2026-09-08T10:00:00.000Z"


def test_parse_feed_rejects_html_and_empty() -> None:
    assert parse_feed("<!doctype html><html>version: nope</html>") is None
    assert parse_feed("") is None
    assert parse_feed(None) is None


async def test_feed_cache_reuses_hits_and_briefly_remembers_misses() -> None:
    calls: list[str] = []
    answers: dict[str, str | None] = {"u": None}

    async def fetcher(url: str) -> str | None:
        calls.append(url)
        return answers["u"]

    cache = FeedCache(ttl_s=300, miss_ttl_s=30)
    assert await cache.get("u", fetcher, now=1000) is None
    assert await cache.get("u", fetcher, now=1010) is None  # miss remembered
    assert calls == ["u", "u"][:1]
    answers["u"] = WIN_FEED
    feed = await cache.get("u", fetcher, now=1040)  # miss expired -> re-read
    assert feed is not None and feed.version == "0.2.0"
    assert len(calls) == 2
    assert await cache.get("u", fetcher, now=1200) is feed  # hit within ttl
    assert len(calls) == 2
    assert await cache.get("u", fetcher, now=1400) is not None  # ttl expired
    assert len(calls) == 3


async def test_feed_cache_swallows_fetcher_errors() -> None:
    async def boom(_url: str) -> str | None:
        raise RuntimeError("edge down")

    cache = FeedCache(ttl_s=300)
    assert await cache.get("u", boom) is None


# --- describe_release ---------------------------------------------------------


def test_describe_release_required_below_minimum() -> None:
    feed = parse_feed(WIN_FEED)
    info = describe_release(
        platform="win32",
        current="0.1.0",
        feed=feed,
        downloads_url="https://x/downloads/",
        minimum="0.1.5",
    )
    assert info.status == "update_available"
    assert info.required is True
    assert info.download_url == "https://x/downloads/LeagueOfLeagues-Setup.exe"
    assert info.package_url == "https://x/downloads/LeagueOfLeagues-Setup-0.2.0.exe"
    assert info.package_sha512 == "winhash=="
    assert info.feed_url == "https://x/downloads"


def test_describe_release_override_has_no_package() -> None:
    info = describe_release(
        platform="darwin",
        current="0.1.0",
        feed=None,
        downloads_url="https://x/downloads",
        minimum=None,
        latest_override="9.9.9",
    )
    assert info.status == "update_available"
    assert info.latest == "9.9.9"
    assert info.package_url is None
    assert info.download_url == "https://x/downloads/LeagueOfLeagues.dmg"


# --- endpoint -----------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    base = {"frontend_base_url": "https://lol.example", "desktop_feed_cache_seconds": 300}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _feeds(win: str | None, mac: str | None):  # type: ignore[no-untyped-def]
    seen: list[str] = []

    async def fetcher(url: str) -> str | None:
        seen.append(url)
        if url.endswith("/latest.yml"):
            return win
        if url.endswith("/latest-mac.yml"):
            return mac
        return None

    return fetcher, seen


@pytest.fixture(autouse=True)
def _fresh_cache_and_limiter():  # type: ignore[no-untyped-def]
    desktop_router._cache = None
    desktop_router._limiter._hits.clear()
    yield
    app.dependency_overrides.clear()
    desktop_router._cache = None


async def _get(params: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/v1/desktop/version", params=params)


async def test_endpoint_update_available_per_platform() -> None:
    fetcher, seen = _feeds(WIN_FEED, MAC_FEED)
    app.dependency_overrides[desktop_router._get_settings_dep] = lambda: _settings()
    app.dependency_overrides[desktop_router.get_feed_fetcher] = lambda: fetcher

    resp = await _get({"version": "0.1.0", "platform": "darwin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "update_available"
    assert body["up_to_date"] is False
    assert body["required"] is False
    assert body["latest"] == "0.2.0"
    assert body["current"] == "0.1.0"
    assert (
        body["package_url"] == "https://lol.example/downloads/LeagueOfLeagues-0.2.0-universal.zip"
    )
    assert body["download_url"] == "https://lol.example/downloads/LeagueOfLeagues.dmg"
    assert seen == ["https://lol.example/downloads/latest-mac.yml"]

    resp = await _get({"version": "0.2.0", "platform": "win32"})
    body = resp.json()
    assert body["status"] == "up_to_date"
    assert body["up_to_date"] is True
    assert body["package_url"] == "https://lol.example/downloads/LeagueOfLeagues-Setup-0.2.0.exe"
    assert seen[-1] == "https://lol.example/downloads/latest.yml"


async def test_endpoint_unknown_feed_never_fails() -> None:
    fetcher, _ = _feeds(None, None)
    app.dependency_overrides[desktop_router._get_settings_dep] = lambda: _settings()
    app.dependency_overrides[desktop_router.get_feed_fetcher] = lambda: fetcher
    resp = await _get({"version": "0.1.0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unknown"
    assert body["up_to_date"] is True
    assert body["latest"] is None
    assert body["download_url"] == "https://lol.example/downloads/LeagueOfLeagues-Setup.exe"


async def test_endpoint_minimum_makes_update_required() -> None:
    fetcher, _ = _feeds(WIN_FEED, MAC_FEED)
    app.dependency_overrides[desktop_router._get_settings_dep] = lambda: _settings(
        desktop_minimum_version="0.2.0"
    )
    app.dependency_overrides[desktop_router.get_feed_fetcher] = lambda: fetcher
    body = (await _get({"version": "0.1.0"})).json()
    assert body["required"] is True
    assert body["minimum"] == "0.2.0"


async def test_endpoint_override_and_custom_downloads_url() -> None:
    fetcher, seen = _feeds(WIN_FEED, MAC_FEED)
    app.dependency_overrides[desktop_router._get_settings_dep] = lambda: _settings(
        desktop_latest_version="0.3.0", desktop_downloads_url="https://cdn.example/dl/"
    )
    app.dependency_overrides[desktop_router.get_feed_fetcher] = lambda: fetcher
    body = (await _get({"version": "0.2.0", "platform": "darwin"})).json()
    assert body["latest"] == "0.3.0"
    assert body["status"] == "update_available"
    assert body["package_url"] is None
    assert body["download_url"] == "https://cdn.example/dl/LeagueOfLeagues.dmg"
    assert seen == []  # the override skips the feed entirely


async def test_endpoint_rejects_unknown_platform() -> None:
    app.dependency_overrides[desktop_router._get_settings_dep] = lambda: _settings()
    resp = await _get({"platform": "linux"})
    assert resp.status_code == 422


async def test_endpoint_rate_limited_per_ip() -> None:
    fetcher, _ = _feeds(WIN_FEED, MAC_FEED)
    app.dependency_overrides[desktop_router._get_settings_dep] = lambda: _settings()
    app.dependency_overrides[desktop_router.get_feed_fetcher] = lambda: fetcher
    codes = [(await _get({"version": "0.1.0"})).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60
    assert codes[60] == 429
