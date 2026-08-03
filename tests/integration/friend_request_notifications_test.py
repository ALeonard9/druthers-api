# pylint: disable=missing-function-docstring
"""
Friend-request notifications (#282).

An incoming request notifies the recipient; acceptance notifies the sender;
decline/cancel notify nobody and leave no live notification behind.
"""

from fastapi.testclient import TestClient


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


def _notifications(test_client: TestClient, token: str):
    return test_client.get('/v1/users/me/notifications', headers=_auth(token)).json()


def test_incoming_request_raises_exactly_one_notification(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    assert _send(test_client, test_client.first_user.token, 'blake').status_code == 202

    items = _notifications(test_client, test_client.second_user.token)
    assert len(items) == 1
    assert items[0]['type'] == 'friend_request'
    assert items[0]['category'] == 'friend_request'
    assert items[0]['read'] is False

    # Nothing for the sender yet — only the recipient is notified.
    assert _notifications(test_client, test_client.first_user.token) == []


def test_repeated_sweeps_add_no_duplicates(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')

    _notifications(test_client, test_client.second_user.token)
    items = _notifications(test_client, test_client.second_user.token)
    assert len(items) == 1


def test_accept_notifies_the_original_sender(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    incoming = _notifications(test_client, test_client.second_user.token)
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.second_user.token)
    ).json()['incoming'][0]['id']

    accepted = test_client.put(
        f'/v1/users/me/friends/requests/{request_id}/accept',
        headers=_auth(test_client.second_user.token),
    )
    assert accepted.status_code == 200

    sender_items = _notifications(test_client, test_client.first_user.token)
    assert len(sender_items) == 1
    assert sender_items[0]['type'] == 'friend_request_accepted'
    assert sender_items[0]['category'] == 'friend_request'

    # The recipient's original "wants to be friends" notification is
    # resolved — it would deep-link into a friendship they already acted on.
    recipient_items = _notifications(test_client, test_client.second_user.token)
    assert not any(item['type'] == 'friend_request' for item in recipient_items)
    assert incoming  # (sanity: there was something to resolve)


def test_decline_notifies_nobody_and_leaves_no_live_notification(
    test_client: TestClient,
):
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

    assert _notifications(test_client, test_client.first_user.token) == []
    assert _notifications(test_client, test_client.second_user.token) == []


def test_cancel_leaves_no_live_notification(test_client: TestClient):
    _claim_handle(test_client, test_client.second_user.token, 'blake')
    _send(test_client, test_client.first_user.token, 'blake')
    # Recipient reads once so the pending notification actually gets created.
    _notifications(test_client, test_client.second_user.token)
    request_id = test_client.get(
        '/v1/users/me/friends/requests', headers=_auth(test_client.first_user.token)
    ).json()['outgoing'][0]['id']

    cancelled = test_client.delete(
        f'/v1/users/me/friends/requests/{request_id}',
        headers=_auth(test_client.first_user.token),
    )
    assert cancelled.status_code == 204

    assert _notifications(test_client, test_client.second_user.token) == []
