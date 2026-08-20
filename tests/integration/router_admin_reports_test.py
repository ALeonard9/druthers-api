# pylint: disable=missing-function-docstring
"""Behaviour tests for the admin product-health report contract (#342)."""

from datetime import datetime, timedelta, timezone

from app.db.models import DbProductEvent
from app.services.shelves import SHELVES


def _admin(test_client):
    return {'Authorization': f'Bearer {test_client.admin_user.token}'}


def _event(db, user_id, event_type, occurred_at, payload=None):
    db.add(
        DbProductEvent(
            user_id=user_id,
            event_type=event_type,
            payload=payload or {},
            occurred_at=occurred_at,
        )
    )


def test_reports_are_admin_only_and_signups_have_cumulative_csv(test_client):
    db = test_client.test_db_session
    first = test_client.first_user
    second = test_client.second_user
    first.created_at = datetime(2026, 8, 10, tzinfo=timezone.utc)
    second.created_at = datetime(2026, 8, 11, tzinfo=timezone.utc)
    db.commit()

    denied = test_client.get('/v1/admin/reports/signups')
    assert denied.status_code == 401
    response = test_client.get(
        '/v1/admin/reports/signups',
        headers=_admin(test_client),
        params={'from': '2026-08-10', 'to': '2026-08-11'},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['report'] == 'signups'
    assert body['series'] == [
        {'period': '2026-08-10', 'values': {'count': 1, 'cumulative': 1}},
        {'period': '2026-08-11', 'values': {'count': 1, 'cumulative': 2}},
    ]
    assert body['totals'] == {'count': 2}

    csv_response = test_client.get(
        '/v1/admin/reports/signups',
        headers=_admin(test_client),
        params={'from': '2026-08-10', 'to': '2026-08-11', 'format': 'csv'},
    )
    assert csv_response.headers['content-type'].startswith('text/csv')
    assert '2026-08-11,1,2' in csv_response.text


def test_tracking_volume_counts_each_shelf(test_client):
    db = test_client.test_db_session
    timestamp = datetime(2026, 8, 10, tzinfo=timezone.utc)
    for shelf in SHELVES:
        catalog = shelf.catalog_model(title=f'{shelf.category} report title')
        db.add(catalog)
        db.flush()
        tracker = shelf.tracker_model(
            user_id=test_client.first_user.pk,
            **{shelf.join_col: catalog.pk},
            on_rankings=True,
            on_watchlist=True,
            created_at=timestamp,
        )
        db.add(tracker)
    db.commit()

    response = test_client.get(
        '/v1/admin/reports/tracking_volume',
        headers=_admin(test_client),
        params={'from': '2026-08-10', 'to': '2026-08-10'},
    )
    assert response.status_code == 200
    values = response.json()['series'][0]['values']
    for shelf in SHELVES:
        assert values[f'{shelf.category}_tracked'] == 1
        assert values[f'{shelf.category}_ranked'] == 1
        assert values[f'{shelf.category}_watchlisted'] == 1


def test_funnel_reports_distinguish_empty_instrumentation_and_retention(test_client):
    db = test_client.test_db_session
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    empty = test_client.get(
        '/v1/admin/reports/retention',
        headers=_admin(test_client),
        params={'from': '2026-07-01', 'to': '2026-07-01'},
    )
    assert empty.json()['instrumented'] is False
    assert empty.json()['series'] == []

    _event(db, test_client.first_user.pk, 'signup_completed', start)
    _event(
        db, test_client.first_user.pk, 'fifth_item_ranked', start + timedelta(days=1)
    )
    _event(
        db, test_client.first_user.pk, 'profile_completed', start + timedelta(days=1)
    )
    _event(
        db, test_client.first_user.pk, 'returning_session', start + timedelta(days=8)
    )
    _event(
        db, test_client.first_user.pk, 'returning_session', start + timedelta(days=30)
    )
    db.commit()

    retention = test_client.get(
        '/v1/admin/reports/retention',
        headers=_admin(test_client),
        params={'from': '2026-07-01', 'to': '2026-07-01'},
    )
    values = retention.json()['series'][0]['values']
    assert retention.json()['instrumented'] is True
    assert values == {'cohort_size': 1, 'retained_d7': 1, 'retained_d28': 1}

    activation = test_client.get(
        '/v1/admin/reports/activation',
        headers=_admin(test_client),
        params={'from': '2026-07-01', 'to': '2026-07-01'},
    )
    assert activation.json()['series'][0]['values'] == {
        'cohort_size': 1,
        'activated': 1,
    }


def test_top_users_uses_the_tracked_user_label(test_client):
    db = test_client.test_db_session
    user = test_client.first_user
    user.handle = 'reporter'
    db.commit()
    timestamp = datetime(2026, 8, 10, tzinfo=timezone.utc)
    shelf = SHELVES[0]
    catalog = shelf.catalog_model(title='Counted movie')
    db.add(catalog)
    db.flush()
    db.add(
        shelf.tracker_model(
            user_id=user.pk,
            **{shelf.join_col: catalog.pk},
            on_rankings=True,
            created_at=timestamp,
        )
    )
    db.commit()
    response = test_client.get(
        '/v1/admin/reports/top_users',
        headers=_admin(test_client),
        params={'from': '2026-08-10', 'to': '2026-08-10'},
    )
    assert response.json()['series'] == []
    assert response.json()['rows'] == [
        {'label': 'reporter', 'count': 1, 'domain': None}
    ]


def test_remaining_reports_expose_real_aggregate_values(test_client):
    db = test_client.test_db_session
    user = test_client.first_user
    user.created_at = datetime(2026, 8, 10, tzinfo=timezone.utc)
    user.default_privacy = 'public'
    shelf = SHELVES[0]
    catalog = shelf.catalog_model(title='Popular movie')
    db.add(catalog)
    db.flush()
    db.add(
        shelf.tracker_model(
            user_id=user.pk,
            **{shelf.join_col: catalog.pk},
            on_rankings=True,
            created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            updated_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        )
    )
    _event(
        db,
        user.pk,
        'first_share',
        datetime(2026, 8, 10, tzinfo=timezone.utc),
        {'share_id': 'share-1'},
    )
    _event(
        db,
        test_client.second_user.pk,
        'signup_completed',
        datetime(2026, 8, 10, tzinfo=timezone.utc),
        {'share_id': 'share-1'},
    )
    db.commit()
    params = {'from': '2026-08-10', 'to': '2026-08-10'}

    active = test_client.get(
        '/v1/admin/reports/active_users', headers=_admin(test_client), params=params
    )
    assert active.json()['series'][0]['values'] == {'dau': 1, 'wau': 1}

    titles = test_client.get(
        '/v1/admin/reports/top_titles', headers=_admin(test_client), params=params
    )
    assert titles.json()['rows'] == [
        {'label': 'Popular movie', 'count': 1, 'domain': 'movies'}
    ]

    engagement = test_client.get(
        '/v1/admin/reports/engagement_by_tier',
        headers=_admin(test_client),
        params=params,
    )
    assert engagement.json()['series'][0]['values'] == {
        'private': 0,
        'friends': 0,
        'public': 1,
    }

    conversion = test_client.get(
        '/v1/admin/reports/conversion', headers=_admin(test_client), params=params
    )
    assert conversion.json()['series'][0]['values'] == {
        'cohort_size': 1,
        'signup_to_activation': 0,
        'share_cohort_size': 1,
        'share_to_signup': 1,
    }
