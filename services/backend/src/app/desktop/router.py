"""``GET /desktop/version`` — is the installed client current? (PLAN.md §3.3)

Unauthenticated on purpose: the client checks before it is paired, and the
website may show "latest vX". It is rate limited per IP like the other public
endpoints, reveals nothing but what ``/downloads/latest*.yml`` already says, and
never fails the caller — an unreadable feed answers ``status: unknown``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.desktop import service
from app.desktop.service import FeedCache, Fetcher, Platform, ReleaseInfo, Status
from app.ratelimit import FixedWindowLimiter

router = APIRouter(prefix="/desktop", tags=["desktop"])

#: Installed clients poll every 30 minutes; 60/min per IP is generous for a
#: LAN party behind one NAT and still a brake on scripted hammering.
_limiter = FixedWindowLimiter(max_hits=60, window_s=60.0)

_cache: FeedCache | None = None


class DesktopVersionResponse(BaseModel):
    platform: Platform
    current: str | None = Field(description="The version the caller reported, if any.")
    latest: str | None = Field(description="Newest published version; null when unknown.")
    minimum: str | None = Field(description="Oldest version still allowed to run.")
    status: Status
    up_to_date: bool
    required: bool = Field(description="Caller is below the minimum: update before playing.")
    published_at: str | None
    download_url: str = Field(description="The installer a person can run by hand.")
    package_url: str | None = Field(description="What the in-app updater fetches.")
    package_sha512: str | None = Field(description="Base64 SHA-512 of package_url.")
    package_size: int | None
    feed_url: str


def _get_settings_dep() -> Settings:
    return get_settings()


def get_feed_fetcher(settings: Annotated[Settings, Depends(_get_settings_dep)]) -> Fetcher:
    """HTTP by default; the mounted directory when ``DESKTOP_DOWNLOADS_DIR`` is set."""
    if settings.desktop_downloads_dir:
        return service.local_dir_fetcher(settings.desktop_downloads_dir)
    return service.fetch_text_http


def get_feed_cache(settings: Annotated[Settings, Depends(_get_settings_dep)]) -> FeedCache:
    global _cache  # noqa: PLW0603 - process-wide memo, same lifetime as the app
    if _cache is None:
        _cache = FeedCache(ttl_s=float(settings.desktop_feed_cache_seconds))
    return _cache


def downloads_url(settings: Settings) -> str:
    if settings.desktop_downloads_url:
        return settings.desktop_downloads_url.rstrip("/")
    return f"{settings.frontend_base_url.rstrip('/')}/downloads"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _response(info: ReleaseInfo) -> DesktopVersionResponse:
    return DesktopVersionResponse(
        platform=info.platform,
        current=info.current,
        latest=info.latest,
        minimum=info.minimum,
        status=info.status,
        up_to_date=info.status != "update_available",
        required=info.required,
        published_at=info.published_at,
        download_url=info.download_url,
        package_url=info.package_url,
        package_sha512=info.package_sha512,
        package_size=info.package_size,
        feed_url=info.feed_url,
    )


@router.get(
    "/version",
    response_model=DesktopVersionResponse,
    summary="Latest desktop client version and whether the caller must update",
)
async def get_desktop_version(
    request: Request,
    settings: Annotated[Settings, Depends(_get_settings_dep)],
    fetcher: Annotated[Fetcher, Depends(get_feed_fetcher)],
    cache: Annotated[FeedCache, Depends(get_feed_cache)],
    platform: Annotated[Platform, Query(description="win32 or darwin")] = "win32",
    version: Annotated[
        str | None, Query(max_length=64, description="The caller's running version")
    ] = None,
) -> DesktopVersionResponse:
    if not _limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")
    base = downloads_url(settings)
    override = settings.desktop_latest_version or None
    feed = None
    if override is None:
        feed = await cache.get(f"{base}/{service.FEED_FILES[platform]}", fetcher)
    info = service.describe_release(
        platform=platform,
        current=version,
        feed=feed,
        downloads_url=base,
        minimum=settings.desktop_minimum_version or None,
        latest_override=override,
    )
    return _response(info)
