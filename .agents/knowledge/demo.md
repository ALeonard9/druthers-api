# Demo and approval protocol

Load this when you are running or preparing the demo, or when you need the
seeded local accounts. Workers implementing an issue do not need it.

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
