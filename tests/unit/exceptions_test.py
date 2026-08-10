"""
Test the custom exception handlers.
"""

import json

import pytest
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from starlette.status import (
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)

from app.run import app
from app.utils.exceptions import (
    generic_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)

client = TestClient(app)


@pytest.mark.asyncio
async def test_http_exception_handler():
    """
    Test the custom HTTPException handler.
    """
    request = Request(scope={'type': 'http'})
    exc = HTTPException(status_code=404, detail='Not Found')
    response = await http_exception_handler(request, exc)
    response_body = json.loads(response.body.decode('utf-8'))
    assert response.status_code == 404
    assert response_body['success'] is False
    assert response_body['message'] == 'Not Found'
    assert response_body['data'] == []


@pytest.mark.asyncio
async def test_generic_exception_handler(caplog):
    """
    Test the custom generic exception handler.
    """
    request = Request(scope={'type': 'http', 'method': 'GET', 'path': '/boom'})
    exc = Exception('Test exception')
    with caplog.at_level('ERROR', logger='aleonard_api'):
        response = await generic_exception_handler(request, exc)
    response_body = json.loads(response.body.decode('utf-8'))
    assert response.status_code == HTTP_500_INTERNAL_SERVER_ERROR
    assert response_body['success'] is False
    assert response_body['message'] == 'Test exception'
    assert response_body['data'] == []
    # The 500 must leave a traceback in the logs (Error Reporting feeds on it)
    assert 'Unhandled exception on GET /boom' in caplog.text
    assert 'Test exception' in caplog.text


@pytest.mark.asyncio
async def test_validation_exception_handler():
    """
    Test the custom RequestValidationError handler.
    """
    request = Request(scope={'type': 'http'})
    exc = RequestValidationError(
        [
            {
                'loc': ['body', 'field'],
                'msg': 'field required',
                'type': 'value_error.missing',
            }
        ]
    )
    response = await validation_exception_handler(request, exc)
    response_body = json.loads(response.body.decode('utf-8'))
    assert response.status_code == HTTP_422_UNPROCESSABLE_CONTENT
    assert response_body['success'] is False
    assert response_body['message'] == 'Validation Error'
    assert response_body['data'] == [
        {
            'loc': ['body', 'field'],
            'msg': 'field required',
            'type': 'value_error.missing',
        }
    ]


@pytest.mark.asyncio
async def test_validation_handler_survives_a_raw_exception_in_ctx():
    """
    A ``field_validator`` raising ValueError must still produce a 422.

    Pydantic parks the exception *object* in ``ctx['error']`` for a
    value_error. JSONResponse cannot encode that, so the handler used to
    raise while building the response and the caller got a 500 for input the
    API had already correctly rejected -- the failure mode was invisible
    until a schema actually used a validator this way.
    """
    request = Request({'type': 'http', 'method': 'PUT', 'path': '/x', 'headers': []})
    exc = RequestValidationError(
        [
            {
                'type': 'value_error',
                'loc': ['body', 'time_zone'],
                'msg': 'Value error, Unknown IANA time zone: Mars/Olympus_Mons',
                'input': 'Mars/Olympus_Mons',
                'ctx': {'error': ValueError('Unknown IANA time zone')},
            }
        ]
    )

    response = await validation_exception_handler(request, exc)
    body = json.loads(response.body.decode('utf-8'))

    assert response.status_code == HTTP_422_UNPROCESSABLE_CONTENT
    assert body['data'][0]['ctx'] == {'error': 'Unknown IANA time zone'}
    assert 'Mars/Olympus_Mons' in body['data'][0]['msg']
