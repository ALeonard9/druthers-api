"""
Tests the user API calls
"""

from typing import Callable
from unittest.mock import patch

from faker import Faker
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import (
    DbApiKey,
    DbFollow,
    DbFriendship,
    DbRefreshToken,
    DbUser,
)
from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbNotification,
    DbTVEpisode,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)
from app.services.friendships import FriendshipStatus

fake = Faker()


def test_api_create_user(
    test_client: TestClient,
    test_user_data_generator: Callable[..., list],
    test_assert_timestamps: Callable[..., None],
):
    """
    Test creating a new user.
    """
    user_data_list = test_user_data_generator(num_users=1)
    test_user_data = user_data_list[0]
    user_data = {
        'display_name': test_user_data.display_name,
        'email': test_user_data.email,
        'password': test_user_data.password,
    }
    response = test_client.post('/v1/users/', json=user_data)

    assert response.status_code == 201
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User created'
    assert response_data['data'][0]['email'] == test_user_data.email
    assert response_data['data'][0]['display_name'] == test_user_data.display_name
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


@patch('app.router.v1.user.get_settings')
def test_api_create_user_disabled_returns_clear_message(
    mock_settings,
    test_client: TestClient,
    test_user_data_generator: Callable[..., list],
):
    """
    #183: with DISABLE_SIGNUP set, self-registration is rejected with a
    clear invite-only message instead of silently creating the account.
    """
    mock_settings.return_value = Settings(disable_signup=True)
    user_data_list = test_user_data_generator(num_users=1)
    test_user_data = user_data_list[0]
    user_data = {
        'display_name': test_user_data.display_name,
        'email': test_user_data.email,
        'password': test_user_data.password,
    }
    response = test_client.post('/v1/users/', json=user_data)

    assert response.status_code == 403
    response_data = response.json()
    assert response_data['success'] is False
    assert 'invite-only' in response_data['message'].lower()


def test_api_user_get_user(
    test_client: TestClient,
    test_assert_timestamps: Callable[..., None],
):
    """
    Test user getting a themself.
    """
    user_id = test_client.first_user.id
    token = test_client.first_user.token

    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.get(f"/v1/users/{user_id}", headers=headers)

    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User found'
    assert response_data['data'][0]['id'] == test_client.first_user.id
    assert response_data['data'][0]['email'] == test_client.first_user.email
    assert (
        response_data['data'][0]['display_name'] == test_client.first_user.display_name
    )
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


def test_api_admin_get_user(
    test_client: TestClient,
    test_assert_timestamps: Callable[..., None],
):
    """
    Test admin getting a user.
    """
    user_id = test_client.first_user.id
    token = test_client.admin_user.token

    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.get(f"/v1/users/{user_id}", headers=headers)

    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User found'
    assert response_data['data'][0]['id'] == test_client.first_user.id
    assert response_data['data'][0]['email'] == test_client.first_user.email
    assert (
        response_data['data'][0]['display_name'] == test_client.first_user.display_name
    )
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


def test_api_admin_cant_get_unknown_user(
    test_client: TestClient,
):
    """
    Test admin unable to get an unknown user.
    """
    user_id = fake.uuid4()
    token = test_client.admin_user.token

    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.get(f"/v1/users/{user_id}", headers=headers)

    assert response.status_code == 404
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['message'] == f'User with id {user_id} not found'
    assert response_data['data'] == []


def test_api_user_cant_get_other_user(
    test_client: TestClient,
):
    """
    Test user unable to get other a user.
    """
    user_id = test_client.first_user.id
    token = test_client.second_user.token

    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.get(f"/v1/users/{user_id}", headers=headers)

    assert response.status_code == 403
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['message'] == 'User can only view their own account.'
    assert response_data['data'] == []


def test_api_admin_get_all_users(
    test_client: TestClient,
):
    """
    Test admin can listing all users.
    """
    token = test_client.admin_user.token
    headers = {'Authorization': f"Bearer {token}"}
    list_response = test_client.get('/v1/users/', headers=headers)
    assert list_response.status_code == 200
    response_data = list_response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'Users found'
    # Verify we have at least three users in the response.
    # Pre-loaded users are admin, user1, and user2
    assert len(response_data['data']) >= 3


def test_api_user_cant_get_all_users(
    test_client: TestClient,
    # test_create_user: Callable[..., list],
):
    """
    Test user unable to list all users.
    """
    token = test_client.first_user.token
    headers = {'Authorization': f"Bearer {token}"}
    list_response = test_client.get('/v1/users/', headers=headers)
    assert list_response.status_code == 403
    response_data = list_response.json()
    assert response_data['success'] is False
    assert (
        response_data['message'] == 'User does not have permission to view all users.'
    )


def test_api_admin_update_user(
    test_client: TestClient,
    test_user_data_generator: Callable[..., list],
    test_assert_timestamps: Callable[..., None],
):
    """
    Test admin updating a user.
    """

    update_user_data = test_user_data_generator(num_users=1)[0]

    user_id = test_client.first_user.id
    token = test_client.admin_user.token

    # Update user
    updated_payload = {
        'display_name': update_user_data.display_name,
        'email': update_user_data.email,
        'password': update_user_data.password,
    }
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.put(
        f"/v1/users/{user_id}", json=updated_payload, headers=headers
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User updated'
    assert response_data['data'][0]['id'] == test_client.first_user.id
    assert response_data['data'][0]['email'] == update_user_data.email
    assert response_data['data'][0]['display_name'] == update_user_data.display_name
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


def test_api_user_update_self(
    test_client: TestClient,
    test_user_data_generator: Callable[..., list],
    test_assert_timestamps: Callable[..., None],
):
    """
    Test updating a user.
    """

    update_user_data = test_user_data_generator(num_users=1)[0]

    user_id = test_client.first_user.id
    token = test_client.first_user.token

    # Update user
    updated_payload = {
        'display_name': update_user_data.display_name,
        'email': update_user_data.email,
        'password': update_user_data.password,
    }
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.put(
        f"/v1/users/{user_id}", json=updated_payload, headers=headers
    )
    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User updated'
    assert response_data['data'][0]['id'] == test_client.first_user.id
    assert response_data['data'][0]['email'] == update_user_data.email
    assert response_data['data'][0]['display_name'] == update_user_data.display_name
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


def test_api_user_cant_update_other(
    test_client: TestClient,
    test_user_data_generator: Callable[..., list],
):
    """
    Test user unable to update other user.
    """

    update_user_data = test_user_data_generator(num_users=1)[0]

    user_id = test_client.first_user.id
    token = test_client.second_user.token

    # Update user
    updated_payload = {
        'display_name': update_user_data.display_name,
        'email': update_user_data.email,
        'password': update_user_data.password,
    }
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.put(
        f"/v1/users/{user_id}", json=updated_payload, headers=headers
    )
    assert response.status_code == 403
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['message'] == 'User can only update their own account.'
    assert response_data['data'] == []


def test_api_user_cant_update_unknown_other(
    test_client: TestClient,
    test_user_data_generator: Callable[..., list],
):
    """
    Test admin unable to update unknown user.
    """

    update_user_data = test_user_data_generator(num_users=1)[0]

    user_id = fake.uuid4()
    token = test_client.admin_user.token

    # Update user
    updated_payload = {
        'display_name': update_user_data.display_name,
        'email': update_user_data.email,
        'password': update_user_data.password,
    }
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.put(
        f"/v1/users/{user_id}", json=updated_payload, headers=headers
    )
    assert response.status_code == 404
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['message'] == f'User with id {user_id} not found'
    assert response_data['data'] == []


def test_api_admin_delete_user(
    test_client: TestClient,
    test_assert_timestamps: Callable[..., None],
):
    """
    Test admin deleting a user.
    """
    user_id = test_client.first_user.id
    token = test_client.admin_user.token
    # Delete user
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.delete(f"/v1/users/{user_id}", headers=headers)
    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User deleted'
    assert response_data['data'][0]['id'] == test_client.first_user.id
    assert response_data['data'][0]['email'] == test_client.first_user.email
    assert (
        response_data['data'][0]['display_name'] == test_client.first_user.display_name
    )
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


def test_api_user_delete_self(
    test_client: TestClient,
    test_assert_timestamps: Callable[..., None],
):
    """
    Test deleting a user.
    """
    user_id = test_client.first_user.id
    token = test_client.first_user.token
    # Delete user
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.delete(f"/v1/users/{user_id}", headers=headers)
    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert response_data['message'] == 'User deleted'
    assert response_data['data'][0]['id'] == test_client.first_user.id
    assert response_data['data'][0]['email'] == test_client.first_user.email
    assert (
        response_data['data'][0]['display_name'] == test_client.first_user.display_name
    )
    assert response_data['data'][0]['user_group'] == 'user'
    test_assert_timestamps(response_data['data'][0])


def test_api_key_cannot_delete_owner_but_session_can(test_client: TestClient):
    """Long-lived script credentials cannot perform irreversible deletion."""
    user = test_client.first_user
    session_headers = {'Authorization': f'Bearer {user.token}'}
    create_response = test_client.post(
        '/v1/users/me/api-keys',
        headers=session_headers,
        json={'name': 'Cannot delete account'},
    )
    assert create_response.status_code == 201
    api_key = create_response.json()['key']

    api_key_response = test_client.delete(
        f'/v1/users/{user.id}',
        headers={'Authorization': f'Bearer {api_key}'},
    )

    assert api_key_response.status_code == 401
    assert api_key_response.json()['message'] == 'Could not validate credentials'
    assert test_client.test_db_session.query(DbUser).filter_by(pk=user.pk).count() == 1

    session_response = test_client.delete(
        f'/v1/users/{user.id}', headers=session_headers
    )
    assert session_response.status_code == 200
    assert test_client.test_db_session.query(DbUser).filter_by(pk=user.pk).count() == 0


def test_api_user_delete_self_removes_owned_rows_only(test_client: TestClient):
    """Account deletion removes every user-owned row, not shared catalog data."""
    db = test_client.test_db_session
    deleted_user = test_client.first_user
    surviving_user = test_client.second_user

    movie = DbMovie(title='Shared movie', tmdb=900_001)
    show = DbTVShow(title='Shared show', tvmaze=900_002)
    episode = DbTVEpisode(title='Shared episode', tvmaze=900_003, tv_show=show)
    book = DbBook(title='Shared book', isbn='delete-test-book')
    game = DbVideoGame(title='Shared game', igdb=900_004)
    db.add_all([movie, show, book, game])
    db.flush()

    deleted_rows = [
        DbUserMovie(user_id=deleted_user.pk, movie_id=movie.pk, on_watchlist=True),
        DbUserTVShow(user_id=deleted_user.pk, tv_show_id=show.pk, on_watchlist=True),
        DbUserTVEpisode(user_id=deleted_user.pk, episode_id=episode.pk, watched=1),
        DbUserBook(user_id=deleted_user.pk, book_id=book.pk, on_watchlist=True),
        DbUserVideoGame(user_id=deleted_user.pk, game_id=game.pk, on_watchlist=True),
        DbNotification(
            user_id=deleted_user.pk,
            type='movie_release',
            title='Owned notification',
            dedupe_key='account-delete-owned-notification',
        ),
        DbApiKey(
            user_id=deleted_user.pk,
            name='Owned key',
            key_hash='a' * 64,
            prefix='drk_owned',
        ),
    ]
    surviving_rows = [
        DbUserMovie(
            user_id=surviving_user.pk,
            movie_id=movie.pk,
            source_user_id=deleted_user.pk,
            on_watchlist=True,
        ),
        DbUserTVShow(
            user_id=surviving_user.pk,
            tv_show_id=show.pk,
            source_user_id=deleted_user.pk,
            on_watchlist=True,
        ),
        DbUserTVEpisode(user_id=surviving_user.pk, episode_id=episode.pk, watched=1),
        DbUserBook(
            user_id=surviving_user.pk,
            book_id=book.pk,
            source_user_id=deleted_user.pk,
            on_watchlist=True,
        ),
        DbUserVideoGame(
            user_id=surviving_user.pk,
            game_id=game.pk,
            source_user_id=deleted_user.pk,
            on_watchlist=True,
        ),
        DbNotification(
            user_id=surviving_user.pk,
            type='movie_release',
            title='Surviving notification',
            dedupe_key='account-delete-surviving-notification',
        ),
        DbApiKey(
            user_id=surviving_user.pk,
            name='Surviving key',
            key_hash='b' * 64,
            prefix='drk_survive',
        ),
    ]
    db.add_all(deleted_rows + surviving_rows)
    low, high = sorted((deleted_user.pk, surviving_user.pk))
    db.add(
        DbFriendship(
            user_low_id=low,
            user_high_id=high,
            requested_by_id=deleted_user.pk,
            status=FriendshipStatus.ACCEPTED,
        )
    )
    db.add(DbFollow(follower_id=deleted_user.pk, followee_id=surviving_user.pk))
    db.commit()

    assert db.query(DbRefreshToken).filter_by(user_id=deleted_user.pk).count() == 1
    response = test_client.delete(
        f'/v1/users/{deleted_user.id}',
        headers={'Authorization': f'Bearer {deleted_user.token}'},
    )

    assert response.status_code == 200
    assert db.query(DbUser).filter_by(pk=deleted_user.pk).count() == 0
    for model in (
        DbUserMovie,
        DbUserTVShow,
        DbUserTVEpisode,
        DbUserBook,
        DbUserVideoGame,
        DbNotification,
        DbApiKey,
        DbRefreshToken,
    ):
        assert db.query(model).filter_by(user_id=deleted_user.pk).count() == 0
        assert db.query(model).filter_by(user_id=surviving_user.pk).count() == 1
    for model in (DbUserMovie, DbUserTVShow, DbUserBook, DbUserVideoGame):
        assert (
            db.query(model).filter_by(user_id=surviving_user.pk).one().source_user_id
            is None
        )
    assert db.query(DbFriendship).count() == 0
    assert db.query(DbFollow).count() == 0
    assert db.query(DbUser).filter_by(pk=surviving_user.pk).count() == 1
    assert db.query(DbMovie).filter_by(pk=movie.pk).count() == 1
    assert db.query(DbTVShow).filter_by(pk=show.pk).count() == 1
    assert db.query(DbTVEpisode).filter_by(pk=episode.pk).count() == 1
    assert db.query(DbBook).filter_by(pk=book.pk).count() == 1
    assert db.query(DbVideoGame).filter_by(pk=game.pk).count() == 1


def test_api_user_delete_failure_is_safe_and_rolls_back(
    test_client: TestClient, caplog
):
    """Database failures return a stable message without SQL or row details."""
    db = test_client.test_db_session
    user = test_client.first_user
    database_error = IntegrityError(
        'UPDATE user_video_games SET user_id=NULL',
        {'user_id': None},
        ValueError('failing row contains private data'),
    )

    with (
        patch.object(db, 'commit', side_effect=database_error),
        patch.object(db, 'rollback', wraps=db.rollback) as rollback,
    ):
        response = test_client.delete(
            f'/v1/users/{user.id}',
            headers={'Authorization': f'Bearer {user.token}'},
        )

    assert response.status_code == 500
    assert response.json() == {
        'success': False,
        'message': 'Unable to delete user account',
        'data': [],
    }
    rollback.assert_called_once_with()
    assert 'exception=IntegrityError' in caplog.text
    assert 'database_code=gkpj' in caplog.text
    assert 'UPDATE user_video_games' not in caplog.text
    assert 'failing row contains private data' not in caplog.text
    assert "'user_id': None" not in caplog.text


def test_api_user_cant_delete_other(
    test_client: TestClient,
):
    """
    Test user unable to delete other user.
    """
    user_id = test_client.first_user.id
    token = test_client.second_user.token
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.delete(f"/v1/users/{user_id}", headers=headers)
    assert response.status_code == 403
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['message'] == 'User can only delete their own account.'
    assert response_data['data'] == []


def test_api_admin_cant_delete_unknown_other(
    test_client: TestClient,
):
    """
    Test admin unable to delete unknown user.
    """
    user_id = fake.uuid4()
    token = test_client.admin_user.token
    headers = {'Authorization': f"Bearer {token}"}
    response = test_client.delete(f"/v1/users/{user_id}", headers=headers)
    assert response.status_code == 404
    response_data = response.json()
    assert response_data['success'] is False
    assert response_data['message'] == f'User with id {user_id} not found'
    assert response_data['data'] == []
