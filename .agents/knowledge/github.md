# GitHub conventions: issues, PRs, numbering

Load this when reading, filing or closing an issue or PR.

## Issue vs. PR numbers

GitHub issue numbers and PR numbers share one repo-wide counter, so a bare
number is ambiguous - `289` could be either, and groomed backlog stories from
`story-intake` are always issues, never PRs. When told to "pull in" / "start" /
"work on" a bare number (or a repo-prefixed one like "web 134"), don't assume
which it is from context or phrasing - confirm with `gh issue view <n>` and/or
`gh pr view <n>` before branching off it, reporting its status, or otherwise
acting on it.

Don't trust issue/PR *state* at face value either - it can drift from what's
actually in the code:

- A PR body listing `Closes #a, #b, #c, ...` as one comma-separated list after
  a single keyword reliably auto-closes only the **first** issue on merge -
  the rest silently stay open even though the code shipped (#283 merged and
  claimed six closes; only one fired). When writing a PR body that closes
  several issues, repeat the keyword per issue (`Closes #a. Closes #b.`) -
  don't rely on the comma form. When *reading* a merged PR that lists several
  issues via the comma form, verify each one's state with `gh issue view`
  rather than assuming the merge closed all of them.
- A PR can also be closed **without merging** and silently orphan a whole
  downstream stack of branches (#287 closed, blocking #279's work and
  everything branched on top of it from ever reaching `main`). If a
  dependency issue/PR looks unexpectedly open or blocked, check whether the
  PR that was supposed to deliver it actually merged - don't assume "closed"
  means "done," and don't assume a dependency is real work remaining without
  checking whether it already shipped under a different PR.

## Issue and PR format

Issues follow the forms in `.github/ISSUE_TEMPLATE/` - Story, Problem,
Acceptance Criteria, Context, Channel Impact, and an Estimate block carrying
**Recommended model** and **Human effort**. The fleet dispatcher routes work
off that Estimate block, so it is not decoration.

Every issue also carries exactly one `priority:p1`–`priority:p5` label
(applied via labels/project board, not the template body):

- **P1** - immediate build; prod or the build is broken.
- **P2** - high value, time-sensitive.
- **P3** - chores, done as time permits (default when urgency is unstated).
- **P4** - backlog / nice-to-have; low urgency and low value.
- **P5** - roadmap: big, high-value, long-term, not an immediate need.

PRs follow `.github/PULL_REQUEST_TEMPLATE.md`:

- **`## Summary`** - bullets that lead with the cause, then the fix. Not a
  changelog of files touched. Call out what does *not* change when that is
  load-bearing.
- **`## Test plan`** - checked boxes with real evidence: actual pass counts,
  the specific cases added, and what was verified against the local dev stack.
- Closing keywords repeated per issue (`Closes #a. Closes #b.`), never the
  comma form.
