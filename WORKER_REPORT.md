# druthers-api#240 — Run the TV duplicate-slot audit automatically instead of by hand

## What changed

- The existing repair operated on globally shared episode rows but only preserved the user whose audit pass found the duplicate, so an unattended all-user run could delete another user's watch or favorite state.
- The duplicate-slot repair now merges every user's watched/favorited flags and original timestamps onto the oldest episode row, flushes tracker moves before the delete-orphan cascade, and only then deletes duplicate episode rows.
- Added a two-user regression proving that a global `audit(..., fix=True)` run collapses the slot while retaining both users' watch history and the second user's independent favorite.

## Tests

- `tests/integration/router_tv_reassignment_test.py::test_audit_fix_preserves_every_users_state` asserts that an unfiltered fix repairs one shared duplicate slot for two tracked users, leaves one mark per user on the keeper, and preserves `watched`, `watched_at`, `favorited`, and `favorited_at` state combined across keeper/orphan rows.
- Existing `test_ambiguous_slot_is_left_alone` remains unchanged and passed, so ingest still refuses to guess when a slot is already ambiguous.
- Focused run: `6 passed, 9 warnings in 2.11s`.
- Whole suite: `867 passed, 17 warnings in 8.07s`.
- Pre-commit hooks passed, including Black and pylint.

## Demo notes

This is an operator CLI job; there is no HTTP URL or user seat to exercise. In the orchestrator-owned local stack, prepare one show with two episode rows sharing `(tv_show_id, season, season_number)`, put one user's favorite on the oldest row and that user's watch mark on the newer row, then run:

```bash
python -m app.jobs.audit_watch_gaps --fix
```

The command should print a `Repaired: 1` section naming the slot and `Committed.`. The database should then contain one episode row for the slot and one combined tracker row for that user with both the original favorite and watch state. A separate aired lone gap should appear under `Lone unwatched episodes (review by hand)` and remain unwatched.

For the actual scheduled-job demo, inspect the infra cron log under `~/dev/druthers/cron/log/` after invoking the new infra script; repaired slots and lone gaps are already printed by this CLI and should be captured there.

## Decisions I made

- I changed the existing API repair because scheduling its current unfiltered `--fix` path would otherwise risk cross-user data loss. This is a prerequisite safety fix, not a second scheduling mechanism.
- When duplicate tracker rows contain state on both episode rows, the merged tracker uses logical OR for watched/favorited and retains the earliest non-null timestamp for each active state.
- This is TV-only. Movies, books, and games do not have shared episode slots or an equivalent per-episode duplicate audit, so no matching domain change applies.

## Not done / uncertain

- I did not create `druthers-infra/cron/audit-watch-gaps.sh`, edit the hephaestus crontab, or update `druthers-infra/docs/CRONS.md`; those are the actual scheduling changes and are outside this worktree/repository boundary.
- I did not access Docker, the local dev stack, hephaestus, Neon production, or cron logs, per fleet-worker constraints. The orchestrator must verify the infra script cadence, image/env wiring, all-user invocation (no `--email`), and log redirection.
- Existing CLI output already reports repaired duplicate slots and lone gaps, so no logging-format change was necessary here.
