# druthers-api#350 — Fix rate limiter IP spoofing via XFF header and add key eviction

## What changed

- Cloud Run appends the real client IP after any client-supplied `X-Forwarded-For` values, but the limiter used the spoofable leftmost value.
- The limiter now uses the rightmost XFF hop and periodically removes rate-limit buckets after their own window expires, preventing stale keys from accumulating indefinitely.

## Tests

- `tests/integration/rate_limit_test.py::test_auth_limit_uses_cloud_runs_rightmost_xff_hop` sends four failed authentication attempts with different forged leftmost hops and one shared rightmost hop; it asserts the fourth receives `429`.
- `tests/integration/rate_limit_test.py::test_periodic_eviction_removes_stale_rate_limit_keys` advances monotonic time beyond the auth window and asserts the stale bucket is removed while the new bucket remains.
- `pytest -q tests/integration/rate_limit_test.py` — `9 passed, 9 warnings in 2.18s`.
- `pytest -q` — `874 passed, 17 warnings in 7.36s`.

## Demo notes

- **Sign in as:** no account is required; the rate limit runs before credential validation.
- **Go to:** `http://localhost:8000/v1/auth/token`.
- **Do this:** send four `POST` requests with the same invalid form credentials and an `X-Forwarded-For` header of `198.51.100.N, 203.0.113.10`, changing only `N` from 1 through 4.
- **You should see:** the first three requests return the normal invalid-account response (`404`); the fourth returns `429` with `Retry-After: 300`.
- **What it looked like before:** changing the forged leftmost header value made every request use a fresh bucket, so all four returned `404`.

## Decisions I made

- Eviction runs at most once per 60 seconds during rate-limit checks. Each key is pruned using its corresponding rate-limit window, retaining active catalog, friend, follow, search, refresh, and auth buckets while deleting expired ones.

## Not done / uncertain

- No domain-specific movies/TV/books/games behavior is involved; this is shared rate-limit infrastructure.
- I did not start the local stack, per fleet-worker constraints.
- `graphify update .` was attempted after the code change but could not rebuild because the sandbox returned `Operation not permitted`; no graph files were changed.
