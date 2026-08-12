# druthers-api#328 — Set a new account's time zone from the device

## What changed

- **API:** Unset `TIME_ZONE` used Central time, so configuration and both deployment templates now default to `America/New_York`. Existing `users.time_zone` values and `NULL` rows are unchanged.
- **API:** The sign-in token response now carries the raw nullable `time_zone`. This preserves the distinction hidden by the preferences endpoint's effective-zone fallback, so the web app can safely tell an empty value from an intentional `America/New_York` choice.
- **Web:** Successful password and Google sign-ins detect and save the browser's IANA zone exactly when that raw value is empty. The shared detector is also used by Settings' existing “Use this device's zone” control.
- **Web:** A chosen zone makes detection a no-op; a rejected or failed detection is ignored after authentication, so the signed-in session continues.

## Tests

- `tests/integration/router_preferences_test.py`: an untouched account's effective preference is specifically `America/New_York`.
- `tests/integration/router_auth_test.py`: a token response exposes `None` for an account whose stored column is empty.
- `src/lib/deviceTimeZoneDetection.test.ts`: a `NULL` zone sends one device-zone `PUT`; an already chosen zone neither reads the device nor writes; a 422 resolves without failing the sign-in flow.
- `pytest -q`: **870 passed, 17 warnings** in 12.38s. The warnings are existing FastAPI TestClient deprecations and SQLAlchemy transaction warnings.
- `npm test`: **61 test files passed, 283 tests passed** in 4.80s.
- Commit hooks also passed: API Black/Pylint and web ESLint/typecheck.

## Demo notes

### Detect an empty zone

1. **Sign in as** — `$ADMIN_EMAIL` / `$ADMIN_PASSWORD` from `env/dev.env`; this seeded `you` account is the local account whose time zone starts unset.
2. **Go to** — <http://localhost:3000/login>.
3. **Do this** — Set the browser/device zone to a recognizable IANA zone different from Eastern (for example `America/Los_Angeles`), choose **Other sign-in options**, sign in, then open <http://localhost:3000/settings>.
4. **You should see** — Settings → **Time zone** is `America/Los_Angeles`; a `GET /v1/users/me/preferences` for that session returns `"time_zone": "America/Los_Angeles"`.
5. **What it looked like before** — the column stayed `NULL` and Settings showed the deployment fallback instead of the device zone.

For a repeatable fresh-state demo, clear only the local `you` account's `users.time_zone` back to `NULL` before signing in; reseeding is additive and does not clear a zone detection already saved.

### Prove a chosen zone is never overwritten

1. **Sign in as** — `friend@example.com` / `change-me`; its seeded stored zone is `Europe/London`.
2. **Go to** — <http://localhost:3000/login>.
3. **Do this** — With the same browser/device zone set to `America/Los_Angeles`, choose **Other sign-in options**, sign in, then open <http://localhost:3000/settings>.
4. **You should see** — Settings → **Time zone** remains `Europe/London`, not `America/Los_Angeles`; no preferences `PUT` is sent after the successful sign-in.
5. **What it looked like before** — there was no automatic sign-in detection; a naive detector would have silently replaced the saved London preference while travelling.

## Decisions I made

- Added the raw nullable zone to the existing auth response rather than changing the effective preferences response. That response must remain concrete for greetings and schedule rendering, while the sign-in flow needs the raw state to uphold the never-overwrite rule.
- Kept all HTTP outcomes from the best-effort detection non-fatal. In particular, 422 leaves the database column unchanged and still completes navigation into the signed-in app.

## Not done / uncertain

- Per fleet-worker instructions, I did not start the local stack or perform the browser demo; the orchestrator should run the steps above.
- Ran `graphify update .` in the API worktree as required by its `AGENTS.md`, but graphify could not rebuild due to `Operation not permitted`; no graph output was changed.
