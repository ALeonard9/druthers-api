# pylint: disable=missing-module-docstring, missing-function-docstring
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth.oauth2 import (
    IMPERSONATION_TOKEN_TYPE,
    create_access_token,
    is_impersonation_token,
)
from app.db.models import DbAdminAuditLog, DbApiKey, DbImpersonationSession, DbUser


def _admin(test_client: TestClient) -> dict:
    return {'Authorization': f"Bearer {test_client.admin_user.token}"}


def _as(user) -> dict:
    return {'Authorization': f"Bearer {user.token}"}


def _start(test_client: TestClient, target, reason: str = 'diagnosing a bug'):
    return test_client.post(
        '/v1/admin/impersonation',
        json={'target_uuid': target.id, 'reason': reason},
        headers=_admin(test_client),
    )


def _impersonating(test_client: TestClient, target) -> dict:
    resp = _start(test_client, target)
    assert resp.status_code == 200, resp.text
    return {'Authorization': f"Bearer {resp.json()['token']}"}


def test_start_returns_a_scoped_token_and_both_parties(test_client: TestClient):
    resp = _start(test_client, test_client.first_user)
    assert resp.status_code == 200
    body = resp.json()
    assert body['target']['id'] == test_client.first_user.id
    assert body['acting_admin']['id'] == test_client.admin_user.id
    assert body['session_id']
    # Expiry is serialized as UTC, same as every other admin datetime.
    assert body['expires_at'].endswith('Z')
    # The mint response must never carry a refresh token: an impersonation
    # session has to die at expiry rather than being renewable.
    assert 'refresh_token' not in body


def test_impersonated_reads_return_the_target_not_the_admin(
    test_client: TestClient,
):
    """
    ``GET /v1/users/{uuid}`` only serves the caller their own record, so it
    doubles as an identity probe: under impersonation the TARGET's record
    must resolve and the acting admin's must not.
    """
    headers = _impersonating(test_client, test_client.first_user)

    as_target = test_client.get(
        f'/v1/users/{test_client.first_user.id}', headers=headers
    )
    assert as_target.status_code == 200

    as_admin = test_client.get(
        f'/v1/users/{test_client.admin_user.id}', headers=headers
    )
    assert as_admin.status_code == 403


def test_impersonation_cannot_delete_the_target_account(test_client: TestClient):
    """
    The bug this whole design exists to prevent.

    ``DELETE /v1/users/{uuid}`` resolves through ``get_current_session_user``,
    a second decode path. If the identity swap lived in ``get_current_user``
    instead of the shared resolver, that path would resolve the impersonation
    token's ``sub`` to the target with no impersonation context, and the
    endpoint's self-delete branch would fire because the impersonated
    identity IS the target.
    """
    target = test_client.first_user
    headers = _impersonating(test_client, target)
    resp = test_client.delete(f'/v1/users/{target.id}', headers=headers)
    assert resp.status_code == 403
    db = test_client.test_db_session
    assert db.query(DbUser).filter(DbUser.id == target.id).first() is not None


def test_impersonation_refuses_every_write(test_client: TestClient):
    headers = _impersonating(test_client, test_client.first_user)
    writes = (
        ('post', '/v1/users/me/api-keys', {'name': 'stolen'}),
        (
            'put',
            f'/v1/users/{test_client.first_user.id}',
            {
                'display_name': 'Taken Over',
                'email': 'taken-over@gmail.com',
                'password': 'hunter22222',
            },
        ),
        ('put', '/v1/users/me/visibility', {'visibility_profile': 'public'}),
        ('put', '/v1/users/me/preferences', {'theme': 'dark'}),
    )
    for method, path, payload in writes:
        resp = getattr(test_client, method)(path, json=payload, headers=headers)
        assert resp.status_code == 403, f'{method} {path} was not refused'

    db = test_client.test_db_session
    owner_pk = (
        db.query(DbUser).filter(DbUser.id == test_client.first_user.id).first().pk
    )
    assert db.query(DbApiKey).filter(DbApiKey.user_id == owner_pk).count() == 0


def test_impersonation_cannot_reach_admin_routes(test_client: TestClient):
    headers = _impersonating(test_client, test_client.first_user)
    for path in ('/v1/admin/users', '/v1/admin/audit'):
        assert test_client.get(path, headers=headers).status_code == 403


def test_an_api_key_can_never_impersonate(test_client: TestClient):
    """
    API keys share the Authorization header with JWTs, so the resolver must
    never read impersonation claims off one. It cannot: the ``drk_`` branch
    does not decode a payload at all.
    """
    target = test_client.first_user
    # A live session exists, so if a key could ever be treated as carrying
    # impersonation this is when it would show.
    impersonation_headers = _impersonating(test_client, target)
    probe = f'/v1/users/{target.id}'
    assert test_client.get(probe, headers=impersonation_headers).status_code == 200

    key_resp = test_client.post(
        '/v1/users/me/api-keys',
        json={'name': 'admin cron'},
        headers=_admin(test_client),
    )
    assert key_resp.status_code in (200, 201), key_resp.text
    secret = key_resp.json()['key']
    assert secret.startswith('drk_')
    assert is_impersonation_token(secret) is False

    # A write is the sharpest probe available: the impersonation token is
    # refused for one, so if the key were ever treated as carrying the live
    # session it would be refused too. It is not, because the drk_ branch
    # returns the key's owner without ever decoding a payload.
    key_headers = {'Authorization': f'Bearer {secret}'}
    assert (
        test_client.put(
            '/v1/users/me/preferences', json={'theme': 'dark'}, headers=key_headers
        ).status_code
        == 200
    )
    assert (
        test_client.put(
            '/v1/users/me/preferences',
            json={'theme': 'dark'},
            headers=impersonation_headers,
        ).status_code
        == 403
    )


def test_admin_cannot_impersonate_another_admin(test_client: TestClient):
    db = test_client.test_db_session
    other = db.query(DbUser).filter(DbUser.id == test_client.first_user.id).first()
    other.user_group = 'admin'
    db.commit()
    resp = _start(test_client, test_client.first_user)
    assert resp.status_code == 403
    assert 'admin' in resp.json()['message'].lower()


def test_target_promoted_mid_session_stops_being_impersonable(
    test_client: TestClient,
):
    headers = _impersonating(test_client, test_client.first_user)
    probe = f'/v1/users/{test_client.first_user.id}'
    assert test_client.get(probe, headers=headers).status_code == 200
    db = test_client.test_db_session
    promoted = db.query(DbUser).filter(DbUser.id == test_client.first_user.id).first()
    promoted.user_group = 'admin'
    db.commit()
    assert test_client.get(probe, headers=headers).status_code == 403


def test_stopping_kills_the_token_immediately(test_client: TestClient):
    headers = _impersonating(test_client, test_client.first_user)
    probe = f'/v1/users/{test_client.first_user.id}'
    assert test_client.get(probe, headers=headers).status_code == 200
    stop = test_client.delete('/v1/admin/impersonation', headers=_admin(test_client))
    assert stop.status_code == 200
    assert test_client.get(probe, headers=headers).status_code == 403


def test_expired_session_is_refused(test_client: TestClient):
    resp = _start(test_client, test_client.first_user)
    headers = {'Authorization': f"Bearer {resp.json()['token']}"}
    db = test_client.test_db_session
    row = (
        db.query(DbImpersonationSession)
        .filter(DbImpersonationSession.id == resp.json()['session_id'])
        .first()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    probe = f'/v1/users/{test_client.first_user.id}'
    assert test_client.get(probe, headers=headers).status_code == 403


def test_demoting_the_acting_admin_kills_the_session(test_client: TestClient):
    headers = _impersonating(test_client, test_client.first_user)
    db = test_client.test_db_session
    admin_row = db.query(DbUser).filter(DbUser.id == test_client.admin_user.id).first()
    admin_row.user_group = 'user'
    db.commit()
    probe = f'/v1/users/{test_client.first_user.id}'
    assert test_client.get(probe, headers=headers).status_code == 403


def test_start_and_stop_are_audited(test_client: TestClient):
    _impersonating(test_client, test_client.first_user)
    test_client.delete('/v1/admin/impersonation', headers=_admin(test_client))
    db = test_client.test_db_session
    actions = {row.action for row in db.query(DbAdminAuditLog).all()}
    assert 'admin.impersonation.start' in actions
    assert 'admin.impersonation.stop' in actions


def test_a_non_admin_cannot_start_impersonation(test_client: TestClient):
    resp = test_client.post(
        '/v1/admin/impersonation',
        json={'target_uuid': test_client.second_user.id},
        headers=_as(test_client.first_user),
    )
    assert resp.status_code == 403


def test_a_forged_impersonation_claim_on_an_ordinary_token_is_refused(
    test_client: TestClient,
):
    """
    A token whose ``typ`` says impersonation but which names no real session
    row must not resolve. Signed with the real key, so this proves the check
    is the session lookup and not merely the signature.
    """
    forged = create_access_token(
        {
            'sub': test_client.first_user.id,
            'act': test_client.admin_user.id,
            'typ': IMPERSONATION_TOKEN_TYPE,
            'sid': 'no-such-session',
        }
    )
    resp = test_client.get(
        f'/v1/users/{test_client.first_user.id}',
        headers={'Authorization': f'Bearer {forged}'},
    )
    assert resp.status_code == 403
