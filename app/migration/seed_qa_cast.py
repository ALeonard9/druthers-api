"""
Seed the dev cast into QA, idempotently, on every deploy (#413).

QA's database is a Neon branch reset from prod, so it holds real library data
and no fixture accounts. Without a cast there, druthers-web's authenticated
Playwright lane can only run the handful of specs that need nobody in
particular: everything about visibility, comparison, friendship and shelves
names a seat.

A Neon reset also wipes anything seeded by hand, silently, which is why this
runs on deploy rather than once.

**Deliberately not ``seed_dev``.** That script refuses to run anywhere but the
local dev Postgres and should keep refusing: it performs bulk randomized
writes and must never reach a prod-derived database. This one shares its cast
*definition* (``_CAST_USERS`` is the only place those facts live besides
``docs/dev-cast.md``) but seeds only the cast: the accounts, their tiers,
their three relationship edges, and the canon titles their shelf sizes depend
on. No randomized volume, and nothing outside the accounts it creates.

Every tracker row it writes is marked ``is_seed_data=True``, so it stays
distinguishable from the prod-derived rows around it.

Usage (the deploy runs the first form)::

    python -m app.migration.seed_qa_cast
    python -m app.migration.seed_qa_cast --email someone@example.com

The target defaults to ``QA_E2E_TARGET_EMAIL``. It must already exist: this
script never creates the account the whole cast is anchored to, because that
account is the one with real credentials and an operator should have made a
deliberate decision about it.

**admin-two is opt-in.** The seat exists so "an admin cannot impersonate or
disable another admin" (#341) is demonstrable, and it is an admin account. On
a local database a known password on it unlocks nothing. On QA it would be
admin access to a prod-derived copy, reachable from the internet, so it is
seeded only when ``QA_ADMIN_TWO_PASSWORD`` supplies a real password. Without
that variable the seat is skipped and the admin-on-admin spec stays local.
"""

import argparse
import logging
import os
import sys
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import SessionLocal
from app.db.models import DbUser
from app.migration.seed_dev import _CAST_USERS, _seed_cast

logger = logging.getLogger(__name__)

TARGET_EMAIL_VAR = 'QA_E2E_TARGET_EMAIL'
ADMIN_TWO_PASSWORD_VAR = 'QA_ADMIN_TWO_PASSWORD'


def _assert_qa() -> None:
    """
    Refuse to run anywhere but QA.

    The mirror of ``seed_dev._assert_local_dev``, and it exists for the same
    reason: this writes to a database, and the one it must never reach is
    prod. Checks the *resolved* connection host as well as ENV, since
    DATABASE_URL wins over the discrete POSTGRES_* parts and an env var alone
    is not proof of where the writes are going.
    """
    settings = get_settings()
    host = (urlsplit(settings.sqlalchemy_database_url).hostname or '').lower()
    if settings.env != 'qa':
        logger.error(
            'seed_qa_cast refuses to run: ENV=%s is not qa. This script '
            'writes to a database and must never reach prod.',
            settings.env,
        )
        sys.exit(2)
    # An unset DATABASE_URL does not leave the host empty: settings falls back
    # to the discrete POSTGRES_* parts and the hostname resolves to the literal
    # string 'none'. A truthiness check would sail straight past that, so name
    # the placeholders. localhost is here too: ENV=qa pointed at a local
    # database means something is misconfigured, and seeding it would be
    # writing QA fixtures into whatever happens to be running.
    if host in ('', 'none', 'localhost', '127.0.0.1'):
        logger.error(
            'seed_qa_cast refuses to run: ENV=qa but the resolved database '
            'host is %r, which is not a deployed QA database.',
            host,
        )
        sys.exit(2)


def _specs_for_qa() -> tuple:
    """
    The cast to seed here.

    Every relationship seat, always: the matrix is defined by them and a
    subset is a different cast rather than a smaller one. ``admin-two`` only
    when a real password is supplied, since seeding an admin with a shared
    known password on an internet-facing host is a different proposition from
    doing it locally.
    """
    admin_password = os.getenv(ADMIN_TWO_PASSWORD_VAR)
    specs = []
    for spec in _CAST_USERS:
        if spec.get('admin'):
            if not admin_password:
                logger.info(
                    'Skipping %s: set %s to seed the second admin seat. '
                    'The admin-on-admin rule stays local-only without it.',
                    spec['handle'],
                    ADMIN_TWO_PASSWORD_VAR,
                )
                continue
            spec = {**spec, 'password': admin_password}
        specs.append(spec)
    return tuple(specs)


def _target(session: Session, email: str | None) -> DbUser:
    """The account the cast is anchored to. Never created here."""
    email = email or os.getenv(TARGET_EMAIL_VAR)
    if not email:
        logger.error(
            'seed_qa_cast needs a target: set %s or pass --email.',
            TARGET_EMAIL_VAR,
        )
        sys.exit(2)
    user = session.query(DbUser).filter_by(email=email).one_or_none()
    if user is None:
        logger.error(
            'seed_qa_cast refuses to create the target account %s. Create it '
            'deliberately (druthers-infra scripts/qa-create-user.sh), then '
            're-run.',
            email,
        )
        sys.exit(2)
    return user


def run(email: str = None) -> dict:
    """Seed the cast against QA and return what was created."""
    _assert_qa()
    session = SessionLocal()
    try:
        result = _seed_cast(session, _target(session, email), specs=_specs_for_qa())
        session.commit()
        logger.info(
            'QA cast seeded: %s accounts, %s ranked rows.',
            result['cast_users'],
            result['ranked_rows'],
        )
        return result
    finally:
        session.close()


def main() -> None:
    """CLI entry point: parse --email and seed."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--email',
        default=None,
        help=f'Target account the cast anchors to (default: ${TARGET_EMAIL_VAR}).',
    )
    args = parser.parse_args()
    run(args.email)


if __name__ == '__main__':
    main()
