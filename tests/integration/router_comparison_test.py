# pylint: disable=missing-function-docstring
"""Viewer-safe cross-profile comparisons and recommendation provenance."""

from fastapi.testclient import TestClient

from app.db.models import DbUser
from app.db.models_sandbox import DbMovie, DbUserMovie


def _auth(token: str) -> dict:
    return {'Authorization': f"Bearer {token}"}


def _user(client: TestClient, email: str) -> DbUser:
    return client.test_db_session.query(DbUser).filter(DbUser.email == email).one()


def _public_movies(client: TestClient, token: str, watchlist='public') -> None:
    response = client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={
            'handle': 'brandon',
            'visibility_profile': 'public',
            'visibility_movies': 'public',
            'visibility_watchlist_movies': watchlist,
        },
    )
    assert response.status_code == 200, response.text


def _stock_movies(client: TestClient) -> list[DbMovie]:
    db = client.test_db_session
    viewer = _user(client, client.second_user.email)
    target = _user(client, client.first_user.email)
    movies = [
        DbMovie(title=f"Movie {number}", imdb=f"tt90000{number}", year=2000 + number)
        for number in range(1, 9)
    ]
    db.add_all(movies)
    db.flush()
    # Five shared rankings, ordered in opposite directions. Two unseen target
    # picks then exercise the recommendation list; the first is already on
    # the viewer's watchlist and must remain eligible with a marker.
    for index, movie in enumerate(movies[:7], start=1):
        db.add(
            DbUserMovie(
                user_id=target.pk,
                movie_id=movie.pk,
                on_rankings=True,
                rank=index,
            )
        )
    for index, movie in enumerate(reversed(movies[:5]), start=1):
        db.add(
            DbUserMovie(
                user_id=viewer.pk,
                movie_id=movie.pk,
                on_rankings=True,
                rank=index,
            )
        )
    db.add(
        DbUserMovie(
            user_id=viewer.pk,
            movie_id=movies[5].pk,
            on_watchlist=True,
        )
    )
    # Common watchlist that is not part of the ranked overlap.
    db.add(
        DbUserMovie(
            user_id=viewer.pk,
            movie_id=movies[7].pk,
            on_watchlist=True,
        )
    )
    db.add(
        DbUserMovie(
            user_id=target.pk,
            movie_id=movies[7].pk,
            on_watchlist=True,
        )
    )
    db.commit()
    return movies


def test_comparison_scores_visible_rankings_and_marks_watchlist(
    test_client: TestClient,
):
    _public_movies(test_client, test_client.first_user.token)
    _stock_movies(test_client)

    response = test_client.get(
        '/v1/users/me/comparison/brandon',
        headers=_auth(test_client.second_user.token),
    )
    assert response.status_code == 200, response.text
    movies = next(
        domain
        for domain in response.json()['domains']
        if domain['category'] == 'movies'
    )
    assert movies['shared_ranked_count'] == 5
    assert movies['alignment_status'] == 'ready'
    assert movies['alignment_score'] == 60
    assert [item['title'] for item in movies['common_watchlist']] == ['Movie 8']
    assert [item['title'] for item in movies['recommendations']] == [
        'Movie 6',
        'Movie 7',
    ]
    assert movies['recommendations'][0]['on_your_watchlist'] is True
    assert movies['recommendations'][1]['on_your_watchlist'] is False
    assert [item['gap'] for item in movies['biggest_gaps']] == [4, 4, 2, 2, 0]
    assert [item['gap'] for item in movies['most_aligned']] == [0, 2, 2, 4, 4]
    assert movies['most_aligned'][0]['title'] == 'Movie 3'


def test_hidden_watchlist_does_not_block_ranked_comparison(test_client: TestClient):
    _public_movies(test_client, test_client.first_user.token, watchlist='friends')
    _stock_movies(test_client)

    response = test_client.get(
        '/v1/users/me/comparison/brandon',
        headers=_auth(test_client.second_user.token),
    )
    movies = next(
        domain
        for domain in response.json()['domains']
        if domain['category'] == 'movies'
    )
    assert movies['rankings_visible'] is True
    assert movies['watchlist_visible'] is False
    assert movies['common_watchlist'] == []
    assert movies['shared_ranked_count'] == 5


def test_comparison_reports_a_viewers_outgoing_pending_request(test_client: TestClient):
    _public_movies(test_client, test_client.first_user.token)
    sent = test_client.post(
        '/v1/users/me/friends/requests',
        headers=_auth(test_client.second_user.token),
        json={'handle': 'brandon'},
    )
    assert sent.status_code == 202, sent.text

    requests = test_client.get(
        '/v1/users/me/friends/requests',
        headers=_auth(test_client.second_user.token),
    )
    req_id = requests.json()['outgoing'][0]['id']

    response = test_client.get(
        '/v1/users/me/comparison/brandon',
        headers=_auth(test_client.second_user.token),
    )
    assert response.status_code == 200, response.text
    assert response.json()['relationship'] == 'none'
    assert response.json()['friend_request_state'] == 'pending'
    assert response.json()['outgoing_request_id'] == req_id


def test_private_profile_and_unknown_handle_are_the_same_404(test_client: TestClient):
    db = test_client.test_db_session
    target = _user(test_client, test_client.first_user.email)
    target.handle = 'brandon'
    target.visibility_profile = 'private'
    db.commit()
    headers = _auth(test_client.second_user.token)

    hidden = test_client.get('/v1/users/me/comparison/brandon', headers=headers)
    missing = test_client.get('/v1/users/me/comparison/nobody-here', headers=headers)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_save_recommendation_records_only_its_first_source(test_client: TestClient):
    _public_movies(test_client, test_client.first_user.token)
    movies = _stock_movies(test_client)
    headers = _auth(test_client.second_user.token)
    path = f"/v1/users/me/comparison/brandon/movies/{movies[6].id}"

    first = test_client.post(path, headers=headers, json={'destination': 'watchlist'})
    assert first.status_code == 201, first.text
    assert first.json()['source_handle'] == 'brandon'
    assert first.json()['source_recorded'] is True

    moved = test_client.post(path, headers=headers, json={'destination': 'rankings'})
    assert moved.status_code == 201, moved.text
    assert moved.json()['source_handle'] == 'brandon'
    assert moved.json()['source_recorded'] is False

    viewer = _user(test_client, test_client.second_user.email)
    tracker = (
        test_client.test_db_session.query(DbUserMovie)
        .filter(DbUserMovie.user_id == viewer.pk, DbUserMovie.movie_id == movies[6].pk)
        .one()
    )
    assert tracker.on_rankings is True
    assert tracker.on_watchlist is False
    assert tracker.rank is None
    assert tracker.source_user.handle == 'brandon'

    listed = test_client.get('/v1/users/me/movies', headers=headers)
    assert listed.status_code == 200, listed.text
    saved = next(row for row in listed.json() if row['movie']['id'] == movies[6].id)
    assert saved['source_handle'] == 'brandon'
