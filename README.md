[![lint](https://github.com/ALeonard9/druthers-api/actions/workflows/lint.yaml/badge.svg?branch=main)](https://github.com/ALeonard9/druthers-api/actions/workflows/lint.yaml)
[![tests](https://github.com/ALeonard9/druthers-api/actions/workflows/test.yaml/badge.svg?branch=main)](https://github.com/ALeonard9/druthers-api/actions/workflows/test.yaml)
[![security](https://github.com/ALeonard9/druthers-api/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/ALeonard9/druthers-api/actions/workflows/security.yml)

# Druthers API

> **[Druthers](https://druthers.io)** is social taste-sharing for the things you love —
> **Movies, TV, Books, and Games**. Track what you've watched, played, and read;
> share a formatted top-5; and find the overlap with a friend.

## What this is

The **backend API** that powers it: a JWT-authenticated FastAPI service with
Google sign-in, personal API keys, per-domain trackers, and clean auto-generated
docs. It runs serverless on **Google Cloud Run** over **Neon** Postgres in
production.

- **Sign in with Google** (OAuth) or a long-lived personal **API key** (`drk_…`) for tools/scripts
- **Track four domains** — Movies, TV (with episodes), Books, and Games — each with watched/played/read status, notes, and completion dates
- **Search & add** from external catalogs (TMDB, TVmaze, Open Library, IGDB) behind one API
- **Role-based access** — you manage your own library; admins manage shared catalog data
- **Secure by default** — OSS security pipeline (secrets, SAST, dependencies, container) on every PR

## Stack

| Layer | Technology |
|---|---|
| Framework | [FastAPI](https://fastapi.tiangolo.com/) |
| ORM / migrations | [SQLAlchemy](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) |
| Validation | [Pydantic](https://docs.pydantic.dev/) |
| Auth | Google OAuth · JWTs · hashed `drk_` API keys |
| Database | PostgreSQL — [Neon](https://neon.tech) in prod, local Docker Postgres for dev |
| Runtime | Docker (Alpine, Python 3.14) on **Cloud Run** |
| CI/CD & security | GitHub Actions · Gitleaks · Semgrep · Trivy · Dependabot |

## Setup / Local Development

```bash
git clone https://github.com/ALeonard9/druthers-api.git
cd druthers-api
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
uvicorn app.run:app --reload           # http://localhost:8000
```

Open **http://localhost:8000/docs** for the interactive API explorer. A running
Postgres isn't required to boot the app; most settings have local-friendly
defaults (see `app/config.py`) — set `DATABASE_URL` for a real database.

## API reference

All endpoints are prefixed with `/v1`; protected routes require `Authorization: Bearer <token>`.

| Area | Example routes |
|---|---|
| **Auth** | `POST /v1/auth/token` · Google OAuth exchange · `POST /v1/users/me/api-keys` (mint a `drk_` key) |
| **Users** | `POST /v1/users` · `GET/PUT/DELETE /v1/users/{uuid}` |
| **Catalog** (movies · tv-shows · books · games) | `GET /v1/{domain}` · admin `POST/PUT/DELETE` |
| **My library** | `GET /v1/users/me/{domain}` · `POST/PUT/DELETE /v1/users/me/{domain}/{id}` (mark watched/played/read, notes, dates) |
| **TV episodes** | `…/tv-shows/{id}/episodes` · `…/users/me/episodes` |
| **Docs** | `/docs` (Swagger) · `/redoc` · `/openapi.json` |

## Customer-facing docs

- **Postman collection:** [`docs/druthers-api.postman_collection.json`](docs/druthers-api.postman_collection.json)
  — every route as a ready-to-run request, generated from `openapi.json` by
  [`scripts/generate_postman_collection.py`](scripts/generate_postman_collection.py).
  Import it into Postman and set the `apiToken` variable to a personal API key.
- **MCP usage guide:** [`docs/mcp-usage.md`](docs/mcp-usage.md) — connect
  Claude Desktop/Code to your Druthers library.

Both are also linked from [druthers.io/developers](https://www.druthers.io/developers).

## Development
Docker Compose is also available (`task du` / `dc-dev.yml`), but requires a
local `env/dev.env` (gitignored, not committed) populated from the variables in
`app/config.py`.

```bash
task du            # docker compose up (dev)     task dd   # down
task test          # pytest with coverage
```

**Pre-commit** runs fast checks on every commit (Gitleaks secret scan, Black,
Pylint, OpenAPI/YAML/JSON validation). **Tests run at _pre-push_**, and only for
what changed (`pytest-testmon`) — CI runs the full suite as the merge gate.

```bash
pip install pre-commit && pre-commit install && pre-commit install --hook-type pre-push
```

## API reference

All endpoints are prefixed with `/v1`; protected routes require `Authorization: Bearer <token>`.
Auto-generated docs: **Swagger UI** at `/docs`, **ReDoc** at `/redoc`, raw schema at `/openapi.json`.

| Area | Example routes |
|---|---|
| **Auth** | `POST /v1/auth/token` · Google OAuth exchange · `POST /v1/users/me/api-keys` (mint a `drk_` key) |
| **Users** | `POST /v1/users` · `GET/PUT/DELETE /v1/users/{uuid}` |
| **Catalog** (movies · tv-shows · books · games) | `GET /v1/{domain}` · admin `POST/PUT/DELETE` |
| **My library** | `GET /v1/users/me/{domain}` · `POST/PUT/DELETE /v1/users/me/{domain}/{id}` (mark watched/played/read, notes, dates) |
| **TV episodes** | `…/tv-shows/{id}/episodes` · `…/users/me/episodes` |

## Security

Every pull request and push to `main` is scanned by an all–open-source pipeline —
**Gitleaks** (secrets), **Semgrep** (SAST), and **Trivy** (dependencies, container,
IaC) — with results in the repo's **Security** tab, plus **Dependabot** and GitHub
**push protection**. See [`SECURITY.md`](https://github.com/ALeonard9/.github/blob/main/SECURITY.md)
to report a vulnerability.

## Related repos

- **[druthers-web](https://github.com/ALeonard9/druthers-web)** — Next.js frontend and BFF for druthers.io.
- **[druthers-mcp](https://github.com/ALeonard9/druthers-mcp)** — MCP server that lets Claude and other assistants manage your library.
- **[druthers-infra](https://github.com/ALeonard9/druthers-infra)** — infrastructure-as-code and ops runbooks (private repo).

## Contributing

Issues and pull requests are welcome — fork, branch, and open a PR. The required
`security` check and CI must pass before merge. See
[`SDLC.md`](SDLC.md) for the full development lifecycle shared across druthers
repos.

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
