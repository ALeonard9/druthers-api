# druthers-api#352 — Add UNIQUE constraints and rank index to tracker tables

## What changed

- Composite tracker indexes were non-unique, so concurrent writes could create duplicate rows for the same user and catalog item; ranked-list reads also had no index matching their filter and sort.
- Added Alembic revision `8f2c1a4d9b73`, which deduplicates all five tracker tables, drops each old composite index, asserts that zero duplicate groups remain, and then creates a named `UNIQUE (user_id, <fk>)` constraint.
- Added partial `(user_id, rank) WHERE on_rankings AND rank IS NOT NULL` indexes to the four trackers that have ranking columns: movies, TV shows, books, and video games.
- Updated `app/db/models_sandbox.py` so SQLAlchemy metadata declares the five unique constraints and four partial rank indexes.

## Tests

- `tests/unit/tracker_constraints_migration_test.py` exercises the migration cleanup against real SQLite tables for movies, TV shows, books, games, and episodes. It asserts list membership wins over an empty row, Rankings wins over Watchlist, lower rank wins next, earlier `created_at` breaks remaining ties, users are partitioned independently, and episode active state wins over an empty duplicate.
- The same module asserts that a remaining duplicate group raises before unique DDL, verifies the upgrade ordering for every table, verifies downgrade restoration of the original non-unique indexes, inspects all model constraints/index predicates, and proves every model-created schema rejects a duplicate tracker pair.
- Focused run: `18 passed, 9 warnings in 2.03s`.
- Whole suite after commit: `890 passed, 17 warnings in 6.58s`.
- Pre-commit hooks passed, including Black and pylint.

## Demo notes

This is an operator-only database migration; there is no HTTP URL, user seat, or visible web flow to exercise.

On the orchestrator-owned Postgres copy of production, first record duplicate-group counts for each `(user_id, <fk>)` pair, then run:

```bash
alembic upgrade 8f2c1a4d9b73
```

Afterward, each duplicate-group query should return zero rows. Inspect `pg_constraint` for these five constraints:

- `uq_user_movies_user_id_movie_id`
- `uq_user_tv_shows_user_id_tv_show_id`
- `uq_user_books_user_id_book_id`
- `uq_user_video_games_user_id_game_id`
- `uq_user_tv_episodes_user_id_episode_id`

Inspect `pg_indexes` for four `ix_*_user_id_rank_on_rankings` indexes. Each definition should index `(user_id, rank)` and include `WHERE on_rankings AND rank IS NOT NULL`. Attempting to insert a second tracker row for the same user/catalog pair should fail with a unique-constraint violation. Before this change, that duplicate insert succeeded because the composite object was only a non-unique index.

For production-data survivor verification, compare duplicate groups captured before migration with their post-migration row: ranked rows should beat watchlist rows, watchlist rows should beat empty rows, lower rank should win among otherwise equal ranked rows, then earlier non-null `created_at`, then lower `pk`.

## Decisions I made

- Applied uniqueness to all five named tracker tables. Partial rank indexes apply only to movies, TV shows, books, and games because `user_tv_episodes` has neither `on_rankings` nor `rank`.
- For episode duplicates, treated `watched <> 0 OR favorited` as active state so an empty duplicate cannot displace a real mark; active rows then use earliest non-null `created_at` and lowest `pk` as deterministic tie-breakers.
- Sorted null `created_at` values after real timestamps and used `pk` as the final tie-breaker so cleanup is deterministic even when legacy timestamps are absent or equal.
- Kept the old composite index in place during dedupe, then dropped it before the zero-duplicate assertion and immediately following `CREATE UNIQUE`.
- Covered movies, TV shows, books, and games in lockstep with the same migration configuration, model helper, and parameterized behavioral tests.
- Downgrade restores schema shape but intentionally does not recreate deleted duplicate rows.

## Not done / uncertain

- I did not verify the migration against a copy of the production dataset. Fleet-worker rules prohibit starting Postgres/Docker or using the orchestrator-owned dev stack, so this acceptance criterion must be completed during the orchestrator's migration demo before approval.
- I did not run the revision against PostgreSQL in this worktree for the same reason. The cleanup SQL was executed behaviorally against SQLite, and PostgreSQL-specific index options were asserted from Alembic calls and SQLAlchemy metadata.
- `WORKER_REPORT.md` was already tracked on `origin/main` before this task (from commit `d328e9e`). This updated handoff is intentionally not in commit `aa44472`, but Git therefore reports it as modified rather than untracked.
