# pylint: disable=missing-module-docstring, missing-function-docstring
"""
The enrichers pick their work by proxy fields (plot/director, description/
authors, summary/genre). A row that carries those but has no ``year`` used to
fall outside the filter and stay year-less forever - which is what blanked
half the years on the home Top 5. These pin the missing field itself as a
selection criterion.
"""

from datetime import timedelta

from app.db.models_sandbox import DbBook, DbMovie, DbVideoGame
from app.migration.enrich_books import RETRY_AFTER, pending_books
from app.migration.enrich_games import pending_games
from app.migration.enrich_movies import pending_movies
from app.services.tracker_rules import utc_now


def _titles(rows):
    return sorted(r.title for r in rows)


def test_movie_with_detail_but_no_year_is_still_pending(test_client):
    session = test_client.test_db_session
    session.add_all(
        [
            # The OMDb-era shape: enriched enough to look done, no year.
            DbMovie(
                title='The Matrix',
                imdb='tt0133093',
                tmdb=603,
                plot='A hacker learns the truth.',
                director='The Wachowskis',
                year=None,
            ),
            DbMovie(title='Never touched', imdb='tt0000001', tmdb=1, year=None),
            DbMovie(
                title='Fully enriched',
                imdb='tt0816692',
                tmdb=157336,
                plot='Astronauts.',
                director='Christopher Nolan',
                year=2014,
            ),
        ]
    )
    session.commit()

    assert _titles(pending_movies(session)) == ['Never touched', 'The Matrix']


def test_movie_without_a_tmdb_key_is_not_pending(test_client):
    # enrich_movies calls get_movie_detail(movie.tmdb); an unkeyed row would
    # burn a request on None. backfill_tmdb keys it first.
    session = test_client.test_db_session
    session.add(DbMovie(title='Unkeyed', imdb='tt0000002', tmdb=None, year=None))
    session.commit()

    assert pending_movies(session) == []


def test_book_with_detail_but_no_year_is_still_pending(test_client):
    session = test_client.test_db_session
    session.add_all(
        [
            DbBook(
                title='The Name of the Wind',
                isbn='9780756404741',
                authors='Patrick Rothfuss',
                description='Kvothe tells his story.',
                year=None,
            ),
            DbBook(
                title='Fully enriched',
                isbn='9780765326355',
                authors='Brandon Sanderson',
                description='Bridge Four.',
                year=2010,
            ),
        ]
    )
    session.commit()

    assert _titles(pending_books(session)) == ['The Name of the Wind']


def test_book_resolved_recently_but_still_incomplete_is_not_re_pending(test_client):
    # #258: The Power of Habit's Google volume is live and answered, but
    # publishedDate is null -- that's a real, permanent answer, not "never
    # enriched", so a recent attempt should suppress re-selection.
    session = test_client.test_db_session
    session.add(
        DbBook(
            title='The Power of Habit',
            googleid='abc123',
            authors='Charles Duhigg',
            description='Habits explained.',
            year=None,
            enrichment_attempted_at=utc_now(),
        )
    )
    session.commit()

    assert pending_books(session) == []


def test_book_incomplete_and_attempted_long_ago_is_pending_again(test_client):
    session = test_client.test_db_session
    session.add(
        DbBook(
            title='The Power of Habit',
            googleid='abc123',
            authors='Charles Duhigg',
            description='Habits explained.',
            year=None,
            enrichment_attempted_at=utc_now() - RETRY_AFTER - timedelta(days=1),
        )
    )
    session.commit()

    assert _titles(pending_books(session)) == ['The Power of Habit']


def test_game_with_detail_but_no_year_is_still_pending(test_client):
    session = test_client.test_db_session
    session.add_all(
        [
            DbVideoGame(
                title='Portal',
                igdb=7346,
                summary='Now you are thinking with portals.',
                genre='Puzzle',
                year=None,
            ),
            DbVideoGame(
                title='Fully enriched',
                igdb=1234,
                summary='Robots.',
                genre='Action',
                year=2017,
            ),
        ]
    )
    session.commit()

    assert _titles(pending_games(session)) == ['Portal']
