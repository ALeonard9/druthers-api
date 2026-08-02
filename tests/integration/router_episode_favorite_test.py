# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient


def _auth(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


def _show_with_episode(test_client: TestClient):
    admin = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    show_id = test_client.post(
        '/v1/tv-shows',
        headers=admin,
        json={'title': 'Rick and Morty', 'imdb': 'tt2861424'},
    ).json()['id']
    episode_id = test_client.post(
        f'/v1/tv-shows/{show_id}/episodes',
        headers=admin,
        json={'title': 'Mortgully', 'season': 9, 'season_number': 6},
    ).json()['id']
    return show_id, episode_id


def _get_mark(test_client: TestClient, show_id: str, episode_id: str) -> dict | None:
    marks = test_client.get(
        f'/v1/users/me/tv-shows/{show_id}/episodes', headers=_auth(test_client)
    ).json()
    return next((m for m in marks if m['episode']['id'] == episode_id), None)


def test_favoriting_stamps_favorited_at(test_client: TestClient):
    _, episode_id = _show_with_episode(test_client)
    body = test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    ).json()
    assert body['favorited'] is True
    assert body['favorited_at'] is not None


def test_refavoriting_preserves_original_favorited_at(test_client: TestClient):
    _, episode_id = _show_with_episode(test_client)
    first = test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    ).json()['favorited_at']
    second = test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    ).json()['favorited_at']
    assert second == first


def test_favorite_does_not_mark_watched(test_client: TestClient):
    _, episode_id = _show_with_episode(test_client)
    body = test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    ).json()
    assert not body['watched']


def test_unwatching_a_favorited_episode_keeps_the_favorite(test_client: TestClient):
    show_id, episode_id = _show_with_episode(test_client)
    test_client.post(f'/v1/users/me/episodes/{episode_id}', headers=_auth(test_client))
    test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    )
    resp = test_client.delete(
        f'/v1/users/me/episodes/{episode_id}', headers=_auth(test_client)
    )
    assert resp.status_code == 204
    mark = _get_mark(test_client, show_id, episode_id)
    assert mark is not None
    assert mark['favorited'] is True
    assert not mark['watched']


def test_unfavoriting_a_watched_episode_keeps_watched(test_client: TestClient):
    show_id, episode_id = _show_with_episode(test_client)
    test_client.post(f'/v1/users/me/episodes/{episode_id}', headers=_auth(test_client))
    test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    )
    resp = test_client.delete(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    )
    assert resp.status_code == 204
    mark = _get_mark(test_client, show_id, episode_id)
    assert mark is not None
    assert mark['watched'] == 1
    assert mark['favorited'] is False


def test_unfavoriting_the_only_mark_drops_the_row(test_client: TestClient):
    show_id, episode_id = _show_with_episode(test_client)
    test_client.post(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    )
    resp = test_client.delete(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    )
    assert resp.status_code == 204
    assert _get_mark(test_client, show_id, episode_id) is None


def test_unfavoriting_when_not_favorited_returns_404(test_client: TestClient):
    _, episode_id = _show_with_episode(test_client)
    resp = test_client.delete(
        f'/v1/users/me/episodes/{episode_id}/favorite', headers=_auth(test_client)
    )
    assert resp.status_code == 404
