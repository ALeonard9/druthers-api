# pylint: disable=missing-module-docstring, missing-function-docstring, protected-access
import pytest

from app.config import Settings
from app.db import db_follow, db_friendship
from app.db.hash import Hash
from app.db.models import DbFollow, DbFriendship, DbUser
from app.db.models_sandbox import DbMovie, DbTVShow, DbUserMovie, DbUserTVShow
from app.migration import seed_dev
from app.services.friendships import FriendshipStatus

_MOVIE_A = {
    'title': 'Interstellar',
    'tmdb': 157336,
    'imdb': 'tt0816692',
    'year': 2014,
    'plot': 'Explorers travel through a wormhole.',
    'director': 'Christopher Nolan',
}
_MOVIE_B = {
    'title': 'The Matrix',
    'tmdb': 603,
    'imdb': 'tt0133093',
    'year': 1999,
    'plot': 'A hacker learns the truth.',
    'director': 'The Wachowskis',
}


def test_get_or_create_movie_inserts_once_then_reuses(test_client):
    session = test_client.test_db_session

    first = seed_dev._get_or_create_movie(session, _MOVIE_A)
    second = seed_dev._get_or_create_movie(session, dict(_MOVIE_A))

    assert first.pk == second.pk
    assert session.query(DbMovie).filter(DbMovie.tmdb == 157336).count() == 1


def test_get_or_create_movie_matches_on_imdb_when_tmdb_absent(test_client):
    session = test_client.test_db_session
    data = dict(_MOVIE_A)
    data.pop('tmdb')

    first = seed_dev._get_or_create_movie(session, data)
    second = seed_dev._get_or_create_movie(session, dict(data))

    assert first.pk == second.pk


def test_seed_movies_upserts_catalog_and_marks_trackers_seeded(test_client):
    session = test_client.test_db_session
    user = test_client.first_user

    seed_dev._seed_movies(session, user, [_MOVIE_A, _MOVIE_B])
    session.commit()

    trackers = session.query(DbUserMovie).filter(DbUserMovie.user_id == user.pk).all()
    assert len(trackers) == 2
    assert all(t.is_seed_data for t in trackers)
    assert session.query(DbMovie).count() == 2


def test_seed_movies_does_not_duplicate_an_existing_tracker(test_client):
    # A movie the user already tracks (seeded or not) must be left alone --
    # re-tracking it would silently overwrite real list membership/rank.
    session = test_client.test_db_session
    user = test_client.first_user
    movie = seed_dev._get_or_create_movie(session, _MOVIE_A)
    session.add(
        DbUserMovie(
            movie_id=movie.pk,
            user_id=user.pk,
            on_watchlist=True,
            is_seed_data=False,
        )
    )
    session.commit()

    seed_dev._seed_movies(session, user, [_MOVIE_A, _MOVIE_B])
    session.commit()

    trackers = session.query(DbUserMovie).filter(DbUserMovie.user_id == user.pk).all()
    # The Interstellar tracker is untouched (still not seed data); only The
    # Matrix was newly created.
    assert len(trackers) == 2
    by_movie = {t.movie_id: t for t in trackers}
    assert by_movie[movie.pk].is_seed_data is False


def test_wipe_removes_only_seeded_trackers_for_the_target_user(test_client):
    session = test_client.test_db_session
    first, second = test_client.first_user, test_client.second_user

    seed_dev._seed_movies(session, first, [_MOVIE_A, _MOVIE_B])
    real_movie = seed_dev._get_or_create_movie(session, _MOVIE_A)
    session.add(
        DbUserMovie(movie_id=real_movie.pk, user_id=second.pk, on_watchlist=True)
    )
    session.commit()

    wiped = seed_dev._wipe(session, first)
    session.commit()

    assert wiped['movies'] == 2
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == first.pk).count() == 0
    )
    # Catalog rows survive a wipe -- they're real either way.
    assert session.query(DbMovie).count() == 2
    # The other user's (non-seed) tracker on the same movie is untouched.
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == second.pk).count() == 1
    )


def test_sample_caps_to_fixture_size_and_warns(caplog):
    rows = [_MOVIE_A, _MOVIE_B]
    with caplog.at_level('WARNING'):
        sampled = seed_dev._sample(rows, 10, 'movies')
    assert len(sampled) == 2
    assert 'only has 2' in caplog.text


def test_sample_does_not_warn_when_within_fixture_size(caplog):
    rows = [_MOVIE_A, _MOVIE_B]
    with caplog.at_level('WARNING'):
        sampled = seed_dev._sample(rows, 1, 'movies')
    assert len(sampled) == 1
    assert caplog.text == ''


def test_purge_legacy_fake_rows_removes_catalog_and_trackers(test_client):
    session = test_client.test_db_session
    first, second = test_client.first_user, test_client.second_user

    legacy_movie = seed_dev._get_or_create_movie(
        session,
        {'title': 'Object-based mobile definition', 'imdb': 'ttfakemovie000001'},
    )
    session.add(
        DbUserMovie(movie_id=legacy_movie.pk, user_id=first.pk, on_watchlist=True)
    )
    legacy_show = DbTVShow(title='Fake Show', imdb='ttfaketv000001', tvmaze=900_001)
    session.add(legacy_show)
    session.flush()
    session.add(
        DbUserTVShow(tv_show_id=legacy_show.pk, user_id=second.pk, on_watchlist=True)
    )
    real_movie = seed_dev._get_or_create_movie(session, _MOVIE_A)
    session.add(
        DbUserMovie(movie_id=real_movie.pk, user_id=first.pk, on_watchlist=True)
    )
    session.commit()
    # Captured before the purge commits -- afterward these rows are gone and
    # SQLAlchemy's post-commit expiry would try (and fail) to re-fetch them.
    legacy_movie_pk, legacy_show_pk, real_movie_pk = (
        legacy_movie.pk,
        legacy_show.pk,
        real_movie.pk,
    )

    purged = seed_dev._purge_legacy_fake_rows(session)
    session.commit()

    assert purged['legacy_movies'] == 1
    assert purged['legacy_shows'] == 1
    assert session.query(DbMovie).filter(DbMovie.pk == legacy_movie_pk).count() == 0
    assert session.query(DbTVShow).filter(DbTVShow.pk == legacy_show_pk).count() == 0
    assert (
        session.query(DbUserMovie)
        .filter(DbUserMovie.movie_id == legacy_movie_pk)
        .count()
        == 0
    )
    # The real movie (and its tracker) is untouched.
    assert session.query(DbMovie).filter(DbMovie.pk == real_movie_pk).count() == 1
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.movie_id == real_movie_pk).count()
        == 1
    )


def test_purge_legacy_fake_rows_is_idempotent(test_client):
    session = test_client.test_db_session
    seed_dev._get_or_create_movie(
        session, {'title': 'Fake', 'imdb': 'ttfakemovie000002'}
    )
    session.commit()

    first_pass = seed_dev._purge_legacy_fake_rows(session)
    session.commit()
    second_pass = seed_dev._purge_legacy_fake_rows(session)

    assert first_pass['legacy_movies'] == 1
    assert second_pass == {
        'legacy_movies': 0,
        'legacy_shows': 0,
        'legacy_games': 0,
        'legacy_books': 0,
    }


def test_run_seed_wipe_only_does_not_reseed(test_client, monkeypatch):
    session = test_client.test_db_session
    user = test_client.first_user
    seed_dev._seed_movies(session, user, [_MOVIE_A])
    session.commit()

    monkeypatch.setattr(seed_dev, 'SessionLocal', lambda: session)
    monkeypatch.setattr(session, 'close', lambda: None)

    seed_dev.run_seed(count=5, wipe_only=True, email=user.email)

    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == user.pk).count() == 0
    )


def _cast_user(session, email):
    return session.query(DbUser).filter_by(email=email).one()


def test_seed_cast_creates_fixed_users_and_relationships(test_client):
    session = test_client.test_db_session
    target = test_client.first_user

    result = seed_dev._seed_cast(session, target)
    session.commit()

    assert result['cast_users'] == 6
    assert result['ranked_rows'] == 24

    friend = _cast_user(session, 'friend@example.com')
    assert friend.handle == 'friend'
    assert friend.visibility_profile == 'friends'
    assert Hash.verify(friend.password, seed_dev._CAST_PASSWORD)

    follower = _cast_user(session, 'follower@example.com')
    followee = _cast_user(session, 'followee@example.com')
    public_user = _cast_user(session, 'public@example.com')
    private_user = _cast_user(session, 'private@example.com')
    stranger = _cast_user(session, 'stranger@example.com')

    # The target is the anchor: public everywhere with the fixed handle.
    assert target.handle == seed_dev._TARGET_HANDLE
    assert target.visibility_profile == 'public'

    # target and friend are accepted friends, requested by the target.
    friendship = db_friendship.friendship_between(session, target.pk, friend.pk)
    assert friendship is not None
    assert friendship.requested_by_id == target.pk
    assert friendship.status == FriendshipStatus.ACCEPTED

    # Exactly the two matrix follows: follower -> target and target -> followee.
    assert db_follow.find(session, follower.pk, target.pk) is not None
    assert db_follow.find(session, target.pk, follower.pk) is None
    assert db_follow.find(session, target.pk, followee.pk) is not None
    assert db_follow.find(session, followee.pk, target.pk) is None
    assert session.query(DbFollow).count() == 2

    # Per-member visibility tiers.
    assert followee.visibility_profile == 'public'
    assert followee.visibility_books == 'friends'
    assert public_user.visibility_profile == 'public'
    assert private_user.visibility_profile == 'private'
    assert stranger.visibility_profile == 'public'


def test_seed_cast_ranks_the_canon_movies(test_client):
    session = test_client.test_db_session
    target = test_client.first_user

    seed_dev._seed_cast(session, target)
    session.commit()

    assert (
        session.query(DbMovie)
        .filter(DbMovie.tmdb.in_(seed_dev._CAST_CANON_TMDB))
        .count()
        == 8
    )

    def ranked(user):
        return {
            t.movie_id
            for t in session.query(DbUserMovie).filter(
                DbUserMovie.user_id == user.pk,
                DbUserMovie.on_rankings.is_(True),
                DbUserMovie.is_seed_data.is_(True),
            )
        }

    target_canon = ranked(target)
    assert len(target_canon) == 8
    assert ranked(_cast_user(session, 'friend@example.com')) == target_canon
    assert len(ranked(_cast_user(session, 'follower@example.com'))) == 2
    assert len(ranked(_cast_user(session, 'public@example.com'))) == 3
    assert len(ranked(_cast_user(session, 'followee@example.com'))) == 1
    # stranger: zero canon overlap, but a couple of non-canon ranks so the
    # profile is not empty -- that keeps the compare state not_enough_overlap.
    stranger = _cast_user(session, 'stranger@example.com')
    stranger_ranked = ranked(stranger)
    assert len(stranger_ranked) == 2
    assert not stranger_ranked & target_canon
    assert (
        session.query(DbUserMovie)
        .filter(DbUserMovie.user_id == stranger.pk, DbUserMovie.is_seed_data.is_(True))
        .count()
        == 2
    )


def test_seed_cast_is_idempotent(test_client):
    session = test_client.test_db_session
    target = test_client.first_user

    seed_dev._seed_cast(session, target)
    session.commit()
    second = seed_dev._seed_cast(session, target)
    session.commit()

    assert second == {'cast_users': 6, 'ranked_rows': 0}
    assert (
        session.query(DbUser)
        .filter(DbUser.email.in_([s['email'] for s in seed_dev._CAST_USERS]))
        .count()
        == 6
    )
    assert session.query(DbFriendship).count() == 1
    assert session.query(DbFollow).count() == 2
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.is_seed_data.is_(True)).count()
        == 24
    )
    assert session.query(DbMovie).count() == 8


def test_wipe_removes_cast_trackers_but_keeps_relationships(test_client):
    session = test_client.test_db_session
    target = test_client.first_user

    seed_dev._seed_cast(session, target)
    session.commit()

    friend = _cast_user(session, 'friend@example.com')
    friend_pk = friend.pk
    wiped = seed_dev._wipe(session, friend)
    session.commit()

    assert wiped['movies'] == 8
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == friend_pk).count() == 0
    )
    # Catalog rows survive a wipe -- they are real either way.
    assert session.query(DbMovie).count() == 8
    # The user and the friendship row are not wiped; the relationship outlives
    # the tracker rows.
    assert session.query(DbUser).filter(DbUser.pk == friend_pk).count() == 1
    assert (
        session.query(DbFriendship)
        .filter(
            (DbFriendship.user_low_id == friend_pk)
            | (DbFriendship.user_high_id == friend_pk)
        )
        .count()
        == 1
    )


def test_run_seed_seeds_cast_alongside_target(test_client, monkeypatch):
    session = test_client.test_db_session
    user = test_client.first_user

    monkeypatch.setattr(seed_dev, 'SessionLocal', lambda: session)
    monkeypatch.setattr(session, 'close', lambda: None)

    seed_dev.run_seed(count=5, wipe_only=False, email=user.email)

    assert user.handle == seed_dev._TARGET_HANDLE
    assert user.visibility_profile == 'public'
    friend = _cast_user(session, 'friend@example.com')
    friendship = db_friendship.friendship_between(session, user.pk, friend.pk)
    assert friendship is not None
    assert friendship.status == FriendshipStatus.ACCEPTED
    # The full 8-movie canon landed on the target regardless of the randomized
    # sample run_seed drew for the ordinary seed path.
    assert (
        session.query(DbUserMovie)
        .join(DbMovie, DbUserMovie.movie_id == DbMovie.pk)
        .filter(
            DbUserMovie.user_id == user.pk,
            DbMovie.tmdb.in_(seed_dev._CAST_CANON_TMDB),
            DbUserMovie.is_seed_data.is_(True),
        )
        .count()
        == 8
    )
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == friend.pk).count() == 8
    )


def test_run_seed_wipe_only_clears_cast_trackers_too(test_client, monkeypatch):
    session = test_client.test_db_session
    user = test_client.first_user
    seed_dev._seed_cast(session, user)
    session.commit()

    monkeypatch.setattr(seed_dev, 'SessionLocal', lambda: session)
    monkeypatch.setattr(session, 'close', lambda: None)

    seed_dev.run_seed(count=5, wipe_only=True, email=user.email)

    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == user.pk).count() == 0
    )
    friend = _cast_user(session, 'friend@example.com')
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == friend.pk).count() == 0
    )
    # The cast users and their relationships are left in place.
    assert session.query(DbUser).filter(DbUser.email == friend.email).count() == 1
    assert session.query(DbFriendship).count() == 1
    assert session.query(DbFollow).count() == 2


def test_assert_local_dev_refuses_when_database_url_overrides_safe_postgres_host(
    monkeypatch,
):
    # #257: POSTGRES_HOST=localhost looked safe, but sqlalchemy_database_url
    # prefers DATABASE_URL whenever it is set -- the guard must validate the
    # host actually being connected to, not a variable that may not be in play.
    settings = Settings(
        env='dev',
        postgres_host='localhost',
        database_url='postgresql://user:pw@ep-prod-db.us-east-2.aws.neon.tech/druthers',
    )
    monkeypatch.setattr(seed_dev, 'get_settings', lambda: settings)

    with pytest.raises(SystemExit):
        seed_dev._assert_local_dev()


def test_assert_local_dev_allows_local_postgres_host_with_no_database_url(monkeypatch):
    settings = Settings(env='dev', postgres_host='localhost', database_url=None)
    monkeypatch.setattr(seed_dev, 'get_settings', lambda: settings)

    seed_dev._assert_local_dev()  # does not raise


def test_assert_local_dev_allows_dev_suffixed_docker_host_via_database_url(monkeypatch):
    settings = Settings(
        env='dev',
        database_url='postgresql://user:pw@m3_druthers_db_dev:5432/druthers',
    )
    monkeypatch.setattr(seed_dev, 'get_settings', lambda: settings)

    seed_dev._assert_local_dev()  # does not raise


def test_assert_local_dev_refuses_non_dev_env_even_with_safe_host(monkeypatch):
    settings = Settings(env='prod', postgres_host='localhost')
    monkeypatch.setattr(seed_dev, 'get_settings', lambda: settings)

    with pytest.raises(SystemExit):
        seed_dev._assert_local_dev()
