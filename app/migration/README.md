# Legacy data migration - orion (MySQL) → druthers (PostgreSQL)

One-off, **idempotent** ETL that imports users and the four tracker domains
(movies, TV + episodes, video games, books) from the legacy `orion`
MySQL database into the modern `druthers` PostgreSQL schema.

Betting (`bet`), crypto, and Smash Up (`smash`) are **out of scope** for this
pass (those domains are not yet modeled in the API).

## What it does

- Reads a **read-only** source (a `mysqldump` loaded into a throwaway MySQL, or
  the live DB) via `ORION_MYSQL_URL`.
- Writes to the same target the app uses (`DATABASE_URL` / `POSTGRES_*`).
- Upserts catalogs on their natural keys (`imdb`, `tvmaze`, `igdb`, `googleid`,
  `email`) and tracker rows on `(user, item)`, so it is safe to
  re-run - a second run reports **0 inserts, all updates**.
- Prints a reconciliation table (source vs insert/update/skip per table).

## Notes / deliberate decisions

- **Passwords are not migrated.** Legacy hashes are bcrypt (or NULL for Google
  accounts); the new stack uses Argon2 and cannot verify them. Each imported
  user gets an unusable random password - they re-auth via Google or a reset.
- Legacy `user_group` (`User`/`Admin`) is lowercased to match the new RBAC.
- `g_first` (first-completed) is preserved into `created_at`.
- Rows with a null natural key are skipped and counted (e.g. blank-email users,
  untracked books with no `googleid`/title).

## Run it

```bash
# 1. Load a dump into a throwaway MySQL 5.7 (Apple Silicon needs --platform):
docker run -d --name orion_src --platform linux/amd64 \
  -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=orion -p 13306:3306 \
  -v "$PWD/orion_backup.sql:/docker-entrypoint-initdb.d/orion.sql:ro" mysql:5.7

# 2. Create the target schema (against druthers Postgres):
export DATABASE_URL=postgresql://druthers:druthers@127.0.0.1:5432/druthers ENV=prod
alembic upgrade head

# 3. Dry-run (full transaction, rolled back), then the real import:
export ORION_MYSQL_URL=mysql+pymysql://root:root@127.0.0.1:13306/orion
task import:orion -- --dry-run
task import:orion
```

Requires the dev dependency `PyMySQL` (in `requirements/dev.txt`).

## seed_dev.py

Populates the **local dev** Postgres with a realistic volume of catalog +
tracker data, sourced from the checked-in fixtures
`app/migration/fixtures/seed_*.json` - real movies/shows/books/games
captured from TMDB/TVMaze/Open Library/IGDB, the same providers
`orion_import.py` and the `enrich_*`/`backfill_*` scripts use (#228). Unlike
the old Faker-based seeder it replaced, what's synthesized here is
*selection and tracker state* - which titles get seeded, which list they
land on, rank order, completion dates - not the content itself.

- Catalog rows are upserted on their natural key (`tmdb`/`imdb`/`tvmaze`/
  `isbn`/`igdb`), so a re-run - or a fixture title that happens to already
  exist from an `orion_import` run - never produces a duplicate movie/show/
  book/game.
- Only the *tracker* rows this script creates are marked
  (`is_seed_data=True`); catalog rows are left alone by `--wipe` either way,
  since once a title is real there's no such thing as a "fake" catalog row
  to clean up - see `DbUserMovie.is_seed_data`'s docstring.
- **Refuses to run against anything but the local dev Postgres** (checks
  `ENV` and the *resolved* connection host - `DATABASE_URL` when set, not
  just `POSTGRES_HOST`, see #257) - this script performs bulk writes and
  must never be able to reach QA or prod.
- Re-runnable: each run wipes the tracker rows it previously created for the
  target user, then reseeds. `--wipe` clears them without reseeding.
  `--count N` controls movie volume (default 270); TV/books/games scale off
  it at roughly prod's real proportions, capped to what the fixture holds.

```bash
task seed:dev                 # populate/refresh
task seed:dev -- --count 150  # less volume
task seed:dev -- --wipe       # clear only
```

### build_seed_fixtures.py

Regenerates `app/migration/fixtures/seed_*.json` from the live providers.
Not run automatically by anything - a maintenance tool for when the fixture
should pick up newer titles. Needs `TMDB_API_KEY` and
`TWITCH_CLIENT_ID`/`TWITCH_CLIENT_SECRET`; Open Library and TVMaze need no
key. Takes a few minutes (throttled to be polite to each provider).

```bash
task fixtures:build
```
