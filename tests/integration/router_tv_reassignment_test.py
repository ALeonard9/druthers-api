# pylint: disable=missing-module-docstring, missing-function-docstring
from datetime import date, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.models_sandbox import DbTVEpisode, DbUserTVEpisode
from app.jobs.audit_watch_gaps import audit, find_duplicate_slots

EPISODE = {
    'tvmaze': 13006,
    'title': 'Trash',
    'season': 1,
    'season_number': 12,
    'airdate': None,
}


def _admin(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.admin_user.token}'}


def _user(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


def _second_user(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.second_user.token}'}


def _make_show(test_client: TestClient, tvmaze=180):
    # Creating a show enriches *and* pulls the episode list, so both TVMaze
    # calls have to be stubbed or the test reaches the network.
    with patch('app.router.v1.router_tv.get_tv_show_detail', return_value=None), patch(
        'app.services.tv_search.get_show_episodes', return_value=[]
    ):
        return test_client.post(
            '/v1/tv-shows',
            headers=_admin(test_client),
            json={'title': 'Firefly', 'tvmaze': tvmaze},
        ).json()['id']


def _track(test_client: TestClient, show_id):
    """Put the show on the user's watchlist - the audit only walks tracked shows."""
    test_client.post(
        f'/v1/users/me/tv-shows/{show_id}',
        headers=_user(test_client),
        json={'on_watchlist': True},
    )


def _sync(test_client: TestClient, show_id, episodes):
    with patch('app.services.tv_search.get_show_episodes', return_value=episodes):
        return test_client.post(
            f'/v1/tv-shows/{show_id}/episodes/sync', headers=_admin(test_client)
        )


def _show_pk(test_client: TestClient, show_id):
    from app.db.models_sandbox import DbTVShow  # pylint: disable=C0415

    return (
        test_client.test_db_session.query(DbTVShow)
        .filter(DbTVShow.id == show_id)
        .one()
        .pk
    )


def test_reassigned_tvmaze_id_updates_in_place(test_client: TestClient):
    """#169: a reassigned id must not create a second row for the same slot."""
    show_id = _make_show(test_client)
    assert len(_sync(test_client, show_id, [EPISODE]).json()) == 1

    # TVMaze now serves the same slot under a different id.
    reassigned = {**EPISODE, 'tvmaze': 13007}
    listing = _sync(test_client, show_id, [reassigned]).json()

    assert len(listing) == 1, 'a duplicate row was created for the same slot'
    assert listing[0]['tvmaze'] == 13007


def test_reassigned_id_keeps_watch_history(test_client: TestClient):
    show_id = _make_show(test_client)
    _track(test_client, show_id)
    episode_id = _sync(test_client, show_id, [EPISODE]).json()[0]['id']

    test_client.post(f'/v1/users/me/episodes/{episode_id}', headers=_user(test_client))
    _sync(test_client, show_id, [{**EPISODE, 'tvmaze': 13007}])

    watched = test_client.get(
        f'/v1/users/me/tv-shows/{show_id}/episodes', headers=_user(test_client)
    ).json()
    assert [e['watched'] for e in watched if e['episode']['id'] == episode_id] == [1]


def test_ambiguous_slot_is_left_alone(test_client: TestClient):
    """With an existing duplicate pair, syncing must not guess which is real."""
    session = test_client.test_db_session
    show_id = _make_show(test_client)
    show_pk = _show_pk(test_client, show_id)
    for tvmaze in (13006, 13007):
        session.add(DbTVEpisode(tv_show_id=show_pk, **{**EPISODE, 'tvmaze': tvmaze}))
    session.commit()

    _sync(test_client, show_id, [{**EPISODE, 'tvmaze': 99999}])

    rows = session.query(DbTVEpisode).filter(DbTVEpisode.tv_show_id == show_pk).all()
    # The unmatched incoming episode is added; neither existing row is clobbered.
    assert sorted(r.tvmaze for r in rows) == [13006, 13007, 99999]


def test_audit_finds_and_repairs_a_duplicate_slot(test_client: TestClient):
    session = test_client.test_db_session
    show_id = _make_show(test_client)
    _track(test_client, show_id)
    show_pk = _show_pk(test_client, show_id)

    # The original, watched; plus the orphan duplicate the old sync created.
    original_id = _sync(test_client, show_id, [EPISODE]).json()[0]['id']
    test_client.post(f'/v1/users/me/episodes/{original_id}', headers=_user(test_client))
    session.add(DbTVEpisode(tv_show_id=show_pk, **{**EPISODE, 'tvmaze': 13007}))
    # Give the show a second, fully-watched episode so the season looks normal.
    session.commit()

    assert len(find_duplicate_slots(session, show_pk)) == 1

    found = audit(session, email=test_client.first_user.email)
    assert any('Firefly S1E12' in row for row in found['duplicates'])

    repaired = audit(session, email=test_client.first_user.email, fix=True)
    assert any('Firefly S1E12' in row for row in repaired['repaired'])
    assert not find_duplicate_slots(session, show_pk)

    # The surviving row kept the watch mark.
    watched = test_client.get(
        f'/v1/users/me/tv-shows/{show_id}/episodes', headers=_user(test_client)
    ).json()
    assert [e['watched'] for e in watched] == [1]


def test_audit_fix_preserves_every_users_state(test_client: TestClient):
    session = test_client.test_db_session
    show_id = _make_show(test_client)
    _track(test_client, show_id)
    test_client.post(
        f'/v1/users/me/tv-shows/{show_id}',
        headers=_second_user(test_client),
        json={'on_watchlist': True},
    )
    show_pk = _show_pk(test_client, show_id)
    keeper = DbTVEpisode(tv_show_id=show_pk, **EPISODE)
    orphan = DbTVEpisode(
        tv_show_id=show_pk, **{**EPISODE, 'tvmaze': EPISODE['tvmaze'] + 1}
    )
    session.add_all((keeper, orphan))
    session.flush()

    first_watched_at = datetime(2020, 1, 2)
    second_watched_at = datetime(2021, 2, 3)
    second_favorited_at = datetime(2022, 3, 4)
    session.add_all(
        (
            DbUserTVEpisode(
                user_id=test_client.first_user.pk,
                episode_id=keeper.pk,
                watched=1,
                watched_at=first_watched_at,
            ),
            DbUserTVEpisode(
                user_id=test_client.second_user.pk,
                episode_id=keeper.pk,
                watched=0,
                favorited=True,
                favorited_at=second_favorited_at,
            ),
            DbUserTVEpisode(
                user_id=test_client.second_user.pk,
                episode_id=orphan.pk,
                watched=1,
                watched_at=second_watched_at,
            ),
        )
    )
    session.commit()

    repaired = audit(session, fix=True)

    assert any('Firefly S1E12' in row for row in repaired['repaired'])
    assert not find_duplicate_slots(session, show_pk)
    marks = (
        session.query(DbUserTVEpisode)
        .filter(DbUserTVEpisode.episode_id == keeper.pk)
        .order_by(DbUserTVEpisode.user_id)
        .all()
    )
    assert [mark.user_id for mark in marks] == sorted(
        (test_client.first_user.pk, test_client.second_user.pk)
    )
    first_mark = next(
        mark for mark in marks if mark.user_id == test_client.first_user.pk
    )
    second_mark = next(
        mark for mark in marks if mark.user_id == test_client.second_user.pk
    )
    assert first_mark.watched == 1
    assert first_mark.watched_at == first_watched_at
    assert second_mark.watched == 1
    assert second_mark.watched_at == second_watched_at
    assert second_mark.favorited is True
    assert second_mark.favorited_at == second_favorited_at


def test_audit_reports_a_lone_gap_without_touching_it(test_client: TestClient):
    session = test_client.test_db_session
    show_id = _make_show(test_client)
    _track(test_client, show_id)
    # Aired episodes only - the audit ignores rows with no airdate so an
    # unaired episode never reads as a gap.
    episodes = [
        {
            **EPISODE,
            'tvmaze': 100 + i,
            'season_number': i,
            'title': f'Ep {i}',
            'airdate': date(2002, 9, i),
        }
        for i in range(1, 4)
    ]
    listing = _sync(test_client, show_id, episodes).json()
    # Watch everything but the last.
    for ep in listing[:-1]:
        test_client.post(
            f'/v1/users/me/episodes/{ep["id"]}', headers=_user(test_client)
        )

    found = audit(session, email=test_client.first_user.email)
    assert any('Ep 3' in row for row in found['gaps'])
    assert not found.get('duplicates')
    # Gaps are reported, never invented into a watch.
    assert not found.get('repaired')
