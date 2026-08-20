"""Tests for the app.run module."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.run import app


def test_health_endpoint_no_db_query():
    """
    Asserts the app can serve /health when the database is unreachable.
    """
    with patch('app.db.database.SessionLocal', side_effect=Exception('DB is down')):
        with patch('app.run.get_db', side_effect=Exception('DB is down')):
            client = TestClient(app)
            response = client.get('/health')
            assert response.status_code == 200
            data = response.json()
            assert data['status'] == 'ok'
            assert 'env' in data
            assert 'git_sha' in data
