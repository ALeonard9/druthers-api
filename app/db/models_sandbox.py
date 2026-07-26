"""
This module defines the database models for the Sandbox entities.
"""

# pylint: disable=missing-class-docstring

from sqlalchemy import (
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


class DbCountry(DBBaseModel):
    __tablename__ = 'countries'

    title = Column(String(255))
    country_code = Column(String(4), unique=True)
    # Rich detail (populated from REST Countries) for the detail view.
    region = Column(String(100), nullable=True)
    subregion = Column(String(100), nullable=True)
    capital = Column(String(255), nullable=True)
    population = Column(Integer, nullable=True)
    flag_emoji = Column(String(8), nullable=True)
    flag_url = Column(String(500), nullable=True)

    user_countries = relationship('DbUserCountry', back_populates='country')


class DbUserCountry(DBBaseModel):
    __tablename__ = 'user_countries'

    country_id = Column(Integer, ForeignKey('countries.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    # Two independent lists, mirroring the Movies tracker: on_watchlist is the
    # travel bucket list, on_rankings is the visited-and-ranked list.
    # `completed` is retained from the legacy import but no longer drives the UI.
    on_watchlist = Column(Boolean, nullable=False, default=False)
    on_rankings = Column(Boolean, nullable=False, default=False)
    rank = Column(Integer, nullable=True)
    # When the current rank was assigned — drives Activity, so notes edits
    # and other tracker updates never re-date a ranking (#141).
    ranked_at = Column(DateTime, nullable=True)
    completed = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    first_visited = Column(DateTime, nullable=True)

    country = relationship('DbCountry', back_populates='user_countries')
    user = relationship('DbUser', backref='user_countries')


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

    user_movies = relationship('DbUserMovie', back_populates='movie')


class DbUserMovie(DBBaseModel):
    __tablename__ = 'user_movies'

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

    user_tv_shows = relationship('DbUserTVShow', back_populates='tv_show')
    episodes = relationship('DbTVEpisode', back_populates='tv_show')


class DbUserTVShow(DBBaseModel):
    __tablename__ = 'user_tv_shows'

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
    user_episodes = relationship('DbUserTVEpisode', back_populates='episode')


class DbUserTVEpisode(DBBaseModel):
    __tablename__ = 'user_tv_episodes'

    episode_id = Column(Integer, ForeignKey('tv_episodes.pk'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)

    watched = Column(Integer, default=0)
    # When the episode was actually watched (#160): stamped on mark, restored
    # from orion's g_first for pre-cutover history. Activity orders by this.
    watched_at = Column(DateTime, nullable=True)

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

    user_games = relationship('DbUserVideoGame', back_populates='game')


class DbUserVideoGame(DBBaseModel):
    __tablename__ = 'user_video_games'

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

    user_books = relationship('DbUserBook', back_populates='book')


class DbUserBook(DBBaseModel):
    __tablename__ = 'user_books'

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
