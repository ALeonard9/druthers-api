'''
This file contains unit tests for the product analytics privacy contract in
app/analytics/contract.py, and asserts that the reproducible metric queries in
app/analytics/queries.py stay non-empty.
'''

import uuid

import pytest

from app.analytics.contract import ProductEvent, FORBIDDEN_PAYLOAD_KEYS
from app.analytics.queries import (
    ACTIVATION_QUERY,
    SIGNUP_TO_ACTIVATION_CONVERSION_QUERY,
    WEEKLY_ACTIVATION_QUERY,
    D7_RETENTION_QUERY,
    D28_RETENTION_QUERY,
    SHARE_TO_SIGNUP_CONVERSION_QUERY,
    WEEKLY_EVENT_COUNTS_QUERY,
)


def test_valid_event():
    '''
    Tests that a well-formed event is accepted unchanged.
    '''
    uid = uuid.uuid4()
    event = ProductEvent(
        user_id=uid, event_type='signup_completed', payload={'source': 'organic'}
    )
    assert event.event_type == 'signup_completed'
    assert event.user_id == uid


def test_profile_completed_event():
    '''
    Tests that an event with an empty payload is accepted.
    '''
    uid = uuid.uuid4()
    event = ProductEvent(user_id=uid, event_type='profile_completed', payload={})
    assert event.event_type == 'profile_completed'


def test_privacy_validation_forbids_keys():
    '''
    Tests that every forbidden key is caught as a substring: each is prefixed
    with 'user_' so an exact-match check would let all of them through.
    '''
    uid = uuid.uuid4()
    for forbidden in FORBIDDEN_PAYLOAD_KEYS:
        with pytest.raises(ValueError, match='Privacy violation'):
            ProductEvent(
                user_id=uid,
                event_type='first_share',
                payload={f"user_{forbidden}": 'secret'},
            )


def test_privacy_validation_forbids_emails_in_values():
    '''
    Tests that an email-shaped value is rejected even under an allowed key,
    where the key-name check alone would not fire.
    '''
    uid = uuid.uuid4()
    with pytest.raises(ValueError, match='looks like an email'):
        ProductEvent(
            user_id=uid,
            event_type='invite_opened',
            payload={'recipient': 'adam@druthers.io'},
        )


def test_invalid_event_type():
    '''
    Tests that an event type outside the agreed vocabulary is rejected.
    '''
    uid = uuid.uuid4()
    with pytest.raises(ValueError, match='Unknown event type'):
        ProductEvent(user_id=uid, event_type='random_unsupported_event', payload={})


def test_queries_are_defined():
    '''
    Tests that every exported metric query is real SQL against product_events,
    catching a query renamed or emptied without its caller noticing.
    '''
    queries = [
        ACTIVATION_QUERY,
        SIGNUP_TO_ACTIVATION_CONVERSION_QUERY,
        WEEKLY_ACTIVATION_QUERY,
        D7_RETENTION_QUERY,
        D28_RETENTION_QUERY,
        SHARE_TO_SIGNUP_CONVERSION_QUERY,
        WEEKLY_EVENT_COUNTS_QUERY,
    ]
    for q in queries:
        assert 'SELECT' in q.upper()
        assert 'product_events' in q

    # Assert fix 1: Queries group by cohort period
    assert 'GROUP BY' in ACTIVATION_QUERY
    assert 'GROUP BY' in SIGNUP_TO_ACTIVATION_CONVERSION_QUERY
    assert 'GROUP BY' in SHARE_TO_SIGNUP_CONVERSION_QUERY

    # Assert fix 2: Retention windows
    assert '+ 7' in D7_RETENTION_QUERY and '+ 13' in D7_RETENTION_QUERY
    assert '+ 28' in D28_RETENTION_QUERY and '+ 34' in D28_RETENTION_QUERY

    # Assert fix 3: Activation uses profile_completed
    assert 'profile_completed' in ACTIVATION_QUERY
    assert 'profile_completed' in SIGNUP_TO_ACTIVATION_CONVERSION_QUERY
