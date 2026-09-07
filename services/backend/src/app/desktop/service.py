"""Feed parsing, version comparison and caching for ``GET /desktop/version``.

Pure and offline: the only IO is the injected ``Fetcher`` (HTTP or local file),
so the router tests run without a network or a feed on disk.
"""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.logging import get_logger

log = get_logger(__name__)

Platform = Literal["win32", "darwin"]

#: The electron-builder feed file per platform (see electron-builder.yml).
FEED_FILES: dict[Platform, str] = {"win32": "latest.yml", "darwin": "latest-mac.yml"}

#: The stable installer names the website links at and the client opens for a
#: manual install (``infra/publish-desktop.sh`` keeps them pointed at the newest
#: build). Must match ``services/desktop/src/update/installers.ts``.
INSTALLER_FILES: dict[Platform, str] = {
    "win32": "LeagueOfLeagues-Setup.exe",
    "darwin": "LeagueOfLeagues.dmg",
}

#: Reads a feed file's text; ``None`` when it is missing/unreachable. Injected so
#: tests need no network and production can read a mounted directory instead.
Fetcher = Callable[[str], Awaitable[str | None]]


# ---------------------------------------------------------------------------
# Version ordering
# ---------------------------------------------------------------------------
_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$")


@dataclass(frozen=True, slots=True)
class Version:
    """A parsed semver-ish version: numeric core + optional pre-release tag."""

    core: tuple[int, ...]
    pre: tuple[str, ...] | None

    def sort_key(self) -> tuple[tuple[int, ...], int, tuple[tuple[int, str], ...]]:
        # A release outranks any pre-release of the same core ("1.0.0" > "1.0.0-rc.1");
        # pre-release parts compare numerically when both sides are numbers.
        core = tuple(self.core) + (0,) * (4 - len(self.core))
        if self.pre is None:
            return (core, 1, ())
        parts = tuple((0, p.zfill(12)) if p.isdigit() else (1, p) for p in self.pre)
        return (core, 0, parts)


def parse_version(text: str | None) -> Version | None:
    """``"v1.2.3-beta.1"`` -> Version; ``None`` for anything that is not a version."""
    if not text:
        return None
    m = _VERSION_RE.match(text)
    if m is None:
        return None
    core = tuple(int(x) for x in m.group(1).split("."))
    pre = tuple(m.group(2).split(".")) if m.group(2) else None
    return Version(core=core, pre=pre)


def compare_versions(a: str, b: str) -> int:
    """-1 / 0 / 1 like ``cmp``. Unparseable versions sort below every real one."""
    va, vb = parse_version(a), parse_version(b)
    if va is None and vb is None:
        return 0
    if va is None:
        return -1
    if vb is None:
        return 1
    ka, kb = va.sort_key(), vb.sort_key()
    return (ka > kb) - (ka < kb)


def is_newer(candidate: str | None, current: str | None) -> bool:
    """True when ``candidate`` is a real version strictly above ``current``."""
    if parse_version(candidate) is None:
        return False
    if parse_version(current) is None:
        return True
    assert candidate is not None and current is not None
    return compare_versions(candidate, current) > 0


# ---------------------------------------------------------------------------
# Feed parsing (electron-builder's latest*.yml)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Feed:
    """What a ``latest*.yml`` says about the newest build."""

    version: str
    release_date: str | None
    #: The package the in-app updater fetches (Windows: the NSIS .exe; macOS: the zip).
    path: str | None
    sha512: str | None
    size: int | None


_SCALAR_RE = re.compile(r"^\s*(?:-\s+)?([A-Za-z][A-Za-z0-9_]*):\s*(.*?)\s*$")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_feed(text: str | None) -> Feed | None:
    """Parse the handful of scalar keys electron-builder writes.

    A tiny line scanner instead of a YAML dependency: the file is flat (``version``,
    ``path``, ``sha512``, ``releaseDate``) plus a ``files`` list whose first entry
    carries ``url``/``sha512``/``size``. The first value seen for each key wins,
    which is the top-level one for ``path``/``sha512`` and the first file for
    ``size``. Returns ``None`` when there is no ``version`` (an HTML 404 page, an
    empty file, a feed that never got published).
    """
    if not text:
        return None
    found: dict[str, str] = {}
    for line in text.splitlines():
        m = _SCALAR_RE.match(line)
        if m is None:
            continue
        key, value = m.group(1), _unquote(m.group(2))
        if key in ("version", "releaseDate", "path", "sha512", "size", "url") and key not in found:
            found[key] = value
    version = found.get("version")
    if parse_version(version) is None:
        return None
    assert version is not None
    size_text = found.get("size")
    size = int(size_text) if size_text is not None and size_text.isdigit() else None
    return Feed(
        version=version,
        release_date=found.get("releaseDate"),
        path=found.get("path") or found.get("url"),
        sha512=found.get("sha512"),
        size=size,
    )


# ---------------------------------------------------------------------------
# Cached feed reads
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _CacheEntry:
    fetched_at: float
    feed: Feed | None


class FeedCache:
    """Per-URL memo of parsed feeds with a TTL; misses are cached too (briefly).

    A missing feed (fresh deploy, publish half done) must not turn every client
    poll into an upstream request, so ``None`` is remembered for ``miss_ttl_s``.
    """

    def __init__(self, *, ttl_s: float, miss_ttl_s: float = 30.0) -> None:
        self._ttl = ttl_s
        self._miss_ttl = miss_ttl_s
        self._entries: dict[str, _CacheEntry] = {}

    async def get(self, url: str, fetcher: Fetcher, *, now: float | None = None) -> Feed | None:
        at = time.time() if now is None else now
        hit = self._entries.get(url)
        if hit is not None:
            ttl = self._ttl if hit.feed is not None else self._miss_ttl
            if at - hit.fetched_at < ttl:
                return hit.feed
        try:
            text = await fetcher(url)
        except Exception as exc:  # noqa: BLE001 - a broken feed must never 500 the check
            log.warning("desktop.feed.fetch_failed", url=url, detail=str(exc))
            text = None
        feed = parse_feed(text)
        if feed is None:
            log.info("desktop.feed.unavailable", url=url)
        self._entries[url] = _CacheEntry(fetched_at=at, feed=feed)
        return feed

    def clear(self) -> None:
        self._entries.clear()


async def fetch_text_http(url: str) -> str | None:
    """Default fetcher: GET the feed over HTTP, ``None`` on any non-200."""
    import httpx  # noqa: PLC0415 - deferred so the pure parts import without it

    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        return None
    return resp.text


def local_dir_fetcher(directory: str) -> Fetcher:
    """Fetcher reading ``<directory>/<basename of url>`` (the mounted feed dir)."""

    async def read(url: str) -> str | None:
        path = Path(directory) / url.rsplit("/", 1)[-1]
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    return read


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------
Status = Literal["update_available", "up_to_date", "unknown"]


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    platform: Platform
    current: str | None
    latest: str | None
    minimum: str | None
    status: Status
    required: bool
    published_at: str | None
    download_url: str
    package_url: str | None
    package_sha512: str | None
    package_size: int | None
    feed_url: str


def describe_release(
    *,
    platform: Platform,
    current: str | None,
    feed: Feed | None,
    downloads_url: str,
    minimum: str | None,
    latest_override: str | None = None,
) -> ReleaseInfo:
    """Compare the caller's version with what the feed announces."""
    base = downloads_url.rstrip("/")
    latest = latest_override or (feed.version if feed is not None else None)
    minimum = minimum or None
    if latest is None:
        status: Status = "unknown"
    elif is_newer(latest, current):
        status = "update_available"
    else:
        status = "up_to_date"
    required = status == "update_available" and minimum is not None and is_newer(minimum, current)
    package_path = feed.path if feed is not None and not latest_override else None
    return ReleaseInfo(
        platform=platform,
        current=current,
        latest=latest,
        minimum=minimum,
        status=status,
        required=required,
        published_at=feed.release_date if feed is not None and not latest_override else None,
        download_url=f"{base}/{INSTALLER_FILES[platform]}",
        package_url=f"{base}/{package_path}" if package_path else None,
        package_sha512=feed.sha512 if feed is not None and package_path else None,
        package_size=feed.size if feed is not None and package_path else None,
        feed_url=base,
    )
