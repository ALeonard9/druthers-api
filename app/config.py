"""
Centralized application configuration.

All environment-driven settings are read here through a single Pydantic
``Settings`` object instead of scattered ``os.getenv`` calls. Import
``get_settings()`` wherever configuration is needed.
"""

from functools import lru_cache
from typing import FrozenSet, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings, populated from the process environment.

    Unknown environment variables are ignored so the same environment can be
    shared with other services (web, mcp) without validation errors.
    """

    model_config = SettingsConfigDict(extra='ignore', case_sensitive=False)

    env: str = 'local'
    lz: Optional[str] = None
    log_level: str = 'INFO'
    time_zone: str = 'America/New_York'
    # Baked in at image build time (see Dockerfile ARG/ENV GIT_SHA). Lets a
    # running container be checked against the working tree instead of
    # silently serving a stale build (api#232).
    git_sha: Optional[str] = None

    # --- Database ---
    database_url: Optional[str] = None
    postgres_user: Optional[str] = None
    postgres_password: Optional[str] = None
    postgres_host: Optional[str] = None
    postgres_connection_port: str = '5432'
    postgres_db: str = 'druthers'
    # SQLAlchemy's default pool is 5 + 10 overflow. One page render fans out
    # several concurrent API calls, so the default silently serialises the
    # tail of a render behind the pool. Sized here instead of implied.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: int = 10

    # --- Auth ---
    jwt_secret_key: Optional[str] = None
    access_token_expire_minutes: int = 1440
    # Refresh tokens (#246) keep the access token short-lived without making
    # people re-authenticate with Google. Expiry slides on every rotation, so
    # a user who opens the app at least once a month never signs in again;
    # one who disappears for longer does.
    refresh_token_expire_days: int = 30
    # Grace window in which re-presenting a just-rotated token is treated as
    # two requests racing, not as a replay. A page render fans out several
    # calls at once; without this, the second one to arrive after expiry
    # would look like theft and sign the user out. Beyond the window, reuse
    # still burns the whole session down.
    refresh_token_reuse_leeway_seconds: int = 30

    # Argon2 password hashing cost parameters (#285).
    argon2_time_cost: Optional[int] = None
    argon2_memory_cost: Optional[int] = None
    argon2_parallelism: Optional[int] = None
    google_client_id: Optional[str] = None
    # Additional OAuth client ids accepted at sign-in, comma-separated. Native
    # clients need their own client id (an iOS client is keyed to the bundle
    # id and cannot share the web one), and the id token they mint carries
    # that id as its audience. Additive: deployments setting only
    # GOOGLE_CLIENT_ID are unaffected.
    google_additional_client_ids: Optional[str] = None

    # --- Abuse resistance (#148, threat model H1/H2) ---
    # Kill switch for /v1/auth/token: prod is Google + API keys only.
    disable_password_login: bool = False
    # None = enforce in dev/prod, skip in local/CI; set explicitly to override.
    rate_limits_enabled: Optional[bool] = None
    rate_limit_auth: int = 10  # sign-in attempts per IP per 5 minutes
    # Refresh is keyed per user, not per IP: the web BFF calls the API
    # server-to-server, so every browser shares one source IP and an IP bucket
    # would throttle the whole site. A signed-in user needs ~2 refreshes an
    # hour, so this only catches a client stuck in a loop.
    rate_limit_refresh: int = 60  # refreshes per user per 5 minutes
    rate_limit_search: int = 60  # search-proxy calls per user per minute
    catalog_add_daily_cap: int = 200  # catalog creations per user per day
    # Friend requests (#275) are the one write that takes a handle belonging
    # to someone else, so the cap is also the brake on probing for which
    # handles exist. A person adds a handful of friends in a sitting; a
    # scraper wants thousands.
    rate_limit_friend_requests: int = 30  # friend requests per user per hour
    # Following (#276) targets only already-public profiles, so there is no
    # probing concern the way there is for friend requests — this cap exists
    # purely to stop a mass-follow script.
    rate_limit_follows: int = 60  # follow actions per user per hour

    # --- Invite-only access (#183) ---
    # Kill switch for POST /v1/users: closes open password self-registration.
    # Off by default so local/CI (and any pre-existing deployment) keep
    # working until an operator opts in per environment.
    disable_signup: bool = False
    # Comma-separated email allowlist. Unset = no-op (today's open-Google
    # behavior). Set = only these addresses may complete ANY Google sign-in
    # (new account or existing) — everyone else gets a clear invite-only
    # rejection. Intended for QA/pre-launch: set to just the operator's own
    # Google account.
    oauth_allowlist: Optional[str] = None

    # --- Handle claiming (#278) ---
    # Comma-separated handles exempted from the profanity check, case-
    # insensitive. No wordlist is complete or neutral — `better-profanity`'s
    # will occasionally flag a legitimate handle as a false positive (see
    # `app/services/handles.py`). This is Adam's lever to unblock a specific
    # user without touching the wordlist or redeploying code: add the handle,
    # set the env var, restart. Unset = no exemptions (today's behavior for
    # everyone).
    handle_profanity_allowlist: Optional[str] = None

    # --- Observability ---
    loki_url: Optional[str] = None

    # --- CORS (comma-separated origins) ---
    cors_origins: str = 'http://localhost:3000'

    # --- External APIs (movies search proxy — TMDB) ---
    tmdb_api_key: Optional[str] = None

    # --- External APIs (games search proxy — IGDB via Twitch OAuth) ---
    twitch_client_id: Optional[str] = None
    twitch_client_secret: Optional[str] = None

    # --- External APIs (book enrichment fallback — Google Books) ---
    # Open Library is the primary book source and needs no key. This is only
    # used to enrich rows Open Library cannot resolve by ISBN but which carry
    # a legacy ``googleid``; enrichment simply skips them when unset.
    google_books_api_key: Optional[str] = None

    @property
    def time_zone_info(self) -> ZoneInfo:
        """Return the configured IANA time zone for operator-facing time."""
        return ZoneInfo(self.time_zone)

    @field_validator('time_zone')
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        """Reject an invalid IANA time zone before the app starts."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f'Unknown IANA time zone: {value}') from exc
        return value

    @property
    def sqlalchemy_database_url(self) -> str:
        """
        Resolve the SQLAlchemy connection URL.

        ``DATABASE_URL`` wins when set; otherwise local uses SQLite and every
        other environment builds a PostgreSQL URL from the discrete parts.
        """
        if self.database_url:
            return self.database_url
        if self.env == 'local':
            return 'sqlite:///./aleonard-api-local.db'
        return (
            f'postgresql://{self.postgres_user}:{self.postgres_password}'
            f'@{self.postgres_host}:{self.postgres_connection_port}/{self.postgres_db}'
        )

    @property
    def cors_origin_list(self) -> List[str]:
        """Return CORS origins as a list."""
        return [o.strip() for o in self.cors_origins.split(',') if o.strip()]

    @property
    def is_local(self) -> bool:
        """True for the local/SQLite developer environment."""
        return self.env == 'local'

    @property
    def is_ci(self) -> bool:
        """True when running inside CI (GitHub Actions)."""
        return self.env == 'github'

    @property
    def oauth_allowlist_emails(self) -> Optional[FrozenSet[str]]:
        """
        Parsed, lowercased allowlist, or ``None`` when the feature is off.

        ``None`` (rather than an empty set) is the "not configured" sentinel
        so callers can distinguish "no restriction" from "restricted to
        nobody" (an empty/blank ``OAUTH_ALLOWLIST`` is treated as unset).
        """
        if not self.oauth_allowlist:
            return None
        emails = frozenset(
            email.strip().lower()
            for email in self.oauth_allowlist.split(',')
            if email.strip()
        )
        return emails or None

    @property
    def handle_profanity_allowlist_set(self) -> FrozenSet[str]:
        """
        Parsed, lowercased handle exemptions (empty set when unset).

        Unlike ``oauth_allowlist_emails`` this has no "feature off" sentinel
        to preserve — an empty set and "unset" behave identically at the
        call site (nothing is exempted), so there's no ambiguity to protect
        against.
        """
        if not self.handle_profanity_allowlist:
            return frozenset()
        return frozenset(
            handle.strip().lower()
            for handle in self.handle_profanity_allowlist.split(',')
            if handle.strip()
        )

    @property
    def google_client_ids(self) -> List[str]:
        """
        Every OAuth client id whose tokens we accept, primary first.

        ``google-auth`` takes a list for ``audience`` and requires the token's
        ``aud`` to match one entry, so widening this list adds accepted issuing
        clients without weakening verification of any of them. Empty list means
        Google sign-in is not configured.
        """
        ids = []
        for raw in (self.google_client_id, self.google_additional_client_ids):
            if not raw:
                continue
            for client_id in raw.split(','):
                client_id = client_id.strip()
                # Preserve order and drop duplicates: a client id listed in
                # both settings should not be sent twice.
                if client_id and client_id not in ids:
                    ids.append(client_id)
        return ids

    @property
    def argon2_params(self) -> dict:
        """
        Argon2 password hashing parameters (#285).

        In test environment (env == 'test'), cheap settings (time_cost=1, memory_cost=8,
        parallelism=1) are used to accelerate tests. In non-test environments, standard
        Argon2 defaults apply.
        """
        if self.env == 'test':
            return {
                'time_cost': self.argon2_time_cost or 1,
                'memory_cost': self.argon2_memory_cost or 8,
                'parallelism': self.argon2_parallelism or 1,
            }
        res = {}
        if self.argon2_time_cost is not None:
            res['time_cost'] = self.argon2_time_cost
        if self.argon2_memory_cost is not None:
            res['memory_cost'] = self.argon2_memory_cost
        if self.argon2_parallelism is not None:
            res['parallelism'] = self.argon2_parallelism
        return res


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
