# Testing conventions

Load this when deciding what test a change needs and where it goes.

## Testing

A new module needs a test file in the same PR that introduces it — not as
follow-up work. This project's test debt (audited 2026-08-03, tracked in
issues #290–293) came almost entirely from modules that shipped without one
and were never revisited.

- **New router/service/job** (`app/router/`, `app/services/`, `app/jobs/`,
  `app/migration/`): add a matching `tests/integration/<name>_test.py` or
  `tests/unit/<name>_test.py`. Every existing router already has one —
  match that, don't be the exception.
- **Per-domain work** (movies/TV/books/games, or the same pattern in
  druthers-mcp's tool families): if you're touching one domain, check
  whether the other three need the same change *and* the same test. Silent
  gaps like this are exactly what #291 and #39/#40 went back to fix —
  cheaper to keep the four in lockstep than to backfill later.
- **New interactive web component** (`src/components/`): add a
  `<name>.test.tsx` alongside it once the React Testing Library setup from
  #136/#137 is in place. Pure logic still belongs in `src/lib/*.ts` with a
  `.test.ts` sibling, not inside the component.
- **New MCP tool** (`druthers_mcp/server.py`): add a test in
  `tests/server_test.py` following the pattern of the nearest existing
  sibling tool (e.g. a new `set_*_note` tool mirrors `set_note`'s test).
- Coverage is a floor, not a target: CI fails if total coverage drops below
  its current baseline (the ratchet from #292/#138), but a passing ratchet
  only proves nothing else regressed — it's not evidence the new code itself
  is tested. Don't point to a green build in place of a test for the thing
  you just wrote.
- `test`/`lint` are becoming required status checks on `main` alongside the
  security scan (#24) — once that lands, a PR with failing tests won't merge,
  not just won't get reviewed. Until then, treat a red `test`/`lint` run as
  a hard blocker anyway; the check not being enforced yet isn't permission
  to ignore it.
