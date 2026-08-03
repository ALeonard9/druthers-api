"""
This module defines the database models for the Sandbox entities.
"""

# pylint: disable=missing-class-docstring

from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    ForeignKey,
    Text,
    Boolean,
)
from sqlalchemy.orm import relationship

from app.db.models import DBBaseModel


def rank_is_1_based(table_name: str) -> tuple:
    """
    ``__table_args__`` asserting this tracker's rank is a 1-based position.

    A rank is either NULL (not placed) or >= 1. Rank 0 kept coming back to
    prod — the legacy site stored ranks 0-based, and every fix was a *repair*
    script bolted onto the deploy pipeline, so any release where it didn't run
    left "0" printed at the top of the Top 5. A CHECK makes the bad state
    unrepresentable instead of periodically swept up: whatever writes a 0 now
    fails loudly at the write, in tests (SQLite enforces CHECK too) rather
    than on the home page weeks later. See migration d3b81f4a9c67.
    """
    return (
        CheckConstraint(
            'rank IS NULL OR rank >= 1', name=f'ck_{table_name}_rank_1_based'
        ),
    )


class DbNotification(DBBaseModel):
    __tablename__ = 'notifications'

    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    # Machine-readable kind (e.g. 'movie_release') so clients — web today,
    # mobile later — can pick icons/routes without parsing the title.
    type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=True)
    # Link target: which tracker domain and which catalog entity to open.
    category = Column(String(20), nullable=True)
    entity_id = Column(String(40), nullable=True)
    # One notification per (user, event): generators upsert on this key, so
    # re-running a sweep never duplicates. E.g. 'movie_release:tt1375666'.
    dedupe_key = Column(String(120), nullable=False)
    read = Column(Boolean, nullable=False, default=False)

    user = relationship('DbUser', backref='notifications')


class DbMovie(DBBaseModel):
    __tablename__ = 'movies'

    title = Column(String(255))
    # TMDB id is the catalog's join key (#163): TMDB's search endpoint returns
    # no IMDb id, so search results can only be matched to tracked rows on this.
    tmdb = Column(Integer, unique=True, nullable=True)
    imdb = Column(String(40), unique=True)
    release_date = Column(DateTime, nullable=True)
    # Legacy: real IMDb ratings from the OMDb era. Frozen — no longer written
    # or displayed, since TMDB has no IMDb rating to keep it current (#163).
    rating_imdb = Column(Float, nullable=True)
    # TMDB's own vote_average, which replaced it.
    rating_tmdb = Column(Float, nullable=True)
    runtime = Column(Integer, nullable=True)
    language = Column(String(40), nullable=True)
    rated = Column(String(11), nullable=True)
    poster_url = Column(String(500), nullable=True)
    # Rich detail (populated from TMDB) for the detail view + filtering.
    year = Column(Integer, nullable=True)
    genre = Column(String(255), nullable=True)
    director = Column(String(512), nullable=True)
    actors = Column(Text, nullable=True)
    plot = Column(Text, nullable=True)

    # See DbTVShow.user_tv_shows for why this cascades (#227).
    user_movies = relationship(
        'DbUserMovie', back_populates='movie', cascade='all, delete-orphan'
    )


class DbUserMovie(DBBaseModel):
    __tablename__ = 'user_movies'
    __table_args__ = rank_is_1_based(__tablename__)

    movie_id = Column(Integer, ForeignKey('movies.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    # Two independent lists: a movie may be on the watchlist, in the ranked
    # list (with a rank position), or both. `completed` is retained from the
    # legacy import but no longer drives the UI.
    on_watchlist = Column(Boolean, nullable=False, default=False)
    on_rankings = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=True)
    # When the current rank was assigned — drives Activity, so notes edits
    # and other tracker updates never re-date a ranking (#141).
    ranked_at = Column(DateTime, nullable=True)
    completed = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    # When the user finished it (#159): defaults to the day it entered
    # Rankings, editable on the detail page.
    completed_at = Column(Date, nullable=True)
    # Set by app.migration.seed_dev -- marks a tracker row (not the catalog
    # row it points at, which may well be real) as seeded rather than
    # user-created, so a targeted wipe can find it again.
    is_seed_data = Column(Boolean, nullable=False, default=False)

    movie = relationship('DbMovie', back_populates='user_movies')
    user = relationship('DbUser', backref='user_movies')


class DbTVShow(DBBaseModel):
    __tablename__ = 'tv_shows'

    title = Column(String(254), nullable=False)
    imdb = Column(String(254), unique=True, nullable=True)
    tvmaze = Column(Integer, nullable=True)
    status = Column(String(254), nullable=True)
    poster_url = Column(String(254), nullable=True)
    # Rich detail (populated from TVMaze) for the detail view + filtering.
    premiered = Column(DateTime, nullable=True)
    year = Column(Integer, nullable=True)
    genre = Column(String(255), nullable=True)
    network = Column(String(255), nullable=True)
    runtime = Column(Integer, nullable=True)
    language = Column(String(40), nullable=True)
    rating = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)

    # Cascade, not the SQLAlchemy default: every child FK below is
    # nullable=False, so the default "disassociate" behaviour issues
    # UPDATE ... SET tv_show_id = NULL and the delete fails (#227).
    user_tv_shows = relationship(
        'DbUserTVShow', back_populates='tv_show', cascade='all, delete-orphan'
    )
    episodes = relationship(
        'DbTVEpisode', back_populates='tv_show', cascade='all, delete-orphan'
    )


class DbUserTVShow(DBBaseModel):
    __tablename__ = 'user_tv_shows'
    __table_args__ = rank_is_1_based(__tablename__)

    tv_show_id = Column(Integer, ForeignKey('tv_shows.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    # Two independent lists, mirroring the Movies tracker. `status` and
    # `freeze` are retained from the legacy import but no longer drive the UI.
    on_watchlist = Column(Boolean, nullable=False, default=False)
    on_rankings = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=True)
    # When the current rank was assigned — drives Activity, so notes edits
    # and other tracker updates never re-date a ranking (#141).
    ranked_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    # When the user finished it (#159): defaults to the day it entered
    # Rankings, editable on the detail page.
    completed_at = Column(Date, nullable=True)
    status = Column(String(254), nullable=True)
    freeze = Column(Integer, default=0)
    # See DbUserMovie.is_seed_data.
    is_seed_data = Column(Boolean, nullable=False, default=False)

    tv_show = relationship('DbTVShow', back_populates='user_tv_shows')
    user = relationship('DbUser', backref='user_tv_shows')


class DbTVEpisode(DBBaseModel):
    __tablename__ = 'tv_episodes'

    title = Column(String(254), nullable=False)
    tvmaze = Column(Integer, unique=True, nullable=True)
    tv_show_id = Column(Integer, ForeignKey('tv_shows.pk'), nullable=False)

    airdate = Column(DateTime, nullable=True)
    season = Column(Integer, nullable=True)
    season_number = Column(Integer, nullable=True)

    tv_show = relationship('DbTVShow', back_populates='episodes')
    # Second level: deleting a show cascades to its episodes, and each episode
    # must in turn take its per-user watch marks with it (#227).
    user_episodes = relationship(
        'DbUserTVEpisode', back_populates='episode', cascade='all, delete-orphan'
    )


class DbUserTVEpisode(DBBaseModel):
    __tablename__ = 'user_tv_episodes'

    episode_id = Column(Integer, ForeignKey('tv_episodes.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    watched = Column(Integer, default=0)
    # When the episode was actually watched (#160): stamped on mark, restored
    # from orion's g_first for pre-cutover history. Activity orders by this.
    watched_at = Column(DateTime, nullable=True)
    # Independent of watched (#262): a standout episode, not a watch mark.
    favorited = Column(Boolean, nullable=False, default=False)
    favorited_at = Column(DateTime, nullable=True)

    episode = relationship('DbTVEpisode', back_populates='user_episodes')
    user = relationship('DbUser', backref='user_tv_episodes')


class DbVideoGame(DBBaseModel):
    __tablename__ = 'video_games'

    title = Column(String(255))
    igdb = Column(Integer, unique=True, nullable=True)
    poster_url = Column(String(254), nullable=True)
    release_date = Column(DateTime, nullable=True)
    rating = Column(Float, nullable=True)
    time_to_beat = Column(Integer, nullable=True)
    igdb_last_update = Column(DateTime, nullable=True)
    slug = Column(String(255), nullable=True)
    # Rich detail (populated from IGDB) for the detail view + filtering.
    year = Column(Integer, nullable=True)
    genre = Column(String(255), nullable=True)
    platforms = Column(String(254), nullable=True)
    summary = Column(Text, nullable=True)

    # See DbTVShow.user_tv_shows for why this cascades (#227).
    user_games = relationship(
        'DbUserVideoGame', back_populates='game', cascade='all, delete-orphan'
    )


class DbUserVideoGame(DBBaseModel):
    __tablename__ = 'user_video_games'
    __table_args__ = rank_is_1_based(__tablename__)

    game_id = Column(Integer, ForeignKey('video_games.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    # Two independent lists, mirroring the Movies tracker: on_watchlist is the
    # backlog, on_rankings the played-and-ranked list. `completed` is retained
    # from the legacy import but no longer drives the UI.
    on_watchlist = Column(Boolean, nullable=False, default=False)
    on_rankings = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=True)
    # When the current rank was assigned — drives Activity, so notes edits
    # and other tracker updates never re-date a ranking (#141).
    ranked_at = Column(DateTime, nullable=True)
    completed = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    # When the user finished it (#159): defaults to the day it entered
    # Rankings, editable on the detail page.
    completed_at = Column(Date, nullable=True)
    is_100_percent = Column(Boolean, default=False)
    # See DbUserMovie.is_seed_data.
    is_seed_data = Column(Boolean, nullable=False, default=False)

    game = relationship('DbVideoGame', back_populates='user_games')
    user = relationship('DbUser', backref='user_video_games')


class DbBook(DBBaseModel):
    __tablename__ = 'books'

    title = Column(String(254), nullable=False)
    isbn = Column(String(20), nullable=True)
    googleid = Column(String(254), nullable=True)
    poster_url = Column(String(254), nullable=True)
    # Rich detail (populated from Google Books) for the detail view + filtering.
    authors = Column(String(512), nullable=True)
    year = Column(Integer, nullable=True)
    genre = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    page_count = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    language = Column(String(40), nullable=True)
    # When enrich_books last resolved this row, hit or miss (#258). A missing
    # field (e.g. no upstream publishedDate) is a real, permanent answer, not
    # "never enriched" -- pending_books uses this to retry on an interval
    # instead of re-fetching the same unresolvable field every run forever.
    enrichment_attempted_at = Column(DateTime, nullable=True)

    # See DbTVShow.user_tv_shows for why this cascades (#227).
    user_books = relationship(
        'DbUserBook', back_populates='book', cascade='all, delete-orphan'
    )


class DbUserBook(DBBaseModel):
    __tablename__ = 'user_books'
    __table_args__ = rank_is_1_based(__tablename__)

    book_id = Column(Integer, ForeignKey('books.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    # Two independent lists, mirroring the Movies tracker: on_watchlist is the
    # to-read list, on_rankings the read-and-ranked list. `completed` is
    # retained from the legacy import but no longer drives the UI.
    on_watchlist = Column(Boolean, nullable=False, default=False)
    on_rankings = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=True)
    # When the current rank was assigned — drives Activity, so notes edits
    # and other tracker updates never re-date a ranking (#141).
    ranked_at = Column(DateTime, nullable=True)
    completed = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    # When the user finished it (#159): defaults to the day it entered
    # Rankings, editable on the detail page.
    completed_at = Column(Date, nullable=True)
    # See DbUserMovie.is_seed_data.
    is_seed_data = Column(Boolean, nullable=False, default=False)

    book = relationship('DbBook', back_populates='user_books')
    user = relationship('DbUser', backref='user_books')
