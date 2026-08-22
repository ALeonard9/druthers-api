# pylint: disable=missing-function-docstring, protected-access
"""
Tests for the QA cast seeder (#413).

The guard tests matter most. This script writes to a database that is a branch
of prod, so the interesting question is never "does it seed" but "what stops it
seeding somewhere it should not".
"""

import pytest

from app.config import Settings
from app.db.hash import Hash
from app.db.models import DbFollow, DbFriendship, DbUser
from app.migration import seed_dev, seed_qa_cast


def _cast_user(session, email):
    return session.query(DbUser).filter_by(email=email).one()


# --- the guard ------------------------------------------------------------


@pytest.mark.parametrize('env', ['dev', 'prod', 'local', 'github'])
def test_assert_qa_refuses_every_env_but_qa(monkeypatch, env):
    settings = Settings(env=env, database_url='postgresql://u:p@somewhere/db')
    monkeypatch.setattr(seed_qa_cast, 'get_settings', lambda: settings)

    with pytest.raises(SystemExit):
        seed_qa_cast._assert_qa()


@pytest.mark.parametrize('database_url', ['', None])
def test_assert_qa_refuses_a_placeholder_host(monkeypatch, database_url):
    # The mirror of #257 on seed_dev's guard: ENV alone is not proof of where
    # the writes land. And an unset DATABASE_URL does NOT leave the host empty
    # -- settings falls back to the discrete POSTGRES_* parts and the hostname
    # resolves to the literal string 'none', which a truthiness check sails
    # straight past.
    settings = Settings(env='qa', database_url=database_url)
    monkeypatch.setattr(seed_qa_cast, 'get_settings', lambda: settings)

    with pytest.raises(SystemExit):
        seed_qa_cast._assert_qa()


def test_assert_qa_refuses_a_local_database(monkeypatch):
    # ENV=qa pointed at localhost means something is misconfigured, and
    # seeding it would write QA fixtures into whatever happens to be running.
    settings = Settings(
        env='qa', database_url='postgresql://u:p@localhost:5432/druthers'
    )
    monkeypatch.setattr(seed_qa_cast, 'get_settings', lambda: settings)

    with pytest.raises(SystemExit):
        seed_qa_cast._assert_qa()


def test_assert_qa_allows_qa(monkeypatch):
    settings = Settings(
        env='qa',
        database_url='postgresql://u:p@ep-qa-branch.us-east-2.aws.neon.tech/druthers',
    )
    monkeypatch.setattr(seed_qa_cast, 'get_settings', lambda: settings)

    seed_qa_cast._assert_qa()


# --- the target -----------------------------------------------------------


def test_target_refuses_to_create_the_anchor_account(test_client, monkeypatch):
    # The anchor is the one account with real credentials. Creating it here
    # would mean inventing a password for an account on an internet-facing
    # host, which is an operator's decision and not this script's.
    session = test_client.test_db_session
    monkeypatch.delenv(seed_qa_cast.TARGET_EMAIL_VAR, raising=False)

    with pytest.raises(SystemExit):
        seed_qa_cast._target(session, 'nobody-here@example.com')


def test_target_refuses_without_an_email_at_all(test_client, monkeypatch):
    session = test_client.test_db_session
    monkeypatch.delenv(seed_qa_cast.TARGET_EMAIL_VAR, raising=False)

    with pytest.raises(SystemExit):
        seed_qa_cast._target(session, None)


def test_target_reads_the_environment_when_no_email_is_passed(test_client, monkeypatch):
    session = test_client.test_db_session
    existing = test_client.first_user
    monkeypatch.setenv(seed_qa_cast.TARGET_EMAIL_VAR, existing.email)

    assert seed_qa_cast._target(session, None).email == existing.email


# --- which seats get seeded ----------------------------------------------


def test_admin_seat_is_skipped_without_a_password(monkeypatch):
    # An admin seat with a shared known password is harmless locally, where
    # seed_dev's guard means it unlocks nothing. On QA it would be admin access
    # to a prod-derived copy, so it has to be opted into.
    monkeypatch.delenv(seed_qa_cast.ADMIN_TWO_PASSWORD_VAR, raising=False)

    specs = seed_qa_cast._specs_for_qa()

    assert not any(s.get('admin') for s in specs)
    assert len(specs) == len(seed_dev._CAST_USERS) - 1


def test_admin_seat_is_seeded_with_its_own_password_when_supplied(monkeypatch):
    monkeypatch.setenv(seed_qa_cast.ADMIN_TWO_PASSWORD_VAR, 'a-real-secret')

    specs = seed_qa_cast._specs_for_qa()
    admin = next(s for s in specs if s.get('admin'))

    assert admin['password'] == 'a-real-secret'
    assert len(specs) == len(seed_dev._CAST_USERS)


def test_every_relationship_seat_is_always_seeded(monkeypatch):
    # The matrix is defined by the three relationship edges. Dropping a seat
    # would not give a smaller cast, it would give a different one.
    monkeypatch.delenv(seed_qa_cast.ADMIN_TWO_PASSWORD_VAR, raising=False)

    emails = {s['email'] for s in seed_qa_cast._specs_for_qa()}

    for required in (
        'friend@example.com',
        'follower@example.com',
        'followee@example.com',
    ):
        assert required in emails


# --- seeding itself -------------------------------------------------------


def test_seeding_a_subset_still_builds_the_relationship_edges(test_client):
    session = test_client.test_db_session
    target = test_client.first_user
    specs = tuple(s for s in seed_dev._CAST_USERS if not s.get('admin'))

    result = seed_dev._seed_cast(session, target, specs=specs)
    session.commit()

    assert result['cast_users'] == len(specs)
    assert session.query(DbFriendship).count() == 1
    assert session.query(DbFollow).count() == 2


def test_a_spec_password_override_is_used(test_client):
    session = test_client.test_db_session
    target = test_client.first_user
    specs = tuple(
        {**s, 'password': 'per-seat-secret'} if s.get('admin') else s
        for s in seed_dev._CAST_USERS
    )

    seed_dev._seed_cast(session, target, specs=specs)
    session.commit()

    admin_spec = next(s for s in seed_dev._CAST_USERS if s.get('admin'))
    user = _cast_user(session, admin_spec['email'])

    assert Hash.verify(user.password, 'per-seat-secret')
    assert not Hash.verify(user.password, seed_dev._CAST_PASSWORD)
