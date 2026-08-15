# pylint: disable=missing-function-docstring
"""
Mutual friend requests (#275).

Two clusters of assertions: the request/accept/decline/cancel/unfriend
lifecycle over a single canonical row, and the enumeration-resistance rules -
an unknown handle has to be indistinguishable from a known one, including on
the *second* attempt, which is where the naive "duplicate -> 409" version
leaks.
"""

from fastapi.testclient import TestClient

from app.db.db_friendship import are_friends, friend_pks


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _claim_handle(test_client: TestClient, token: str, handle: str) -> str:
    response = test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': handle}
    )
    assert response.status_code == 200, response.text
    return response.json()['handle']


def _send(test_client: TestClient, token: str, handle: str):
    return test_client.post(
        '/v1/users/me/friends/requests', headers=_auth(token), json={'handle': handle}
    )


def _become_friends(test_client: TestClient) -> str:
    """first_user and second_user end up accepted friends; returns its id."""
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    assert _send(test_client, test_client.first_user.token, 'blake').status_code == 202
    incoming = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.second_user.token)
    ).json()['incoming']
    request_id = incoming[0]['id']
    accepted = test_client.put(
        f'/v1/users/me/friends/requests/{request_id}/accept',
        headers=_auth(test_client.second_user.token),
    )
    assert accepted.status_code == 200, accepted.text
    return accepted.json()['id']


def test_request_then_accept_makes_both_sides_friends(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    sent = _send(test_client, test_client.first_user.token, 'blake')
    assert sent.status_code == 202
    assert sent.json()['message'] == 'Friend request sent'

    # Pending in both directions, and a friend to neither yet.
    outgoing = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.first_user.token)
    ).json()
    assert outgoing['incoming'] == []
    assert len(outgoing['outgoing']) == 1
    assert outgoing['outgoing'][0]['user']['handle'] == 'blake'

    incoming = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.second_user.token)
    ).json()
    assert incoming['outgoing'] == []
    assert len(incoming['incoming']) == 1
    request_id = incoming['incoming'][0]['id']
    assert (
        test_client.get(
            '/v1/users/me/friends', headers=_auth(test_client.first_user.token)
        ).json()
        == []
    )

    accepted = test_client.put(
        f'/v1/users/me/friends/requests/{request_id}/accept',
        headers=_auth(test_client.second_user.token),
    )
    assert accepted.status_code == 200
    assert accepted.json()['user']['handle'] is None  # first_user never claimed one
    assert accepted.json()['friends_since']

    for token in (test_client.first_user.token, test_client.second_user.token):
        friends = test_client.get('/v1/users/me/friends', headers=_auth(token)).json()
        assert len(friends) == 1
        assert test_client.get(
            '/v1/users/me/friends/requests', headers=_auth(token)
        ).json() == {'incoming': [], 'outgoing': []}


def test_one_row_backs_both_directions(test_client: TestClient):
    """
    The relationship is a single row: both sides report the same friendship
    id, and the reusable helper #277 will call agrees from either seat.
    """
    friendship_id = _become_friends(test_client)
    for token in (test_client.first_user.token, test_client.second_user.token):
        listed = test_client.get('/v1/users/me/friends', headers=_auth(token)).json()
        assert [row['id'] for row in listed] == [friendship_id]

    db = test_client.test_db_session
    first_pk = test_client.first_user.pk
    second_pk = test_client.second_user.pk
    assert are_friends(db, first_pk, second_pk) is True
    assert are_friends(db, second_pk, first_pk) is True
    # A pending edge is not a friendship, and nobody is their own friend.
    assert are_friends(db, first_pk, first_pk) is False
    assert are_friends(db, first_pk, test_client.admin_user.pk) is False
    assert friend_pks(db, first_pk) == [second_pk]
    assert friend_pks(db, test_client.admin_user.pk) == []


def test_pending_is_not_yet_a_friendship(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    db = test_client.test_db_session
    assert (
        are_friends(db, test_client.first_user.pk, test_client.second_user.pk) is False
    )


def test_unknown_handle_answers_exactly_like_a_known_one(test_client: TestClient):
    """
    The core enumeration rule: status code and body are identical, and the
    caller is left with nothing new in any of their lists to compare.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    token = test_client.first_user.token

    known = _send(test_client, token, 'blake')
    unknown = _send(test_client, token, 'nobody-by-that-name')
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()

    # A malformed handle - which could never exist - takes the same path
    # rather than a distinguishable 422.
    malformed = _send(test_client, token, 'Not A Handle!!')
    assert malformed.status_code == 202
    assert malformed.json() == known.json()

    outgoing = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(token)
    ).json()['outgoing']
    assert [row['user']['handle'] for row in outgoing] == ['blake']


def test_a_first_request_does_not_confirm_the_handle_exists(
    test_client: TestClient,
):
    """
    One probe learns nothing: a brand-new request to a real handle and one to
    an unused handle are the same 202, body included.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    token = test_client.first_user.token

    known = _send(test_client, token, 'blake')
    unknown = _send(test_client, token, 'ghost')

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_resending_conflicts_and_does_not_duplicate_the_row(
    test_client: TestClient,
):
    """
    Adam's call (#275): a resend is a 409 so a double-click gets an answer.

    This test also pins the cost of that decision, so nobody has to rediscover
    it: the second send *does* distinguish a real handle from an unused one.
    The rate limit is what bounds enumeration now, not this response shape.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    token = test_client.first_user.token

    _send(test_client, token, 'blake')
    _send(test_client, token, 'ghost')
    second_known = _send(test_client, token, 'blake')
    second_unknown = _send(test_client, token, 'ghost')

    assert second_known.status_code == 409
    assert 'already sent' in second_known.json()['message'].lower()
    # The accepted leak, stated as an assertion rather than left implicit.
    assert second_unknown.status_code == 202

    # The conflict is not a second row.
    outgoing = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(token)
    ).json()['outgoing']
    assert len(outgoing) == 1


def test_self_directed_request_is_rejected(test_client: TestClient):
    token = test_client.first_user.token
    _claim_handle(test_client, token, 'avery')
    response = _send(test_client, token, 'avery')
    assert response.status_code == 422
    assert 'yourself' in response.json()['message']


def test_already_friends_request_is_rejected(test_client: TestClient):
    _become_friends(test_client)
    response = _send(test_client, test_client.first_user.token, 'blake')
    assert response.status_code == 409
    assert 'already friends' in response.json()['message']


def test_request_from_the_other_direction_says_accept_it(test_client: TestClient):
    """
    Reachable only because the *other* user already asked - a state no amount
    of probing can create, so a clear error leaks nothing.
    """
    _claim_handle(test_client, test_client.first_user.token, 'avery')
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    assert _send(test_client, test_client.first_user.token, 'blake').status_code == 202

    response = _send(test_client, test_client.second_user.token, 'avery')
    assert response.status_code == 409
    assert 'accept it instead' in response.json()['message']


def test_decline_removes_the_request_and_allows_a_retry(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.second_user.token)
    ).json()['incoming'][0]['id']

    declined = test_client.put(
        f'/v1/users/me/friends/requests/{request_id}/decline',
        headers=_auth(test_client.second_user.token),
    )
    assert declined.status_code == 200
    assert 'declined' in declined.json()['message']

    for token in (test_client.first_user.token, test_client.second_user.token):
        assert test_client.get(
            '/v1/users/me/friends/requests', headers=_auth(token)
        ).json() == {'incoming': [], 'outgoing': []}
        assert (
            test_client.get('/v1/users/me/friends', headers=_auth(token)).json() == []
        )

    # No tombstone: the pair can try again.
    assert _send(test_client, test_client.first_user.token, 'blake').status_code == 202


def test_sender_can_cancel_a_pending_request(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.first_user.token)
    ).json()['outgoing'][0]['id']

    cancelled = test_client.delete(
        f'/v1/users/me/friends/requests/{request_id}',
        headers=_auth(test_client.first_user.token),
    )
    assert cancelled.status_code == 204
    assert test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.second_user.token)
    ).json() == {'incoming': [], 'outgoing': []}


def test_each_side_gets_only_its_own_verb(test_client: TestClient):
    """
    Accept and decline belong to the recipient, cancel to the sender. The
    wrong side gets the same 404 as a made-up id.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    sender, recipient = test_client.first_user.token, test_client.second_user.token
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(sender)
    ).json()['outgoing'][0]['id']

    for path in (f'{request_id}/accept', f'{request_id}/decline'):
        response = test_client.put(
            f'/v1/users/me/friends/requests/{path}', headers=_auth(sender)
        )
        assert response.status_code == 404
    assert (
        test_client.delete(
            f'/v1/users/me/friends/requests/{request_id}', headers=_auth(recipient)
        ).status_code
        == 404
    )
    # An uninvolved third party sees the same 404 as anyone guessing an id.
    assert (
        test_client.put(
            f'/v1/users/me/friends/requests/{request_id}/accept',
            headers=_auth(test_client.admin_user.token),
        ).status_code
        == 404
    )
    assert (
        test_client.put(
            '/v1/users/me/friends/requests/not-a-real-id/accept',
            headers=_auth(recipient),
        ).status_code
        == 404
    )


def test_an_outsider_cannot_read_or_mutate_another_pairs_request(
    test_client: TestClient,
):
    """
    A leaked or guessed request id gives an uninvolved user no pending data
    and no accept, decline, cancel, or unfriend path into the pair's row.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    sender = test_client.first_user.token
    recipient = test_client.second_user.token
    outsider = test_client.admin_user.token
    assert _send(test_client, sender, 'blake').status_code == 202
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(sender)
    ).json()['outgoing'][0]['id']

    assert test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(outsider)
    ).json() == {'incoming': [], 'outgoing': []}

    for suffix in ('accept', 'decline'):
        guessed = test_client.put(
            f'/v1/users/me/friends/requests/{request_id}/{suffix}',
            headers=_auth(outsider),
        )
        nonexistent = test_client.put(
            f'/v1/users/me/friends/requests/not-a-real-id/{suffix}',
            headers=_auth(outsider),
        )
        assert guessed.status_code == nonexistent.status_code == 404
        assert guessed.json() == nonexistent.json()

    guessed_cancel = test_client.delete(
        f'/v1/users/me/friends/requests/{request_id}', headers=_auth(outsider)
    )
    nonexistent_cancel = test_client.delete(
        '/v1/users/me/friends/requests/not-a-real-id', headers=_auth(outsider)
    )
    assert guessed_cancel.status_code == nonexistent_cancel.status_code == 404
    assert guessed_cancel.json() == nonexistent_cancel.json()

    assert (
        test_client.delete(
            f'/v1/users/me/friends/{request_id}', headers=_auth(outsider)
        ).status_code
        == 404
    )
    pending = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(recipient)
    ).json()
    assert [row['id'] for row in pending['incoming']] == [request_id]


def test_an_outsider_cannot_unfriend_another_pair_by_id(test_client: TestClient):
    friendship_id = _become_friends(test_client)
    outsider = test_client.admin_user.token

    guessed = test_client.delete(
        f'/v1/users/me/friends/{friendship_id}', headers=_auth(outsider)
    )
    nonexistent = test_client.delete(
        '/v1/users/me/friends/not-a-real-id', headers=_auth(outsider)
    )
    assert guessed.status_code == nonexistent.status_code == 404
    assert guessed.json() == nonexistent.json()
    assert test_client.get('/v1/users/me/friends', headers=_auth(outsider)).json() == []
    for token in (test_client.first_user.token, test_client.second_user.token):
        assert [
            row['id']
            for row in test_client.get(
                '/v1/users/me/friends', headers=_auth(token)
            ).json()
        ] == [friendship_id]


def test_either_side_can_unfriend(test_client: TestClient):
    friendship_id = _become_friends(test_client)
    # The recipient of the original request ends it.
    assert (
        test_client.delete(
            f'/v1/users/me/friends/{friendship_id}',
            headers=_auth(test_client.second_user.token),
        ).status_code
        == 204
    )
    db = test_client.test_db_session
    assert (
        are_friends(db, test_client.first_user.pk, test_client.second_user.pk) is False
    )
    for token in (test_client.first_user.token, test_client.second_user.token):
        assert (
            test_client.get('/v1/users/me/friends', headers=_auth(token)).json() == []
        )
    # Gone for both, so the other side's attempt is a plain 404.
    assert (
        test_client.delete(
            f'/v1/users/me/friends/{friendship_id}',
            headers=_auth(test_client.first_user.token),
        ).status_code
        == 404
    )


def test_sender_can_also_unfriend(test_client: TestClient):
    friendship_id = _become_friends(test_client)
    assert (
        test_client.delete(
            f'/v1/users/me/friends/{friendship_id}',
            headers=_auth(test_client.first_user.token),
        ).status_code
        == 204
    )
    assert (
        test_client.get(
            '/v1/users/me/friends', headers=_auth(test_client.second_user.token)
        ).json()
        == []
    )


def test_unfriend_cannot_be_used_on_a_pending_request(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.first_user.token)
    ).json()['outgoing'][0]['id']
    assert (
        test_client.delete(
            f'/v1/users/me/friends/{request_id}',
            headers=_auth(test_client.first_user.token),
        ).status_code
        == 404
    )


def test_a_user_without_a_handle_cannot_be_reached(test_client: TestClient):
    """
    There is no directory, so the handle is the only address. Clearing it
    takes the account back off the map.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    # Clearing a handle is only allowed while fully private, and every tier
    # now defaults to 'friends' (web#156) - go there explicitly first.
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.second_user.token),
        json={
            'visibility_profile': 'private',
            'visibility_movies': 'private',
            'visibility_tv': 'private',
            'visibility_books': 'private',
            'visibility_games': 'private',
            'visibility_watchlist_movies': 'private',
            'visibility_watchlist_tv': 'private',
            'visibility_watchlist_books': 'private',
            'visibility_watchlist_games': 'private',
        },
    )
    cleared = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.second_user.token),
        json={'handle': None},
    )
    assert cleared.status_code == 200, cleared.text
    assert _send(test_client, test_client.first_user.token, 'blake').status_code == 202
    assert (
        test_client.get(
            '/v1/users/me/friends/requests', headers=_auth(test_client.first_user.token)
        ).json()['outgoing']
        == []
    )


def test_the_friend_graph_needs_authentication(test_client: TestClient):
    assert test_client.get('/v1/users/me/friends').status_code == 401
    assert test_client.get('/v1/users/me/friends/requests').status_code == 401
    assert (
        test_client.post(
            '/v1/users/me/friends/requests', json={'handle': 'blake'}
        ).status_code
        == 401
    )


def test_handle_is_matched_exactly_but_case_insensitively(test_client: TestClient):
    """
    Handles are stored lowercase, so ' BLAKE ' is the same address - but no
    prefix or fuzzy matching, which would be a search endpoint in disguise.
    """
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    assert (
        _send(test_client, test_client.first_user.token, '  BLAKE ').status_code == 202
    )
    outgoing = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.first_user.token)
    ).json()['outgoing']
    assert len(outgoing) == 1
    # A prefix of a real handle reaches nobody.
    assert _send(test_client, test_client.admin_user.token, 'bla').status_code == 202
    assert (
        test_client.get(
            '/v1/users/me/friends/requests', headers=_auth(test_client.admin_user.token)
        ).json()['outgoing']
        == []
    )
