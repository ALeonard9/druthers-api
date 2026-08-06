"""
This module contains Pydantic schemas for Sandbox entities.
"""

# pylint: disable=missing-class-docstring

from datetime import date, datetime
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# A 1-based position in a ranked list (None when the item is tracked but not
# placed). The bound is the API half of the ck_<table>_rank_1_based CHECK: a
# client PUTting rank 0 gets a 422 naming the field, rather than a row the
# database will reject — or, before the CHECK existed, a silently stored 0
# that showed up as "0" at the top of the Top 5 board.
RankPosition = Annotated[int, Field(ge=1)]


# --- Watch providers (shared by Movies and TV) ---
class WatchProvider(BaseModel):
    """One streaming service a title is available on."""

    provider_id: Optional[int] = None
    name: str
    logo_url: Optional[str] = None


class WatchProviders(BaseModel):
    """
    Where a title can be watched in one region (web#26).

    Never 404s and never errors: a title with no availability — or one TMDB
    can't resolve — comes back with empty buckets, so the detail page renders
    the same either way. ``attribution`` carries the credit TMDB requires
    wherever this data is displayed.
    """

    region: str
    # TMDB's own "watch" page for the title, JustWatch-powered; None when the
    # title has no availability in this region.
    link: Optional[str] = None
    attribution: str
    # Subscription (Netflix, Max), free/ad-supported (Tubi), and transactional.
    stream: List[WatchProvider] = []
    free: List[WatchProvider] = []
    rent: List[WatchProvider] = []
    buy: List[WatchProvider] = []


# --- Movies ---
class MovieBase(BaseModel):
    title: str
    # TMDB id is the catalog key; imdb is still stored but no longer required,
    # since TMDB search results don't carry one until detail is fetched (#163).
    tmdb: Optional[int] = None
    imdb: Optional[str] = None
    release_date: Optional[datetime] = None
    # Legacy OMDb-era values, frozen and not displayed; rating_tmdb replaces it.
    rating_imdb: Optional[float] = None
    rating_tmdb: Optional[float] = None
    runtime: Optional[int] = None
    language: Optional[str] = None
    rated: Optional[str] = None
    poster_url: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    director: Optional[str] = None
    actors: Optional[str] = None
    plot: Optional[str] = None


class MovieCreate(MovieBase):
    pass


class MovieUpdate(BaseModel):
    title: Optional[str] = None
    tmdb: Optional[int] = None
    imdb: Optional[str] = None
    release_date: Optional[datetime] = None
    rating_imdb: Optional[float] = None
    rating_tmdb: Optional[float] = None
    runtime: Optional[int] = None
    language: Optional[str] = None
    rated: Optional[str] = None
    poster_url: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    director: Optional[str] = None
    actors: Optional[str] = None
    plot: Optional[str] = None


class MovieResponse(MovieBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MovieSummary(BaseModel):
    """Lightweight movie for list responses — omits the large ``plot`` field."""

    id: str
    title: str
    tmdb: Optional[int] = None
    imdb: Optional[str] = None
    release_date: Optional[datetime] = None
    rating_tmdb: Optional[float] = None
    runtime: Optional[int] = None
    rated: Optional[str] = None
    poster_url: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    director: Optional[str] = None
    actors: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class MovieSearchResult(BaseModel):
    # tmdb is what the add flow posts back; imdb only arrives on an id-shaped
    # query, since TMDB's title search omits it (#163).
    tmdb: int
    imdb: Optional[str] = None
    title: str
    year: Optional[str] = None
    release_date: Optional[str] = None
    poster_url: Optional[str] = None
    type: Optional[str] = None
    popularity: Optional[float] = None
    on_watchlist: bool = False
    on_rankings: bool = False
    rank: Optional[RankPosition] = None


class UserMovieBase(BaseModel):
    on_watchlist: Optional[bool] = None
    on_rankings: Optional[bool] = None
    rank: Optional[RankPosition] = None
    completed: Optional[int] = None
    notes: Optional[str] = None
    completed_at: Optional[date] = None


class UserMovieCreate(UserMovieBase):
    pass


class UserMovieUpdate(UserMovieBase):
    pass


class UserMovieResponse(UserMovieBase):
    id: str
    movie: MovieSummary
    created_at: datetime
    updated_at: datetime
    source_handle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RankingReorder(BaseModel):
    """Ordered list of movie (catalog) ids defining the new ranking order."""

    movie_ids: List[str]


class RankPlacement(BaseModel):
    """Target 1-based position at which to place a movie in the ranked list."""

    position: RankPosition


# --- TV Shows ---
class TVShowBase(BaseModel):
    title: str
    imdb: Optional[str] = None
    tvmaze: Optional[int] = None
    status: Optional[str] = None
    poster_url: Optional[str] = None
    premiered: Optional[datetime] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    network: Optional[str] = None
    runtime: Optional[int] = None
    language: Optional[str] = None
    rating: Optional[float] = None
    summary: Optional[str] = None


class TVShowCreate(TVShowBase):
    pass


class TVShowUpdate(BaseModel):
    title: Optional[str] = None
    imdb: Optional[str] = None
    tvmaze: Optional[int] = None
    status: Optional[str] = None
    poster_url: Optional[str] = None
    premiered: Optional[datetime] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    network: Optional[str] = None
    runtime: Optional[int] = None
    language: Optional[str] = None
    rating: Optional[float] = None
    summary: Optional[str] = None


class TVShowResponse(TVShowBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TVShowSummary(BaseModel):
    """Lightweight show for list responses — omits the large ``summary``."""

    id: str
    title: str
    imdb: Optional[str] = None
    tvmaze: Optional[int] = None
    status: Optional[str] = None
    poster_url: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    network: Optional[str] = None
    runtime: Optional[int] = None
    rating: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


class TVShowSearchResult(BaseModel):
    tvmaze: Optional[int] = None
    imdb: Optional[str] = None
    title: str
    year: Optional[str] = None
    status: Optional[str] = None
    network: Optional[str] = None
    poster_url: Optional[str] = None
    on_watchlist: bool = False
    on_rankings: bool = False
    rank: Optional[RankPosition] = None


class UserTVShowBase(BaseModel):
    on_watchlist: Optional[bool] = None
    on_rankings: Optional[bool] = None
    rank: Optional[RankPosition] = None
    notes: Optional[str] = None
    completed_at: Optional[date] = None
    status: Optional[str] = None
    freeze: Optional[int] = None


class UserTVShowCreate(UserTVShowBase):
    pass


class UserTVShowUpdate(UserTVShowBase):
    pass


class UserTVShowResponse(UserTVShowBase):
    id: str
    tv_show: TVShowSummary
    created_at: datetime
    updated_at: datetime
    source_handle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class UserTVShowWithStatus(UserTVShowResponse):
    """
    Tracker plus the per-user watch status the legacy site badged:
    not_started / behind / up_to_date / complete (all aired watched and the
    show has ended). Counts cover episodes that have already aired.
    """

    watch_status: str = 'not_started'
    aired_count: int = 0
    watched_count: int = 0


class TVRankingReorder(BaseModel):
    """Ordered list of show (catalog) ids defining the new ranking order."""

    show_ids: List[str]


# --- TV Episodes ---
class TVEpisodeBase(BaseModel):
    title: str
    tvmaze: Optional[int] = None
    airdate: Optional[datetime] = None
    season: Optional[int] = None
    season_number: Optional[int] = None


class TVEpisodeCreate(TVEpisodeBase):
    pass


class TVEpisodeUpdate(BaseModel):
    title: Optional[str] = None
    tvmaze: Optional[int] = None
    airdate: Optional[datetime] = None
    season: Optional[int] = None
    season_number: Optional[int] = None


class TVEpisodeResponse(TVEpisodeBase):
    id: str
    tv_show_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UserTVEpisodeBase(BaseModel):
    watched: Optional[int] = 0
    watched_at: Optional[datetime] = None
    favorited: Optional[bool] = False
    favorited_at: Optional[datetime] = None


class UserTVEpisodeResponse(UserTVEpisodeBase):
    id: str
    episode: TVEpisodeResponse
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ScheduleEpisodeItem(BaseModel):
    """One unwatched episode of a tracked (non-frozen) show, for the Schedule view."""

    show_id: str
    show_title: str
    episode_id: str
    episode_title: str
    season: Optional[int] = None
    season_number: Optional[int] = None
    airdate: Optional[datetime] = None


class ScheduleFrozenShow(BaseModel):
    show_id: str
    show_title: str


class ScheduleResponse(BaseModel):
    """
    Mirrors the legacy schedule page: what's airing in the +/- window around
    today (``upcoming``), everything overdue and unwatched (``catch_up``),
    and shows the user has paused tracking on (``frozen_shows``).
    """

    upcoming: List[ScheduleEpisodeItem]
    catch_up: List[ScheduleEpisodeItem]
    frozen_shows: List[ScheduleFrozenShow]


# --- Video Games ---
class VideoGameBase(BaseModel):
    title: str
    igdb: Optional[int] = None
    poster_url: Optional[str] = None
    release_date: Optional[datetime] = None
    rating: Optional[float] = None
    time_to_beat: Optional[int] = None
    igdb_last_update: Optional[datetime] = None
    slug: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    platforms: Optional[str] = None
    summary: Optional[str] = None


class VideoGameCreate(VideoGameBase):
    pass


class VideoGameUpdate(BaseModel):
    title: Optional[str] = None
    igdb: Optional[int] = None
    poster_url: Optional[str] = None
    release_date: Optional[datetime] = None
    rating: Optional[float] = None
    time_to_beat: Optional[int] = None
    igdb_last_update: Optional[datetime] = None
    slug: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    platforms: Optional[str] = None
    summary: Optional[str] = None


class VideoGameResponse(VideoGameBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class VideoGameSummary(BaseModel):
    """Lightweight game for list responses — omits the large ``summary``."""

    id: str
    title: str
    igdb: Optional[int] = None
    poster_url: Optional[str] = None
    rating: Optional[float] = None
    time_to_beat: Optional[int] = None
    slug: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    platforms: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class GameSearchResult(BaseModel):
    igdb: Optional[int] = None
    title: str
    slug: Optional[str] = None
    year: Optional[str] = None
    platforms: Optional[str] = None
    poster_url: Optional[str] = None
    on_watchlist: bool = False
    on_rankings: bool = False
    rank: Optional[RankPosition] = None


class UserVideoGameBase(BaseModel):
    on_watchlist: Optional[bool] = None
    on_rankings: Optional[bool] = None
    rank: Optional[RankPosition] = None
    completed: Optional[int] = None
    notes: Optional[str] = None
    completed_at: Optional[date] = None
    is_100_percent: Optional[bool] = None


class UserVideoGameCreate(UserVideoGameBase):
    pass


class UserVideoGameUpdate(UserVideoGameBase):
    pass


class UserVideoGameResponse(UserVideoGameBase):
    id: str
    game: VideoGameSummary
    created_at: datetime
    updated_at: datetime
    source_handle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class GameRankingReorder(BaseModel):
    """Ordered list of game (catalog) ids defining the new ranking order."""

    game_ids: List[str]


# --- Books ---
class BookBase(BaseModel):
    title: str
    isbn: Optional[str] = None
    googleid: Optional[str] = None
    poster_url: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    description: Optional[str] = None
    page_count: Optional[int] = None
    rating: Optional[float] = None
    language: Optional[str] = None


class BookCreate(BookBase):
    pass


class BookUpdate(BaseModel):
    title: Optional[str] = None
    isbn: Optional[str] = None
    googleid: Optional[str] = None
    poster_url: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    description: Optional[str] = None
    page_count: Optional[int] = None
    rating: Optional[float] = None
    language: Optional[str] = None


class BookResponse(BookBase):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BookSummary(BaseModel):
    """Lightweight book for list responses — omits the large ``description``."""

    id: str
    title: str
    isbn: Optional[str] = None
    googleid: Optional[str] = None
    poster_url: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    page_count: Optional[int] = None
    rating: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


class BookSearchResult(BaseModel):
    isbn: Optional[str] = None
    title: str
    authors: Optional[str] = None
    year: Optional[str] = None
    poster_url: Optional[str] = None
    on_watchlist: bool = False
    on_rankings: bool = False
    rank: Optional[RankPosition] = None


class UserBookBase(BaseModel):
    on_watchlist: Optional[bool] = None
    on_rankings: Optional[bool] = None
    rank: Optional[RankPosition] = None
    completed: Optional[int] = None
    notes: Optional[str] = None
    completed_at: Optional[date] = None


class UserBookCreate(UserBookBase):
    pass


class UserBookUpdate(UserBookBase):
    pass


class UserBookResponse(UserBookBase):
    id: str
    book: BookSummary
    created_at: datetime
    updated_at: datetime
    source_handle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class BookRankingReorder(BaseModel):
    """Ordered list of book (catalog) ids defining the new ranking order."""

    book_ids: List[str]


# --- Global Search ---
class GlobalSearchResponse(BaseModel):
    """One query fanned out across every domain's search provider."""

    query: str
    # Set when the results came from a spell-corrected retry of the query.
    corrected: Optional[str] = None
    movies: List[MovieSearchResult]
    tv_shows: List[TVShowSearchResult]
    games: List[GameSearchResult]
    books: List[BookSearchResult]


# --- Notifications ---
class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    body: Optional[str] = None
    category: Optional[str] = None
    entity_id: Optional[str] = None
    read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class UnreadCountResponse(BaseModel):
    unread: int


# --- Activity & Recommendations ---
class ActivityItem(BaseModel):
    """
    One inferred user action, unified across all four tracker domains. Most
    trackers only carry their latest touch (created_at/updated_at) rather
    than a full history, so one tracker row = one entry here, dated by its
    last update and with its action inferred from current list state.
    TV episodes are the exception (a real per-watch row exists), so each
    watched episode appears individually rather than being collapsed into
    its show's row.
    """

    category: str  # movie | tv_show | tv_episode | game | book
    action: str  # watchlist_added | ranked | marked_done | watched_episode
    title: str
    subtitle: Optional[str] = None
    entity_id: str  # catalog id to link to (the show id, for episodes)
    poster_url: Optional[str] = None
    rank: Optional[RankPosition] = None
    occurred_at: datetime


class BoredItem(BaseModel):
    """One candidate pulled from a to-be-consumed (watchlist/bucket) list."""

    category: str  # movie | tv_show | game | book
    title: str
    subtitle: Optional[str] = None
    entity_id: str
    poster_url: Optional[str] = None


class BoredResponse(BaseModel):
    pick: BoredItem
    pool_size: int
