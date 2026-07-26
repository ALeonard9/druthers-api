"""
Centralized application configuration.

All environment-driven settings are read here through a single Pydantic
``Settings`` object instead of scattered ``os.getenv`` calls. Import
``get_settings()`` wherever configuration is needed.
"""

from functools import lru_cache
from typing import FrozenSet, List, Optional

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
    access_token_expire_minutes: int = 30
    google_client_id: Optional[str] = None

    # --- Abuse resistance (#148, threat model H1/H2) ---
    # Kill switch for /v1/auth/token: prod is Google + API keys only.
    disable_password_login: bool = False
    # None = enforce in dev/prod, skip in local/CI; set explicitly to override.
    rate_limits_enabled: Optional[bool] = None
    rate_limit_auth: int = 10  # sign-in attempts per IP per 5 minutes
    rate_limit_search: int = 60  # search-proxy calls per user per minute
    catalog_add_daily_cap: int = 200  # catalog creations per user per day

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

    # --- Observability ---
    loki_url: Optional[str] = None

    # --- CORS (comma-separated origins) ---
    cors_origins: str = 'http://localhost:3000'

    # --- External APIs (movies search proxy — TMDB) ---
    tmdb_api_key: Optional[str] = None

    # --- External APIs (games search proxy — IGDB via Twitch OAuth) ---
    twitch_client_id: Optional[str] = None
    twitch_client_secret: Optional[str] = None

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


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()
