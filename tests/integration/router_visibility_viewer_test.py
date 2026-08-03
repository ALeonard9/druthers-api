# pylint: disable=missing-function-docstring
"""
Viewer-aware public profiles (#277).

The authorization surface of the sharing epic, so the assertions are written
as leak tests rather than feature tests: for every (viewer, tier) pair in the
matrix the question is what the caller must *not* receive, and whether a
profile they may not see is distinguishable from one that does not exist.

The fixture below is one profile with all four shelves set to different tiers,
so a single setup answers the whole matrix and no test can accidentally check
a tier the others never exercise.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.oauth2 import create_access_token

HANDLE = 'avery'

# Ranked-list tier / watchlist tier per shelf. Deliberately mismatched: the
# watchlist tier is evaluated on its own, but only for a shelf the viewer can
# already see.
TIERS = {
    'handle': HANDLE,
    'visibility_profile': 'public',
    'visibility_movies': 'public',
    'visibility_watchlist_movies': 'public',
    'visibility_tv': 'friends',
    'visibility_watchlist_tv': 'friends',
    'visibility_books': 'public',
    'visibility_watchlist_books': 'friends',
    'visibility_games': 'private',
    'visibility_watchlist_games': 'public',
}


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _catalog(test_client: TestClient, path: str, payload: dict) -> str:
    response = test_client.post(
        f'/v1/{path}', headers=_auth(test_client.admin_user.token), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()['id']


def _stock_every_shelf(test_client: TestClient, token: str) -> None:
    """One ranked item and one watchlist item in each of the four shelves."""
    shelves = (
        (
            'movies',
            'movies',
            {'imdb': 'tt0113277', 'year': 1995},
            {'imdb': 'tt3397884'},
        ),
        ('tv-shows', 'tv-shows', {'imdb': 'tt0903747'}, {'imdb': 'tt0417299'}),
        ('books', 'books', {'isbn': '9780441172719'}, {'isbn': '9780553293357'}),
        ('games', 'games', {'igdb': 1111}, {'igdb': 2222}),
    )
    for catalog_path, tracker_path, ranked_extra, queued_extra in shelves:
        ranked_id = _catalog(
            test_client,
            catalog_path,
            {'title': f'Ranked {catalog_path}', **ranked_extra},
        )
        test_client.post(
            f'/v1/users/me/{tracker_path}/{ranked_id}',
            headers=_auth(token),
            json={'on_rankings': True, 'notes': 'private note!'},
        )
        test_client.put(
            f'/v1/users/me/{tracker_path}/{ranked_id}/rank',
            headers=_auth(token),
            json={'position': 1},
        )
        queued_id = _catalog(
            test_client,
            catalog_path,
            {'title': f'Queued {catalog_path}', **queued_extra},
        )
        test_client.post(
            f'/v1/users/me/{tracker_path}/{queued_id}',
            headers=_auth(token),
            json={'on_watchlist': True, 'notes': 'private note!'},
        )


def _set_visibility(test_client: TestClient, token: str, **fields) -> None:
    response = test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json=fields
    )
    assert response.status_code == 200, response.text


def _befriend(test_client: TestClient, requester_token: str, owner_token: str) -> str:
    """Requester asks the profile owner; the owner accepts. Returns its id."""
    sent = test_client.post(
        '/v1/users/me/friends/requests',
        headers=_auth(requester_token),
        json={'handle': HANDLE},
    )
    assert sent.status_code == 202, sent.text
    incoming = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(owner_token)
    ).json()['incoming']
    accepted = test_client.put(
        f'/v1/users/me/friends/requests/{incoming[0]["id"]}/accept',
        headers=_auth(owner_token),
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()['id']


def _fingerprint(response) -> tuple:
    """
    Everything a caller can observe, minus what varies run to run.

    ``Server-Timing`` is a duration and ``Date`` a clock reading; both differ
    between any two responses, including two identical ones.
    """
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in ('server-timing', 'date')
    }
    return response.status_code, response.json(), headers


def _shelves(response) -> list:
    return [shelf['category'] for shelf in response.json()['shelves']]


def _shelf(response, category: str) -> dict:
    return next(s for s in response.json()['shelves'] if s['category'] == category)


@pytest.fixture(name='stranger_token')
def fixture_stranger_token(test_client, test_create_user, test_authenticate_user):
    """A third signed-in user with no relationship to anybody."""
    user = test_create_user(test_client, user_count=1)[0]
    return test_authenticate_user(test_client, user.email, user.plain_password)


@pytest.fixture(name='profile')
def fixture_profile(test_client: TestClient):
    """
    ``avery``: a public profile with one shelf at each tier, and second_user
    an accepted friend.
    """
    owner_token = test_client.first_user.token
    _stock_every_shelf(test_client, owner_token)
    _set_visibility(test_client, owner_token, **TIERS)
    _befriend(test_client, test_client.second_user.token, owner_token)
    return test_client


# --- Who sees which shelves -------------------------------------------------


def test_anonymous_sees_only_public_shelves(profile: TestClient):
    response = profile.get(f'/v1/public/{HANDLE}')
    assert response.status_code == 200
    assert _shelves(response) == ['Movies', 'Books']
    assert response.json()['viewer'] == {
        'relationship': 'anonymous',
        'following': False,
    }


def test_a_friend_additionally_sees_friends_shelves(profile: TestClient):
    response = profile.get(
        f'/v1/public/{HANDLE}', headers=_auth(profile.second_user.token)
    )
    assert response.status_code == 200
    assert _shelves(response) == ['Movies', 'TV', 'Books']
    assert response.json()['viewer'] == {'relationship': 'friend', 'following': False}


def test_a_non_friend_gets_the_anonymous_response(profile: TestClient, stranger_token):
    anonymous = profile.get(f'/v1/public/{HANDLE}').json()
    stranger = profile.get(f'/v1/public/{HANDLE}', headers=_auth(stranger_token)).json()

    # Identical content; the relationship field is the only difference, and it
    # says "signed in, unrelated" rather than "not signed in".
    assert stranger['viewer'] == {'relationship': 'none', 'following': False}
    assert {k: v for k, v in stranger.items() if k != 'viewer'} == {
        k: v for k, v in anonymous.items() if k != 'viewer'
    }


def test_the_owner_sees_every_shelf_including_private(profile: TestClient):
    response = profile.get(
        f'/v1/public/{HANDLE}', headers=_auth(profile.first_user.token)
    )
    assert response.status_code == 200
    assert _shelves(response) == ['Movies', 'TV', 'Books', 'Video Games']
    assert response.json()['viewer'] == {'relationship': 'self', 'following': False}


def test_friends_shelves_never_reach_a_stranger(profile: TestClient, stranger_token):
    # The leak this whole issue is about, stated directly against the payload.
    for headers in ({}, _auth(stranger_token)):
        flat = str(profile.get(f'/v1/public/{HANDLE}', headers=headers).json())
        assert 'Ranked tv-shows' not in flat
        assert 'Queued tv-shows' not in flat
        assert 'Ranked games' not in flat
        assert 'private note!' not in flat


# --- Watchlists, per shelf and per tier -------------------------------------


def test_watchlists_are_evaluated_per_shelf_and_per_tier(profile: TestClient):
    anonymous = profile.get(f'/v1/public/{HANDLE}')
    # Public shelf + public watchlist.
    assert [i['title'] for i in _shelf(anonymous, 'Movies')['watchlist']] == [
        'Queued movies'
    ]
    # Public shelf + friends watchlist: the shelf shows, the watchlist does not.
    assert 'watchlist' not in _shelf(anonymous, 'Books')

    friend = profile.get(
        f'/v1/public/{HANDLE}', headers=_auth(profile.second_user.token)
    )
    assert [i['title'] for i in _shelf(friend, 'Books')['watchlist']] == [
        'Queued books'
    ]
    assert [i['title'] for i in _shelf(friend, 'TV')['watchlist']] == [
        'Queued tv-shows'
    ]


def test_a_public_watchlist_cannot_outrun_its_private_shelf(profile: TestClient):
    # Games is private with a public watchlist; nobody but the owner gets it.
    for headers in ({}, _auth(profile.second_user.token)):
        assert 'Video Games' not in _shelves(
            profile.get(f'/v1/public/{HANDLE}', headers=headers)
        )
    owner = profile.get(f'/v1/public/{HANDLE}', headers=_auth(profile.first_user.token))
    assert [i['title'] for i in _shelf(owner, 'Video Games')['watchlist']] == [
        'Queued games'
    ]


# --- 404 indistinguishability ----------------------------------------------


def test_a_friends_only_profile_is_a_404_for_everybody_else(
    test_client: TestClient, stranger_token
):
    owner_token = test_client.first_user.token
    _stock_every_shelf(test_client, owner_token)
    _set_visibility(
        test_client,
        owner_token,
        handle=HANDLE,
        visibility_profile='friends',
        visibility_movies='friends',
    )
    _befriend(test_client, test_client.second_user.token, owner_token)

    friend = test_client.get(
        f'/v1/public/{HANDLE}', headers=_auth(test_client.second_user.token)
    )
    assert friend.status_code == 200
    assert _shelves(friend) == ['Movies']

    # Everyone else gets the answer an unclaimed handle gets — byte for byte,
    # headers included.
    unknown = test_client.get('/v1/public/nobody-here')
    assert _fingerprint(test_client.get(f'/v1/public/{HANDLE}')) == _fingerprint(
        unknown
    )
    assert _fingerprint(
        test_client.get(f'/v1/public/{HANDLE}', headers=_auth(stranger_token))
    ) == _fingerprint(unknown)
    # ...including the caller who *is* signed in as somebody with friends.
    assert _fingerprint(
        test_client.get('/v1/public/nobody-here', headers=_auth(stranger_token))
    ) == _fingerprint(unknown)


def test_nothing_visible_404s_even_when_the_profile_tier_admits_you(
    test_client: TestClient,
):
    # Profile reachable by a friend, but every shelf below it is private: the
    # friend must not get an empty 200 that confirms the account exists.
    owner_token = test_client.first_user.token
    _stock_every_shelf(test_client, owner_token)
    _set_visibility(
        test_client, owner_token, handle=HANDLE, visibility_profile='friends'
    )
    _befriend(test_client, test_client.second_user.token, owner_token)

    friend = test_client.get(
        f'/v1/public/{HANDLE}', headers=_auth(test_client.second_user.token)
    )
    assert _fingerprint(friend) == _fingerprint(test_client.get('/v1/public/nobody'))


def test_a_pending_request_is_not_a_friendship(test_client: TestClient):
    owner_token = test_client.first_user.token
    _stock_every_shelf(test_client, owner_token)
    _set_visibility(
        test_client,
        owner_token,
        handle=HANDLE,
        visibility_profile='friends',
        visibility_movies='friends',
    )
    sent = test_client.post(
        '/v1/users/me/friends/requests',
        headers=_auth(test_client.second_user.token),
        json={'handle': HANDLE},
    )
    assert sent.status_code == 202

    pending = test_client.get(
        f'/v1/public/{HANDLE}', headers=_auth(test_client.second_user.token)
    )
    assert _fingerprint(pending) == _fingerprint(test_client.get('/v1/public/nobody'))


def test_unfriending_takes_the_shelves_back(test_client: TestClient):
    owner_token = test_client.first_user.token
    friend_token = test_client.second_user.token
    _stock_every_shelf(test_client, owner_token)
    _set_visibility(
        test_client,
        owner_token,
        handle=HANDLE,
        visibility_profile='friends',
        visibility_movies='friends',
    )
    friendship_id = _befriend(test_client, friend_token, owner_token)
    assert (
        test_client.get(f'/v1/public/{HANDLE}', headers=_auth(friend_token)).status_code
        == 200
    )

    assert (
        test_client.delete(
            f'/v1/users/me/friends/{friendship_id}', headers=_auth(owner_token)
        ).status_code
        == 204
    )
    after = test_client.get(f'/v1/public/{HANDLE}', headers=_auth(friend_token))
    assert _fingerprint(after) == _fingerprint(test_client.get('/v1/public/nobody'))


def test_the_owner_of_a_private_profile_still_sees_it(test_client: TestClient):
    owner_token = test_client.first_user.token
    _stock_every_shelf(test_client, owner_token)
    _set_visibility(test_client, owner_token, handle=HANDLE)

    anonymous = test_client.get(f'/v1/public/{HANDLE}')
    assert anonymous.status_code == 404
    owner = test_client.get(f'/v1/public/{HANDLE}', headers=_auth(owner_token))
    assert owner.status_code == 200
    assert owner.json()['viewer'] == {'relationship': 'self', 'following': False}


# --- Credentials ------------------------------------------------------------


def test_absent_credentials_are_anonymous_not_an_error(profile: TestClient):
    assert profile.get(f'/v1/public/{HANDLE}').status_code == 200


@pytest.mark.parametrize('token', ['garbage', 'drk_not-a-real-key', ''])
def test_bad_credentials_are_rejected_rather_than_ignored(profile: TestClient, token):
    # The deliberate choice: present-but-invalid is never silently downgraded
    # to the anonymous view — except for an empty bearer, which carries no
    # credential at all.
    response = profile.get(f'/v1/public/{HANDLE}', headers=_auth(token))
    if token == '':
        assert response.status_code == 200
        assert response.json()['viewer'] == {
            'relationship': 'anonymous',
            'following': False,
        }
    else:
        assert response.status_code == 401
        assert response.headers['www-authenticate'] == 'Bearer'


def test_an_expired_token_is_a_401_not_the_anonymous_view(profile: TestClient):
    expired = create_access_token(
        {'sub': profile.second_user.id}, expires_delta=timedelta(minutes=-5)
    )
    response = profile.get(f'/v1/public/{HANDLE}', headers=_auth(expired))
    assert response.status_code == 401


def test_a_token_for_a_deleted_account_is_a_401(
    test_client: TestClient, test_create_user, test_authenticate_user
):
    # Same answer as any other bad credential, so the 404-style rule holds
    # here too: an expired token and a deleted account are one response.
    user = test_create_user(test_client, user_count=1)[0]
    token = test_authenticate_user(test_client, user.email, user.plain_password)
    test_client.test_db_session.delete(user)
    test_client.test_db_session.commit()

    response = test_client.get('/v1/public/nobody', headers=_auth(token))
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'


def test_the_response_varies_on_the_authorization_header(profile: TestClient):
    # A shared cache keyed on the URL alone would hand one viewer another
    # viewer's shelves — or another viewer's 404, since whether this handle
    # resolves at all depends on who is asking.
    assert profile.get(f'/v1/public/{HANDLE}').headers['vary'] == 'Authorization'
    assert profile.get('/v1/public/nobody').headers['vary'] == 'Authorization'
