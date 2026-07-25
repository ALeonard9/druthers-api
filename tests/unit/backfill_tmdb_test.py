# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access
from app.migration import backfill_tmdb


def test_legacy_hosts_are_replaced():
    assert backfill_tmdb._needs_new_poster(
        'https://m.media-amazon.com/images/M/matrix.jpg'
    )
    assert backfill_tmdb._needs_new_poster('https://ia.media-imdb.com/images/x.jpg')


def test_missing_poster_is_filled():
    # A row with no poster renders a placeholder, so TMDB's is a pure gain.
    assert backfill_tmdb._needs_new_poster(None)
    assert backfill_tmdb._needs_new_poster('')


def test_existing_tmdb_poster_is_left_alone():
    # The 7 legacy image.tmdb.org posters must not be churned.
    assert not backfill_tmdb._needs_new_poster(
        'https://image.tmdb.org/t/p/w500/legacy.jpg'
    )


def test_unrecognized_host_is_left_alone():
    assert not backfill_tmdb._needs_new_poster('https://example.com/poster.jpg')
