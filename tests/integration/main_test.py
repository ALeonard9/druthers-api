""" "
Integration testing for the main module
"""

import json
from unittest.mock import mock_open, patch

import pytest
from fastapi.testclient import TestClient

from app.run import app, generate_openapi_json

client = TestClient(app)


def test_index():
    """
    Test the index endpoint.
    """
    response = client.get('/')
    assert response.status_code == 200
    response_data = response.json()
    assert response_data['success'] is True
    assert isinstance(response_data['data'], list)
    assert len(response_data['data']) == 0
    assert response_data['message'] == 'druthers.io API - your favorites, ranked.'


@pytest.mark.asyncio
async def test_generate_openapi_json():
    """
    Test the generate_openapi_json function.
    """
    openapi_schema = {
        'openapi': '3.0.0',
        'info': {'title': 'Test API', 'version': '0.1.0'},
    }

    expected_output = json.dumps(openapi_schema, indent=2) + '\n'

    m = mock_open()
    with patch('app.run.app.openapi', return_value=openapi_schema):
        with patch('builtins.open', m):
            await generate_openapi_json()
            m.assert_called_once_with('openapi.json', 'w', encoding='utf-8')
            # Combine all write() calls into one string.
            written_calls = m().write.call_args_list
            written_output = ''.join(call.args[0] for call in written_calls)
            assert written_output == expected_output
