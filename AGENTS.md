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

<!-- BEGIN:druthers-workflow -->
## Development workflow

New functionality gets demoed locally and approved by Adam **before** anything
is committed. Applies to all the druthers repos (`druthers-api`,
`druthers-web`, `druthers-mcp`, `druthers-infra`).

1. **Build it against the local dev stack.** Use the `druthers-up` skill to
   bring up Postgres + the API + `next dev`. Verify against real upstream data
   (TMDB, TVMaze, Open Library, IGDB), not just mocks and unit tests.
2. **Test in browser & demo with local URL + visuals.**
   Use the browser subagent to interactively test new web functionality on the
   local dev stack, then embed screenshots for visual review and session
   recording videos (`.webp`) for complex/important UX flows. Stop and wait for
   Adam's approval.

   **Write the demo as steps Adam can follow, not as a report of what you
   saw.** One block per delivered item — per issue, per PR, per distinct
   change — never one merged narrative for the batch. A batch of three gets
   three blocks. Each block gives, in this order:

   1. **Sign in as** — the exact account *and* password, e.g.
      `follower@example.com` / `change-me`. Every item states its own, even
      when three in a row use the same seat; "as above" costs a scroll and
      breaks if the blocks get reordered. If the item is only reachable from
      the admin seat, say so and say why. For `$ADMIN_EMAIL` /
      `$ADMIN_PASSWORD`, name the variables and point at `env/dev.env` rather
      than pasting the values.
   2. **Go to** — a full clickable URL, not a route fragment.
      `http://localhost:3000/tv/schedule`, not "the schedule page".
   3. **Do this** — the numbered clicks, in order, including any setup the
      screen does not imply (which env the bundle was built for, a preference
      that has to be set first, a reload needed because the value is
      server-rendered).
   4. **You should see** — the specific observable, with the value that makes
      it checkable. "Day heading reads `Today — Sunday, 08/09`", not "dates
      look right". If a number is the proof, quote the number.
   5. **What it looked like before** — one line, when the change is a fix.
      A corrected greeting is indistinguishable from a working one in a
      screenshot; the reviewer needs to know what they are no longer seeing.

   Anything a reviewer would have to discover by trial and error belongs in
   the steps: a rate limit that bites on repeated sign-ins, a seat that
   renders nothing in `dev`, data that had to be inserted by hand and will not
   survive a reseed. State it in the block, not in a footnote.

   **Demo from the seat the change applies to.** `druthers-api/docs/dev-cast.md`
   lists the seeded local accounts — friend, follower, followee, public,
   private, stranger — with their credentials, what each one is for, and which
   to reach for per kind of change. Driving everything as the admin user is the
   single most common way a broken visibility or comparison rule still demos
   green: that seat is public, friendly with everyone, and holds the whole
   catalog.
3. **Only after Adam approves:** spin the local environment down
   (`task dd -- dev` in `druthers-api`), then commit, push, and open the PR.
4. **Hand back the PR link.** Merging, releasing, and deploying stay separate
   asks — never chain them off the same approval.
5. **Once the PR is merged, return the local repo to `main` and pull** (and
   delete the now-merged local branch). A repo left checked out on a stale
   branch is silently inherited by the next session, which either builds new
   work on top of dead history or has to spend a turn untangling it first.

Do not push or open a PR ahead of the demo, even when tests and CI would
pass. The approval gate is the demo, not the green build.

**Exception, and it is not really one:** a fleet worker commits to its own
isolated branch in its own worktree (see below). That branch reaches nobody
until the orchestrator pushes it after Adam approves, so committing there is
not shipping — it is how the work is handed over. The rule above governs
`push` and `gh pr create`, which only the orchestrator runs.

Before starting new work in any of these repos, check `git branch --show-current`
and `git status` first — don't assume the checkout is `main` or clean.

### Working as a fleet worker

When dispatched by `druthers-infra/fleet`, you are one of several agents
working in parallel, each in its own git worktree. Additional rules apply:

- **Never start the local dev stack.** Ports 5432/8000/3000 and the Docker
  compose project belong to the orchestrator, which runs the batched demo.
  Starting them collides with other workers and with the demo.
- **Write tests _and run them_.** Author the test files your change requires
  (see Testing below), then run the whole suite — `pytest -q` or `npm test` —
  in your worktree before committing. Neither needs the dev stack.

  This rule used to say the opposite, on the theory that a worker running the
  suite burns budget re-proving what the orchestrator's post-approval sweep
  will prove anyway. That theory has one hole, and it is not hypothetical: a
  test file that fails to **load** takes every existing test in that file with
  it, silently, and a suite that never runs cannot tell you. web#212 shipped a
  new test block whose mock was evaluated before its own `const` was
  initialised; the file could not import, four unrelated passing tests
  disappeared with it, and none of that surfaced until land — after the demo,
  when the fix was most expensive. Thirty seconds in the worktree beats that
  every time.

  Lint and formatting still stay with the pre-commit hook and the sweep.
- **Stay in your worktree.** Do not touch other branches, other repos, or
  anything outside the issue you were dispatched for. Out-of-scope problems
  you notice go in `WORKER_REPORT.md`, not in the diff.
- **Commit on your branch** in the house format, then stop. This is required,
  not optional: uncommitted work in a worktree is invisible to the fleet and
  gets discarded. The demo-approval rule above governs pushing and opening
  PRs, both of which are the orchestrator's job — not your commit.
- **Leave a `WORKER_REPORT.md`** at the worktree root: what you changed, what
  you could not verify, what the demo should look at, and any decision you
  made that the issue did not settle.

## Issue vs. PR numbers

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

## Issue and PR format

Issues follow the forms in `.github/ISSUE_TEMPLATE/` — Story, Problem,
Acceptance Criteria, Context, Channel Impact, and an Estimate block carrying
**Recommended model** and **Human effort**. The fleet dispatcher routes work
off that Estimate block, so it is not decoration.

Every issue also carries exactly one `priority:p1`–`priority:p5` label
(applied via labels/project board, not the template body):

- **P1** — immediate build; prod or the build is broken.
- **P2** — high value, time-sensitive.
- **P3** — chores, done as time permits (default when urgency is unstated).
- **P4** — backlog / nice-to-have; low urgency and low value.
- **P5** — roadmap: big, high-value, long-term, not an immediate need.

PRs follow `.github/PULL_REQUEST_TEMPLATE.md`:

- **`## Summary`** — bullets that lead with the cause, then the fix. Not a
  changelog of files touched. Call out what does *not* change when that is
  load-bearing.
- **`## Test plan`** — checked boxes with real evidence: actual pass counts,
  the specific cases added, and what was verified against the local dev stack.
- Closing keywords repeated per issue (`Closes #a. Closes #b.`), never the
  comma form.

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
<!-- END:druthers-workflow -->
