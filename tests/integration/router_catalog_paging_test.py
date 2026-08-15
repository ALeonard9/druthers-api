# pylint: disable=missing-module-docstring, missing-function-docstring
import pytest
from fastapi.testclient import TestClient

from app.db.models_sandbox import DbBook, DbMovie, DbTVShow, DbVideoGame

CATALOGS = (
    ('/v1/movies', DbMovie, 'tmdb', lambda index: 10_000 + index),
    ('/v1/books', DbBook, 'googleid', lambda index: f'google-{index}'),
    ('/v1/games', DbVideoGame, 'igdb', lambda index: 20_000 + index),
    ('/v1/tv-shows', DbTVShow, 'tvmaze', lambda index: 30_000 + index),
)


def _auth(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


@pytest.mark.parametrize('path', [catalog[0] for catalog in CATALOGS])
def test_catalog_list_requires_authentication(test_client: TestClient, path):
    assert test_client.get(path).status_code == 401


@pytest.mark.parametrize('path,model,external_id,value_for', CATALOGS)
def test_catalog_list_pages_and_filters_by_external_id(
    test_client: TestClient, path, model, external_id, value_for
):
    rows = [
        model(title=f'Title {index}', **{external_id: value_for(index)})
        for index in range(27)
    ]
    test_client.test_db_session.add_all(rows)
    test_client.test_db_session.commit()

    default_page = test_client.get(path, headers=_auth(test_client))
    assert default_page.status_code == 200
    assert [item['title'] for item in default_page.json()] == [
        f'Title {index}' for index in range(25)
    ]

    second_page = test_client.get(
        path,
        headers=_auth(test_client),
        params={'limit': 2, 'offset': 25},
    )
    assert second_page.status_code == 200
    assert [item['title'] for item in second_page.json()] == [
        'Title 25',
        'Title 26',
    ]

    target_value = value_for(26)
    filtered = test_client.get(
        path,
        headers=_auth(test_client),
        params={external_id: target_value},
    )
    assert filtered.status_code == 200
    assert [item['title'] for item in filtered.json()] == ['Title 26']
    assert filtered.json()[0][external_id] == target_value


@pytest.mark.parametrize('path', [catalog[0] for catalog in CATALOGS])
@pytest.mark.parametrize('params', ({'limit': 0}, {'offset': -1}))
def test_catalog_list_rejects_invalid_pagination(test_client: TestClient, path, params):
    response = test_client.get(path, headers=_auth(test_client), params=params)
    assert response.status_code == 422


def test_tv_catalog_filters_by_imdb_id_beyond_first_page(test_client: TestClient):
    rows = [
        DbTVShow(title=f'Show {index}', tvmaze=40_000 + index) for index in range(26)
    ]
    rows.append(DbTVShow(title='Severance', tvmaze=44_932, imdb='tt11280740'))
    test_client.test_db_session.add_all(rows)
    test_client.test_db_session.commit()

    response = test_client.get(
        '/v1/tv-shows',
        headers=_auth(test_client),
        params={'imdb': 'tt11280740'},
    )

    assert response.status_code == 200
    assert [show['title'] for show in response.json()] == ['Severance']


def test_episode_list_requires_authentication(test_client: TestClient):
    show = DbTVShow(title='Severance', tvmaze=44_932)
    test_client.test_db_session.add(show)
    test_client.test_db_session.commit()

    path = f'/v1/tv-shows/{show.id}/episodes'
    assert test_client.get(path).status_code == 401

    authenticated = test_client.get(path, headers=_auth(test_client))
    assert authenticated.status_code == 200
    assert authenticated.json() == []
