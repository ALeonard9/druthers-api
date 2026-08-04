# pylint: disable=missing-module-docstring, missing-function-docstring
import uuid

from fastapi.testclient import TestClient

TIER_FIELDS = (
    'visibility_profile',
    'visibility_movies',
    'visibility_tv',
    'visibility_books',
    'visibility_games',
    'visibility_watchlist_movies',
    'visibility_watchlist_tv',
    'visibility_watchlist_books',
    'visibility_watchlist_games',
)


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _rank_a_movie(test_client: TestClient, token: str, title='Heat', imdb='tt0113277'):
    admin = _auth(test_client.admin_user.token)
    movie_id = test_client.post(
        '/v1/movies', headers=admin, json={'title': title, 'imdb': imdb, 'year': 1995}
    ).json()['id']
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_rankings': True, 'notes': 'private note!'},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(token),
        json={'position': 1},
    )


def _watchlist_a_movie(
    test_client: TestClient, token: str, title='Sicario', imdb='tt3397884'
):
    admin = _auth(test_client.admin_user.token)
    movie_id = test_client.post(
        '/v1/movies', headers=admin, json={'title': title, 'imdb': imdb, 'year': 2015}
    ).json()['id']
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_watchlist': True, 'notes': 'private note!'},
    )


def test_defaults_are_fully_private(test_client: TestClient):
    body = test_client.get(
        '/v1/users/me/visibility', headers=_auth(test_client.first_user.token)
    ).json()
    assert body['handle'] is None
    assert [body[field] for field in TIER_FIELDS] == ['private'] * len(TIER_FIELDS)


def test_leaving_private_requires_a_handle(test_client: TestClient):
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'visibility_profile': 'public', 'visibility_movies': 'public'},
    )
    assert response.status_code == 422
    assert 'handle' in response.json()['message'].lower()


def test_friends_also_requires_a_handle(test_client: TestClient):
    # friends is inert but it is still not private, so the handle rule holds.
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'visibility_profile': 'friends'},
    )
    assert response.status_code == 422
    assert 'handle' in response.json()['message'].lower()


def test_handle_validation_and_uniqueness(test_client: TestClient):
    token = test_client.first_user.token
    assert (
        test_client.put(
            '/v1/users/me/visibility',
            headers=_auth(token),
            json={'handle': 'No Spaces!'},
        ).status_code
        == 422
    )
    assert (
        test_client.put(
            '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'settings'}
        ).status_code
        == 409
    )
    assert (
        test_client.put(
            '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'Avery'}
        ).json()['handle']
        == 'avery'
    )
    # Second user can't take it
    assert (
        test_client.put(
            '/v1/users/me/visibility',
            headers=_auth(test_client.second_user.token),
            json={'handle': 'avery'},
        ).status_code
        == 409
    )


def test_clearing_a_handle_only_allowed_while_fully_private(test_client: TestClient):
    token = test_client.first_user.token
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
        },
    )

    blocked = test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': None}
    )
    assert blocked.status_code == 422
    assert 'handle' in blocked.json()['message'].lower()
    # Rejected updates commit nothing.
    assert (
        test_client.get('/v1/users/me/visibility', headers=_auth(token)).json()[
            'handle'
        ]
        == 'avery'
    )

    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_profile': 'private', 'visibility_movies': 'private'},
    )
    cleared = test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': None}
    )
    assert cleared.status_code == 200
    assert cleared.json()['handle'] is None


def test_profile_must_be_at_least_as_open_as_its_shelves(test_client: TestClient):
    token = test_client.first_user.token
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'friends',
            'visibility_movies': 'public',
        },
    )
    assert response.status_code == 422
    message = response.json()['message']
    assert 'Movies' in message
    assert 'public' in message


def test_the_offending_shelf_is_named_for_watchlists_too(test_client: TestClient):
    token = test_client.first_user.token
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'private',
            'visibility_watchlist_games': 'friends',
        },
    )
    assert response.status_code == 422
    assert 'Video Games watchlist' in response.json()['message']


def test_lowering_the_profile_under_a_live_shelf_is_rejected(test_client: TestClient):
    token = test_client.first_user.token
    assert (
        test_client.put(
            '/v1/users/me/visibility',
            headers=_auth(token),
            json={
                'handle': 'avery',
                'visibility_profile': 'public',
                'visibility_books': 'public',
            },
        ).status_code
        == 200
    )
    # The other direction: the shelf stays put and the profile drops under it.
    lowered = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_profile': 'friends'},
    )
    assert lowered.status_code == 422
    assert 'Books' in lowered.json()['message']
    body = test_client.get('/v1/users/me/visibility', headers=_auth(token)).json()
    assert body['visibility_profile'] == 'public'


def test_profile_may_be_more_open_than_every_shelf(test_client: TestClient):
    token = test_client.first_user.token
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_movies': 'friends',
            'visibility_tv': 'private',
        },
    )
    assert response.status_code == 200
    assert response.json()['visibility_profile'] == 'public'


def test_unknown_tiers_are_rejected(test_client: TestClient):
    # A stray value must fail loudly rather than resolve to something open.
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'handle': 'avery', 'visibility_movies': 'everyone'},
    )
    assert response.status_code == 422


def test_partial_updates_only_touch_sent_fields(test_client: TestClient):
    token = test_client.first_user.token
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
            'visibility_books': 'friends',
        },
    )
    body = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_tv': 'friends'},
    ).json()
    assert body['handle'] == 'avery'
    assert body['visibility_movies'] == 'public'
    assert body['visibility_books'] == 'friends'
    assert body['visibility_tv'] == 'friends'
    assert body['visibility_games'] == 'private'


def test_public_profile_exposes_only_public_ranked_lists(test_client: TestClient):
    token = test_client.first_user.token
    _rank_a_movie(test_client, token)
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
        },
    )

    body = test_client.get('/v1/public/avery').json()
    assert body['handle'] == 'avery'
    assert [s['category'] for s in body['shelves']] == ['Movies']
    item = body['shelves'][0]['items'][0]
    item_id = item.pop('id')
    uuid.UUID(item_id)  # validates it's a real UUID
    assert item == {
        'rank': 1,
        'title': 'Heat',
        'year': 1995,
        'poster_url': None,
    }
    # Nothing private leaks anywhere in the payload
    flat = str(body)
    assert 'private note!' not in flat
    assert 'on_watchlist' not in flat
    assert test_client.first_user.email not in flat


def test_private_and_unknown_profiles_404_identically(test_client: TestClient):
    token = test_client.second_user.token
    test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'ghost'}
    )
    private = test_client.get('/v1/public/ghost')
    unknown = test_client.get('/v1/public/nobody-here')
    assert private.status_code == unknown.status_code == 404
    assert private.json() == unknown.json()


def test_friends_tier_is_invisible_to_the_public(test_client: TestClient):
    # An anonymous caller is served ``public`` and nothing else, before and
    # after #277 — the viewer-aware matrix lives in
    # tests/integration/router_visibility_viewer_test.py.
    token = test_client.first_user.token
    _rank_a_movie(test_client, token, title='Collateral', imdb='tt0369339')
    assert (
        test_client.put(
            '/v1/users/me/visibility',
            headers=_auth(token),
            json={
                'handle': 'avery',
                'visibility_profile': 'friends',
                'visibility_movies': 'friends',
            },
        ).status_code
        == 200
    )
    assert test_client.get('/v1/public/avery').status_code == 404


def test_toggling_a_category_off_removes_it(test_client: TestClient):
    token = test_client.first_user.token
    _rank_a_movie(test_client, token, title='Ronin', imdb='tt0122690')
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
        },
    )
    assert test_client.get('/v1/public/avery').status_code == 200
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_movies': 'private'},
    )
    assert test_client.get('/v1/public/avery').status_code == 404


def test_watchlist_tier_alone_exposes_nothing(test_client: TestClient):
    # Watchlist visibility (#236) is independent of the ranked-list tier to
    # set, but only takes effect once the ranked-list tier is also public.
    token = test_client.first_user.token
    _rank_a_movie(test_client, token)
    _watchlist_a_movie(test_client, token)
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_watchlist_movies': 'public',
        },
    )

    assert test_client.get('/v1/public/avery').status_code == 404


def test_watchlist_shown_only_when_both_tiers_public(test_client: TestClient):
    token = test_client.first_user.token
    _rank_a_movie(test_client, token)
    _watchlist_a_movie(test_client, token)
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
        },
    )

    # Ranked-list tier alone: no watchlist key at all.
    shelf = test_client.get('/v1/public/avery').json()['shelves'][0]
    assert 'watchlist' not in shelf

    # friends on the watchlist still isn't public.
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_watchlist_movies': 'friends'},
    )
    assert 'watchlist' not in test_client.get('/v1/public/avery').json()['shelves'][0]

    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_watchlist_movies': 'public'},
    )

    shelf = test_client.get('/v1/public/avery').json()['shelves'][0]
    wl_item = shelf['watchlist'][0]
    wl_id = wl_item.pop('id')
    uuid.UUID(wl_id)  # validates it's a real UUID
    assert shelf['watchlist'] == [
        {'title': 'Sicario', 'year': 2015, 'poster_url': None}
    ]
    # Redacted the same way ranked items are: no notes, no watch state.
    flat = str(shelf['watchlist'])
    assert 'private note!' not in flat
    assert 'on_watchlist' not in flat
