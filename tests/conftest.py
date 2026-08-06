# pylint: disable=wrong-import-position, import-outside-toplevel
"""
Creates a fixture to provide a database session for testing.
"""

import os

# Ensure test environment mode is set before app config is loaded (#285)
os.environ['ENV'] = 'test'

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from dateutil.parser import parse
from faker import Faker
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Now we can safely import app modules
from app.db.database import Base, get_db
from app.db.db_user import create_admin_user, create_user
from app.run import app
from app.schemas.model_schemas import InUserBase

fake = Faker()


@pytest.fixture(autouse=True)
def _block_outbound_http(monkeypatch):
    """
    Guard against unmocked outbound network requests in tests (#284).

    Fails any test that attempts a socket connection to a non-loopback address,
    naming the offending host in the failure message.
    """
    import socket

    orig_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = (
            address[0]
            if isinstance(address, tuple) and len(address) > 0
            else str(address)
        )
        if host not in ('127.0.0.1', 'localhost', '::1', 'testserver'):
            pytest.fail(
                f"Outbound network request blocked in test: attempted connection to {host}. "
                'Mock the upstream provider instead of calling live services (#284).'
            )
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)


@pytest.fixture(autouse=True)
def _mock_upstream_providers(request, monkeypatch):
    """
    Autouse fixture that stubs out upstream provider calls (TMDB, TVMaze, Open Library, IGDB)
    during integration tests to keep test execution hermetic and fast (#284).
    """
    if 'integration' in str(request.path):
        monkeypatch.setattr(
            'app.services.tmdb.try_request', lambda *args, **kwargs: None, raising=False
        )
        monkeypatch.setattr(
            'app.services.movie_search.get_movie_details',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.tv_search.get_tv_show_details',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.tv_search.enrich_tv_show',
            lambda db, show, *args, **kwargs: show,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.tv_search._tvmaze_detail',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.tv_search.sync_episodes',
            lambda *args, **kwargs: 0,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.tv_search.get_show_episodes',
            lambda *args, **kwargs: [],
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.book_search.get_book_detail',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.book_search.enrich_book',
            lambda db, book, *args, **kwargs: book,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.book_search._openlibrary_detail',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.game_search.get_game_details',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.game_search.enrich_game',
            lambda db, game, *args, **kwargs: game,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.game_search._igdb_detail',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.services.watch_providers.get_watch_providers',
            lambda *args, **kwargs: None,
            raising=False,
        )

        monkeypatch.setattr(
            'app.router.v1.router_tv.get_tv_show_detail',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.router.v1.router_books.get_book_detail',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.router.v1.router_movies.get_movie_details',
            lambda *args, **kwargs: None,
            raising=False,
        )
        monkeypatch.setattr(
            'app.router.v1.router_games.get_game_details',
            lambda *args, **kwargs: None,
            raising=False,
        )


# Create a new database session for testing
@pytest.fixture(scope='session', name='test_db_engine')
def db_engine():
    """
    Fixture to provide a database engine for testing.
    """

    engine = create_engine(
        'sqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope='function', name='test_db_session')
def db_session(test_db_engine):
    """
    Fixture to provide a database session for testing.
    """
    connection = test_db_engine.connect()
    transaction = connection.begin()
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = session_local()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


# Override the get_db dependency to use the test database
@pytest.fixture(scope='function', name='test_client')
def fixture_test_client(test_db_session: Session, test_load_database):
    """
    Fixture to provide a FastAPI TestClient.
    """

    def override_get_db():
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    local_client = TestClient(app)
    local_client.test_db_session = test_db_session
    loaded_client = test_load_database(local_client)
    yield loaded_client
    app.dependency_overrides.clear()


@pytest.fixture(name='test_load_database')
def fixture_test_load_database(
    test_create_admin_user, test_create_user, test_authenticate_user
):
    '''
    Fixture to load the database with test data.
    '''

    def _load_database(local_client):
        admin_user = test_create_admin_user(local_client)
        local_client.admin_user = admin_user[0]
        admin_token = test_authenticate_user(
            local_client, admin_user[0].email, admin_user[0].plain_password
        )
        local_client.admin_user.token = admin_token
        first_user = test_create_user(local_client, user_count=1)
        local_client.first_user = first_user[0]
        first_user_token = test_authenticate_user(
            local_client, first_user[0].email, first_user[0].plain_password
        )
        local_client.first_user.token = first_user_token
        second_user = test_create_user(local_client, user_count=1)
        local_client.second_user = second_user[0]
        second_user_token = test_authenticate_user(
            local_client, second_user[0].email, second_user[0].plain_password
        )
        local_client.second_user.token = second_user_token

        return local_client

    return _load_database


@pytest.fixture(name='test_user_data_generator')
def fixture_test_user_data_generator():
    '''
    Fixture for user data generator.
    '''

    def _generate_user_data(num_users=1):
        user_data = []
        for _ in range(num_users):
            user_data.append(
                InUserBase(
                    display_name=fake.name(),
                    email=f'{fake.first_name()}.{fake.last_name_nonbinary()}@zoho.com',
                    password=fake.password(length=20),
                )
            )
        return user_data

    return _generate_user_data


@pytest.fixture(name='test_authenticate_user')
def fixture_test_authenticate_user():
    '''
    Fixture for user authentication.
    '''

    def _authenticate_user(local_client, email, password):
        response = local_client.post(
            '/v1/auth/token',
            files={
                'username': (None, email),
                'password': (None, password),
            },
        )
        assert response.status_code == 200
        response_data = response.json()
        return response_data['access_token']

    return _authenticate_user


@pytest.fixture(name='test_create_user')
def fixture_test_create_user(test_user_data_generator):
    '''
    Fixture for user authentication.
    '''

    def _create_user(local_client, user_count=1):
        users = []
        for _ in range(user_count):
            user_data_list = test_user_data_generator()
            test_user_data = user_data_list[0]
            user_data = InUserBase(
                display_name=test_user_data.display_name,
                email=test_user_data.email,
                password=test_user_data.password,
            )
            user = create_user(local_client.test_db_session, user_data)
            user[0].plain_password = test_user_data.password
            users.append(user[0])
        assert len(users) == user_count
        return users

    return _create_user


@pytest.fixture(name='test_create_admin_user')
def fixture_test_create_admin_user(test_user_data_generator):
    '''
    Fixture for user authentication.
    '''

    def _create_admin_user(local_client):
        users = []
        user_data_list = test_user_data_generator()
        admin_data = user_data_list[0]
        with patch.dict(
            os.environ,
            {
                'ADMIN_DISPLAY_NAME': admin_data.display_name,
                'ADMIN_EMAIL': admin_data.email,
                'ADMIN_PASSWORD': admin_data.password,
            },
        ):
            admin = create_admin_user(local_client.test_db_session)

        admin[0].plain_password = admin_data.password
        users.append(admin[0])
        return users

    return _create_admin_user


@pytest.fixture(name='test_assert_timestamps')
def fixture_test_assert_timestamps():
    '''
    Fixture for user authentication.
    '''

    def _assert_timestamps(item, within=timedelta(minutes=5)):
        now = datetime.now(timezone.utc)
        created_at = parse(item['created_at']).replace(tzinfo=timezone.utc)
        updated_at = parse(item['updated_at']).replace(tzinfo=timezone.utc)
        assert now - created_at < within, 'created_at is not recent'
        assert now - updated_at < within, 'updated_at is not recent'
        assert created_at <= updated_at, 'created_at is not before updated_at'

    return _assert_timestamps
