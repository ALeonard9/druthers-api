# pylint: disable=missing-function-docstring
"""
Follow / unfollow (#276): asymmetric, unapproved, and gated on a ``public``
profile tier only.

The critical property under test throughout is that following never widens
what a viewer sees. A follower of a profile is deliberately put through the
exact same ``/v1/public/{handle}`` assertions a stranger would face —
``test_a_follower_sees_exactly_what_a_stranger_sees`` is the one this issue
exists to make true, and ``test_a_follow_survives_the_followee_going_private``
is the one acceptance criterion that is a real test case rather than a
footnote.
"""

from fastapi.testclient import TestClient

from app.db.db_follow import is_following


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _set_visibility(test_client: TestClient, token: str, **fields) -> str:
    response = test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json=fields
    )
    assert response.status_code == 200, response.text
    return response.json()['handle']


def _claim_public_handle(test_client: TestClient, token: str, handle: str) -> str:
    return _set_visibility(
        test_client, token, handle=handle, visibility_profile='public'
    )


def _catalog(test_client: TestClient, token: str, path: str, payload: dict) -> str:
    response = test_client.post(f'/v1/{path}', headers=_auth(token), json=payload)
    assert response.status_code == 201, response.text
    return response.json()['id']


def _rank(test_client: TestClient, token: str, tracker_path: str, item_id: str) -> None:
    posted = test_client.post(
        f'/v1/users/me/{tracker_path}/{item_id}',
        headers=_auth(token),
        json={'on_rankings': True},
    )
    assert posted.status_code in (200, 201), posted.text
    ranked = test_client.put(
        f'/v1/users/me/{tracker_path}/{item_id}/rank',
        headers=_auth(token),
        json={'position': 1},
    )
    assert ranked.status_code == 200, ranked.text


def _shelves(response) -> list:
    return [shelf['category'] for shelf in response.json()['shelves']]


def _without_viewer(response) -> dict:
    """The profile body minus `viewer`, which is the one key allowed to differ."""
    return {k: v for k, v in response.json().items() if k != 'viewer'}


# --- follow / unfollow lifecycle --------------------------------------------


def test_following_a_public_profile_succeeds(test_client: TestClient):
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    response = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['user']['handle'] == 'blake'
    assert body['followed_at']
    assert is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )


def test_follow_and_unfollow_show_up_on_both_sides(test_client: TestClient):
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    follow = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    ).json()

    following = test_client.get(
        '/v1/users/me/following', headers=_auth(test_client.first_user.token)
    ).json()
    assert [f['user']['handle'] for f in following] == ['blake']

    followers = test_client.get(
        '/v1/users/me/followers', headers=_auth(test_client.second_user.token)
    ).json()
    assert len(followers) == 1
    assert followers[0]['id'] == follow['id']
    assert followers[0]['user']['id'] == test_client.first_user.id


def test_following_is_asymmetric(test_client: TestClient):
    """first_user follows second_user; second_user gets nothing back."""
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )

    assert (
        test_client.get(
            '/v1/users/me/following', headers=_auth(test_client.second_user.token)
        ).json()
        == []
    )
    assert (
        test_client.get(
            '/v1/users/me/followers', headers=_auth(test_client.first_user.token)
        ).json()
        == []
    )


def test_follow_lists_and_deletion_are_scoped_to_the_caller(test_client: TestClient):
    """
    Knowing another user's follow id or followee handle must not expose the
    row through an outsider's lists or let the outsider delete it.
    """
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    created = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert created.status_code == 200
    follow_id = created.json()['id']
    outsider = test_client.admin_user.token

    assert (
        test_client.get('/v1/users/me/following', headers=_auth(outsider)).json() == []
    )
    assert (
        test_client.get('/v1/users/me/followers', headers=_auth(outsider)).json() == []
    )
    assert (
        test_client.delete(
            '/v1/users/me/following/blake', headers=_auth(outsider)
        ).status_code
        == 404
    )
    assert (
        test_client.delete(
            f'/v1/users/me/following/{follow_id}', headers=_auth(outsider)
        ).status_code
        == 404
    )

    following = test_client.get(
        '/v1/users/me/following', headers=_auth(test_client.first_user.token)
    ).json()
    assert [row['id'] for row in following] == [follow_id]
    assert is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )


def test_following_requires_no_approval(test_client: TestClient):
    """Unlike a friend request, the row exists immediately — no accept step."""
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    follow = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert follow.status_code == 200
    followers = test_client.get(
        '/v1/users/me/followers', headers=_auth(test_client.second_user.token)
    ).json()
    assert len(followers) == 1


def test_following_twice_is_idempotent(test_client: TestClient):
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    first = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    second = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert first.status_code == second.status_code == 200
    assert first.json()['id'] == second.json()['id']
    following = test_client.get(
        '/v1/users/me/following', headers=_auth(test_client.first_user.token)
    ).json()
    assert len(following) == 1


def test_unfollow_removes_the_row(test_client: TestClient):
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    response = test_client.delete(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 204
    assert (
        test_client.get(
            '/v1/users/me/following', headers=_auth(test_client.first_user.token)
        ).json()
        == []
    )
    assert not is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )


def test_unfollowing_when_not_following_404s(test_client: TestClient):
    _claim_public_handle(test_client, test_client.second_user.token, 'blake')
    response = test_client.delete(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 404


def test_unfollowing_an_unknown_handle_404s(test_client: TestClient):
    response = test_client.delete(
        '/v1/users/me/following/nobody-here',
        headers=_auth(test_client.first_user.token),
    )
    assert response.status_code == 404


def test_following_yourself_is_rejected(test_client: TestClient):
    handle = _claim_public_handle(test_client, test_client.first_user.token, 'blake')
    response = test_client.put(
        f'/v1/users/me/following/{handle}', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 422


def test_following_an_unknown_handle_404s(test_client: TestClient):
    response = test_client.put(
        '/v1/users/me/following/nobody-here',
        headers=_auth(test_client.first_user.token),
    )
    assert response.status_code == 404


# --- the gate: only a public profile is followable --------------------------


def test_cannot_follow_a_private_profile(test_client: TestClient):
    # Explicit: every tier now defaults to 'friends' (web#156), so a test of
    # a genuinely *private* profile has to lower every shelf too — the floor
    # invariant (#274) otherwise rejects a private profile under a friends
    # shelf, and 'friends' alone would 404 here too, but for the wrong reason.
    _set_visibility(
        test_client,
        test_client.second_user.token,
        handle='blake',
        visibility_profile='private',
        visibility_movies='private',
        visibility_tv='private',
        visibility_books='private',
        visibility_games='private',
        visibility_watchlist_movies='private',
        visibility_watchlist_tv='private',
        visibility_watchlist_books='private',
        visibility_watchlist_games='private',
    )
    response = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 404
    assert not is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )


def test_cannot_follow_a_friends_only_profile(test_client: TestClient):
    _set_visibility(
        test_client,
        test_client.second_user.token,
        handle='blake',
        visibility_profile='friends',
    )
    response = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 404
    assert not is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )


def test_cannot_follow_a_friends_only_profile_even_as_an_accepted_friend(
    test_client: TestClient,
):
    """
    Following is gated on the profile tier alone, never on the requester's
    relationship to the owner — an accepted friend gets no special path in.
    """
    owner_token = test_client.second_user.token
    friend_token = test_client.first_user.token
    _set_visibility(
        test_client, owner_token, handle='blake', visibility_profile='friends'
    )
    sent = test_client.post(
        '/v1/users/me/friends/requests',
        headers=_auth(friend_token),
        json={'handle': 'blake'},
    )
    assert sent.status_code == 202
    incoming = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(owner_token)
    ).json()['incoming']
    accepted = test_client.put(
        f'/v1/users/me/friends/requests/{incoming[0]["id"]}/accept',
        headers=_auth(owner_token),
    )
    assert accepted.status_code == 200

    response = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(friend_token)
    )
    assert response.status_code == 404
    assert not is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )


# --- the critical property: following grants nothing ------------------------


def test_a_follower_sees_exactly_what_a_stranger_sees(
    test_client: TestClient, test_create_user, test_authenticate_user
):
    """
    ``blake`` has a public profile with a public Movies shelf and a
    friends-only TV shelf. first_user follows blake (allowed: the profile
    tier is public). A stranger who never followed blake gets the identical
    response — same shelves, same content, same viewer ceiling — proving a
    follower resolves to exactly the anonymous/none ceiling and never to
    ``friends``.
    """
    owner_token = test_client.second_user.token
    follower_token = test_client.first_user.token
    stranger = test_create_user(test_client, user_count=1)[0]
    stranger_token = test_authenticate_user(
        test_client, stranger.email, stranger.plain_password
    )

    _set_visibility(
        test_client,
        owner_token,
        handle='blake',
        visibility_profile='public',
        visibility_movies='public',
        visibility_tv='friends',
    )
    for path, payload in (
        ('movies', {'title': 'Owner Movie', 'imdb': 'tt0113277'}),
        ('tv-shows', {'title': 'Owner Show', 'imdb': 'tt0903747'}),
    ):
        _rank(
            test_client,
            owner_token,
            path,
            _catalog(test_client, owner_token, path, payload),
        )

    assert (
        test_client.put(
            '/v1/users/me/following/blake', headers=_auth(follower_token)
        ).status_code
        == 200
    )

    follower_view = test_client.get('/v1/public/blake', headers=_auth(follower_token))
    stranger_view = test_client.get('/v1/public/blake', headers=_auth(stranger_token))
    anonymous_view = test_client.get('/v1/public/blake')

    # The follower is signed in, so their relationship reads 'none' — same as
    # the stranger — never 'friend'. `following` is the only field allowed to
    # differ, and it differs in exactly the direction that proves the point:
    # the follower's flag is True, and it still bought them nothing.
    assert follower_view.json()['viewer'] == {'relationship': 'none', 'following': True}
    assert stranger_view.json()['viewer'] == {
        'relationship': 'none',
        'following': False,
    }
    assert anonymous_view.json()['viewer'] == {
        'relationship': 'anonymous',
        'following': False,
    }

    # The friends-only shelf and its content never reach any of the three.
    for response in (follower_view, stranger_view, anonymous_view):
        assert response.status_code == 200
        assert _shelves(response) == ['Movies']
        assert 'Owner Show' not in str(response.json())

    # Bodies match byte for byte apart from the deliberately-differing
    # `viewer` key.
    assert _without_viewer(follower_view) == _without_viewer(stranger_view)


def test_a_follow_survives_the_followee_going_private(test_client: TestClient):
    """
    #276's last acceptance criterion, as a real assertion: dropping out of
    `public` does not delete the follow row, but it does stop it from
    admitting anything on the public profile.
    """
    owner_token = test_client.second_user.token
    follower_token = test_client.first_user.token
    # A public profile tier alone still 404s (#277: nothing visible to this
    # caller), so give the owner a public shelf with something on it — the
    # profile has to render 200 first for "stops granting anything" to mean
    # anything.
    _set_visibility(
        test_client,
        owner_token,
        handle='blake',
        visibility_profile='public',
        visibility_movies='public',
    )
    movie_id = _catalog(
        test_client,
        owner_token,
        'movies',
        {'title': 'Owner Movie', 'imdb': 'tt0113277'},
    )
    _rank(test_client, owner_token, 'movies', movie_id)

    follow = test_client.put(
        '/v1/users/me/following/blake', headers=_auth(follower_token)
    )
    assert follow.status_code == 200
    assert (
        test_client.get('/v1/public/blake', headers=_auth(follower_token)).status_code
        == 200
    )

    # Both in one update: #274's invariant rejects a private profile that
    # still has a public shelf under it, so the shelf has to come down too.
    # Every other shelf now defaults to 'friends' (web#156) rather than
    # 'private', so they need lowering explicitly as well, or the same
    # invariant rejects this update on one of *them* instead.
    _set_visibility(
        test_client,
        owner_token,
        visibility_profile='private',
        visibility_movies='private',
        visibility_tv='private',
        visibility_books='private',
        visibility_games='private',
        visibility_watchlist_movies='private',
        visibility_watchlist_tv='private',
        visibility_watchlist_books='private',
        visibility_watchlist_games='private',
    )

    # The row is untouched...
    following = test_client.get(
        '/v1/users/me/following', headers=_auth(follower_token)
    ).json()
    assert [f['user']['handle'] for f in following] == ['blake']
    assert is_following(
        test_client.test_db_session,
        test_client.first_user.pk,
        test_client.second_user.pk,
    )

    # ...but it grants nothing: the profile 404s for the follower exactly as
    # it does for anybody else.
    assert (
        test_client.get('/v1/public/blake', headers=_auth(follower_token)).status_code
        == 404
    )
    assert test_client.get('/v1/public/blake').status_code == 404

    # The follower can still unfollow afterwards.
    unfollow = test_client.delete(
        '/v1/users/me/following/blake', headers=_auth(follower_token)
    )
    assert unfollow.status_code == 204
