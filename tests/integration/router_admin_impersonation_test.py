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


WRITE_BLOCK_MESSAGE = 'This view-as session is read-only. End it to act as yourself.'


def test_impersonation_refuses_every_write(test_client: TestClient):
    """
    Asserting only the status code here would also pass against a build
    where the feature is completely dead (``_resolve_impersonation``
    raising 403 unconditionally, before ever reaching the write-block
    check) - the message is what proves this specific check fired.
    """
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
        assert (
            resp.json()['message'] == WRITE_BLOCK_MESSAGE
        ), f'{method} {path} was refused for the wrong reason: {resp.json()}'

    db = test_client.test_db_session
    owner_pk = (
        db.query(DbUser).filter(DbUser.id == test_client.first_user.id).first().pk
    )
    assert db.query(DbApiKey).filter(DbApiKey.user_id == owner_pk).count() == 0


def test_impersonation_refused_writes_are_audited(test_client: TestClient):
    """
    A denied *start* was already audited; a denied *write* from an already-
    live session previously left no trace at all - the more dangerous of
    the two attempts was the one going unrecorded.
    """
    target = test_client.first_user
    headers = _impersonating(test_client, target)
    db = test_client.test_db_session
    before = db.query(DbAdminAuditLog).count()

    resp = test_client.put(
        '/v1/users/me/preferences', json={'theme': 'dark'}, headers=headers
    )
    assert resp.status_code == 403

    db.expire_all()
    row = db.query(DbAdminAuditLog).order_by(DbAdminAuditLog.pk.desc()).first()
    assert db.query(DbAdminAuditLog).count() == before + 1
    assert row.action == 'admin.impersonation.write_blocked'
    assert row.result == 'denied'
    assert row.status_code == 403
    # The acting admin, not the target whose identity the token swapped in -
    # the same attribution rule as the admin.access denial below.
    assert row.actor_user_pk == test_client.admin_user.pk
    assert row.target_user_pk == target.pk


def test_impersonation_cannot_reach_admin_routes(test_client: TestClient):
    """
    The two GETs alone would pass under a plain non-admin identity too -
    ``require_admin`` refuses those regardless of impersonation, so they
    only prove the swap resolved to a non-admin, not that impersonation
    itself is blocked from the admin surface. ``DELETE /v1/admin/
    impersonation`` is the case that actually distinguishes them: it is a
    write, so the read-only rule has to refuse it before ``require_admin``
    is ever reached - and it is exactly the route a compromised or
    careless impersonation token would want to reach, to end (or interfere
    with) a session that is not its own.
    """
    headers = _impersonating(test_client, test_client.first_user)
    for path in ('/v1/admin/users', '/v1/admin/audit'):
        assert test_client.get(path, headers=headers).status_code == 403
    stop_resp = test_client.delete('/v1/admin/impersonation', headers=headers)
    assert stop_resp.status_code == 403
    assert stop_resp.json()['message'] == WRITE_BLOCK_MESSAGE


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
    """
    ``'admin' in message.lower()`` would also pass against
    ``require_admin``'s own 'Admin privileges required' - not a message
    this endpoint could even produce here, since the caller already IS an
    admin, but a weak enough assertion to miss the swap. Assert the exact,
    specific refusal instead.
    """
    db = test_client.test_db_session
    other = db.query(DbUser).filter(DbUser.id == test_client.first_user.id).first()
    other.user_group = 'admin'
    db.commit()
    resp = _start(test_client, test_client.first_user)
    assert resp.status_code == 403
    assert resp.json()['message'] == 'An admin cannot be impersonated'


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
    """
    Checking only that the action strings exist would also pass with the
    detail column silently empty - which is exactly the defect this pins:
    ``session_id``/``reason``/``ended`` were dropped by the redact
    allowlist until they were added to it, and the commit message claiming
    ``session_id`` was recorded was wrong.
    """
    target = test_client.first_user
    start_resp = _start(test_client, target, reason='diagnosing a bug')
    session_id = start_resp.json()['session_id']
    test_client.delete('/v1/admin/impersonation', headers=_admin(test_client))

    db = test_client.test_db_session
    db.expire_all()
    start_row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.impersonation.start')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert start_row is not None
    assert start_row.result == 'allowed'
    assert start_row.target_user_pk == target.pk
    assert start_row.detail == {
        'session_id': session_id,
        'reason': 'diagnosing a bug',
    }

    stop_row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.impersonation.stop')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert stop_row is not None
    assert stop_row.result == 'allowed'
    # One row per ended session names which target it was, so with more
    # than one live session the trail can still say who was being viewed.
    assert stop_row.target_user_pk == target.pk


def test_stopping_with_nothing_live_still_leaves_a_row(test_client: TestClient):
    db = test_client.test_db_session
    before = db.query(DbAdminAuditLog).count()
    resp = test_client.delete('/v1/admin/impersonation', headers=_admin(test_client))
    assert resp.status_code == 200
    assert resp.json() == {'ended': 0}

    db.expire_all()
    row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.impersonation.stop')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert db.query(DbAdminAuditLog).count() == before + 1
    assert row.target_user_pk is None
    assert row.detail == {'ended': 0}


def test_a_second_start_leaves_the_first_session_live(test_client: TestClient):
    first_target = test_client.first_user
    second_target = test_client.second_user
    first_headers = _impersonating(test_client, first_target)
    second_headers = _impersonating(test_client, second_target)

    probe_first = f'/v1/users/{first_target.id}'
    probe_second = f'/v1/users/{second_target.id}'
    assert test_client.get(probe_first, headers=first_headers).status_code == 200
    assert test_client.get(probe_second, headers=second_headers).status_code == 200

    stop = test_client.delete('/v1/admin/impersonation', headers=_admin(test_client))
    assert stop.status_code == 200
    assert stop.json() == {'ended': 2}
    assert test_client.get(probe_first, headers=first_headers).status_code == 403
    assert test_client.get(probe_second, headers=second_headers).status_code == 403


def test_denied_admin_route_while_impersonating_is_attributed_to_the_admin(
    test_client: TestClient,
):
    """
    The bug: the denial middleware re-decodes the raw token to attribute a
    request that never reached a handler, and an impersonation token's
    resolved identity is the swapped-in target - so without the fix, this
    denial would land on the TARGET's permanent audit record instead of
    the admin who was actually at the keyboard.
    """
    target = test_client.first_user
    headers = _impersonating(test_client, target)
    db = test_client.test_db_session
    before = db.query(DbAdminAuditLog).count()

    resp = test_client.get('/v1/admin/users', headers=headers)
    assert resp.status_code == 403

    db.expire_all()
    row = db.query(DbAdminAuditLog).order_by(DbAdminAuditLog.pk.desc()).first()
    assert db.query(DbAdminAuditLog).count() == before + 1
    assert row.action == 'admin.access'
    assert row.result == 'denied'
    assert row.actor_user_pk == test_client.admin_user.pk
    assert row.actor_user_pk != target.pk
    assert row.detail == {'via_impersonation': True}


def test_sign_out_ends_a_live_impersonation_session(test_client: TestClient):
    admin_signin = test_client.post(
        '/v1/auth/token',
        files={
            'username': (None, test_client.admin_user.email),
            'password': (None, test_client.admin_user.plain_password),
        },
    )
    assert admin_signin.status_code == 200
    admin_refresh_token = admin_signin.json()['refresh_token']

    headers = _impersonating(test_client, test_client.first_user)
    probe = f'/v1/users/{test_client.first_user.id}'
    assert test_client.get(probe, headers=headers).status_code == 200

    logout = test_client.post(
        '/v1/auth/logout', json={'refresh_token': admin_refresh_token}
    )
    assert logout.status_code == 204

    assert test_client.get(probe, headers=headers).status_code == 403


def test_get_optional_current_user_under_impersonation_serves_the_target(
    test_client: TestClient,
):
    """
    ``GET /v1/public/{handle}`` resolves its optional viewer through
    ``get_optional_current_user``, the one dependency in this file not
    exercised by the other tests. Now that ``request`` is a required,
    forwarded parameter there instead of an omitted one, it must not error
    - and the resolved viewer must be the TARGET (a private profile is
    visible to its own owner, but not to a stranger), proving the swap
    reaches this second entry point too, not just ``get_current_user``.
    """
    target = test_client.first_user
    target.handle = 'impersonation-optional-probe'
    target.visibility_profile = 'private'
    db = test_client.test_db_session
    db.commit()

    admin_headers = _admin(test_client)
    as_admin = test_client.get(f'/v1/public/{target.handle}', headers=admin_headers)
    assert as_admin.status_code == 404

    headers = _impersonating(test_client, target)
    as_target = test_client.get(f'/v1/public/{target.handle}', headers=headers)
    assert as_target.status_code == 200


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


def _token_for(test_client, user, test_authenticate_user):
    return test_authenticate_user(test_client, user.email, user.plain_password)


def test_list_impersonation_sessions_is_admin_wide(
    test_client: TestClient, test_create_admin_user, test_authenticate_user
):
    """
    Scoped to every admin, not just the caller - listed here by an admin
    who did NOT start the session, matching GET /v1/admin/audit's own
    whole-trail (not self-scoped) precedent.
    """
    other_admin = test_create_admin_user(test_client)[0]
    other_admin_headers = {
        'Authorization': f'Bearer {_token_for(test_client, other_admin, test_authenticate_user)}'
    }

    target = test_client.first_user
    start_resp = _start(test_client, target)
    assert start_resp.status_code == 200
    session_id = start_resp.json()['session_id']

    listing = test_client.get('/v1/admin/impersonation', headers=other_admin_headers)
    assert listing.status_code == 200
    sessions = listing.json()['sessions']
    matching = next(s for s in sessions if s['session_id'] == session_id)
    assert matching['acting_admin']['id'] == test_client.admin_user.id
    assert matching['target']['id'] == target.id
    assert matching['started_at'].endswith('Z')
    assert matching['expires_at'].endswith('Z')
    assert 'token' not in matching


def test_list_impersonation_sessions_excludes_ended(test_client: TestClient):
    target = test_client.first_user
    start_resp = _start(test_client, target)
    session_id = start_resp.json()['session_id']
    test_client.delete(
        f'/v1/admin/impersonation/{session_id}', headers=_admin(test_client)
    )

    listing = test_client.get('/v1/admin/impersonation', headers=_admin(test_client))
    assert not any(s['session_id'] == session_id for s in listing.json()['sessions'])


def test_list_impersonation_sessions_excludes_expired(test_client: TestClient):
    target = test_client.first_user
    start_resp = _start(test_client, target)
    session_id = start_resp.json()['session_id']
    db = test_client.test_db_session
    row = (
        db.query(DbImpersonationSession)
        .filter(DbImpersonationSession.id == session_id)
        .first()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    listing = test_client.get('/v1/admin/impersonation', headers=_admin(test_client))
    assert not any(s['session_id'] == session_id for s in listing.json()['sessions'])


def test_stop_specific_session_ends_only_that_one(test_client: TestClient):
    first_target = test_client.first_user
    second_target = test_client.second_user
    first_headers = _impersonating(test_client, first_target)
    second_start = _start(test_client, second_target)
    session_id_two = second_start.json()['session_id']
    second_headers = {'Authorization': f"Bearer {second_start.json()['token']}"}

    probe_first = f'/v1/users/{first_target.id}'
    probe_second = f'/v1/users/{second_target.id}'
    assert test_client.get(probe_first, headers=first_headers).status_code == 200
    assert test_client.get(probe_second, headers=second_headers).status_code == 200

    stop = test_client.delete(
        f'/v1/admin/impersonation/{session_id_two}', headers=_admin(test_client)
    )
    assert stop.status_code == 200
    assert stop.json() == {'ended': 1}

    assert test_client.get(probe_first, headers=first_headers).status_code == 200
    assert test_client.get(probe_second, headers=second_headers).status_code == 403


def test_stop_specific_session_is_idempotent(test_client: TestClient):
    unknown = test_client.delete(
        '/v1/admin/impersonation/no-such-session', headers=_admin(test_client)
    )
    assert unknown.status_code == 200
    assert unknown.json() == {'ended': 0}

    target = test_client.first_user
    start_resp = _start(test_client, target)
    session_id = start_resp.json()['session_id']
    first_stop = test_client.delete(
        f'/v1/admin/impersonation/{session_id}', headers=_admin(test_client)
    )
    assert first_stop.json() == {'ended': 1}
    second_stop = test_client.delete(
        f'/v1/admin/impersonation/{session_id}', headers=_admin(test_client)
    )
    assert second_stop.json() == {'ended': 0}


def test_stop_specific_session_is_admin_wide(
    test_client: TestClient, test_create_admin_user, test_authenticate_user
):
    """
    Console oversight, not just self-cleanup: another admin can revoke a
    session they did not start - the whole point of a listing anyone can
    act on rather than a "my sessions only" view.
    """
    other_admin = test_create_admin_user(test_client)[0]
    other_admin_headers = {
        'Authorization': f'Bearer {_token_for(test_client, other_admin, test_authenticate_user)}'
    }

    target = test_client.first_user
    start_resp = _start(test_client, target)
    session_id = start_resp.json()['session_id']
    imp_headers = {'Authorization': f"Bearer {start_resp.json()['token']}"}
    probe = f'/v1/users/{target.id}'
    assert test_client.get(probe, headers=imp_headers).status_code == 200

    stop = test_client.delete(
        f'/v1/admin/impersonation/{session_id}', headers=other_admin_headers
    )
    assert stop.status_code == 200
    assert stop.json() == {'ended': 1}
    assert test_client.get(probe, headers=imp_headers).status_code == 403


def test_list_and_stop_by_id_are_audited(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.first_user
    start_resp = _start(test_client, target)
    session_id = start_resp.json()['session_id']

    test_client.get('/v1/admin/impersonation', headers=_admin(test_client))
    test_client.delete(
        f'/v1/admin/impersonation/{session_id}', headers=_admin(test_client)
    )

    db.expire_all()
    list_row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.impersonation.list')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert list_row is not None
    assert list_row.result == 'allowed'
    assert list_row.detail['live_count'] >= 1

    stop_row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.impersonation.stop')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert stop_row is not None
    assert stop_row.result == 'allowed'
    assert stop_row.target_user_pk == target.pk
    assert stop_row.detail == {'session_id': session_id, 'ended': 1}


def test_a_non_admin_cannot_list_or_stop_a_specific_session(test_client: TestClient):
    target = test_client.first_user
    start_resp = _start(test_client, target)
    session_id = start_resp.json()['session_id']
    headers = _as(test_client.second_user)

    assert (
        test_client.get('/v1/admin/impersonation', headers=headers).status_code == 403
    )
    assert (
        test_client.delete(
            f'/v1/admin/impersonation/{session_id}', headers=headers
        ).status_code
        == 403
    )
