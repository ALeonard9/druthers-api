"""
Tests the refresh-token flow (#246): issuing, rotation, replay detection,
sign-out revocation, and expiry.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth import refresh_tokens
from app.db.models import DbRefreshToken

PROBE = '/v1/users/me/notifications/unread-count'


def _sign_in(client: TestClient) -> dict:
    """Password sign-in, returning the full token payload."""
    user = client.first_user
    response = client.post(
        '/v1/auth/token',
        files={
            'username': (None, user.email),
            'password': (None, user.plain_password),
        },
    )
    assert response.status_code == 200
    return response.json()


def _authed(client: TestClient, access_token: str):
    return client.get(PROBE, headers={'Authorization': f'Bearer {access_token}'})


def test_sign_in_returns_a_refresh_token(test_client: TestClient):
    """Password sign-in hands back a refresh token and the access lifetime."""
    data = _sign_in(test_client)
    assert data['refresh_token'].startswith(refresh_tokens.REFRESH_TOKEN_PREFIX)
    assert data['expires_in'] > 0
    assert data['refresh_expires_in'] > data['expires_in']
    assert data['token_type'] == 'bearer'


def test_refresh_returns_a_working_access_token(test_client: TestClient):
    """A refresh mints an access token that authenticates real requests."""
    signed_in = _sign_in(test_client)
    response = test_client.post(
        '/v1/auth/refresh', json={'refresh_token': signed_in['refresh_token']}
    )
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed['user_id'] == signed_in['user_id']
    assert refreshed['email'] == signed_in['email']
    assert _authed(test_client, refreshed['access_token']).status_code == 200


def test_refresh_rotates_the_refresh_token(test_client: TestClient):
    """The returned refresh token is a new one, and it works in turn."""
    first = _sign_in(test_client)['refresh_token']
    second = test_client.post('/v1/auth/refresh', json={'refresh_token': first}).json()[
        'refresh_token'
    ]
    assert second != first

    third = test_client.post('/v1/auth/refresh', json={'refresh_token': second})
    assert third.status_code == 200
    assert third.json()['refresh_token'] not in (first, second)


def _age_out_reuse_window(client: TestClient, token: str) -> None:
    """Backdate a spent token past the concurrent-refresh grace window."""
    session = client.test_db_session
    row = (
        session.query(DbRefreshToken)
        .filter(DbRefreshToken.token_hash == refresh_tokens.hash_refresh_token(token))
        .first()
    )
    row.used_at = datetime.now(timezone.utc) - timedelta(hours=1)
    session.commit()


def test_concurrent_refresh_is_not_treated_as_a_replay(test_client: TestClient):
    """
    Two requests racing past the same expiry both get a working token.

    A page render fans out several API calls at once; reading the loser of
    that race as theft would sign people out at random, which is precisely
    the experience this story exists to remove.
    """
    first = _sign_in(test_client)['refresh_token']
    winner = test_client.post('/v1/auth/refresh', json={'refresh_token': first})
    racer = test_client.post('/v1/auth/refresh', json={'refresh_token': first})

    assert winner.status_code == 200
    assert racer.status_code == 200
    # Both successors work — neither request poisoned the other's session.
    for response in (winner, racer):
        assert _authed(test_client, response.json()['access_token']).status_code == 200


def test_spent_refresh_token_cannot_be_replayed(test_client: TestClient):
    """Re-presenting a rotated token after the grace window is rejected."""
    first = _sign_in(test_client)['refresh_token']
    test_client.post('/v1/auth/refresh', json={'refresh_token': first})
    _age_out_reuse_window(test_client, first)

    replay = test_client.post('/v1/auth/refresh', json={'refresh_token': first})
    assert replay.status_code == 401


def test_replay_revokes_the_whole_family(test_client: TestClient):
    """
    A replayed token means the chain leaked, so the live token dies too.

    This is the case that makes rotation worth having: the thief's use and
    the real client's next refresh can't both succeed.
    """
    first = _sign_in(test_client)['refresh_token']
    live = test_client.post('/v1/auth/refresh', json={'refresh_token': first}).json()[
        'refresh_token'
    ]
    _age_out_reuse_window(test_client, first)

    # The attacker replays the token they stole before rotation.
    assert (
        test_client.post('/v1/auth/refresh', json={'refresh_token': first}).status_code
        == 401
    )
    # The legitimate client is now locked out as well.
    assert (
        test_client.post('/v1/auth/refresh', json={'refresh_token': live}).status_code
        == 401
    )


def test_sign_out_cannot_be_undone_inside_the_grace_window(test_client: TestClient):
    """
    An already-spent token can't resurrect a signed-out session.

    The grace window forgives a race, but signing out ends the family — so
    replaying the token that was rotated moments before sign-out gets a 401
    rather than a fresh session.
    """
    first = _sign_in(test_client)['refresh_token']
    live = test_client.post('/v1/auth/refresh', json={'refresh_token': first}).json()[
        'refresh_token'
    ]
    test_client.post('/v1/auth/logout', json={'refresh_token': live})

    resurrect = test_client.post('/v1/auth/refresh', json={'refresh_token': first})
    assert resurrect.status_code == 401


def test_replay_lockout_survives_the_grace_window(test_client: TestClient):
    """After theft is detected, no token in the family gets a second chance."""
    first = _sign_in(test_client)['refresh_token']
    second = test_client.post('/v1/auth/refresh', json={'refresh_token': first}).json()[
        'refresh_token'
    ]
    third = test_client.post('/v1/auth/refresh', json={'refresh_token': second}).json()[
        'refresh_token'
    ]
    _age_out_reuse_window(test_client, first)

    # Detected replay revokes the family...
    assert (
        test_client.post('/v1/auth/refresh', json={'refresh_token': first}).status_code
        == 401
    )
    # ...and the recently-rotated tokens can't slip back in on the leeway.
    for token in (second, third):
        assert (
            test_client.post(
                '/v1/auth/refresh', json={'refresh_token': token}
            ).status_code
            == 401
        )


def test_logout_beats_a_concurrent_refresh(test_client: TestClient):
    """
    Sign-out is never excused by the race window.

    The grace period keys off ``used_at``, which only rotation sets — so a
    token killed at sign-out is rejected immediately, not 30 seconds later.
    """
    token = _sign_in(test_client)['refresh_token']
    assert (
        test_client.post('/v1/auth/logout', json={'refresh_token': token}).status_code
        == 204
    )

    assert (
        test_client.post('/v1/auth/refresh', json={'refresh_token': token}).status_code
        == 401
    )


def test_logout_revokes_the_refresh_token(test_client: TestClient):
    """Signing out kills the token server-side, not just the client cookie."""
    token = _sign_in(test_client)['refresh_token']
    logout = test_client.post('/v1/auth/logout', json={'refresh_token': token})
    assert logout.status_code == 204

    after = test_client.post('/v1/auth/refresh', json={'refresh_token': token})
    assert after.status_code == 401


def test_logout_is_idempotent_for_unknown_tokens(test_client: TestClient):
    """Sign-out succeeds even when the client's token is already dead."""
    response = test_client.post(
        '/v1/auth/logout', json={'refresh_token': 'drr_never-existed'}
    )
    assert response.status_code == 204


def test_expired_refresh_token_is_rejected(test_client: TestClient):
    """Expiry is enforced against the stored timestamp."""
    token = _sign_in(test_client)['refresh_token']
    session = test_client.test_db_session
    row = (
        session.query(DbRefreshToken)
        .filter(DbRefreshToken.token_hash == refresh_tokens.hash_refresh_token(token))
        .first()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()

    response = test_client.post('/v1/auth/refresh', json={'refresh_token': token})
    assert response.status_code == 401


def test_unknown_refresh_token_is_rejected(test_client: TestClient):
    """An invented token gets the same flat 401 as an expired one."""
    response = test_client.post(
        '/v1/auth/refresh', json={'refresh_token': 'drr_made-up-token'}
    )
    assert response.status_code == 401


def test_refresh_token_is_not_a_bearer_credential(test_client: TestClient):
    """A refresh token can't stand in for an access token on the API."""
    token = _sign_in(test_client)['refresh_token']
    assert _authed(test_client, token).status_code == 401


def test_deleting_a_user_takes_their_tokens(test_client: TestClient):
    """Sessions don't outlive their owner — the rows go, not just the FK."""
    user = test_client.first_user
    _sign_in(test_client)
    session = test_client.test_db_session
    assert (
        session.query(DbRefreshToken).filter(DbRefreshToken.user_id == user.pk).count()
    )

    test_client.delete(
        f'/v1/users/{user.id}',
        headers={'Authorization': f'Bearer {test_client.admin_user.token}'},
    )
    assert (
        session.query(DbRefreshToken).filter(DbRefreshToken.user_id == user.pk).count()
        == 0
    )


def test_only_the_hash_is_stored(test_client: TestClient):
    """The plaintext token never lands in the database."""
    token = _sign_in(test_client)['refresh_token']
    rows = test_client.test_db_session.query(DbRefreshToken).all()
    assert rows
    assert all(row.token_hash != token for row in rows)
    assert any(
        row.token_hash == refresh_tokens.hash_refresh_token(token) for row in rows
    )
