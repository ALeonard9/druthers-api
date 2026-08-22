# pylint: disable=missing-module-docstring, missing-function-docstring, protected-access
from datetime import datetime, timezone

import pytest

from app.config import Settings, get_settings
from app.db import db_follow, db_friendship
from app.db.hash import Hash
from app.db.models import DbFollow, DbFriendship, DbUser
from app.db.models_sandbox import DbMovie, DbTVShow, DbUserMovie, DbUserTVShow
from app.migration import seed_dev
from app.services import preferences
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


def test_seed_movies_does_not_duplicate_a_tracker_pending_in_the_session(test_client):
    # The sibling test above covers a tracker already committed. This covers one
    # queued but not yet flushed, which is the case that actually bit: the target
    # user is seeded for each canon overlap title twice -- once from the fixture
    # sample, once from the cast-overlap pass -- and neither is flushed while the
    # other is being built. A database-only check saw nothing and queued both,
    # producing eight duplicate tracker rows per seed. That was silent until
    # api#352 added UNIQUE (user_id, movie_id), which turned it into a failed
    # seed: `task seed:dev` aborting with a UniqueViolation.
    session = test_client.test_db_session
    user = test_client.first_user
    movie = seed_dev._get_or_create_movie(session, _MOVIE_A)
    session.add(
        DbUserMovie(
            movie_id=movie.pk,
            user_id=user.pk,
            on_rankings=True,
            rank=1,
            is_seed_data=True,
        )
    )
    # deliberately not committed -- the row is pending, exactly as it is mid-seed

    seed_dev._seed_movies(session, user, [_MOVIE_A, _MOVIE_B])
    session.commit()

    trackers = session.query(DbUserMovie).filter(DbUserMovie.user_id == user.pk).all()
    assert len(trackers) == 2, 'the pending Interstellar row must not be queued twice'
    assert sorted(t.movie_id for t in trackers) == sorted(
        {t.movie_id for t in trackers}
    )


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

    assert result['cast_users'] == 8
    # target 8 canon + friend 8 + follower 2 + followee 1 + public 3
    # + private 6 + stranger 4 non-canon + admin-two 0.
    assert result['ranked_rows'] == 32

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


def test_seed_cast_admin_two_is_a_second_admin(test_client):
    """
    Dev otherwise seeds exactly one admin (the seed admin from
    ADMIN_EMAIL), which made #341's "an admin cannot impersonate or
    disable another admin" provable only by unit test - never
    demonstrable in the console. admin-two exists to fix that; it has no
    friend/follow relationship to the target and ranks nothing, unlike the
    other six cast members.
    """
    session = test_client.test_db_session
    target = test_client.first_user

    seed_dev._seed_cast(session, target)
    session.commit()

    admin_two = _cast_user(session, 'admin-two@gmail.com')
    assert admin_two.user_group == 'admin'
    assert admin_two.handle == 'admin-two'
    assert Hash.verify(admin_two.password, seed_dev._CAST_PASSWORD)
    assert db_friendship.friendship_between(session, target.pk, admin_two.pk) is None
    assert db_follow.find(session, admin_two.pk, target.pk) is None
    assert db_follow.find(session, target.pk, admin_two.pk) is None
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.user_id == admin_two.pk).count()
        == 0
    )


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
    # stranger: zero canon overlap, but non-canon ranks so the profile is not
    # empty -- that keeps the compare state not_enough_overlap.
    stranger = _cast_user(session, 'stranger@example.com')
    stranger_ranked = ranked(stranger)
    assert len(stranger_ranked) == 4
    assert not stranger_ranked & target_canon
    assert (
        session.query(DbUserMovie)
        .filter(DbUserMovie.user_id == stranger.pk, DbUserMovie.is_seed_data.is_(True))
        .count()
        == 4
    )


def test_seed_cast_stocks_the_private_users_shelf(test_client):
    """
    An empty private shelf made the 404 unfalsifiable.

    With nothing behind it, "you cannot see this profile" and "this profile
    has nothing in it" produced the same response, so the visibility rule
    could have been broken without any test or demo noticing.
    """
    session = test_client.test_db_session
    seed_dev._seed_cast(session, test_client.first_user)
    session.commit()

    private_user = _cast_user(session, 'private@example.com')
    assert (
        session.query(DbUserMovie)
        .filter(
            DbUserMovie.user_id == private_user.pk,
            DbUserMovie.on_rankings.is_(True),
        )
        .count()
        == 6
    )


def test_seed_cast_re_enables_only_the_disposable_seat(test_client):
    """
    The destructive admin specs disable the disposable seat on purpose, and a
    spec that fails midway leaves it disabled. Without this the seat is
    single-use and the next run fails for the previous run's reason.

    Scoped to that one seat: silently re-enabling an account an operator
    disabled by hand would be a surprise, and for the rest of the cast a
    disabled seat is a bug rather than leftover state.
    """
    session = test_client.test_db_session
    seed_dev._seed_cast(session, test_client.first_user)
    session.commit()

    disposable = _cast_user(session, 'e2e-disposable@gmail.com')
    ordinary = _cast_user(session, 'follower@example.com')
    disposable.disabled_at = datetime.now(timezone.utc)
    ordinary.disabled_at = datetime.now(timezone.utc)
    session.commit()

    seed_dev._seed_cast(session, test_client.first_user)
    session.commit()

    assert _cast_user(session, 'e2e-disposable@gmail.com').disabled_at is None
    assert (
        _cast_user(session, 'follower@example.com').disabled_at is not None
    ), 'a non-disposable seat must keep whatever disabled state it was given'


def test_seed_cast_gives_every_member_a_distinct_time_zone(test_client):
    """The spread is the point: one zone for all of them demos nothing."""
    session = test_client.test_db_session
    seed_dev._seed_cast(session, test_client.first_user)
    session.commit()

    zones = [
        _cast_user(session, spec['email']).time_zone for spec in seed_dev._CAST_USERS
    ]
    assert all(zones)
    assert len(set(zones)) == len(zones)
    for zone in zones:
        assert preferences.is_valid_time_zone(zone)


def test_seed_cast_leaves_the_target_on_the_deployment_zone(test_client):
    """The target seat stays NULL, which is what exercises the fallback."""
    session = test_client.test_db_session
    target = test_client.first_user
    seed_dev._seed_cast(session, target)
    session.commit()

    assert target.time_zone is None
    assert preferences.coerce_time_zone(target.time_zone) == get_settings().time_zone


def test_seed_cast_is_idempotent(test_client):
    session = test_client.test_db_session
    target = test_client.first_user

    seed_dev._seed_cast(session, target)
    session.commit()
    second = seed_dev._seed_cast(session, target)
    session.commit()

    assert second == {'cast_users': 8, 'ranked_rows': 0}
    assert (
        session.query(DbUser)
        .filter(DbUser.email.in_([s['email'] for s in seed_dev._CAST_USERS]))
        .count()
        == 8
    )
    assert session.query(DbFriendship).count() == 1
    assert session.query(DbFollow).count() == 2
    assert (
        session.query(DbUserMovie).filter(DbUserMovie.is_seed_data.is_(True)).count()
        == 32
    )
    # 8 canon titles plus the 4 non-canon ones stranger's shelf pulls in.
    assert session.query(DbMovie).count() == 12


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
    # Catalog rows survive a wipe -- they are real either way. 8 canon plus
    # the 4 non-canon titles stranger's shelf pulls in.
    assert session.query(DbMovie).count() == 12
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
