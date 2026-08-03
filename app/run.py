"""
This module contains the main application setup and routing.
"""

import asyncio
import json
import os
import time

import uvicorn
from fastapi import FastAPI
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
    router_movies,
    router_games,
    router_books,
    router_tv,
)
from .schemas.model_schemas import OutResponseBaseModel
from .utils.exceptions import (
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)

settings = get_settings()


# Create FastAPI app
app = FastAPI(
    title='druthers.io API ' + settings.env,
    description=(
        'API for druthers.io — track and rank the movies, TV, books, and '
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
            'description': 'Asymmetric, unapproved follows — grants no extra visibility',
        },
        {'name': 'Search', 'description': 'Cross-domain global search'},
        {
            'name': 'Summary',
            'description': 'Home summary — per-shelf Top 5 and counts',
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
app.include_router(router_friends.router)
app.include_router(router_follows.router)
app.include_router(router_preferences.router)
app.include_router(router_summary.router)

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
    return OutResponseBaseModel(message='druthers.io API — your favorites, ranked.')


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

    try:
        db = next(get_db())
        db_user.create_admin_user(db)
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Startup seeding is best-effort: a misconfigured admin seed or an
        # unreachable database must never stop the server from listening
        # (Cloud Run kills revisions that don't bind their port).
        logger.error('Startup seeding skipped: %s', e)

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

    # We're already inside asyncio.run() here — the sync uvicorn.run() would
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
