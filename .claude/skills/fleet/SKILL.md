---
name: fleet
description: Burn down the druthers backlog by dispatching groomed GitHub issues across Claude, Codex, Antigravity, and Opencode workers in parallel git worktrees, then collecting the results for one batched local demo and landing the approved PRs. Use when asked to work the backlog, run the fleet, dispatch issues, burn down stories, or work a theme like "do the taste profile stories".
---

# Fleet

You are the orchestrator. Workers implement; you route, demo, and land.
The tooling lives in `~/dev/druthers-infra/fleet` — read its `README.md`
before the first run of a session.

```bash
FLEET=~/dev/druthers-infra/fleet/fleet.sh
```

## The loop

### 1. Propose

```bash
$FLEET propose --limit 6
$FLEET propose --theme 'taste profile'      # Adam named a theme
$FLEET propose --repo web
$FLEET propose --only 313,322               # Adam named issues
```

Show Adam the table and **wait**. He adds or subtracts. Never dispatch a
batch he hasn't seen.

Anything marked `check-shipped` has a merged PR already referencing it —
verify with `gh pr view` before doing anything with it. If it really shipped,
tell Adam so he can close the issue; don't dispatch a worker onto it.

### 2. Dispatch

```bash
$FLEET dispatch
```

Runs 3–4 workers in parallel, each in its own worktree. Takes a while. Check
in with `$FLEET status`. Statuses you'll see:

- `done` — committed work, ready to integrate
- `no-commit` — the worker ran clean but changed nothing; read its
  `WORKER_REPORT.md`, it usually means the issue was already fixed or is
  blocked on a decision
- `blocked-capped` — every lane for that tier is capped; it stays queued
- `timeout` / `failed` / `ratelimited` — see `runs/<id>/workers/<n>.log`

### 3. Integrate

```bash
$FLEET integrate
```

Merges the finished branches into one demo branch per repo, so the stack
comes up once instead of once per issue. Resolve any conflict or Alembic
multi-head it reports **before** demoing.

### 4. Demo — the approval gate

Read every `WORKER_REPORT.md` first; the "Demo notes" section tells you what
to exercise. Then, from the demo worktrees:

```bash
cd ~/dev/.worktrees/druthers-api/_demo-<run> && task du -- dev && task migrate
cd ~/dev/.worktrees/druthers-web/_demo-<run> && task dev
```

Use the browser subagent to drive each change. Present, per issue: the local
URL, what to look for, embedded screenshots, and a `.webp` recording for
multi-step UX flows. Group them into one message.

Then **stop and wait for Adam's approval.** This gate is the whole point of
the fleet — do not commit, push, or open a PR ahead of it, no matter how
green things look.

### 5. Land

Only after approval. Spin the stack down first (`task dd -- dev`), then:

```bash
$FLEET land --only 313,322      # exactly what Adam approved
```

This runs the deferred lint and test sweep per branch, pushes, and opens each
PR. It will not land a branch whose tests fail.

**Rewrite the PR body before it goes up.** `land` drafts one from the
worker's self-report, which is raw material, not house voice. The Summary
leads with the cause and then the fix; the Test plan carries real pass counts
and what you verified in the browser. Repeat `Closes #n.` per issue — the
comma form silently closes only the first.

Hand Adam the PR links and stop. Merging, releasing, and deploying are
separate asks.

```bash
$FLEET clean        # once the PRs are up
```

## Rules that bite

- **Workers never run tests or the dev stack.** That's deliberate: ports and
  the suite belong to you, at demo and land time. Don't "helpfully" run them
  in a worktree mid-dispatch.
- **Rejected work goes back to the queue** with Adam's notes, not into a
  quiet fix by you.
- **Watch the ledger** (`$FLEET ledger`). When a system caps, in-flight
  workers finish, you demo what's done, and the rest waits for next session.
  Don't reroute a capped batch onto Claude to keep going — that spends the
  scarcest cap in the fleet.
- **Don't grow the batch to fill capacity.** Four systems can produce work
  faster than Adam can review it; the demo is the bottleneck by design.
