# druthers-api#349 — Convert API Dockerfile to multi-stage build to reduce image size

## What changed

- The production image retained compilers, Rust, PostgreSQL headers, and the dependency build process, making Cloud Run pull a large build environment.
- Split the Dockerfile into a builder that creates dependency wheels and a runtime stage that installs only those wheels, the app, and `libpq`; the runtime healthcheck now calls the app's explicit `/health` endpoint.
- Added a regression test that verifies build-only packages remain exclusively in the builder stage and that the runtime consumes the builder wheels and probes `/health`.

## Tests

- `tests/unit/dockerfile_test.py`
  - verifies the builder produces wheels with PostgreSQL headers, gcc, Rust, and Cargo while the runtime copies those wheels without retaining those build packages.
  - verifies the runtime Docker healthcheck targets `/health`.
- `pytest -q` — `874 passed, 17 warnings in 7.07s`

## Demo notes

Sign in as: not applicable; this is an API container-build change.

Go to: [http://localhost:8000/health](http://localhost:8000/health)

Do this:

1. From this worktree, build the production image: `docker build -t druthers-api:349 .`.
2. Check the resulting image size: `docker image inspect druthers-api:349 --format '{{.Size}}'`.
3. Start it with the production environment variables and expose port 8000.
4. Request `http://localhost:8000/health`.

You should see: a successful health response whose JSON contains `"status":"ok"`; the image should be roughly 150–250 MB rather than the previous 1.26 GB.

What it looked like before: the single-stage image retained gcc, Rust, Cargo, PostgreSQL development headers, and other build dependencies.

## Decisions I made

- Kept the Alpine Python base used by the existing deployment and added only `libpq` to runtime, because it supplies the PostgreSQL client library without the development headers.
- Kept removal of pip and ensurepip bootstrap wheels from the runtime image to preserve the existing runtime CVE mitigation.
- Used `/health`, which is the application's explicit liveness endpoint, instead of `/` for the Docker healthcheck.

## Not done / uncertain

- Per fleet-worker boundaries, I did not run Docker, start the API, or measure the runtime image. The orchestrator must verify the production image boot, `/health`, and final image size during the local demo.
- `graphify update .` was attempted as required by `AGENTS.md`, but the graph rebuild failed with `Operation not permitted` under this worktree's filesystem sandbox.
