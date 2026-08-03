# pylint: disable=missing-module-docstring, missing-function-docstring
from unittest.mock import patch

from app.db.models_sandbox import DbVideoGame
from app.migration import backfill_time_to_beat


def _game(session, igdb, time_to_beat=None):
    game = DbVideoGame(title='A Game', igdb=igdb, time_to_beat=time_to_beat)
    session.add(game)
    session.commit()
    return game


def _run_capturing(session, capsys, get_time_to_beat_return):
    with patch.object(
        backfill_time_to_beat, 'SessionLocal', return_value=session
    ), patch.object(
        backfill_time_to_beat, 'get_time_to_beat', return_value=get_time_to_beat_return
    ), patch.object(
        backfill_time_to_beat.time, 'sleep'
    ):
        session.close = lambda: None  # run() closes its own session
        backfill_time_to_beat.run()
    return capsys.readouterr().out


def test_pending_games_excludes_already_filled_and_igdb_less_rows(test_client):
    session = test_client.test_db_session
    _game(session, igdb=1, time_to_beat=None)
    _game(session, igdb=2, time_to_beat=12)
    _game(session, igdb=None, time_to_beat=None)

    pending = backfill_time_to_beat.pending_games(session)

    assert [g.igdb for g in pending] == [1]


def test_run_fills_time_to_beat_when_igdb_has_data(test_client, capsys):
    session = test_client.test_db_session
    game = _game(session, igdb=1)

    out = _run_capturing(session, capsys, get_time_to_beat_return=9)

    assert game.time_to_beat == 9
    assert 'filled 1, misses 0' in out


def test_run_leaves_null_on_a_genuine_miss(test_client, capsys):
    session = test_client.test_db_session
    game = _game(session, igdb=1)

    out = _run_capturing(session, capsys, get_time_to_beat_return=None)

    assert game.time_to_beat is None
    assert 'filled 0, misses 1' in out
