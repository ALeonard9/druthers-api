"""
Abuse resistance (#148, threat model H1/H2): lightweight sliding-window rate
limits served as FastAPI dependencies.

Limits are in-memory and therefore per-instance - plenty for a small Cloud
Run service where the caps exist to stop bots and runaway loops, not to
meter a distributed fleet. Enforcement is on in deployed environments
(dev/prod) and off in local/CI unless ``RATE_LIMITS_ENABLED`` says otherwise;
defaults are generous enough that a human never notices them.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request, status

from app.auth.oauth2 import get_current_user
from app.config import get_settings

_lock = threading.Lock()
_events: dict = defaultdict(deque)

AUTH_WINDOW_SECONDS = 300
SEARCH_WINDOW_SECONDS = 60
CATALOG_WINDOW_SECONDS = 86400
FRIEND_WINDOW_SECONDS = 3600
FOLLOW_WINDOW_SECONDS = 3600
EVICTION_INTERVAL_SECONDS = 60


class _EvictionState:
    def __init__(self):
        self.last_eviction = 0.0


_eviction_state = _EvictionState()


def reset() -> None:
    """Clear all recorded events (tests only)."""
    with _lock:
        _events.clear()
        _eviction_state.last_eviction = 0.0


def _enforced() -> bool:
    settings = get_settings()
    if settings.rate_limits_enabled is not None:
        return settings.rate_limits_enabled
    return settings.env in ('dev', 'qa', 'prod')


def client_ip(request: Request) -> str:
    """
    Best-effort caller IP. Behind Cloud Run/Cloudflare the connecting peer is
    the proxy. Cloud Run appends the connecting client's IP after any
    client-supplied X-Forwarded-For hops, so use the rightmost value.
    """
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.rsplit(',', 1)[-1].strip()
    return request.client.host if request.client else 'unknown'


def _window_for_key(key: str) -> int:
    if key.startswith(('auth:', 'refresh:')):
        return AUTH_WINDOW_SECONDS
    if key.startswith('search:'):
        return SEARCH_WINDOW_SECONDS
    if key.startswith('catalog:'):
        return CATALOG_WINDOW_SECONDS
    if key.startswith(('friend:', 'follow:')):
        return FRIEND_WINDOW_SECONDS
    raise ValueError(f'Unknown rate-limit key: {key}')


def _evict_expired_events(now: float) -> None:
    if now - _eviction_state.last_eviction < EVICTION_INTERVAL_SECONDS:
        return
    for key, events in list(_events.items()):
        cutoff = now - _window_for_key(key)
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            del _events[key]
    _eviction_state.last_eviction = now


def _allow(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _lock:
        _evict_expired_events(now)
        events = _events[key]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


def _reject(what: str, retry_after_seconds: int):
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f'Too many {what} - try again later',
        headers={'Retry-After': str(retry_after_seconds)},
    )


def auth_rate_limit(request: Request) -> None:
    """Per-IP cap on sign-in attempts (password and Google alike)."""
    if not _enforced():
        return
    limit = get_settings().rate_limit_auth
    if not _allow(f'auth:{client_ip(request)}', limit, AUTH_WINDOW_SECONDS):
        _reject('sign-in attempts', AUTH_WINDOW_SECONDS)


def refresh_rate_limit(user) -> None:
    """
    Per-user cap on token refreshes (#246).

    Called from inside the endpoint rather than as a dependency: the user is
    only known once the refresh token resolves. Keying on the user instead of
    the IP matters here - the web BFF refreshes server-to-server, so an IP
    bucket would be shared by every browser on the site.
    """
    if not _enforced():
        return
    limit = get_settings().rate_limit_refresh
    if not _allow(f'refresh:{user.pk}', limit, AUTH_WINDOW_SECONDS):
        _reject('token refreshes', AUTH_WINDOW_SECONDS)


def search_rate_limit(current_user: list = Depends(get_current_user)) -> None:
    """Per-user cap on external search-proxy calls (they burn API quotas)."""
    if not _enforced():
        return
    limit = get_settings().rate_limit_search
    if not _allow(f'search:{current_user[0].pk}', limit, SEARCH_WINDOW_SECONDS):
        _reject('searches', SEARCH_WINDOW_SECONDS)


def catalog_add_cap(current_user: list = Depends(get_current_user)) -> None:
    """Per-user daily cap on catalog creation (spam/pollution brake)."""
    if not _enforced():
        return
    limit = get_settings().catalog_add_daily_cap
    if not _allow(f'catalog:{current_user[0].pk}', limit, CATALOG_WINDOW_SECONDS):
        _reject('catalog additions for today', CATALOG_WINDOW_SECONDS)


def friend_request_rate_limit(current_user: list = Depends(get_current_user)) -> None:
    """
    Per-user cap on outgoing friend requests (#275).

    Doubles as the brake on handle probing. Sending a request answers
    identically whether or not the handle exists, so the only way to learn
    anything from the endpoint is volume - and this counts *attempts*, before
    the handle is looked up, so a miss costs an attacker exactly as much as a
    hit. Keyed per user (not per IP) because the web BFF calls the API
    server-to-server and every browser would otherwise share one bucket.
    """
    if not _enforced():
        return
    limit = get_settings().rate_limit_friend_requests
    if not _allow(f'friend:{current_user[0].pk}', limit, FRIEND_WINDOW_SECONDS):
        _reject('friend requests', FRIEND_WINDOW_SECONDS)


def follow_rate_limit(current_user: list = Depends(get_current_user)) -> None:
    """
    Per-user cap on follow actions (#276).

    Unlike friend requests, following carries no probing concern - a follow
    only ever targets a profile that is already public. This cap exists
    purely to stop a mass-follow script, so it is looser than the friend
    request one.
    """
    if not _enforced():
        return
    limit = get_settings().rate_limit_follows
    if not _allow(f'follow:{current_user[0].pk}', limit, FOLLOW_WINDOW_SECONDS):
        _reject('follows', FOLLOW_WINDOW_SECONDS)
