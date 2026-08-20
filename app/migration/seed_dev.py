"""
Populate the LOCAL dev Postgres with a realistic volume of real catalog data,
randomized tracker state (#228), and the fixed dev cast (#313).

Titles, ids, and metadata come from the checked-in fixtures
``app/migration/fixtures/seed_*.json`` -- real movies/shows/books/games
captured from TMDB/TVMaze/Open Library/IGDB (see ``build_seed_fixtures.py``,
which regenerates them). What's randomized is where each one lands: which
list (watchlist vs rankings), rank order, completion dates -- not the
content itself, matching #228's story ("I want it populated with real
content, just randomized").

Catalog rows are upserted on their natural key (tmdb/imdb/tvmaze/isbn/igdb),
the same idiom ``orion_import`` uses, so re-running never creates duplicate
movies/shows/books/games -- including against rows that exist for some other
reason (a prior ``task import:orion``, something added by hand while
testing). Only the *tracker* rows this script creates are marked
(``is_seed_data=True``) and thus wipeable; the catalog rows they point at are
real either way and a wipe leaves them alone.

**Refuses to run against anything but the local dev Postgres** (checks
``ENV`` and the *resolved* connection host, i.e. ``DATABASE_URL`` when set,
not just ``POSTGRES_HOST`` -- see #257) -- same guard the old Faker-based
seeder used, since this script performs bulk writes and must never reach
QA or prod.

Usage::

    task seed:dev                                # populate/refresh (default --count 270)
    task seed:dev -- --count 150                  # less volume
    task seed:dev -- --wipe                       # clear seeded tracker rows only
    task seed:dev -- --email you@example.com       # target a non-admin local user, e.g.
                                                    # whichever account Google Sign-In
                                                    # actually created when you signed in

Every run also seeds the **fixed dev cast** (#313): six accounts covering the
relationship/visibility positions the social features (compare, sharing,
friends' activity) need, anchored to the *target* user -- the ``--email``
user, default the seed admin -- who becomes "you". A seventh, unrelated
account (``admin-two``) is a second admin, so admin-console rules that
require two admins (#341's "an admin cannot impersonate/disable another
admin") are demonstrable, not just unit-tested. They all share the dev
password ``change-me``, so a demo can be driven from any seat.

``docs/dev-cast.md`` is the reference for who they are: handles, credentials,
time zones, which seat demonstrates which rule, and why the shelf sizes are
what they are. It is deliberately the only copy of that table -- ``_CAST_USERS``
below is the only other place these facts live, and a third would drift.

The cast is additive and idempotent: re-running never duplicates a user, a
friendship, a follow, or a tracker row. ``--wipe`` clears every seeded tracker
row -- the target user's randomized rows and the cast's canon rows alike --
while leaving catalog rows, the cast users themselves, and their
relationships in place.
"""

# This module sat at 993 of pylint's 1000-line default before the pending-row
# check below, so any fix to it trips too-many-lines. Suppressed rather than
# compressed: it wants splitting along domain lines (movies/tv/books/games/
# cast), which is a refactor, not something a bug fix should smuggle in.
# pylint: disable=too-many-lines

import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import db_follow
from app.db import db_friendship
from app.db.database import SessionLocal
from app.db.hash import Hash
from app.db.models import DbFollow, DbFriendship, DbUser
from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbTVEpisode,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)
from app.log.logging_config import logger
from app.services.book_search import apply_detail_to_book
from app.services.friendships import FriendshipStatus, canonical_pair
from app.services.game_search import apply_detail_to_game
from app.services.movie_search import apply_detail_to_movie
from app.services.tv_search import apply_detail_to_show

FIXTURES_DIR = Path(__file__).parent / 'fixtures'

# Other domains scale off --count at roughly prod's real proportions
# (movies >> tv/books/games), same ratios the old Faker seeder used.
_TV_RATIO = 5
_GAME_RATIO = 7
_BOOK_RATIO = 6

_FIVE_YEARS_DAYS = 5 * 365

# The old seed_fake.py identified its own rows by a reserved id namespace
# (ids themselves were fake, so there was no marker column to use instead).
# Kept here only so a dev DB seeded the old way can be cleaned up once.
_LEGACY_FAKE_ID_BASE = 900_000

# --- Fixed dev cast (#313) ---------------------------------------------------
# Six accounts covering the relationship/visibility positions the social
# features need, anchored to the seed target ("you"), plus a seventh
# (admin-two, #341) that is not: a second admin so admin-on-admin refusals
# are demonstrable. All share one password so any position can be signed
# into. Documented in the module docstring and
# the README; ``_seed_cast`` is what brings them to life.

_CAST_PASSWORD = 'change-me'
_TARGET_HANDLE = 'you'

# The nine per-user visibility columns on ``users`` (see ``DbUser``).
_TIER_FIELDS = (
    'visibility_profile',
    'visibility_movies',
    'visibility_tv',
    'visibility_books',
    'visibility_games',
    'visibility_watchlist_movies',
    'visibility_watchlist_tv',
    'visibility_watchlist_books',
    'visibility_watchlist_games',
)


def _cast_tiers(style: str, **overrides) -> dict:
    """All nine tier columns at ``style``, with ``overrides`` applied."""
    return {field: overrides.get(field, style) for field in _TIER_FIELDS}


# Eight real movies from seed_movies.json, selected by TMDB id. They form the
# deterministic overlap canon: the target user ranks all of them and each cast
# member ranks a fixed prefix, so shared-rank counts (and therefore the
# comparison alignment states) are pinned to known values on every run.
_CAST_CANON_TMDB = (157336, 603, 27205, 155, 680, 438631, 496243, 244786)

# Ranked-shelf sizes are deliberately small and deliberately uneven. Every
# movie a cast member ranks is a movie the target also ranks -- the default
# ``--count`` is the entire fixture -- so shelf size *is* overlap size, and
# overlap is what pins each comparison state. Five shared titles is the
# threshold between ``not_enough_overlap`` and ``ready``; the numbers below
# straddle it on purpose. Grow one and you change what its seat demos.
#
# Time zones are spread across the cast so the per-user zone preference is
# visible without editing anything: the greeting and the schedule's idea of
# "today" differ by seat, and Sydney/Tokyo are far enough from Chicago to
# land on a different calendar day for most of the working day.
_CAST_USERS = (
    {
        'email': 'friend@example.com',
        'display_name': 'Friend',
        'handle': 'friend',
        'position': 'friend',
        # Friends-only everywhere: from a friend's seat every shelf is visible
        # and compares ``ready``; from anyone else the profile 404s.
        'tiers': _cast_tiers('friends'),
        'canon_movies': 8,
        'time_zone': 'Europe/London',
    },
    {
        'email': 'follower@example.com',
        'display_name': 'Follower',
        'handle': 'follower',
        'position': 'follower',
        'tiers': _cast_tiers('public'),
        'canon_movies': 2,
        'time_zone': 'Asia/Tokyo',
    },
    {
        'email': 'followee@example.com',
        'display_name': 'Followee',
        'handle': 'followee',
        'position': 'followee',
        # Public profile with a friends-only books shelf: the one cast member
        # whose comparison shows ``hidden`` under a visible profile.
        'tiers': _cast_tiers('public', visibility_books='friends'),
        'canon_movies': 1,
        'time_zone': 'America/Los_Angeles',
    },
    {
        'email': 'public@example.com',
        'display_name': 'Public User',
        'handle': 'public-user',
        'position': 'public',
        'tiers': _cast_tiers('public'),
        'canon_movies': 3,
        'time_zone': 'Australia/Sydney',
    },
    {
        'email': 'private@example.com',
        'display_name': 'Private User',
        'handle': 'private-user',
        'position': 'private',
        'tiers': _cast_tiers('private'),
        # A stocked shelf that nobody else can reach. Left empty, a 404 from
        # this seat proved nothing -- "hidden because private" and "hidden
        # because there is nothing there" looked identical. The count is free
        # to move: no other seat can see this profile, so no comparison state
        # depends on it.
        'canon_movies': 6,
        'time_zone': 'America/New_York',
    },
    {
        'email': 'stranger@example.com',
        'display_name': 'Stranger',
        'handle': 'stranger',
        'position': 'stranger',
        'tiers': _cast_tiers('public'),
        'canon_movies': 0,
        # A visible profile that still compares as ``not_enough_overlap`` --
        # the case that only exists while the shared count stays under five.
        # These are non-canon rows, but the target ranks the whole fixture,
        # so they *do* count as shared titles: four is the most this seat can
        # hold without turning into another ``ready``.
        'extra_movies': 4,
        'time_zone': 'UTC',
    },
    {
        # Not a relationship-position seat like the six above - the local
        # dev DB otherwise has exactly one admin (the seed admin from
        # ADMIN_EMAIL), which makes "an admin cannot impersonate/disable
        # another admin" (#341) provable only by unit test and never
        # demonstrable in the console (api#341/#344 review, 2026-08-19).
        # ``gmail.com``, not ``@example.com`` like the rest of the cast:
        # this account only ever needs to authenticate through the normal
        # app paths (sign-in, admin actions), and those validate the email
        # (MX lookup) unlike this script's direct DB inserts, which don't.
        'email': 'admin-two@gmail.com',
        'display_name': 'Admin Two',
        'handle': 'admin-two',
        'position': 'admin',
        'tiers': _cast_tiers('private'),
        'canon_movies': 0,
        'time_zone': 'America/Chicago',
        'admin': True,
    },
)


def _assert_local_dev() -> None:
    """Refuse to run against anything but the local dev Postgres."""
    settings = get_settings()
    # Validate the host actually being connected to (settings.sqlalchemy_database_url
    # prefers DATABASE_URL over the discrete POSTGRES_* parts), not a variable that
    # may not be in play (#257).
    url = settings.sqlalchemy_database_url
    host = (urlsplit(url).hostname or '').lower()
    is_safe_host = (
        host in ('localhost', '127.0.0.1') or host.endswith('_dev')
    ) and 'neon.tech' not in host
    if settings.env != 'dev' or not is_safe_host:
        logger.error(
            'seed_dev refuses to run: ENV=%s resolved host=%s does not look '
            'like the local dev Postgres. This script performs bulk writes '
            'and must never touch QA or prod.',
            settings.env,
            host,
        )
        sys.exit(2)


def _target_user(session: Session, email: str = None) -> DbUser:
    """
    Look up the user to attach seeded tracker rows to.

    Defaults to the seed admin (``ADMIN_EMAIL``, same env var the app's own
    bootstrap uses). Pass ``--email`` to target a different local user
    instead -- e.g. whichever account Google Sign-In actually creates when
    you sign in locally, which is a different row than the seed admin.
    """
    email = email or os.getenv('ADMIN_EMAIL')
    if not email:
        logger.error(
            'ADMIN_EMAIL is not set and no --email given -- cannot attach '
            'seeded tracker rows'
        )
        sys.exit(2)
    user = session.query(DbUser).filter_by(email=email).one_or_none()
    if user is None:
        logger.error(
            'No local user with email %s -- sign in once (or start the API '
            'so the seed admin is created) and then re-run.',
            email,
        )
        sys.exit(2)
    return user


def _load_fixture(name: str) -> list:
    path = FIXTURES_DIR / name
    if not path.exists():
        logger.error(
            '%s is missing -- run `python -m app.migration.build_seed_fixtures` '
            '(needs TMDB_API_KEY/TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET) to '
            'generate the fixtures first.',
            path,
        )
        sys.exit(2)
    with path.open() as f:
        return json.load(f)


def _sample(rows: list, target: int, domain: str) -> list:
    """``target`` random rows from the fixture, capped to what it actually has."""
    if target > len(rows):
        logger.warning(
            'seed_dev: requested %d %s but the fixture only has %d -- using all of it',
            target,
            domain,
            len(rows),
        )
    return random.sample(rows, min(target, len(rows)))


def _parse_dt(value):
    return datetime.fromisoformat(value) if value else None


def _random_past_date() -> date:
    return date.today() - timedelta(days=random.randint(0, _FIVE_YEARS_DAYS))


def _random_past_datetime() -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        days=random.randint(0, _FIVE_YEARS_DAYS), seconds=random.randint(0, 86399)
    )


def _next_rank(session: Session, model, user: DbUser) -> int:
    """
    First free 1-based rank for this user/domain.

    Starts past any pre-existing ranked rows (e.g. from a prior
    ``task import:orion`` run in the same local DB) so seeding never produces
    duplicate rank numbers.
    """
    current_max = (
        session.query(model.rank)
        .filter(model.user_id == user.pk, model.rank.isnot(None))
        .order_by(model.rank.desc())
        .first()
    )
    return (current_max[0] if current_max else 0) + 1


def _already_tracked(
    session: Session, model, user_pk: int, fk_column, catalog_pk: int
) -> bool:
    """True if this user already has *any* tracker row for this catalog item.

    Pending rows count: the target is seeded for the eight canon titles twice
    (fixture sample, then cast-overlap) and neither pass is flushed while the
    other builds. Checked before the query, which autoflushes -- and flushing
    the duplicate is what raises.
    """
    if any(
        isinstance(p, model)
        and p.user_id == user_pk
        and getattr(p, fk_column.key, None) == catalog_pk
        for p in session.new
    ):
        return True
    return (
        session.query(model)
        .filter(model.user_id == user_pk, fk_column == catalog_pk)
        .first()
        is not None
    )


def _purge_legacy_fake_rows(session: Session) -> dict:
    """
    One-time cleanup for a dev DB seeded by the old Faker-based seed_fake.py.

    Those rows are unambiguously synthetic regardless of which user they're
    attached to (real catalog data never lands in the reserved id range), so
    -- unlike ``is_seed_data`` rows -- they're deleted outright, catalog rows
    and all, across every user. Idempotent: nothing matches after the first
    run, so this is cheap to call on every invocation.
    """
    movie_ids = [
        pk
        for (pk,) in session.query(DbMovie.pk).filter(DbMovie.imdb.like('ttfakemovie%'))
    ]
    show_ids = [
        pk
        for (pk,) in session.query(DbTVShow.pk).filter(DbTVShow.imdb.like('ttfaketv%'))
    ]
    episode_ids = (
        [
            pk
            for (pk,) in session.query(DbTVEpisode.pk).filter(
                DbTVEpisode.tv_show_id.in_(show_ids)
            )
        ]
        if show_ids
        else []
    )
    game_ids = [
        pk
        for (pk,) in session.query(DbVideoGame.pk).filter(
            DbVideoGame.igdb >= _LEGACY_FAKE_ID_BASE
        )
    ]
    book_ids = [
        pk for (pk,) in session.query(DbBook.pk).filter(DbBook.googleid.like('FAKE-%'))
    ]

    if episode_ids:
        session.query(DbUserTVEpisode).filter(
            DbUserTVEpisode.episode_id.in_(episode_ids)
        ).delete(synchronize_session=False)
    if show_ids:
        session.query(DbUserTVShow).filter(
            DbUserTVShow.tv_show_id.in_(show_ids)
        ).delete(synchronize_session=False)
    if movie_ids:
        session.query(DbUserMovie).filter(DbUserMovie.movie_id.in_(movie_ids)).delete(
            synchronize_session=False
        )
    if game_ids:
        session.query(DbUserVideoGame).filter(
            DbUserVideoGame.game_id.in_(game_ids)
        ).delete(synchronize_session=False)
    if book_ids:
        session.query(DbUserBook).filter(DbUserBook.book_id.in_(book_ids)).delete(
            synchronize_session=False
        )

    session.query(DbTVEpisode).filter(DbTVEpisode.pk.in_(episode_ids)).delete(
        synchronize_session=False
    )
    session.query(DbTVShow).filter(DbTVShow.pk.in_(show_ids)).delete(
        synchronize_session=False
    )
    session.query(DbMovie).filter(DbMovie.pk.in_(movie_ids)).delete(
        synchronize_session=False
    )
    session.query(DbVideoGame).filter(DbVideoGame.pk.in_(game_ids)).delete(
        synchronize_session=False
    )
    session.query(DbBook).filter(DbBook.pk.in_(book_ids)).delete(
        synchronize_session=False
    )
    session.flush()

    return {
        'legacy_movies': len(movie_ids),
        'legacy_shows': len(show_ids),
        'legacy_games': len(game_ids),
        'legacy_books': len(book_ids),
    }


def _wipe(session: Session, user: DbUser) -> dict:
    """
    Delete every tracker row this script owns for ``user``.

    Catalog rows (movies/shows/books/games) are never touched -- see
    ``DbUserMovie.is_seed_data``'s docstring for why that's safe.
    """
    seeded_shows = session.query(DbUserTVShow).filter(
        DbUserTVShow.user_id == user.pk, DbUserTVShow.is_seed_data.is_(True)
    )
    seeded_show_ids = [row.tv_show_id for row in seeded_shows.all()]
    episodes_deleted = 0
    if seeded_show_ids:
        episode_ids = [
            pk
            for (pk,) in session.query(DbTVEpisode.pk).filter(
                DbTVEpisode.tv_show_id.in_(seeded_show_ids)
            )
        ]
        if episode_ids:
            episodes_deleted = (
                session.query(DbUserTVEpisode)
                .filter(
                    DbUserTVEpisode.user_id == user.pk,
                    DbUserTVEpisode.episode_id.in_(episode_ids),
                )
                .delete(synchronize_session=False)
            )

    counts = {
        'movies': session.query(DbUserMovie)
        .filter(DbUserMovie.user_id == user.pk, DbUserMovie.is_seed_data.is_(True))
        .delete(synchronize_session=False),
        'tv': seeded_shows.delete(synchronize_session=False),
        'games': session.query(DbUserVideoGame)
        .filter(
            DbUserVideoGame.user_id == user.pk, DbUserVideoGame.is_seed_data.is_(True)
        )
        .delete(synchronize_session=False),
        'books': session.query(DbUserBook)
        .filter(DbUserBook.user_id == user.pk, DbUserBook.is_seed_data.is_(True))
        .delete(synchronize_session=False),
        'tv_episode_marks': episodes_deleted,
    }
    session.flush()
    return counts


def _get_or_create_movie(session: Session, data: dict) -> DbMovie:
    movie = None
    if data.get('tmdb'):
        movie = (
            session.query(DbMovie).filter(DbMovie.tmdb == data['tmdb']).one_or_none()
        )
    if movie is None and data.get('imdb'):
        movie = (
            session.query(DbMovie).filter(DbMovie.imdb == data['imdb']).one_or_none()
        )
    if movie is not None:
        return movie
    movie = DbMovie()
    # apply_detail_to_movie truncates to each column's length limit -- the
    # fixture stores get_movie_detail's raw output, which can exceed one
    # (e.g. a multi-language ``language`` string past 40 chars).
    apply_detail_to_movie(
        movie, {**data, 'release_date': _parse_dt(data.get('release_date'))}
    )
    session.add(movie)
    session.flush()
    return movie


def _get_or_create_show(session: Session, data: dict) -> DbTVShow:
    show = None
    if data.get('tvmaze'):
        show = (
            session.query(DbTVShow)
            .filter(DbTVShow.tvmaze == data['tvmaze'])
            .one_or_none()
        )
    if show is None and data.get('imdb'):
        show = (
            session.query(DbTVShow).filter(DbTVShow.imdb == data['imdb']).one_or_none()
        )
    if show is not None:
        return show
    show = DbTVShow()
    # 'episodes' isn't a DbTVShow column (it's the relationship to
    # DbTVEpisode) -- apply_detail_to_show would setattr it to a list of raw
    # dicts and corrupt the ORM state, so it's excluded and handled below.
    clean = {k: v for k, v in data.items() if k != 'episodes'}
    clean['premiered'] = _parse_dt(data.get('premiered'))
    apply_detail_to_show(show, clean)
    session.add(show)
    session.flush()
    for ep in data.get('episodes') or []:
        session.add(
            DbTVEpisode(
                title=ep.get('title') or 'Untitled',
                tvmaze=ep.get('tvmaze'),
                tv_show_id=show.pk,
                airdate=_parse_dt(ep.get('airdate')),
                season=ep.get('season'),
                season_number=ep.get('season_number'),
            )
        )
    session.flush()
    return show


def _get_or_create_book(session: Session, data: dict) -> DbBook:
    book = None
    if data.get('isbn'):
        book = session.query(DbBook).filter(DbBook.isbn == data['isbn']).one_or_none()
    if book is not None:
        return book
    book = DbBook()
    apply_detail_to_book(book, data)
    session.add(book)
    session.flush()
    return book


def _get_or_create_game(session: Session, data: dict) -> DbVideoGame:
    game = None
    if data.get('igdb'):
        game = (
            session.query(DbVideoGame)
            .filter(DbVideoGame.igdb == data['igdb'])
            .one_or_none()
        )
    if game is not None:
        return game
    game = DbVideoGame()
    apply_detail_to_game(
        game,
        {
            **data,
            'release_date': _parse_dt(data.get('release_date')),
            'igdb_last_update': _parse_dt(data.get('igdb_last_update')),
        },
    )
    session.add(game)
    session.flush()
    return game


def _seed_movies(session: Session, user: DbUser, rows: list) -> None:
    ranked, watchlist = [], []
    for data in rows:
        movie = _get_or_create_movie(session, data)
        if _already_tracked(
            session, DbUserMovie, user.pk, DbUserMovie.movie_id, movie.pk
        ):
            continue
        (ranked if random.random() < 0.7 else watchlist).append(movie)

    rank_start = _next_rank(session, DbUserMovie, user)
    for rank, movie in enumerate(ranked, start=rank_start):
        session.add(
            DbUserMovie(
                movie_id=movie.pk,
                user_id=user.pk,
                on_rankings=True,
                rank=rank,
                ranked_at=_random_past_datetime(),
                completed=1,
                completed_at=_random_past_date(),
                is_seed_data=True,
            )
        )
    for movie in watchlist:
        session.add(
            DbUserMovie(
                movie_id=movie.pk, user_id=user.pk, on_watchlist=True, is_seed_data=True
            )
        )


def _seed_tv(session: Session, user: DbUser, rows: list) -> None:
    ranked_trackers = []
    for data in rows:
        show = _get_or_create_show(session, data)
        if _already_tracked(
            session, DbUserTVShow, user.pk, DbUserTVShow.tv_show_id, show.pk
        ):
            continue

        is_ranked = random.random() < 0.7
        tracker = DbUserTVShow(
            tv_show_id=show.pk,
            user_id=user.pk,
            on_rankings=is_ranked,
            on_watchlist=not is_ranked,
            ranked_at=_random_past_datetime() if is_ranked else None,
            completed_at=_random_past_date() if is_ranked else None,
            is_seed_data=True,
        )
        session.add(tracker)
        if not is_ranked:
            continue
        ranked_trackers.append(tracker)

        episodes = (
            session.query(DbTVEpisode)
            .filter(DbTVEpisode.tv_show_id == show.pk)
            .order_by(DbTVEpisode.season_number)
            .all()
        )
        watched_through = random.randint(0, len(episodes))
        for episode in episodes[:watched_through]:
            session.add(
                DbUserTVEpisode(
                    episode_id=episode.pk,
                    user_id=user.pk,
                    watched=1,
                    watched_at=_random_past_datetime(),
                )
            )

    rank_start = _next_rank(session, DbUserTVShow, user)
    for rank, tracker in enumerate(ranked_trackers, start=rank_start):
        tracker.rank = rank


def _seed_games(session: Session, user: DbUser, rows: list) -> None:
    ranked, watchlist = [], []
    for data in rows:
        game = _get_or_create_game(session, data)
        if _already_tracked(
            session, DbUserVideoGame, user.pk, DbUserVideoGame.game_id, game.pk
        ):
            continue
        (ranked if random.random() < 0.6 else watchlist).append(game)

    rank_start = _next_rank(session, DbUserVideoGame, user)
    for rank, game in enumerate(ranked, start=rank_start):
        session.add(
            DbUserVideoGame(
                game_id=game.pk,
                user_id=user.pk,
                on_rankings=True,
                rank=rank,
                ranked_at=_random_past_datetime(),
                completed=1,
                completed_at=_random_past_date(),
                is_100_percent=random.random() < 0.2,
                is_seed_data=True,
            )
        )
    for game in watchlist:
        session.add(
            DbUserVideoGame(
                game_id=game.pk, user_id=user.pk, on_watchlist=True, is_seed_data=True
            )
        )


def _seed_books(session: Session, user: DbUser, rows: list) -> None:
    ranked, watchlist = [], []
    for data in rows:
        book = _get_or_create_book(session, data)
        if _already_tracked(session, DbUserBook, user.pk, DbUserBook.book_id, book.pk):
            continue
        (ranked if random.random() < 0.65 else watchlist).append(book)

    rank_start = _next_rank(session, DbUserBook, user)
    for rank, book in enumerate(ranked, start=rank_start):
        session.add(
            DbUserBook(
                book_id=book.pk,
                user_id=user.pk,
                on_rankings=True,
                rank=rank,
                ranked_at=_random_past_datetime(),
                completed=1,
                completed_at=_random_past_date(),
                is_seed_data=True,
            )
        )
    for book in watchlist:
        session.add(
            DbUserBook(
                book_id=book.pk, user_id=user.pk, on_watchlist=True, is_seed_data=True
            )
        )


def _claim_target_user(user: DbUser) -> None:
    """
    Make the seed target the anchor of the cast: public everywhere with a
    handle, so a follower can exist and others can compare against them.

    An existing handle is never clobbered -- only a ``None`` handle is
    claimed. The tiers are re-applied every run: the cast positions are this
    script's job to hold.
    """
    if user.handle is None:
        user.handle = _TARGET_HANDLE
    for field in _TIER_FIELDS:
        setattr(user, field, 'public')


def _get_or_create_cast_user(session: Session, spec: dict) -> DbUser:
    """
    The cast account for ``spec``, created on first sight and reused after.

    Idempotent by email. The fixed handle is claimed only when the account
    has none, and the nine tiers, the time zone, and (when ``spec['admin']``
    is set) the admin group are re-applied every run.
    """
    user = session.query(DbUser).filter_by(email=spec['email']).one_or_none()
    if user is None:
        user = DbUser(
            display_name=spec['display_name'],
            email=spec['email'],
            password=Hash.hash_password(_CAST_PASSWORD),
            handle=spec['handle'],
        )
        session.add(user)
        session.flush()
    elif user.handle is None:
        user.handle = spec['handle']
    for field, tier in spec['tiers'].items():
        setattr(user, field, tier)
    user.time_zone = spec['time_zone']
    if spec.get('admin'):
        user.user_group = 'admin'
    return user


def _cast_canon_movies() -> list:
    """
    The overlap canon as real fixture rows, selected by TMDB id.

    A canon movie missing from a regenerated fixture is skipped with a warning
    rather than fabricated -- the cast never invents catalog data.
    """
    fixture = {row['tmdb']: row for row in _load_fixture('seed_movies.json')}
    canon = []
    for tmdb in _CAST_CANON_TMDB:
        row = fixture.get(tmdb)
        if row is None:
            logger.warning(
                'seed_dev cast: canon movie tmdb=%d missing from fixture', tmdb
            )
            continue
        canon.append(row)
    return canon


def _extra_cast_movies(canon: list, count: int) -> list:
    """
    ``count`` non-canon fixture movies, picked deterministically.

    Gives a cast member a ranked shelf beyond the canon. "Non-canon" means
    outside the eight titles that pin the comparison canon -- it does *not*
    mean the title goes unshared: the target user's default ``--count`` is
    the whole movie fixture, so every extra is a shared title too. Keep
    ``canon_movies + extra_movies`` under five on any seat whose demo depends
    on ``not_enough_overlap``.
    """
    canon_tmdb = {row['tmdb'] for row in canon}
    return [
        row
        for row in _load_fixture('seed_movies.json')
        if row['tmdb'] not in canon_tmdb
    ][:count]


def _rank_movies(session: Session, user: DbUser, rows: list) -> int:
    """
    Rank each of ``rows`` for ``user`` from the first free rank onward.

    Skips anything the user already tracks so seeding never overwrites an
    existing rank. Returns how many new tracker rows were created.
    """
    created = 0
    rank = _next_rank(session, DbUserMovie, user)
    for data in rows:
        movie = _get_or_create_movie(session, data)
        if _already_tracked(
            session, DbUserMovie, user.pk, DbUserMovie.movie_id, movie.pk
        ):
            continue
        session.add(
            DbUserMovie(
                movie_id=movie.pk,
                user_id=user.pk,
                on_rankings=True,
                rank=rank,
                ranked_at=_random_past_datetime(),
                completed=1,
                completed_at=_random_past_date(),
                is_seed_data=True,
            )
        )
        created += 1
        rank += 1
    return created


def _ensure_friendship(session: Session, requester: DbUser, other: DbUser) -> None:
    """
    The accepted friendship the cast matrix calls for, created once.

    ``requester`` sent the request. Idempotent: an existing row -- pending or
    accepted -- is left exactly where it is, since a row that already exists
    was not written by this seeder run.
    """
    if db_friendship.friendship_between(session, requester.pk, other.pk) is not None:
        return
    low, high = canonical_pair(requester.pk, other.pk)
    now = datetime.now(timezone.utc)
    session.add(
        DbFriendship(
            user_low_id=low,
            user_high_id=high,
            requested_by_id=requester.pk,
            status=FriendshipStatus.ACCEPTED,
            requested_at=now,
            responded_at=now,
        )
    )


def _ensure_follow(session: Session, follower: DbUser, followee: DbUser) -> None:
    """One asymmetric follow, created once; never duplicated."""
    if db_follow.find(session, follower.pk, followee.pk) is None:
        session.add(DbFollow(follower_id=follower.pk, followee_id=followee.pk))


def _seed_cast(session: Session, target: DbUser) -> dict:
    """
    Create the fixed dev cast and wire their relationships (#313).

    Anchored to ``target`` -- the seed's ``--email`` user, default the seed
    admin -- who is made public with the handle ``you``, becomes the friend of
    ``friend``, the followee of ``follower``, and follows ``followee``. The
    eight-movie canon is ranked for the target and a fixed prefix of it for
    each cast member, pinning the compare states; see the module docstring for
    the full matrix.
    """
    _claim_target_user(target)
    users = {
        spec['email']: _get_or_create_cast_user(session, spec) for spec in _CAST_USERS
    }

    _ensure_friendship(session, target, users['friend@example.com'])
    _ensure_follow(session, users['follower@example.com'], target)
    _ensure_follow(session, target, users['followee@example.com'])

    canon = _cast_canon_movies()
    ranked = {target.pk: _rank_movies(session, target, canon)}
    for spec in _CAST_USERS:
        user = users[spec['email']]
        count = _rank_movies(session, user, canon[: spec.get('canon_movies', 0)])
        count += _rank_movies(
            session, user, _extra_cast_movies(canon, spec.get('extra_movies', 0))
        )
        ranked[user.pk] = count
    return {'cast_users': len(users), 'ranked_rows': sum(ranked.values())}


def _existing_cast_users(session: Session) -> list:
    """
    The cast accounts that already exist, by their fixed emails.

    ``--wipe`` wipes what is there without creating anything, so wipe looks up
    rather than upserts.
    """
    emails = [spec['email'] for spec in _CAST_USERS]
    return session.query(DbUser).filter(DbUser.email.in_(emails)).all()


def run_seed(count: int, wipe_only: bool, email: str = None) -> dict:
    """
    Wipe this script's previously-seeded tracker rows, then optionally reseed.

    Wipe covers the target user and the fixed dev cast alike; the cast users
    themselves, their relationships, and catalog rows are all left in place.
    """
    session = SessionLocal()
    try:
        purged = _purge_legacy_fake_rows(session)
        session.commit()
        if any(purged.values()):
            logger.info('seed_dev: purged legacy Faker-seeded rows: %s', purged)

        user = _target_user(session, email)
        wiped = _wipe(session, user)
        for cast_user in _existing_cast_users(session):
            for key, value in _wipe(session, cast_user).items():
                wiped[key] = wiped.get(key, 0) + value
        session.commit()
        logger.info('seed_dev: wiped previously-seeded tracker rows: %s', wiped)
        if wipe_only:
            return wiped

        _seed_movies(
            session, user, _sample(_load_fixture('seed_movies.json'), count, 'movies')
        )
        _seed_tv(
            session,
            user,
            _sample(
                _load_fixture('seed_tv_shows.json'),
                max(10, count // _TV_RATIO),
                'tv shows',
            ),
        )
        _seed_games(
            session,
            user,
            _sample(
                _load_fixture('seed_games.json'), max(10, count // _GAME_RATIO), 'games'
            ),
        )
        _seed_books(
            session,
            user,
            _sample(
                _load_fixture('seed_books.json'), max(10, count // _BOOK_RATIO), 'books'
            ),
        )
        cast = _seed_cast(session, user)

        session.commit()
        logger.info(
            'seed_dev: populated from real-catalog fixtures (target count=%d) '
            'and cast (%d users, %d ranked rows)',
            count,
            cast['cast_users'],
            cast['ranked_rows'],
        )
        return wiped
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--count',
        type=int,
        default=270,
        help='Number of real movies to sample from the fixture (other domains scale off this).',
    )
    parser.add_argument(
        '--wipe',
        action='store_true',
        help='Only delete previously-seeded tracker rows; do not reseed.',
    )
    parser.add_argument(
        '--email',
        default=None,
        help=(
            'Local user to attach seeded tracker rows to (default: ADMIN_EMAIL). '
            'Use this to target the account Google Sign-In actually creates '
            'when you sign in locally, which is a different user than the '
            'seed admin.'
        ),
    )
    args = parser.parse_args()
    _assert_local_dev()
    run_seed(count=args.count, wipe_only=args.wipe, email=args.email)


if __name__ == '__main__':
    main()
