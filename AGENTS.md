## Python formatting

The pre-commit `black` hook runs with `--skip-string-normalization` (see
`.pre-commit-config.yaml`) - it never rewrites quote style. A separate
`double-quote-string-fixer` hook converts double quotes to single after.
If you run `black` by hand instead of relying on the hook, pass
`--skip-string-normalization` too, or it'll flip the file to double quotes
and force an extra fix-and-recommit round trip.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

<!-- BEGIN:druthers-workflow -->
## druthers.io

A personal ranking product for movies, TV, books and games. Pairwise duels
place a title into an ordinal shelf; shelves are private, shared or public.
Four repos, one product: `druthers-api` (FastAPI, Postgres, Alembic),
`druthers-web` (Next.js), `druthers-mcp` (MCP server), `druthers-infra`
(fleet, CI, deploy).

## Commands

Run these instead of reasoning about whether the code is correct. They are
deterministic, they are cheap, and they are the gate.

| | |
|---|---|
| `task validate` | everything below, in order. Run this before you commit. |
| `task test` | the suite. Run the whole thing, not just your file. |
| `task lint` | format and lint |
| `task typecheck` | web only |
| `task wiring` | every route has a client caller |
| `task sca` / `task sast` | dependency and static security scans |

Do not spend model tokens re-deriving what these prove. Read their output.

## Non-negotiables

1. **The approval gate is the demo, not the green build.** Do not `push` or
   open a PR before Adam has approved the demo, even when CI would pass.
   Merging, releasing and deploying are separate asks, never chained off the
   same approval.
2. **A worker commits to its own branch.** In a worktree this is required, not
   optional: uncommitted work is invisible to the fleet and gets discarded.
   That branch reaches nobody until the orchestrator pushes it, so committing
   there is not shipping. `push` and `gh pr create` are the orchestrator's
   alone.
3. **Do not add code nothing calls, and do not leave code nothing calls.** A
   new route, tool or export needs a caller in this same change or a companion
   issue with a real number. Two fully built API features shipped to
   production with zero callers. `task wiring` checks this; run it.
4. **Keep the four domains in lockstep.** Touching movies, TV, books or games
   means checking whether the other three need the same change and the same
   test. Per-domain drift is how one surface ended up destroying rows the
   other three did not.
5. **A test must be able to fail.** Asserting that a hardcoded list contains
   its own hardcoded values satisfies the rule and hides the gap.
6. **An honest no-op beats a speculative change.** Already fixed, or blocked
   on a decision only Adam can make, is a valid outcome. Say so; do not invent
   a direction.
7. **A bare number is ambiguous.** Issues and PRs share one counter. Confirm
   with `gh issue view` / `gh pr view` before acting on one.
8. **No em dashes** in anything written for Adam: code comments, commit
   messages, issue and PR bodies. Use commas, semicolons or a rewrite.

Before starting new work, check `git branch --show-current` and `git status`.
Do not assume the checkout is `main` or clean.

## Architectural boundaries

- **`app/services/shelves.py` is the generic path.** The `SHELVES` registry
  already handles all four domains and the newer code uses it (summary,
  comparison, visibility, activity, the feed). The older half predates it and
  is duplicated four ways. Extend `SHELVES`; do not add a fifth copy of a
  per-domain helper. Migrate the file you are already in, not as a separate
  refactor.
- **The API contract is the API's.** `druthers-web` and `druthers-mcp` consume
  it. A change to a shared response shape or a new default on a list endpoint
  is a cross-repo change, and the ordering has to be stated.
- Alembic has one head. Check before you add a revision.

## Your role

Read the role you were dispatched as. Do not read the others.

| | |
|---|---|
| `.agents/roles/agilist.md` | plan it, slice by feature, pick the team |
| `.agents/roles/developer.md` | implement it |
| `.agents/roles/reviewer.md` | independent pass over the diff |
| `.agents/roles/security.md` | trust boundaries, when warranted |
| `.agents/roles/ux.md` | user impact, on the plan |

Routing and the handoff schema: `.agents/roles/README.md`.

## Knowledge, loaded only when relevant

| | |
|---|---|
| `.agents/knowledge/demo.md` | demo protocol, seeded accounts, approval flow |
| `.agents/knowledge/github.md` | issue and PR format, numbering, priorities |
| `.agents/knowledge/graphify.md` | the repo knowledge graph, and when not to use it |
| `.agents/knowledge/testing.md` | what test a change needs and where it goes |

Do not read these by reflex. Open the one the task calls for.
<!-- END:druthers-workflow -->
