# pylint: disable=missing-module-docstring, missing-function-docstring
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.services import watch_providers


def _make_show(test_client: TestClient, title='Breaking Bad', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/tv-shows', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_episode(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    test_client: TestClient,
    show_id: str,
    title='Pilot',
    season=1,
    number=1,
    airdate=None,
) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    payload = {'title': title, 'season': season, 'season_number': number}
    if airdate is not None:
        payload['airdate'] = airdate
    resp = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes",
        headers=headers,
        json=payload,
    )
    assert resp.status_code == 201
    return resp.json()['id']


# --- Global catalog ---
def test_create_tv_show(test_client: TestClient):
    admin_headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    response = test_client.post(
        '/v1/tv-shows',
        headers=admin_headers,
        json={'title': 'Breaking Bad', 'imdb': 'tt0903747'},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['title'] == 'Breaking Bad'
    assert data['imdb'] == 'tt0903747'


def test_get_tv_shows(test_client: TestClient):
    _make_show(test_client)
    response = test_client.get('/v1/tv-shows')
    assert response.status_code == 200
    assert len(response.json()) > 0


def test_create_tv_show_unauthenticated(test_client: TestClient):
    response = test_client.post('/v1/tv-shows', json={'title': 'Breaking Bad'})
    assert response.status_code == 401


def test_create_tv_show_allowed_for_any_user(test_client: TestClient):
    """Regular users add to the shared catalog via the add-from-search flow."""
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.post(
        '/v1/tv-shows', headers=headers, json={'title': 'Breaking Bad'}
    )
    assert response.status_code == 201


def test_create_duplicate_tvmaze_rejected(test_client: TestClient):
    admin_headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    with patch('app.router.v1.router_tv.get_tv_show_detail', return_value=None):
        first = test_client.post(
            '/v1/tv-shows',
            headers=admin_headers,
            json={'title': 'Severance', 'tvmaze': 44932},
        )
        assert first.status_code == 201
        dup = test_client.post(
            '/v1/tv-shows',
            headers=admin_headers,
            json={'title': 'Severance again', 'tvmaze': 44932},
        )
    assert dup.status_code == 400


def test_update_tv_show_requires_admin(test_client: TestClient):
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.put(
        f"/v1/tv-shows/{show_id}", headers=headers, json={'title': 'Hacked'}
    )
    assert response.status_code == 403


@patch('app.router.v1.router_tv.get_tv_show_detail')
def test_get_tv_show_enriches_on_view(mock_detail, test_client: TestClient):
    show_id = _make_show(test_client)
    mock_detail.return_value = {
        'status': 'Ended',
        'genre': 'Crime, Drama, Thriller',
        'network': 'AMC',
        'year': 2008,
        'rating': 9.2,
        'summary': 'A chemistry teacher breaks bad.',
    }
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get(f"/v1/tv-shows/{show_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['genre'] == 'Crime, Drama, Thriller'
    assert data['network'] == 'AMC'
    assert data['year'] == 2008
    assert data['summary'] == 'A chemistry teacher breaks bad.'


# --- Search proxy ---
def test_search_tv_shows_requires_auth(test_client: TestClient):
    response = test_client.get('/v1/tv-shows/search?q=severance')
    assert response.status_code == 401


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_returns_results(mock_get, test_client: TestClient):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = [
        {
            'score': 0.9,
            'show': {
                'id': 44932,
                'name': 'Severance',
                'premiered': '2022-02-18',
                'status': 'Running',
                'externals': {'imdb': 'tt11280740'},
                'network': None,
                'webChannel': {'name': 'Apple TV+'},
                'image': {'medium': 'https://x/m.jpg', 'original': 'https://x/o.jpg'},
            },
        }
    ]
    mock_get.return_value = mock_response

    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.get('/v1/tv-shows/search?q=severance', headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]['tvmaze'] == 44932
    assert data[0]['imdb'] == 'tt11280740'
    assert data[0]['title'] == 'Severance'
    assert data[0]['year'] == '2022'
    assert data[0]['network'] == 'Apple TV+'
    assert data[0]['poster_url'] == 'https://x/o.jpg'


# --- Show trackers (Movies-parity lists) ---
def test_mark_first_show_to_rankings_auto_places_at_one(test_client: TestClient):
    """First show into an empty ranked list auto-places at #1 (#289)."""
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_rankings': True, 'notes': 'Peak TV'},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['on_watchlist'] is False
    assert data['rank'] == 1
    assert data['notes'] == 'Peak TV'


def test_mark_second_show_to_rankings_is_unplaced(test_client: TestClient):
    """Once a show is already ranked, the next one lands unplaced pending a duel."""
    first_id = _make_show(test_client)
    second_id = _make_show(test_client, title='Also Ranked')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    test_client.post(
        f"/v1/users/me/tv-shows/{first_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    response = test_client.post(
        f"/v1/users/me/tv-shows/{second_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['rank'] is None


def test_lists_are_exclusive(test_client: TestClient):
    """One-home rule (#145): joining Rankings leaves the Watchlist."""
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    # Add to watchlist only.
    r = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_watchlist': True},
    )
    assert r.json()['on_watchlist'] is True
    assert r.json()['on_rankings'] is False

    # Promote to rankings -> leaves the watchlist. It's the only ranked show,
    # so it auto-places at #1 (#289).
    r = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    assert r.json()['on_rankings'] is True
    assert r.json()['on_watchlist'] is False
    assert r.json()['rank'] == 1

    # Leave rankings -> on neither list, so the tracker is dropped entirely.
    test_client.put(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_rankings': False},
    )
    listing = test_client.get('/v1/users/me/tv-shows', headers=headers).json()
    assert all(t['tv_show']['id'] != show_id for t in listing)


def test_set_show_rank_inserts_and_shifts(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        sid = _make_show(test_client, title=f"Ranked {i}")
        test_client.post(
            f"/v1/users/me/tv-shows/{sid}",
            headers=headers,
            json={'on_rankings': True},
        )
        ids.append(sid)
    test_client.put(
        '/v1/users/me/tv-shows/rankings/order',
        headers=headers,
        json={'show_ids': ids},
    )

    new_id = _make_show(test_client, title='Inserted')
    test_client.post(
        f"/v1/users/me/tv-shows/{new_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    resp = test_client.put(
        f"/v1/users/me/tv-shows/{new_id}/rank",
        headers=headers,
        json={'position': 2},
    )
    assert resp.status_code == 200
    assert resp.json()['rank'] == 2

    listing = test_client.get('/v1/users/me/tv-shows', headers=headers).json()
    ranked = sorted(
        [t for t in listing if t['rank'] is not None], key=lambda t: t['rank']
    )
    order = [(t['rank'], t['tv_show']['id']) for t in ranked]
    assert order == [(1, ids[0]), (2, new_id), (3, ids[1]), (4, ids[2])]


def test_reorder_rankings(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        sid = _make_show(test_client, title=f"Show {i}")
        test_client.post(
            f"/v1/users/me/tv-shows/{sid}",
            headers=headers,
            json={'on_rankings': True},
        )
        ids.append(sid)

    reordered = list(reversed(ids))
    resp = test_client.put(
        '/v1/users/me/tv-shows/rankings/order',
        headers=headers,
        json={'show_ids': reordered},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [t['tv_show']['id'] for t in data] == reordered
    assert [t['rank'] for t in data] == [1, 2, 3]


def test_reentering_rankings_starts_unplaced(test_client: TestClient):
    """Re-adding a show to Rankings ignores any leftover rank (starts unplaced)."""
    other_id = _make_show(test_client, title='Stays Ranked')
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    # rank `other` first so the list isn't empty when `show_id` re-enters below.
    test_client.post(
        f"/v1/users/me/tv-shows/{other_id}", headers=headers, json={'on_rankings': True}
    )
    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}", headers=headers, json={'on_rankings': True}
    )
    test_client.put(
        f"/v1/users/me/tv-shows/{show_id}/rank", headers=headers, json={'position': 2}
    )
    test_client.put(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_watchlist': True, 'on_rankings': False},
    )
    r = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}", headers=headers, json={'on_rankings': True}
    )
    assert r.json()['on_rankings'] is True
    assert r.json()['rank'] is None


# --- Episodes ---
def test_create_episode(test_client: TestClient):
    show_id = _make_show(test_client)
    episode_id = _make_episode(test_client, show_id)
    assert episode_id

    listing = test_client.get(f"/v1/tv-shows/{show_id}/episodes")
    assert listing.status_code == 200
    data = listing.json()
    assert len(data) == 1
    assert data[0]['title'] == 'Pilot'
    assert data[0]['season'] == 1


@patch('app.services.tv_search.get_show_episodes')
def test_sync_episodes_upserts(mock_episodes, test_client: TestClient):
    admin_headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    with patch('app.router.v1.router_tv.get_tv_show_detail', return_value=None):
        show_id = _make_show(test_client, title='Severance', tvmaze=44932)
    mock_episodes.return_value = [
        {
            'tvmaze': 2128885,
            'title': 'Good News About Hell',
            'season': 1,
            'season_number': 1,
            'airdate': None,
        },
        {
            'tvmaze': 2128886,
            'title': 'Half Loop',
            'season': 1,
            'season_number': 2,
            'airdate': None,
        },
    ]
    resp = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes/sync", headers=admin_headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Re-sync is idempotent (upsert, not duplicate).
    resp = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes/sync", headers=admin_headers
    )
    assert len(resp.json()) == 2


def test_mark_and_unmark_episode_watched(test_client: TestClient):
    show_id = _make_show(test_client)
    episode_id = _make_episode(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    resp = test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=headers)
    assert resp.status_code == 201
    assert resp.json()['watched'] == 1

    # Idempotent re-mark.
    resp = test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=headers)
    assert resp.status_code == 201
    assert resp.json()['watched'] == 1

    listing = test_client.get(
        f"/v1/users/me/tv-shows/{show_id}/episodes", headers=headers
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    resp = test_client.delete(f"/v1/users/me/episodes/{episode_id}", headers=headers)
    assert resp.status_code == 204

    resp = test_client.delete(f"/v1/users/me/episodes/{episode_id}", headers=headers)
    assert resp.status_code == 404


def test_mark_all_episodes_watched(test_client: TestClient):
    show_id = _make_show(test_client)
    s1e1 = _make_episode(test_client, show_id, title='S1E1', season=1, number=1)
    s1e2 = _make_episode(test_client, show_id, title='S1E2', season=1, number=2)
    s2e1 = _make_episode(test_client, show_id, title='S2E1', season=2, number=1)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    resp = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}/episodes/watch-all", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert {e['episode']['id'] for e in body} == {s1e1, s1e2, s2e1}
    assert all(e['watched'] == 1 for e in body)


def test_mark_all_episodes_watched_by_season(test_client: TestClient):
    show_id = _make_show(test_client)
    s1e1 = _make_episode(test_client, show_id, title='S1E1', season=1, number=1)
    _make_episode(test_client, show_id, title='S2E1', season=2, number=1)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    resp = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}/episodes/watch-all?season=1",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]['episode']['id'] == s1e1

    listing = test_client.get(
        f"/v1/users/me/tv-shows/{show_id}/episodes", headers=headers
    )
    assert len(listing.json()) == 1


def test_mark_all_episodes_watched_is_idempotent(test_client: TestClient):
    show_id = _make_show(test_client)
    _make_episode(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}/episodes/watch-all", headers=headers
    )
    resp = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}/episodes/watch-all", headers=headers
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_mark_all_episodes_watched_no_match_404s(test_client: TestClient):
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    resp = test_client.post(
        f"/v1/users/me/tv-shows/{show_id}/episodes/watch-all?season=9",
        headers=headers,
    )
    assert resp.status_code == 404


# --- Schedule ---
def _iso(delta_days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=delta_days)).isoformat()


def test_schedule_splits_upcoming_and_catch_up(test_client: TestClient):
    show_id = _make_show(test_client, title='Watched Show')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_rankings': True},
    )

    upcoming_ep = _make_episode(
        test_client, show_id, title='Airs in 2 days', airdate=_iso(2)
    )
    overdue_ep = _make_episode(
        test_client,
        show_id,
        title='Aired yesterday',
        season=1,
        number=2,
        airdate=_iso(-1),
    )
    far_past_ep = _make_episode(
        test_client,
        show_id,
        title='Aired long ago',
        season=1,
        number=3,
        airdate=_iso(-30),
    )

    resp = test_client.get('/v1/users/me/schedule', headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    upcoming_ids = {e['episode_id'] for e in body['upcoming']}
    catch_up_ids = {e['episode_id'] for e in body['catch_up']}

    assert upcoming_ids == {upcoming_ep, overdue_ep}
    assert catch_up_ids == {overdue_ep, far_past_ep}
    assert body['frozen_shows'] == []


def test_schedule_excludes_watched_episodes(test_client: TestClient):
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_watchlist': True},
    )
    episode_id = _make_episode(test_client, show_id, airdate=_iso(-1))
    test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=headers)

    resp = test_client.get('/v1/users/me/schedule', headers=headers)
    body = resp.json()
    assert body['upcoming'] == []
    assert body['catch_up'] == []


def test_schedule_excludes_frozen_shows(test_client: TestClient):
    show_id = _make_show(test_client, title='Paused Show')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    _make_episode(test_client, show_id, airdate=_iso(-1))
    test_client.put(
        f"/v1/users/me/tv-shows/{show_id}", headers=headers, json={'freeze': 1}
    )

    resp = test_client.get('/v1/users/me/schedule', headers=headers)
    body = resp.json()
    assert body['upcoming'] == []
    assert body['catch_up'] == []
    assert body['frozen_shows'] == [{'show_id': show_id, 'show_title': 'Paused Show'}]


def test_schedule_ignores_untracked_shows(test_client: TestClient):
    show_id = _make_show(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    _make_episode(test_client, show_id, airdate=_iso(-1))

    resp = test_client.get('/v1/users/me/schedule', headers=headers)
    body = resp.json()
    assert body['upcoming'] == []
    assert body['catch_up'] == []
    assert body['frozen_shows'] == []


def test_user_episode_marks_are_per_user(test_client: TestClient):
    show_id = _make_show(test_client)
    episode_id = _make_episode(test_client, show_id)
    first = {'Authorization': f"Bearer {test_client.first_user.token}"}
    second = {'Authorization': f"Bearer {test_client.second_user.token}"}

    test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=first)
    listing = test_client.get(
        f"/v1/users/me/tv-shows/{show_id}/episodes", headers=second
    )
    assert listing.json() == []


# --- Watch status badges ---
def _track(test_client: TestClient, show_id: str) -> None:
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}", headers=headers, json={'on_watchlist': True}
    )


def _my_shows(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    return test_client.get('/v1/users/me/tv-shows', headers=headers).json()


def test_watch_status_not_started(test_client: TestClient):
    show_id = _make_show(test_client)
    _make_episode(test_client, show_id, airdate=_iso(-30))
    _track(test_client, show_id)

    (item,) = _my_shows(test_client)
    assert item['watch_status'] == 'not_started'
    assert item['aired_count'] == 1
    assert item['watched_count'] == 0


def test_watch_status_behind(test_client: TestClient):
    show_id = _make_show(test_client)
    first = _make_episode(test_client, show_id, title='E1', airdate=_iso(-30))
    _make_episode(test_client, show_id, title='E2', number=2, airdate=_iso(-7))
    _track(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(f"/v1/users/me/episodes/{first}", headers=headers)

    (item,) = _my_shows(test_client)
    assert item['watch_status'] == 'behind'
    assert item['aired_count'] == 2
    assert item['watched_count'] == 1


def test_watch_status_up_to_date_ignores_unaired(test_client: TestClient):
    show_id = _make_show(test_client)
    aired = _make_episode(test_client, show_id, title='E1', airdate=_iso(-7))
    _make_episode(test_client, show_id, title='E2', number=2, airdate=_iso(30))
    _track(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(f"/v1/users/me/episodes/{aired}", headers=headers)

    (item,) = _my_shows(test_client)
    assert item['watch_status'] == 'up_to_date'
    assert item['aired_count'] == 1


def test_watch_status_complete_when_ended(test_client: TestClient):
    show_id = _make_show(test_client, status='Ended')
    episode = _make_episode(test_client, show_id, airdate=_iso(-30))
    _track(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(f"/v1/users/me/episodes/{episode}", headers=headers)

    (item,) = _my_shows(test_client)
    assert item['watch_status'] == 'complete'


def test_removing_ranked_show_closes_the_gap(test_client: TestClient):
    """Removing #1 shifts every show below it up one slot."""
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i, title in enumerate(['First', 'Second', 'Third'], start=1):
        show_id = _make_show(test_client, title=title)
        ids.append(show_id)
        test_client.post(
            f"/v1/users/me/tv-shows/{show_id}",
            headers=headers,
            json={'on_rankings': True},
        )
        test_client.put(
            f"/v1/users/me/tv-shows/{show_id}/rank",
            headers=headers,
            json={'position': i},
        )

    # Remove #1 the way the rankings board does: leave the ranked list.
    resp = test_client.put(
        f"/v1/users/me/tv-shows/{ids[0]}", headers=headers, json={'on_rankings': False}
    )
    assert resp.status_code == 200

    remaining = {
        item['tv_show']['title']: item['rank']
        for item in _my_shows(test_client)
        if item['on_rankings']
    }
    assert remaining == {'Second': 1, 'Third': 2}


def test_deleting_ranked_tracker_closes_the_gap(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i, title in enumerate(['First', 'Second', 'Third'], start=1):
        show_id = _make_show(test_client, title=title)
        ids.append(show_id)
        test_client.post(
            f"/v1/users/me/tv-shows/{show_id}",
            headers=headers,
            json={'on_rankings': True},
        )
        test_client.put(
            f"/v1/users/me/tv-shows/{show_id}/rank",
            headers=headers,
            json={'position': i},
        )

    resp = test_client.delete(f"/v1/users/me/tv-shows/{ids[1]}", headers=headers)
    assert resp.status_code == 204

    remaining = {
        item['tv_show']['title']: item['rank']
        for item in _my_shows(test_client)
        if item['on_rankings']
    }
    assert remaining == {'First': 1, 'Third': 2}


# --- Watch providers (web#26) ---
@patch('app.services.tmdb.try_request')
def test_tv_watch_providers_resolves_via_imdb(mock_request, test_client: TestClient):
    watch_providers.reset_cache()
    # Shows carry no TMDB id, so /find maps the IMDb id first.
    mock_request.side_effect = [
        {'tv_results': [{'id': 1396}]},
        {
            'results': {
                'US': {
                    'link': 'https://www.themoviedb.org/tv/1396/watch?locale=US',
                    'flatrate': [
                        {
                            'provider_id': 8,
                            'provider_name': 'Netflix',
                            'logo_path': '/netflix.jpg',
                            'display_priority': 0,
                        }
                    ],
                }
            }
        },
    ]
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    show_id = _make_show(test_client, imdb='tt0903747')

    response = test_client.get(
        f"/v1/tv-shows/{show_id}/watch-providers", headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data['attribution'] == 'JustWatch'
    assert data['stream'] == [
        {
            'provider_id': 8,
            'name': 'Netflix',
            'logo_url': 'https://image.tmdb.org/t/p/w92/netflix.jpg',
        }
    ]


@patch('app.services.tmdb.try_request')
def test_tv_watch_providers_without_imdb_id(mock_request, test_client: TestClient):
    watch_providers.reset_cache()
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    show_id = _make_show(test_client, title='Unmatched Show')

    response = test_client.get(
        f"/v1/tv-shows/{show_id}/watch-providers", headers=headers
    )

    assert response.status_code == 200
    assert response.json()['stream'] == []
    mock_request.assert_not_called()


def test_tv_watch_providers_unknown_show_404s(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    response = test_client.get('/v1/tv-shows/nope/watch-providers', headers=headers)
    assert response.status_code == 404


# --- Schedule window follows the caller's time zone ---
# Kiritimati is UTC+14 and Midway is UTC-11: 25 hours apart, so their local
# calendar dates are *always* a day apart, whatever the suite happens to run
# at. That is what makes the assertions below deterministic instead of
# passing only during part of the day.
_AHEAD = 'Pacific/Kiritimati'
_BEHIND = 'Pacific/Midway'


def _set_time_zone(test_client: TestClient, headers: dict, zone: str) -> None:
    resp = test_client.put(
        '/v1/users/me/preferences', headers=headers, json={'time_zone': zone}
    )
    assert resp.status_code == 200, resp.text


def _local_today(zone: str) -> date:
    return datetime.now(ZoneInfo(zone)).date()


def test_schedule_window_turns_over_at_the_users_midnight(test_client: TestClient):
    """
    One episode, one instant, two zones -- and it is only in one window.

    The episode airs five days after *Kiritimati's* today, which is the last
    day that zone's +/-5 window reaches. Midway is a calendar day behind, so
    the same date is six days out from its today and falls outside. A window
    anchored on the server's clock puts the episode in both, or neither.
    """
    show_id = _make_show(test_client, title='Timezone Show')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/tv-shows/{show_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    edge = _local_today(_AHEAD) + timedelta(days=5)
    episode_id = _make_episode(
        test_client, show_id, title='Edge of the window', airdate=f"{edge}T00:00:00"
    )

    _set_time_zone(test_client, headers, _AHEAD)
    ahead = test_client.get('/v1/users/me/schedule', headers=headers).json()

    _set_time_zone(test_client, headers, _BEHIND)
    behind = test_client.get('/v1/users/me/schedule', headers=headers).json()

    assert episode_id in {e['episode_id'] for e in ahead['upcoming']}
    assert episode_id not in {e['episode_id'] for e in behind['upcoming']}


def test_schedule_zone_is_per_user(test_client: TestClient):
    """A second user's window is unaffected by the first user's choice."""
    show_id = _make_show(test_client, title='Shared Show')
    first = {'Authorization': f"Bearer {test_client.first_user.token}"}
    second = {'Authorization': f"Bearer {test_client.second_user.token}"}
    for headers in (first, second):
        test_client.post(
            f"/v1/users/me/tv-shows/{show_id}",
            headers=headers,
            json={'on_rankings': True},
        )
    edge = _local_today(_AHEAD) + timedelta(days=5)
    episode_id = _make_episode(
        test_client, show_id, title='Shared edge', airdate=f"{edge}T00:00:00"
    )

    _set_time_zone(test_client, first, _AHEAD)
    _set_time_zone(test_client, second, _BEHIND)

    ahead = test_client.get('/v1/users/me/schedule', headers=first).json()
    behind = test_client.get('/v1/users/me/schedule', headers=second).json()

    assert episode_id in {e['episode_id'] for e in ahead['upcoming']}
    assert episode_id not in {e['episode_id'] for e in behind['upcoming']}
