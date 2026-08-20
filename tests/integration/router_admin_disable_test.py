# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient

from app.db.models import DbAdminAuditLog, DbApiKey, DbFriendship, DbRefreshToken
from app.db.models_sandbox import DbMovie, DbUserMovie
from app.services.friendships import FriendshipStatus
from app.services.visibility import VisibilityTier

PROBE = '/v1/users/me/notifications/unread-count'


def _admin(test_client: TestClient) -> dict:
    return {'Authorization': f"Bearer {test_client.admin_user.token}"}


def _as(user) -> dict:
    return {'Authorization': f"Bearer {user.token}"}


def _sign_in(client: TestClient, user) -> dict:
    response = client.post(
        '/v1/auth/token',
        files={
            'username': (None, user.email),
            'password': (None, user.plain_password),
        },
    )
    return response


def _create_api_key(test_client: TestClient, token: str) -> str:
    resp = test_client.post(
        '/v1/users/me/api-keys',
        json={'name': 'test key'},
        headers={'Authorization': f'Bearer {token}'},
    )
    assert resp.status_code == 201
    return resp.json()['key']


def test_disable_requires_admin(test_client: TestClient):
    target = test_client.second_user
    resp = test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_as(test_client.first_user)
    )
    assert resp.status_code == 403
    assert resp.json()['message'] == 'Admin privileges required'


def test_disable_sets_status_and_returns_user_detail(test_client: TestClient):
    target = test_client.first_user
    resp = test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['status'] == 'disabled'
    assert body['id'] == target.id
    # Same shape as GET /v1/admin/users/{uuid}.
    assert 'domains' in body and 'visibility' in body and 'social' in body


def test_disable_is_audited(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.first_user
    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )
    db.expire_all()
    row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.user.disable')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert row is not None
    assert row.result == 'allowed'
    assert row.target_user_pk == target.pk
    assert row.actor_user_pk == test_client.admin_user.pk


def test_disabled_users_live_access_token_stops_working(test_client: TestClient):
    """
    The test that proves enforcement is real, not sign-in-only: a token
    minted before the disable keeps its full JWT TTL, so only a resolver
    check on every use can stop it mid-flight.
    """
    target = test_client.first_user
    assert test_client.get(PROBE, headers=_as(target)).status_code == 200

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    denied = test_client.get(PROBE, headers=_as(target))
    assert denied.status_code == 403
    assert denied.json()['message'] == 'Account disabled'


def test_disabled_users_api_key_stops_working(test_client: TestClient):
    """
    Disabling deletes the key outright (see test_disable_deletes_api_keys_
    outright), so a probe with the old key gets the generic "unknown
    credential" 401, not a 403 - there is no live row left for the
    resolver's disabled check to even run against. The key is just gone,
    which is the stronger guarantee.
    """
    target = test_client.first_user
    key = _create_api_key(test_client, target.token)
    assert (
        test_client.get(
            '/v1/users/me/api-keys', headers={'Authorization': f'Bearer {key}'}
        ).status_code
        == 200
    )

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    denied = test_client.get(
        '/v1/users/me/api-keys', headers={'Authorization': f'Bearer {key}'}
    )
    assert denied.status_code == 401


def test_disable_deletes_api_keys_outright(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.first_user
    _create_api_key(test_client, target.token)
    assert db.query(DbApiKey).filter(DbApiKey.user_id == target.pk).count() == 1

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    db.expire_all()
    assert db.query(DbApiKey).filter(DbApiKey.user_id == target.pk).count() == 0


def test_disable_revokes_every_refresh_token_family(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.first_user
    signed_in = _sign_in(test_client, target).json()
    refresh_token = signed_in['refresh_token']

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    db.expire_all()
    live = (
        db.query(DbRefreshToken)
        .filter(
            DbRefreshToken.user_id == target.pk, DbRefreshToken.revoked_at.is_(None)
        )
        .count()
    )
    assert live == 0

    refresh_resp = test_client.post(
        '/v1/auth/refresh', json={'refresh_token': refresh_token}
    )
    assert refresh_resp.status_code == 401


def test_disable_rejects_new_sign_in(test_client: TestClient):
    target = test_client.first_user
    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )
    resp = _sign_in(test_client, target)
    assert resp.status_code == 403
    assert resp.json()['message'] == 'Account disabled'


def test_admin_cannot_disable_self(test_client: TestClient):
    db = test_client.test_db_session
    admin = test_client.admin_user
    resp = test_client.post(
        f'/v1/admin/users/{admin.id}/disable', headers=_admin(test_client)
    )
    assert resp.status_code == 403
    assert resp.json()['message'] == 'You cannot disable your own account'

    db.expire_all()
    row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.user.disable')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert row.result == 'denied'
    assert row.target_user_pk == admin.pk

    # A handler-level denial must not also generate the middleware's
    # generic admin.access row for the same request - one, specific row,
    # not a duplicate.
    access_rows = (
        db.query(DbAdminAuditLog)
        .filter(
            DbAdminAuditLog.action == 'admin.access',
            DbAdminAuditLog.request_id == row.request_id,
        )
        .count()
    )
    assert access_rows == 0


def test_admin_cannot_disable_another_admin(
    test_client: TestClient, test_create_admin_user
):
    db = test_client.test_db_session
    second_admin = test_create_admin_user(test_client)[0]
    resp = test_client.post(
        f'/v1/admin/users/{second_admin.id}/disable', headers=_admin(test_client)
    )
    assert resp.status_code == 403
    assert resp.json()['message'] == 'Cannot disable another admin account'

    db.expire_all()
    row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.user.disable')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert row.result == 'denied'
    assert row.target_user_pk == second_admin.pk

    db.refresh(second_admin)
    assert second_admin.disabled_at is None


def test_enable_restores_sign_in(test_client: TestClient):
    target = test_client.first_user
    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )
    assert _sign_in(test_client, target).status_code == 403

    enable_resp = test_client.post(
        f'/v1/admin/users/{target.id}/enable', headers=_admin(test_client)
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()['status'] == 'active'

    assert _sign_in(test_client, target).status_code == 200


def test_enable_does_not_restore_the_old_refresh_token(test_client: TestClient):
    """
    Re-enabling is not "undo" - the refresh token a disable revoked stays
    revoked; the user has to sign in again. Load-bearing for the console's
    confirmation copy.
    """
    target = test_client.first_user
    signed_in = _sign_in(test_client, target).json()
    refresh_token = signed_in['refresh_token']

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )
    test_client.post(f'/v1/admin/users/{target.id}/enable', headers=_admin(test_client))

    refresh_resp = test_client.post(
        '/v1/auth/refresh', json={'refresh_token': refresh_token}
    )
    assert refresh_resp.status_code == 401


def test_enable_is_audited(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.first_user
    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )
    test_client.post(f'/v1/admin/users/{target.id}/enable', headers=_admin(test_client))
    db.expire_all()
    row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.user.enable')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert row is not None
    assert row.result == 'allowed'
    assert row.target_user_pk == target.pk


def test_disable_not_found(test_client: TestClient):
    resp = test_client.post(
        '/v1/admin/users/not-a-real-id/disable', headers=_admin(test_client)
    )
    assert resp.status_code == 404


def test_disabled_user_absent_from_public_profile(test_client: TestClient):
    target = test_client.first_user
    target.handle = 'ghost-profile'
    target.visibility_profile = VisibilityTier.PUBLIC.value
    test_client.test_db_session.commit()

    assert test_client.get('/v1/public/ghost-profile').status_code == 200

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    resp = test_client.get('/v1/public/ghost-profile')
    assert resp.status_code == 404


def test_disabled_user_absent_from_friends_and_follows_listings(
    test_client: TestClient,
):
    owner = test_client.second_user
    target = test_client.first_user
    target.handle = 'ghost-social'
    target.visibility_profile = VisibilityTier.PUBLIC.value
    db = test_client.test_db_session
    db.commit()

    # Establish a friendship and a follow before disabling.
    sent = test_client.post(
        '/v1/users/me/friends/requests',
        headers=_as(owner),
        json={'handle': target.handle},
    )
    assert sent.status_code == 202
    accept_target_view = test_client.get(
        '/v1/users/me/friends/requests', headers=_as(target)
    )
    request_id = accept_target_view.json()['incoming'][0]['id']
    assert (
        test_client.put(
            f'/v1/users/me/friends/requests/{request_id}/accept', headers=_as(target)
        ).status_code
        == 200
    )
    assert (
        test_client.put(
            f'/v1/users/me/following/{target.handle}', headers=_as(owner)
        ).status_code
        == 200
    )

    before_friends = test_client.get('/v1/users/me/friends', headers=_as(owner))
    assert any(f['user']['id'] == target.id for f in before_friends.json())
    before_following = test_client.get('/v1/users/me/following', headers=_as(owner))
    assert any(f['user']['id'] == target.id for f in before_following.json())

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    after_friends = test_client.get('/v1/users/me/friends', headers=_as(owner))
    assert not any(f['user']['id'] == target.id for f in after_friends.json())
    after_following = test_client.get('/v1/users/me/following', headers=_as(owner))
    assert not any(f['user']['id'] == target.id for f in after_following.json())


def test_disabled_user_absent_from_friends_social_feed(
    test_client: TestClient,
):
    owner = test_client.second_user
    target = test_client.first_user
    db = test_client.test_db_session

    movie = DbMovie(title='Ghost Movie')
    db.add(movie)
    db.flush()
    db.add(DbUserMovie(user_id=target.pk, movie_id=movie.pk, on_rankings=True, rank=1))
    db.add(
        DbFriendship(
            user_low_id=min(target.pk, owner.pk),
            user_high_id=max(target.pk, owner.pk),
            requested_by_id=target.pk,
            status=FriendshipStatus.ACCEPTED,
        )
    )
    db.commit()

    before = test_client.get('/v1/users/me/feed', headers=_as(owner))
    assert before.status_code == 200
    assert any(item['actor']['id'] == target.id for item in before.json()['items'])

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    after = test_client.get('/v1/users/me/feed', headers=_as(owner))
    assert after.status_code == 200
    assert not any(item['actor']['id'] == target.id for item in after.json()['items'])


def test_disabled_user_absent_from_global_search(test_client: TestClient):
    target = test_client.first_user
    target.handle = 'ghost-search'
    target.display_name = 'Ghost Searchable'
    target.visibility_profile = VisibilityTier.PUBLIC.value
    test_client.test_db_session.commit()

    before = test_client.get(
        '/v1/search/users?q=Ghost+Searchable', headers=_as(test_client.second_user)
    )
    assert before.status_code == 200
    assert any(u['id'] == target.id for u in before.json()['users'])

    test_client.post(
        f'/v1/admin/users/{target.id}/disable', headers=_admin(test_client)
    )

    after = test_client.get(
        '/v1/search/users?q=Ghost+Searchable', headers=_as(test_client.second_user)
    )
    assert after.status_code == 200
    assert not any(u['id'] == target.id for u in after.json()['users'])
