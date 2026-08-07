## Development workflow

New functionality gets demoed locally and approved by Adam **before** anything
is committed. Applies to all the druthers repos (`druthers-api`,
`druthers-web`, `druthers-mcp`, `druthers-infra`).

1. **Build it against the local dev stack.** Use the `druthers-up` skill to
   bring up Postgres + the API + `next dev`. Verify against real upstream data
   (TMDB, TVMaze, Open Library, IGDB), not just mocks and unit tests.
2. **Test in browser & demo with local URL + visuals.**
   Use the browser subagent to interactively test new web functionality on the
   local dev stack. Present a local URL (e.g. `http://localhost:3000/u/dadam` or
   `http://localhost:3000/movies/<id>`), a list of what to look for, embedded
   screenshots for visual review, and session recording videos (`.webp`) for
   complex/important UX flows. Stop and wait for Adam's approval.
3. **Only after Adam approves:** spin the local environment down
   (`task dd -- dev` in `druthers-api`), then commit, push, and open the PR.
4. **Hand back the PR link.** Merging, releasing, and deploying stay separate
   asks — never chain them off the same approval.
5. **Once the PR is merged, return the local repo to `main` and pull** (and
   delete the now-merged local branch). A repo left checked out on a stale
   branch is silently inherited by the next session, which either builds new
   work on top of dead history or has to spend a turn untangling it first.

Do not commit or open a PR ahead of the demo, even when tests and CI would
pass. The approval gate is the demo, not the green build.

Before starting new work in any of these repos, check `git branch --show-current`
and `git status` first — don't assume the checkout is `main` or clean.

### Issue vs. PR numbers

GitHub issue numbers and PR numbers share one repo-wide counter, so a bare
number is ambiguous — `289` could be either, and groomed backlog stories from
`story-intake` are always issues, never PRs. When told to "pull in" / "start" /
"work on" a bare number (or a repo-prefixed one like "web 134"), don't assume
which it is from context or phrasing — confirm with `gh issue view <n>` and/or
`gh pr view <n>` before branching off it, reporting its status, or otherwise
acting on it.

Don't trust issue/PR *state* at face value either — it can drift from what's
actually in the code:

- A PR body listing `Closes #a, #b, #c, ...` as one comma-separated list after
  a single keyword reliably auto-closes only the **first** issue on merge —
  the rest silently stay open even though the code shipped (#283 merged and
  claimed six closes; only one fired). When writing a PR body that closes
  several issues, repeat the keyword per issue (`Closes #a. Closes #b.`) —
  don't rely on the comma form. When *reading* a merged PR that lists several
  issues via the comma form, verify each one's state with `gh issue view`
  rather than assuming the merge closed all of them.
- A PR can also be closed **without merging** and silently orphan a whole
  downstream stack of branches (#287 closed, blocking #279's work and
  everything branched on top of it from ever reaching `main`). If a
  dependency issue/PR looks unexpectedly open or blocked, check whether the
  PR that was supposed to deliver it actually merged — don't assume "closed"
  means "done," and don't assume a dependency is real work remaining without
  checking whether it already shipped under a different PR.

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
- **New MCP tool** (`aleonard_mcp/server.py`): add a test in
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

## Python formatting

The pre-commit `black` hook runs with `--skip-string-normalization` (see
`.pre-commit-config.yaml`) — it never rewrites quote style. A separate
`double-quote-string-fixer` hook converts double quotes to single after.
If you run `black` by hand instead of relying on the hook, pass
`--skip-string-normalization` too, or it'll flip the file to double quotes
and force an extra fix-and-recommit round trip.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
