# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient
from app.db.models import DbFriendship
from app.db.models import DbFollow
from app.services.friendships import FriendshipStatus
from app.services.visibility import VisibilityTier


def test_user_search_requires_auth(test_client: TestClient):
    assert test_client.get('/v1/search/users?q=bob').status_code == 401


def test_user_search_empty_query(test_client: TestClient):
    headers = {'Authorization': f'Bearer {test_client.first_user.token}'}
    resp = test_client.get('/v1/search/users?q=', headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['query'] == ''
    assert data['users'] == []


def test_user_search_visibility(test_client: TestClient, test_create_user):
    headers = {'Authorization': f'Bearer {test_client.first_user.token}'}
    db = test_client.test_db_session

    users = test_create_user(test_client, user_count=4)
    public_user = users[0]
    friend_user = users[1]
    private_user = users[2]
    stranger_public = users[3]

    # set public_user to public
    public_user.visibility_profile = VisibilityTier.PUBLIC.value
    public_user.display_name = 'Charlie Public'
    public_user.handle = 'charlie'

    # friend_user is private, but friends with first_user
    friend_user.visibility_profile = VisibilityTier.PRIVATE.value
    friend_user.display_name = 'Charlie Friend'

    db.add(
        DbFriendship(
            user_low_id=min(test_client.first_user.pk, friend_user.pk),
            user_high_id=max(test_client.first_user.pk, friend_user.pk),
            requested_by_id=friend_user.pk,
            status=FriendshipStatus.ACCEPTED,
        )
    )

    # private_user is private, not friends
    private_user.visibility_profile = VisibilityTier.PRIVATE.value
    private_user.display_name = 'Charlie Private'

    # stranger_public is public, not friends
    stranger_public.visibility_profile = VisibilityTier.PUBLIC.value
    stranger_public.display_name = 'Bob Public'

    db.commit()

    # search for 'Charlie'
    resp = test_client.get('/v1/search/users?q=Charlie', headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['query'] == 'Charlie'
    users_returned = data['users']

    ids = [u['id'] for u in users_returned]
    # Should find Charlie Public and Charlie Friend, NOT Charlie Private.
    assert public_user.id in ids
    assert friend_user.id in ids
    assert private_user.id not in ids

    # Should not find Bob Public (doesn't match query)
    assert stranger_public.id not in ids

    # search for 'Bob'
    resp2 = test_client.get('/v1/search/users?q=Bob', headers=headers)
    assert resp2.status_code == 200
    assert len(resp2.json()['users']) == 1
    assert resp2.json()['users'][0]['id'] == stranger_public.id


def test_user_search_anti_enumeration(test_client: TestClient, test_create_user):
    headers = {'Authorization': f'Bearer {test_client.first_user.token}'}
    db = test_client.test_db_session

    users = test_create_user(test_client, user_count=1)
    secret_user = users[0]
    secret_user.visibility_profile = VisibilityTier.PRIVATE.value
    secret_user.display_name = 'TopSecretName'
    db.commit()

    # Search specifically for the secret user
    resp = test_client.get('/v1/search/users?q=TopSecretName', headers=headers)
    assert resp.status_code == 200
    # Should be empty, meaning existence is not revealed
    assert resp.json()['users'] == []


def test_user_search_exposes_counts_for_public_profiles_only(
    test_client: TestClient, test_create_user
):
    headers = {'Authorization': f'Bearer {test_client.first_user.token}'}
    db = test_client.test_db_session
    public_user, friend_user = test_create_user(test_client, user_count=2)
    public_user.display_name = 'Counted Public'
    public_user.handle = 'counted-public'
    public_user.visibility_profile = VisibilityTier.PUBLIC.value
    friend_user.display_name = 'Counted Friend'
    friend_user.handle = 'counted-friend'
    friend_user.visibility_profile = VisibilityTier.FRIENDS.value
    db.add_all(
        [
            DbFriendship(
                user_low_id=min(test_client.first_user.pk, friend_user.pk),
                user_high_id=max(test_client.first_user.pk, friend_user.pk),
                requested_by_id=friend_user.pk,
                status=FriendshipStatus.ACCEPTED,
            ),
            DbFollow(follower_id=test_client.first_user.pk, followee_id=public_user.pk),
            DbFollow(
                follower_id=test_client.second_user.pk, followee_id=public_user.pk
            ),
            DbFollow(follower_id=test_client.first_user.pk, followee_id=friend_user.pk),
        ]
    )
    db.commit()

    response = test_client.get('/v1/search/users?q=Counted', headers=headers)
    assert response.status_code == 200
    results = {user['id']: user for user in response.json()['users']}
    assert results[public_user.id]['follower_count'] == 2
    assert results[friend_user.id]['follower_count'] is None
