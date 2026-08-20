# pylint: disable=missing-module-docstring, missing-function-docstring
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.models import DbAdminAuditLog, DbFollow, DbFriendship, DbUser
from app.services.friendships import FriendshipStatus
from app.services.shelves import SHELVES

ADMIN_PATHS = ('/v1/admin/users', '/v1/admin/audit')


def _admin(test_client: TestClient) -> dict:
    return {'Authorization': f"Bearer {test_client.admin_user.token}"}


def _as(user) -> dict:
    return {'Authorization': f"Bearer {user.token}"}


def _track(test_client: TestClient, user_pk: int, category: str, **overrides):
    """Insert one tracker row for ``user_pk`` in the named shelf's domain."""
    shelf = next(shelf for shelf in SHELVES if shelf.category == category)
    db = test_client.test_db_session
    catalog_row = shelf.catalog_model(
        title=f"{category} title {overrides.get('rank', 'x')}"
    )
    db.add(catalog_row)
    db.flush()
    tracker_row = shelf.tracker_model(
        user_id=user_pk, **{shelf.join_col: catalog_row.pk}, **overrides
    )
    db.add(tracker_row)
    db.commit()
    return tracker_row


def test_admin_routes_require_admin(test_client: TestClient):
    headers = _as(test_client.first_user)
    for path in ADMIN_PATHS:
        resp = test_client.get(path, headers=headers)
        assert resp.status_code == 403
        assert resp.json()['message'] == 'Admin privileges required'

    detail_resp = test_client.get(
        f'/v1/admin/users/{test_client.second_user.id}', headers=headers
    )
    assert detail_resp.status_code == 403
    assert detail_resp.json()['message'] == 'Admin privileges required'


def test_admin_routes_reject_anonymous(test_client: TestClient):
    for path in ADMIN_PATHS:
        assert test_client.get(path).status_code == 401


def test_admin_denial_is_audited(test_client: TestClient):
    db = test_client.test_db_session
    before = db.query(DbAdminAuditLog).count()

    headers = _as(test_client.first_user)
    headers['X-Forwarded-For'] = '203.0.113.9'
    resp = test_client.get('/v1/admin/users', headers=headers)
    assert resp.status_code == 403

    db.expire_all()
    rows = db.query(DbAdminAuditLog).order_by(DbAdminAuditLog.pk.desc()).all()
    assert len(rows) == before + 1
    row = rows[0]
    assert row.result == 'denied'
    assert row.actor_user_pk == test_client.first_user.pk
    assert row.actor_user_id == test_client.first_user.id
    assert row.actor_email == test_client.first_user.email
    assert row.path == '/v1/admin/users'
    assert row.status_code == 403
    assert row.request_id
    # Rightmost X-Forwarded-For hop (rate_limit.client_ip's convention), not
    # the connecting peer - behind Cloud Run/Cloudflare that peer is always
    # the ingress proxy, never the real caller.
    assert row.source_ip == '203.0.113.9'


def test_admin_anonymous_probe_is_audited(test_client: TestClient):
    """
    A 401 (missing/expired token) is a denial too - a prober who never even
    authenticates is exactly the case an admin surface's audit trail should
    not go blind on.
    """
    db = test_client.test_db_session
    before = db.query(DbAdminAuditLog).count()

    resp = test_client.get('/v1/admin/users')
    assert resp.status_code == 401

    db.expire_all()
    rows = db.query(DbAdminAuditLog).order_by(DbAdminAuditLog.pk.desc()).all()
    assert len(rows) == before + 1
    row = rows[0]
    assert row.result == 'denied'
    assert row.actor_user_pk is None
    assert row.status_code == 401


def test_admin_search_by_display_name_handle_and_email(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.first_user
    target.display_name = 'Charlie Findable'
    target.handle = 'findable-handle'
    target.email = 'findable@example.com'
    db.commit()

    by_name = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'q': 'charlie find'}
    )
    assert by_name.status_code == 200
    assert target.id in [u['id'] for u in by_name.json()['users']]

    by_handle = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'q': 'findable-han'}
    )
    assert target.id in [u['id'] for u in by_handle.json()['users']]

    by_email = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'q': 'FINDABLE@EXAMPLE'}
    )
    assert target.id in [u['id'] for u in by_email.json()['users']]

    no_match = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'q': 'no-such-user-xyz'}
    )
    assert no_match.json()['users'] == []


def test_admin_search_pagination_and_total(test_client: TestClient, test_create_user):
    test_create_user(test_client, user_count=5)
    db = test_client.test_db_session
    expected_total = db.query(DbUser).count()
    # Same tiebreaker as the endpoint (created_at desc, pk desc) - a
    # secondary sort on a non-unique created_at, so this is the one order
    # the endpoint is allowed to return, not just "an" order.
    expected_ids = [
        user.id
        for user in db.query(DbUser)
        .order_by(DbUser.created_at.desc(), DbUser.pk.desc())
        .all()
    ]

    first_page = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'limit': 2, 'offset': 0}
    )
    assert first_page.status_code == 200
    body = first_page.json()
    assert body['total'] == expected_total
    assert body['limit'] == 2
    assert body['offset'] == 0
    assert [u['id'] for u in body['users']] == expected_ids[0:2]

    second_page = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'limit': 2, 'offset': 2}
    )
    assert [u['id'] for u in second_page.json()['users']] == expected_ids[2:4]


def test_admin_search_status_filter(test_client: TestClient, test_create_user):
    disabled_user = test_create_user(test_client, user_count=1)[0]
    db = test_client.test_db_session
    disabled_user.disabled_at = datetime.now(timezone.utc)
    db.commit()

    active_resp = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'status': 'active'}
    )
    assert active_resp.status_code == 200
    active_ids = [u['id'] for u in active_resp.json()['users']]
    assert disabled_user.id not in active_ids
    assert test_client.first_user.id in active_ids

    disabled_resp = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'status': 'disabled'}
    )
    assert disabled_resp.status_code == 200
    disabled_body = disabled_resp.json()
    assert disabled_body['total'] == 1
    assert [u['id'] for u in disabled_body['users']] == [disabled_user.id]
    assert disabled_body['users'][0]['status'] == 'disabled'


def test_admin_search_sort_by_joined(test_client: TestClient, test_create_user):
    """
    Sorting has to be corpus-wide, in SQL, not client-side on the loaded
    page - a page of 2 sorted client-side would answer "oldest accounts
    first" correctly for those 2 and silently wrong for everyone else.
    Proven here by sorting ascending with a limit smaller than the corpus
    and checking the returned page is still the true oldest-first prefix.
    """
    test_create_user(test_client, user_count=5)
    db = test_client.test_db_session
    expected_ids = [
        user.id
        for user in db.query(DbUser)
        .order_by(DbUser.created_at.asc(), DbUser.pk.desc())
        .all()
    ]

    resp = test_client.get(
        '/v1/admin/users',
        headers=_admin(test_client),
        params={'sort': 'joined', 'direction': 'asc', 'limit': 3},
    )
    assert resp.status_code == 200
    assert [u['id'] for u in resp.json()['users']] == expected_ids[:3]


def test_admin_search_sort_by_status(test_client: TestClient, test_create_user):
    disabled_user = test_create_user(test_client, user_count=1)[0]
    db = test_client.test_db_session
    disabled_user.disabled_at = datetime.now(timezone.utc)
    db.commit()

    resp = test_client.get(
        '/v1/admin/users',
        headers=_admin(test_client),
        params={'sort': 'status', 'direction': 'desc'},
    )
    assert resp.status_code == 200
    users = resp.json()['users']
    # Disabled (1) sorts before active (0) under direction=desc.
    assert users[0]['id'] == disabled_user.id
    assert users[0]['status'] == 'disabled'


def test_admin_search_sort_by_tracked_total(test_client: TestClient, test_create_user):
    heavy_user, light_user = test_create_user(test_client, user_count=2)
    _track(test_client, heavy_user.pk, 'movies', on_rankings=True, rank=1)
    _track(test_client, heavy_user.pk, 'books', on_watchlist=True)
    _track(test_client, light_user.pk, 'movies', on_watchlist=True)

    resp = test_client.get(
        '/v1/admin/users',
        headers=_admin(test_client),
        params={'sort': 'tracked_total', 'direction': 'desc', 'limit': 2},
    )
    assert resp.status_code == 200
    users = resp.json()['users']
    assert users[0]['id'] == heavy_user.id
    assert users[0]['tracked_total'] == 2
    assert users[1]['id'] == light_user.id
    assert users[1]['tracked_total'] == 1


def test_admin_search_sort_by_last_tracked(test_client: TestClient, test_create_user):
    older_user, newer_user = test_create_user(test_client, user_count=2)
    older_row = _track(test_client, older_user.pk, 'movies', on_watchlist=True)
    newer_row = _track(test_client, newer_user.pk, 'movies', on_watchlist=True)
    db = test_client.test_db_session
    older_row.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    newer_row.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.commit()

    resp = test_client.get(
        '/v1/admin/users',
        headers=_admin(test_client),
        params={'sort': 'last_tracked', 'direction': 'desc', 'limit': 2},
    )
    assert resp.status_code == 200
    users = resp.json()['users']
    assert users[0]['id'] == newer_user.id
    assert users[1]['id'] == older_user.id


def test_admin_user_detail_aggregates(test_client: TestClient, test_create_user):
    friend, follower = test_create_user(test_client, user_count=2)
    target = test_client.first_user

    _track(test_client, target.pk, 'movies', on_rankings=True, rank=1)
    _track(test_client, target.pk, 'movies', on_watchlist=True)
    _track(test_client, target.pk, 'books', on_rankings=True, rank=1)

    db = test_client.test_db_session
    db.add(
        DbFriendship(
            user_low_id=min(target.pk, friend.pk),
            user_high_id=max(target.pk, friend.pk),
            requested_by_id=friend.pk,
            status=FriendshipStatus.ACCEPTED,
        )
    )
    db.add(DbFollow(follower_id=follower.pk, followee_id=target.pk))
    db.commit()

    resp = test_client.get(f'/v1/admin/users/{target.id}', headers=_admin(test_client))
    assert resp.status_code == 200
    body = resp.json()

    assert body['id'] == target.id
    assert body['status'] == 'active'
    assert body['domains']['movies'] == {'ranked': 1, 'watchlist': 1, 'total': 2}
    assert body['domains']['books'] == {'ranked': 1, 'watchlist': 0, 'total': 1}
    assert body['domains']['tv'] == {'ranked': 0, 'watchlist': 0, 'total': 0}
    assert body['domains']['games'] == {'ranked': 0, 'watchlist': 0, 'total': 0}
    assert body['social'] == {'friends': 1, 'followers': 1, 'following': 0}
    assert body['visibility']['profile'] == target.visibility_profile
    assert body['visibility']['share_activity'] == target.share_activity


def test_admin_user_detail_not_found(test_client: TestClient):
    resp = test_client.get('/v1/admin/users/not-a-real-id', headers=_admin(test_client))
    assert resp.status_code == 404


def test_admin_reads_are_audited(test_client: TestClient):
    db = test_client.test_db_session
    target = test_client.second_user

    search_resp = test_client.get(
        '/v1/admin/users', headers=_admin(test_client), params={'q': ''}
    )
    assert search_resp.status_code == 200

    detail_resp = test_client.get(
        f'/v1/admin/users/{target.id}', headers=_admin(test_client)
    )
    assert detail_resp.status_code == 200

    db.expire_all()
    search_row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.user.search')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert search_row is not None
    assert search_row.result == 'allowed'
    assert search_row.actor_user_pk == test_client.admin_user.pk
    assert search_row.status_code == 200
    assert search_row.request_id

    view_row = (
        db.query(DbAdminAuditLog)
        .filter(DbAdminAuditLog.action == 'admin.user.view')
        .order_by(DbAdminAuditLog.pk.desc())
        .first()
    )
    assert view_row is not None
    assert view_row.target_user_pk == target.pk
    assert view_row.target_email == target.email
    assert view_row.status_code == 200
    assert view_row.request_id
    # Same request never reused the search's id - each carries its own.
    assert view_row.request_id != search_row.request_id


def test_admin_datetime_fields_carry_a_utc_designator(test_client: TestClient):
    """
    A naive ISO string (no ``Z``/offset) parses as *local* time under
    ECMAScript, so every JS client would silently shift these by its own
    UTC offset even though the underlying values are UTC. Assert on the
    wire string itself, not a round-trip through Python's own datetime
    parser - that would happily accept the broken naive form too and prove
    nothing (api#344 review).
    """
    list_resp = test_client.get('/v1/admin/users', headers=_admin(test_client))
    assert list_resp.status_code == 200
    users = list_resp.json()['users']
    assert users
    assert users[0]['created_at'].endswith('Z')

    target = test_client.first_user
    detail_resp = test_client.get(
        f'/v1/admin/users/{target.id}', headers=_admin(test_client)
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()['created_at'].endswith('Z')

    audit_resp = test_client.get('/v1/admin/audit', headers=_admin(test_client))
    assert audit_resp.status_code == 200
    events = audit_resp.json()['events']
    assert events
    assert events[0]['created_at'].endswith('Z')


def test_admin_audit_trail_lists_and_filters(test_client: TestClient):
    target = test_client.second_user
    headers = _admin(test_client)
    headers['X-Forwarded-For'] = '203.0.113.42'
    test_client.get(f'/v1/admin/users/{target.id}', headers=headers)

    resp = test_client.get(
        '/v1/admin/audit',
        headers=_admin(test_client),
        params={'action': 'admin.user.view', 'target': target.email},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] >= 1
    event = body['events'][0]
    assert event['action'] == 'admin.user.view'
    assert event['result'] == 'allowed'
    assert event['target']['email'] == target.email
    assert event['actor']['id'] == test_client.admin_user.id
    assert event['method'] == 'GET'
    assert event['path'] == f'/v1/admin/users/{target.id}'
    assert event['status_code'] == 200
    assert event['request_id']
    # Attribution is the point of an audit trail - the console has to be
    # able to answer "where was this taken from" without a psql session.
    # user_agent is deliberately not exposed alongside it (too long, wrecks
    # a table row) - it stays database-only.
    assert event['source_ip'] == '203.0.113.42'
    assert 'user_agent' not in event


def test_admin_audit_target_filter_accepts_a_handle(test_client: TestClient):
    """
    The console's audit table renders the TARGET column as a handle, so
    the natural motion is to read one there and paste it into the filter.
    Before this, that produced a confident "no events match" for a target
    with 30 real rows (api#341 review) - only email/id resolved.
    """
    target = test_client.second_user
    target.handle = 'audit-target-handle'
    test_client.test_db_session.commit()
    test_client.get(f'/v1/admin/users/{target.id}', headers=_admin(test_client))

    resp = test_client.get(
        '/v1/admin/audit',
        headers=_admin(test_client),
        params={'action': 'admin.user.view', 'target': 'audit-target-handle'},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] >= 1
    assert body['events'][0]['target']['id'] == target.id


def test_admin_audit_actor_and_target_filters_combine(test_client: TestClient):
    """
    Both filters join DbUser - via distinct aliases, since joining the same
    unaliased table twice for one query is invalid SQL. Exercising both
    together is what would have caught a regression to a bare (unaliased)
    second join.
    """
    target = test_client.second_user
    test_client.get(f'/v1/admin/users/{target.id}', headers=_admin(test_client))

    resp = test_client.get(
        '/v1/admin/audit',
        headers=_admin(test_client),
        params={
            'actor': test_client.admin_user.handle,
            'target': target.email,
            'action': 'admin.user.view',
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['total'] >= 1
    event = body['events'][0]
    assert event['actor']['id'] == test_client.admin_user.id
    assert event['target']['id'] == target.id


def test_admin_audit_trail_excludes_its_own_reads_by_default(test_client: TestClient):
    """
    Reading the trail is itself audited (``admin.audit.view``), but the
    default (unfiltered) listing must not include those rows - otherwise
    paging through the trail during an investigation keeps pushing the
    events under investigation off page one.
    """
    # At least one admin.audit.view row to try to hide.
    test_client.get('/v1/admin/audit', headers=_admin(test_client))

    default_view = test_client.get('/v1/admin/audit', headers=_admin(test_client))
    assert default_view.status_code == 200
    actions = {event['action'] for event in default_view.json()['events']}
    assert 'admin.audit.view' not in actions

    # Still retrievable when asked for explicitly.
    filtered_view = test_client.get(
        '/v1/admin/audit',
        headers=_admin(test_client),
        params={'action': 'admin.audit.view'},
    )
    assert filtered_view.status_code == 200
    assert filtered_view.json()['total'] >= 1
    assert all(
        event['action'] == 'admin.audit.view'
        for event in filtered_view.json()['events']
    )
