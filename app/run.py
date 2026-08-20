"""
This module contains the main application setup and routing.
"""

import asyncio
import json
import os
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import authentication
from .config import get_settings
from .db import db_user, models
from .db.database import engine, get_db
from .log.logging_config import logger
from .router.v1 import (
    user,
    router_activity,
    router_admin,
    router_api_keys,
    router_export,
    router_follows,
    router_friends,
    router_import,
    router_notifications,
    router_preferences,
    router_search,
    router_summary,
    router_visibility,
    router_comparison,
    router_movies,
    router_games,
    router_books,
    router_tv,
)
from .schemas.model_schemas import OutResponseBaseModel
from .services import admin_audit
from .utils.exceptions import (
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)

settings = get_settings()


def _is_mutable_user_read(method: str, path: str) -> bool:
    """Return whether a response reflects tracker or viewer-specific state."""
    if method != 'GET':
        return False
    return (
        path.startswith('/v1/users/me/')
        or path.startswith('/v1/public/')
        or path == '/v1/search'
        or path.endswith('/search')
    )


# Create FastAPI app
app = FastAPI(
    title='druthers.io API ' + settings.env,
    description=(
        'API for druthers.io - track and rank the movies, TV, books, and '
        'games you actually care about.'
    ),
    version='0.0.1',
    contact={
        'name': 'Druthers',
        'url': 'https://www.druthers.io',
    },
    openapi_tags=[
        {'name': 'intro', 'description': 'Welcome message'},
        {'name': 'authentication', 'description': 'Auth operations'},
        {'name': 'users', 'description': 'User operations'},
        {
            'name': 'Movies',
            'description': 'Movie catalog, search, and per-user tracker',
        },
        {'name': 'TV', 'description': 'TV shows, episodes, and per-user tracker'},
        {'name': 'Games', 'description': 'Video game catalog and per-user tracker'},
        {'name': 'Books', 'description': 'Book catalog and per-user tracker'},
        {
            'name': 'Activity',
            'description': 'Cross-domain activity log and "I\'m bored" recommendation',
        },
        {'name': 'Notifications', 'description': 'Per-user notification feed'},
        {
            'name': 'Friends',
            'description': 'Mutual friend requests, friends list, and unfriend',
        },
        {
            'name': 'Follows',
            'description': 'Asymmetric, unapproved follows - grants no extra visibility',
        },
        {'name': 'Search', 'description': 'Cross-domain global search'},
        {
            'name': 'Summary',
            'description': 'Home summary - per-shelf Top 5 and counts',
        },
        {
            'name': 'Admin',
            'description': 'Admin-only user directory and audit trail',
        },
    ],
    openapi_url='/openapi.json',
    servers=[
        {'url': 'https://api.druthers.io', 'description': 'Production server'},
        {'url': 'http://localhost:8000', 'description': 'Local server'},
    ],
    license_info={
        'name': 'GPL-3.0',
        'url': 'https://www.gnu.org/licenses/gpl-3.0.html',
    },
)


@app.middleware('http')
async def log_request_latency(request, call_next):
    """
    Emit a duration for every request so slow pages can be diagnosed from
    Loki instead of inferred from the code. Uses the route template (not the
    raw path) so per-endpoint latency aggregates cleanly.
    """
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    route = request.scope.get('route')
    logger.info(
        'request %s %s -> %s in %.1fms',
        request.method,
        getattr(route, 'path', request.url.path),
        response.status_code,
        elapsed_ms,
        extra={
            'http_method': request.method,
            'http_route': getattr(route, 'path', request.url.path),
            'http_status': response.status_code,
            'duration_ms': round(elapsed_ms, 1),
        },
    )
    # Lets the browser's network panel attribute the time without a log dive.
    response.headers['Server-Timing'] = f"app;dur={elapsed_ms:.1f}"
    # Tracker mutations make public profiles, rankings, and catalog-search
    # badges stale immediately. These reads are cheap live SQL lookups and
    # viewer-specific, so do not let a browser, CDN, or framework data cache
    # retain them under a URL-only key (#297).
    if _is_mutable_user_read(request.method, request.url.path):
        response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.middleware('http')
async def request_id_middleware(request, call_next):
    """
    Stamp every request with an id an application log line and, for the
    admin router, an audit row can both carry - the only way to join the two
    later.

    Starlette actually wraps ``@app.middleware`` handlers in *reverse*
    registration order - the last one added is outermost, so
    ``admin_audit_denial_middleware`` below (registered after this one) runs
    its own "before ``call_next``" code first, ahead of this middleware's.
    That does not matter here: ``admin_audit_denial_middleware`` only reads
    ``request.state.request_id`` *after* its own ``call_next`` returns, and
    that call is what invokes this middleware (and everything inside it) in
    the first place - by the time control comes back, the id has already
    been set. ``scope['state']`` (which ``request.state`` reads and writes)
    is shared across the whole chain, not copied per middleware, so this
    holds regardless of nesting order. Confirmed on a live 403.
    """
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers['X-Request-Id'] = request.state.request_id
    return response


@app.middleware('http')
async def admin_audit_denial_middleware(request, call_next):
    """
    Log a denied (or unauthenticated) admin-router request that never
    reached a route handler.

    ``require_admin`` is a router-level dependency (see
    ``router_admin.router``), so it raises before any endpoint body - and
    therefore before that endpoint's own ``admin_audit.record`` call - runs.
    This is the one place such a denial can still be recorded. Covers both
    403 (authenticated, not an admin) and 401 (missing/invalid/expired
    token) - a prober who never authenticates at all is exactly the case an
    audit trail of an admin surface should not go blind on.

    Skipped when ``request.state.admin_audit_recorded`` is set: a handler
    that reached its own body and denied the action for a business reason
    (``disable_user`` refusing a self- or another-admin target) already
    wrote its own, more specific row via ``admin_audit.record`` - logging
    again here would duplicate it as a generic, less informative
    ``admin.access`` entry for the exact same request.

    The whole block is guarded: a DB failure here must come back as the
    denial response the caller already has, not an unhandled exception. An
    exception escaping a ``@app.middleware`` handler surfaces at
    ``ServerErrorMiddleware``, which sits *outside* ``CORSMiddleware`` - so
    an unguarded failure here would turn a clean, CORS-headered 403 into an
    opaque CORS error in the browser instead of either the denial or the
    underlying error.
    """
    response = await call_next(request)
    is_admin_denial = (
        request.url.path.startswith('/v1/admin')
        and response.status_code
        in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
        and not getattr(request.state, 'admin_audit_recorded', False)
    )
    if is_admin_denial:
        try:
            # Middleware sits outside FastAPI's dependency graph, so a bare
            # SessionLocal() would bind to the real engine even under a test
            # client that has overridden ``get_db`` (dependency_overrides
            # only intercepts Depends() resolution). Going through the same
            # override lookup FastAPI itself uses keeps this on the test's
            # session too.
            db_dependency = app.dependency_overrides.get(get_db, get_db)
            db_generator = db_dependency()
            db = next(db_generator)
            try:
                actor, via_impersonation = admin_audit.resolve_actor_best_effort(
                    request, db
                )
                admin_audit.record(
                    request,
                    actor=actor,
                    action='admin.access',
                    result=admin_audit.AdminAuditResult.DENIED,
                    status_code=response.status_code,
                    # Marks a denial made from a live impersonation session
                    # so the trail is explicit that this was the acting
                    # admin probing the admin surface from behind another
                    # user's identity, not that user themself doing it.
                    detail=({'via_impersonation': True} if via_impersonation else None),
                )
            finally:
                next(db_generator, None)
        except Exception:  # pylint: disable=broad-exception-caught
            # Never let a logging failure replace the real response - see
            # docstring. Full traceback so it's still investigable.
            logger.exception(
                'Failed to record admin denial audit row for %s %s',
                request.method,
                request.url.path,
            )
    return response


app.include_router(authentication.router, prefix='/v1/auth')
app.include_router(user.router, prefix='/v1/users')
app.include_router(router_movies.router)
app.include_router(router_games.router)
app.include_router(router_books.router)
app.include_router(router_tv.router)
app.include_router(router_activity.router)
app.include_router(router_notifications.router)
app.include_router(router_search.router)
app.include_router(router_api_keys.router)
app.include_router(router_export.router)
app.include_router(router_import.router)
app.include_router(router_visibility.router)
app.include_router(router_comparison.router)
app.include_router(router_friends.router)
app.include_router(router_follows.router)
app.include_router(router_preferences.router)
app.include_router(router_summary.router)
app.include_router(router_admin.router)

# Serve static files
app.mount('/static', StaticFiles(directory='app/static'), name='static')

# Register custom exception handlers
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)


@app.get('/', tags=['intro'], response_model=OutResponseBaseModel)
def index():
    """
    Index endpoint that returns a welcome message.
    """
    return OutResponseBaseModel(message='druthers.io API - your favorites, ranked.')


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    """
    Endpoint to serve favicon.
    """
    return FileResponse('app/static/favicon.ico')


@app.get('/health', include_in_schema=False)
def health():
    """
    Liveness/staleness check: carries the git SHA baked into this image at
    build time, so a running container can be checked against the working
    tree instead of silently serving a stale build (api#232).
    """
    return {'status': 'ok', 'env': settings.env, 'git_sha': settings.git_sha}


async def generate_openapi_json():
    """
    Generate the OpenAPI schema and write it to a file upon startup.
    """
    openapi_schema = app.openapi()
    with open('openapi.json', 'w', encoding='utf-8') as f:
        json.dump(openapi_schema, f, indent=2)
        f.write('\n')
    logger.info('OpenAPI schema generated and written to openapi.json')


if settings.env in ('local', 'dev'):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )


async def start_server():
    """
    Starts uvicorn server with the FastAPI app.

    Schema ownership: local/CI use SQLite and create tables directly for a
    zero-setup developer loop. Deployed environments (dev/prod) own their schema
    through Alembic migrations (``alembic upgrade head``) so data is never
    dropped on restart.
    """
    logger.info(
        'Starting druthers API env=%s git_sha=%s', settings.env, settings.git_sha
    )

    if settings.is_local or settings.is_ci:
        models.Base.metadata.create_all(engine)
        logger.info('Created all tables (local/CI)')
    else:
        logger.info('Skipping create_all; schema managed by Alembic migrations')

    def seed_admin():
        try:
            db = next(get_db())
            db_user.create_admin_user(db)
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Startup seeding is best-effort: a misconfigured admin seed or an
            # unreachable database must never stop the server from listening
            # (Cloud Run kills revisions that don't bind their port).
            logger.error('Startup seeding skipped: %s', e)

    threading.Thread(target=seed_admin, daemon=True).start()

    if settings.is_local:
        await generate_openapi_json()

    port = int(os.getenv('PORT', '8000'))
    if settings.env in ('local', 'dev'):
        # The reloader must own the process (it spawns worker subprocesses),
        # so local/dev keep the sync entry point. ENV=dev is the containerized
        # local stack (dc-dev.yml bind-mounts the working tree over it), so
        # it gets the same edit-reload loop as ENV=local (api#232).
        uvicorn.run(
            'app.run:app',
            host='0.0.0.0',
            port=port,
            reload=True,
            log_level=settings.log_level.lower(),
        )
        return

    # We're already inside asyncio.run() here - the sync uvicorn.run() would
    # try to start a second event loop and crash (exactly what took down the
    # first Cloud Run revision). Serve on the running loop instead.
    config = uvicorn.Config(
        'app.run:app',
        host='0.0.0.0',
        port=port,
        log_level=settings.log_level.lower(),
    )
    await uvicorn.Server(config).serve()


def run():
    """
    Run the FastAPI application.
    """
    asyncio.run(start_server())
