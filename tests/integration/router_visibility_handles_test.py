# pylint: disable=missing-module-docstring, missing-function-docstring
"""
End-to-end coverage for the profanity check wired into `update_visibility`
(#278): a profane handle is rejected at claim time with a 422, and Adam has
a real override for a specific handle via `HANDLE_PROFANITY_ALLOWLIST`.

As in `tests/unit/handles_test.py`, no profane term is hardcoded here — the
handle used in every case below is read out of the library's own installed
wordlist at runtime.
"""

import os
from unittest.mock import patch

import better_profanity
from fastapi.testclient import TestClient

from app.config import Settings


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _a_library_word() -> str:
    """A single-token, alphabetic entry from the installed package's list."""
    path = os.path.join(
        os.path.dirname(better_profanity.__file__), 'profanity_wordlist.txt'
    )
    with open(path, encoding='utf-8') as wordlist_file:
        for line in wordlist_file:
            word = line.strip().lower()
            # Handles are 3-30 chars (HANDLE_RE); leave room for the "-fan"
            # suffix used below.
            if word.isalpha() and 3 <= len(word) <= 20:
                return word
    raise AssertionError('installed better-profanity wordlist has no usable entry')


def test_profane_handle_is_rejected_with_422(test_client: TestClient):
    word = _a_library_word()
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'handle': word},
    )
    assert response.status_code == 422


def test_profane_handle_is_rejected_alongside_hyphen_variant(test_client: TestClient):
    word = _a_library_word()
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'handle': f'{word}-fan'},
    )
    assert response.status_code == 422


@patch('app.router.v1.router_visibility.get_settings')
def test_allowlisted_handle_bypasses_the_profanity_check(
    mock_settings, test_client: TestClient
):
    word = _a_library_word()
    mock_settings.return_value = Settings(handle_profanity_allowlist=word.upper())
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'handle': word},
    )
    assert response.status_code == 200
    assert response.json()['handle'] == word


@patch('app.router.v1.router_visibility.get_settings')
def test_allowlist_only_exempts_the_listed_handle(
    mock_settings, test_client: TestClient
):
    word = _a_library_word()
    mock_settings.return_value = Settings(handle_profanity_allowlist='someone-else')
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'handle': word},
    )
    assert response.status_code == 422
