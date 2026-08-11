# pylint: disable=missing-module-docstring, missing-function-docstring
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.db.models import DbFollow, DbFriendship
from app.db.models_sandbox import DbUserMovie
from app.services.friendships import FriendshipStatus, canonical_pair


def _make_movie(test_client: TestClient, imdb='tt1375666', title='Inception') -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/movies', headers=headers, json={'title': title, 'imdb': imdb}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_show(test_client: TestClient, title='Breaking Bad', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/tv-shows', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_episode(test_client: TestClient, show_id: str, title='Pilot', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes",
        headers=headers,
        json={'title': title, 'season': 1, 'season_number': 1, **extra},
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_game(test_client: TestClient, title='Breath of the Wild', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/games', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_book(test_client: TestClient, title='Dune', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/books', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _friend_users(test_client: TestClient, accepted=True) -> None:
    viewer_pk = test_client.first_user.pk
    owner_pk = test_client.second_user.pk
    low, high = canonical_pair(viewer_pk, owner_pk)
    test_client.test_db_session.add(
        DbFriendship(
            user_low_id=low,
            user_high_id=high,
            requested_by_id=viewer_pk,
            status=(
                FriendshipStatus.ACCEPTED if accepted else FriendshipStatus.PENDING
            ),
            requested_at=datetime.now(timezone.utc),
            responded_at=datetime.now(timezone.utc) if accepted else None,
        )
    )
    test_client.test_db_session.commit()


def _follow_user(test_client: TestClient) -> None:
    test_client.test_db_session.add(
        DbFollow(
            follower_id=test_client.first_user.pk,
            followee_id=test_client.second_user.pk,
            followed_at=datetime.now(timezone.utc),
        )
    )
    test_client.test_db_session.commit()


def _claim_handle(test_client: TestClient, token: str) -> None:
    response = test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'blake'}
    )
    assert response.status_code == 200, response.text


def _track(
    test_client: TestClient,
    token: str,
    path: str,
    entity_id: str,
    action: str,
) -> None:
    payload = {'on_rankings': True} if action == 'ranked' else {'on_watchlist': True}
    response = test_client.post(
        f'/v1/users/me/{path}/{entity_id}', headers=_auth(token), json=payload
    )
    assert response.status_code in (200, 201), response.text
    if action == 'ranked' and response.json()['rank'] is None:
        ranked = test_client.put(
            f'/v1/users/me/{path}/{entity_id}/rank',
            headers=_auth(token),
            json={'position': 1},
        )
        assert ranked.status_code == 200, ranked.text


# --- Activity Log ---
def test_activity_includes_ranked_movie(test_client: TestClient):
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_rankings': True}
    )
    test_client.put(
        f"/v1/users/me/movies/{movie_id}/rank", headers=headers, json={'position': 1}
    )

    resp = test_client.get('/v1/users/me/activity', headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    movie_items = [i for i in items if i['category'] == 'movie']
    assert len(movie_items) == 1
    assert movie_items[0]['action'] == 'ranked'
    assert movie_items[0]['rank'] == 1
    assert movie_items[0]['title'] == 'Inception'


def test_activity_watchlist_add_not_ranked(test_client: TestClient):
    book_id = _make_book(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/books/{book_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get('/v1/users/me/activity', headers=headers)
    items = resp.json()
    book_items = [i for i in items if i['category'] == 'book']
    assert len(book_items) == 1
    assert book_items[0]['action'] == 'watchlist_added'
    assert book_items[0]['rank'] is None


def test_activity_includes_watched_episode(test_client: TestClient):
    show_id = _make_show(test_client)
    episode_id = _make_episode(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=headers)

    resp = test_client.get('/v1/users/me/activity', headers=headers)
    items = resp.json()
    ep_items = [i for i in items if i['category'] == 'tv_episode']
    assert len(ep_items) == 1
    assert ep_items[0]['action'] == 'watched_episode'
    assert ep_items[0]['title'] == 'Breaking Bad'
    assert 'S1E1' in ep_items[0]['subtitle']
    assert ep_items[0]['entity_id'] == show_id


def test_activity_filters_by_category(test_client: TestClient):
    movie_id = _make_movie(test_client)
    game_id = _make_game(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_watchlist': True}
    )
    test_client.post(
        f"/v1/users/me/games/{game_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get(
        '/v1/users/me/activity', headers=headers, params={'category': 'game'}
    )
    items = resp.json()
    assert len(items) == 1
    assert items[0]['category'] == 'game'


def test_activity_untracked_items_excluded(test_client: TestClient):
    _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/users/me/activity', headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_activity_requires_auth(test_client: TestClient):
    resp = test_client.get('/v1/users/me/activity')
    assert resp.status_code == 401


# --- Friends and follows feed ---
def test_social_feed_includes_friend_rankings_and_watchlist_adds(
    test_client: TestClient,
):
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    movie_id = _make_movie(test_client, title='Heat', imdb='tt0113277')
    book_id = _make_book(test_client, title='Dune')
    _track(test_client, owner_token, 'movies', movie_id, 'ranked')
    _track(test_client, owner_token, 'books', book_id, 'watchlist_added')

    response = test_client.get(
        '/v1/users/me/feed', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 200, response.text
    items = response.json()['items']
    assert {(item['title'], item['action']) for item in items} == {
        ('Heat', 'ranked'),
        ('Dune', 'watchlist_added'),
    }
    assert {item['actor']['id'] for item in items} == {test_client.second_user.id}
    assert next(item for item in items if item['title'] == 'Heat')['rank'] == 1


def test_follow_only_receives_public_shelf_activity(test_client: TestClient):
    _follow_user(test_client)
    owner_token = test_client.second_user.token
    visibility = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={
            'handle': 'blake',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
        },
    )
    assert visibility.status_code == 200, visibility.text
    movie_id = _make_movie(test_client, title='Arrival', imdb='tt2543164')
    book_id = _make_book(test_client, title='The Dispossessed')
    _track(test_client, owner_token, 'movies', movie_id, 'ranked')
    _track(test_client, owner_token, 'books', book_id, 'watchlist_added')

    items = test_client.get(
        '/v1/users/me/feed', headers=_auth(test_client.first_user.token)
    ).json()['items']
    assert [(item['title'], item['category']) for item in items] == [
        ('Arrival', 'movie')
    ]


def test_pending_friendship_does_not_contribute_activity(test_client: TestClient):
    _friend_users(test_client, accepted=False)
    movie_id = _make_movie(test_client)
    _track(
        test_client,
        test_client.second_user.token,
        'movies',
        movie_id,
        'ranked',
    )

    response = test_client.get(
        '/v1/users/me/feed', headers=_auth(test_client.first_user.token)
    )
    assert response.json() == {'items': [], 'next_cursor': None}


def test_lowering_current_shelf_tier_retroactively_removes_activity(
    test_client: TestClient,
):
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    _claim_handle(test_client, owner_token)
    movie_id = _make_movie(test_client)
    _track(test_client, owner_token, 'movies', movie_id, 'ranked')
    viewer_headers = _auth(test_client.first_user.token)
    assert [
        item['title']
        for item in test_client.get('/v1/users/me/feed', headers=viewer_headers).json()[
            'items'
        ]
    ] == ['Inception']

    lowered = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={'visibility_movies': 'private'},
    )
    assert lowered.status_code == 200, lowered.text
    assert test_client.get('/v1/users/me/feed', headers=viewer_headers).json() == {
        'items': [],
        'next_cursor': None,
    }


def test_feed_resolves_a_shelf_with_no_override_against_the_default(
    test_client: TestClient,
):
    """
    A shelf with no override inherits ``default_privacy`` (api#298), so the
    feed has to resolve inheritance the way the profile reads do. Comparing
    the raw column instead drops every inherited shelf out of every feed,
    because ``NULL = 'friends'`` is null rather than false.
    """
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    _claim_handle(test_client, owner_token)
    movie_id = _make_movie(test_client)
    _track(test_client, owner_token, 'movies', movie_id, 'ranked')
    viewer_headers = _auth(test_client.first_user.token)

    inherited = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={
            'default_privacy': 'friends',
            'visibility_profile': 'friends',
            'visibility_movies': None,
        },
    )
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()['visibility_movies'] is None
    assert [
        item['title']
        for item in test_client.get('/v1/users/me/feed', headers=viewer_headers).json()[
            'items'
        ]
    ] == ['Inception']

    # Closing the global default has to close the inherited shelf with it.
    closed = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={'default_privacy': 'private'},
    )
    assert closed.status_code == 200, closed.text
    assert test_client.get('/v1/users/me/feed', headers=viewer_headers).json() == {
        'items': [],
        'next_cursor': None,
    }


def test_watchlist_event_requires_current_ranked_and_watchlist_tiers(
    test_client: TestClient,
):
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    _claim_handle(test_client, owner_token)
    movie_id = _make_movie(test_client)
    _track(test_client, owner_token, 'movies', movie_id, 'watchlist_added')
    viewer_headers = _auth(test_client.first_user.token)
    assert (
        len(
            test_client.get('/v1/users/me/feed', headers=viewer_headers).json()['items']
        )
        == 1
    )

    lowered = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={'visibility_watchlist_movies': 'private'},
    )
    assert lowered.status_code == 200, lowered.text
    assert (
        test_client.get('/v1/users/me/feed', headers=viewer_headers).json()['items']
        == []
    )


def test_owner_can_opt_out_and_back_in_of_activity_sharing(
    test_client: TestClient,
):
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    movie_id = _make_movie(test_client)
    _track(test_client, owner_token, 'movies', movie_id, 'ranked')
    viewer_headers = _auth(test_client.first_user.token)
    defaults = test_client.get(
        '/v1/users/me/visibility', headers=_auth(owner_token)
    ).json()
    assert defaults['share_activity'] is True

    opted_out = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={'share_activity': False},
    )
    assert opted_out.status_code == 200, opted_out.text
    assert opted_out.json()['share_activity'] is False
    assert (
        test_client.get('/v1/users/me/feed', headers=viewer_headers).json()['items']
        == []
    )

    opted_in = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(owner_token),
        json={'share_activity': True},
    )
    assert opted_in.json()['share_activity'] is True
    assert [
        item['title']
        for item in test_client.get('/v1/users/me/feed', headers=viewer_headers).json()[
            'items'
        ]
    ] == ['Inception']


def test_social_feed_uses_all_four_domain_shelves(test_client: TestClient):
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    entities = (
        ('movies', _make_movie(test_client), 'ranked'),
        ('tv-shows', _make_show(test_client), 'watchlist_added'),
        ('games', _make_game(test_client), 'ranked'),
        ('books', _make_book(test_client), 'watchlist_added'),
    )
    for path, entity_id, action in entities:
        _track(test_client, owner_token, path, entity_id, action)

    items = test_client.get(
        '/v1/users/me/feed', headers=_auth(test_client.first_user.token)
    ).json()['items']
    assert {(item['category'], item['action']) for item in items} == {
        ('movie', 'ranked'),
        ('tv_show', 'watchlist_added'),
        ('game', 'ranked'),
        ('book', 'watchlist_added'),
    }


def test_friendship_wins_when_the_same_owner_is_also_followed(
    test_client: TestClient,
):
    _friend_users(test_client)
    _follow_user(test_client)
    movie_id = _make_movie(test_client)
    _track(
        test_client,
        test_client.second_user.token,
        'movies',
        movie_id,
        'ranked',
    )

    items = test_client.get(
        '/v1/users/me/feed', headers=_auth(test_client.first_user.token)
    ).json()['items']
    assert [(item['title'], item['action']) for item in items] == [
        ('Inception', 'ranked')
    ]


def test_social_feed_keyset_pages_without_duplicates(test_client: TestClient):
    _friend_users(test_client)
    owner_token = test_client.second_user.token
    for index in range(3):
        movie_id = _make_movie(
            test_client,
            title=f'Movie {index}',
            imdb=f'tt900000{index}',
        )
        _track(test_client, owner_token, 'movies', movie_id, 'watchlist_added')

    # Exercise the complete keyset, not just its timestamp: simultaneous
    # events in one shelf must page by tracker pk without skipping or repeats.
    tied_at = datetime(2026, 8, 11, 12, 0, 0)
    trackers = (
        test_client.test_db_session.query(DbUserMovie)
        .filter(DbUserMovie.user_id == test_client.second_user.pk)
        .all()
    )
    for tracker in trackers:
        tracker.created_at = tied_at
        tracker.updated_at = tied_at
    test_client.test_db_session.commit()

    first = test_client.get(
        '/v1/users/me/feed',
        headers=_auth(test_client.first_user.token),
        params={'limit': 2},
    )
    assert first.status_code == 200, first.text
    assert len(first.json()['items']) == 2
    assert first.json()['next_cursor']

    second = test_client.get(
        '/v1/users/me/feed',
        headers=_auth(test_client.first_user.token),
        params={'limit': 2, 'cursor': first.json()['next_cursor']},
    )
    assert second.status_code == 200, second.text
    assert len(second.json()['items']) == 1
    assert second.json()['next_cursor'] is None
    titles = [item['title'] for item in first.json()['items'] + second.json()['items']]
    assert len(titles) == len(set(titles)) == 3


def test_social_feed_is_sql_bounded_without_relationship_n_plus_one(
    test_client: TestClient,
):
    _friend_users(test_client)
    movie_id = _make_movie(test_client)
    _track(
        test_client,
        test_client.second_user.token,
        'movies',
        movie_id,
        'ranked',
    )
    statements = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = test_client.test_db_session.get_bind()
    event.listen(engine, 'before_cursor_execute', record_statement)
    try:
        response = test_client.get(
            '/v1/users/me/feed', headers=_auth(test_client.first_user.token)
        )
    finally:
        event.remove(engine, 'before_cursor_execute', record_statement)

    assert response.status_code == 200, response.text
    feed_queries = [statement for statement in statements if 'UNION ALL' in statement]
    assert len(feed_queries) == 4
    assert all(' LIMIT ' in statement for statement in feed_queries)


def test_social_feed_rejects_invalid_or_unbounded_pages(test_client: TestClient):
    headers = _auth(test_client.first_user.token)
    invalid_cursor = test_client.get(
        '/v1/users/me/feed', headers=headers, params={'cursor': 'not-a-cursor'}
    )
    assert invalid_cursor.status_code == 422
    assert 'Invalid activity feed cursor' in invalid_cursor.text
    assert (
        test_client.get(
            '/v1/users/me/feed', headers=headers, params={'limit': 101}
        ).status_code
        == 422
    )


def test_social_feed_requires_auth(test_client: TestClient):
    assert test_client.get('/v1/users/me/feed').status_code == 401


# --- Bored ---
def test_bored_picks_from_watchlists(test_client: TestClient):
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get('/v1/users/me/bored', headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['pool_size'] == 1
    assert data['pick']['category'] == 'movie'
    assert data['pick']['entity_id'] == movie_id


def test_bored_excludes_ranked_or_completed_items(test_client: TestClient):
    """Only items still on a watchlist are candidates — ranked ones are done."""
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_rankings': True}
    )

    resp = test_client.get('/v1/users/me/bored', headers=headers)
    assert resp.status_code == 404


def test_bored_404_when_nothing_tracked(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/users/me/bored', headers=headers)
    assert resp.status_code == 404


def test_bored_exclude_param_avoids_repeat(test_client: TestClient):
    movie_id = _make_movie(test_client)
    book_id = _make_book(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_watchlist': True}
    )
    test_client.post(
        f"/v1/users/me/books/{book_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get(
        '/v1/users/me/bored', headers=headers, params={'exclude': movie_id}
    )
    assert resp.status_code == 200
    assert resp.json()['pick']['entity_id'] == book_id


def test_bored_requires_auth(test_client: TestClient):
    resp = test_client.get('/v1/users/me/bored')
    assert resp.status_code == 401
