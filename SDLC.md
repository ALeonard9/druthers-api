# aleonard.us SDLC (canonical)

Shared software-development lifecycle for all aleonard.us services
(`druthers-api`, `druthers-web`, `druthers-mcp`, `www.aleonard.us-docker`).
Other repos link here instead of duplicating it.

## Branching - trunk-based

- **`main` is protected** and always deployable. No direct pushes.
- Work on **short-lived branches** off `main`, named by type:
  - `feat/<slug>` - new capability
  - `fix/<slug>` - bug fix
  - `chore/<slug>` - deps, tooling, docs, refactors
- Open a **pull request** early. Merge only when **CI is green**.
- **Squash-merge** to keep `main` linear; delete the branch after merge.
- Keep branches small and short-lived; rebase on `main` rather than long-running forks.

## Pull requests

- Fill in the PR template: what changed, why, how it was verified.
- At least one approving review (self-review acceptable for solo work, but CI must pass).
- Security-sensitive changes: add the `scan` label to trigger the Snyk workflow.

## CI gates (must pass to merge)

| Repo | Gates |
|------|-------|
| api | Black, Pylint, Pytest (+ OpenAPI validation), Snyk on `scan`/main |
| web | ESLint, `tsc --noEmit`, Vitest, `next build`, Playwright smoke |
| mcp | Black, Pylint, Pytest |

## Local pre-commit

Every repo ships a `.pre-commit-config.yaml`. Run `pre-commit install` once;
hooks run formatting, linting, and tests on commit. Don't bypass with
`--no-verify` except for documented environment issues.

## Versioning & releases

- Images publish to **GHCR** (`ghcr.io/aleonard9/<repo>`) on a **GitHub Release**
  (`publish_docker.yaml`), with build-provenance attestation.
- Tag releases `vMAJOR.MINOR.PATCH`. `main` builds are `latest`/`prod`.

## Environments & portability

- `LZ` (landing zone: `m3` local, `gs` homelab) and `ENV` (`local`/`dev`/`prod`/`github`)
  template container names, networks (`${LZ}_network_${ENV}`), and volumes.
- Zero inbound ports (Cloudflare Tunnel); standard OCI images ⇒ the same compose
  retargets to AWS/GCP by swapping `LZ` and the DB/secret source.

## Secrets

- Never commit secrets. Commit `*.env.template` / `.env.example` only; real
  `env/*.env` are gitignored (`chmod 600` on servers).
- Rotate anything that leaks.
