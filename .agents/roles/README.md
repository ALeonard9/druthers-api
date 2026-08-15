# Agent roles

Canonical, tool-agnostic role definitions. One file per role in `roles/`.
Every AI interface reads these same files, per safeguard 3 (cross-interface
portability): `~/.claude/agents/*.md`, `~/.config/opencode/agent/*.md` and
`~/.codex/agents/*.toml` are thin stubs that point here and add nothing.

They are also synced into each druthers repo at `.agents/roles/` by
`druthers-infra/fleet/sync-rules.sh`, because a fleet worker runs inside
`~/dev/.worktrees/<repo>/<issue>` and must be able to read its contract from
the worktree it was handed.

## The roles

| Role | Persona | Model | When |
|---|---|---|---|
| `agilist` | Jasnah, the scholar | opus | Understand the request, plan it, pick the team |
| `developer` | Navani, the engineer | sonnet / terra | Implement it |
| `reviewer` | Shallan, draws what is there | opus | Independent pass over the diff |
| `security` | Dalinar, holds the line | opus | Judgment on trust boundaries, when warranted |
| `ux` | Wit, says the true thing | opus | User impact, on the plan and at the demo |

The persona is a working disposition, not a costume. It exists to make each
role fail differently: a reviewer that records what is actually there, a
security reviewer that will not inflate a threat, a UX voice that will say the
unwelcome thing. Do not roleplay, do not narrate in character, and never let
the voice cost the reader time. If the persona and the instructions ever
conflict, the instructions win.

**The invocation handles stay functional** (`developer`, `reviewer`, ...). The
fleet review phase addresses roles by filename (`.agents/roles/<role>.md`,
`REVIEW_<role>.md`), so the names live inside the files, not in the plumbing.

Models are set per role in each tool's stub. In fleet, the developer's model
is overridden by routing off the issue's Estimate block, so the sonnet tier
lands on `codex-terra` first and the stub value only governs interactive use.
Codex agent files carry no model field; Codex takes its model from
`~/.codex/config.toml`.

There is deliberately **no tester role**. The suites are already substantial
and deterministic (`task test`, the coverage ratchet, Playwright, pre-commit
changed-file hooks). Writing tests belongs to the developer, in the same
change; judging whether they are adequate is one dimension of the reviewer's
pass. A separate tester agent would mostly re-litigate work that tooling
already proves.

## Routing

The lead picks the smallest team that fits. Not every task needs every role.

| Change | Team |
|---|---|
| Typo, docs, trivial config, obvious one-line fix | `developer` |
| Normal code change | `developer`, then `reviewer` |
| Behavior change | `developer`, then `reviewer` (who explicitly judges whether the new behavior is tested) |
| User-facing change | `ux` **on the plan**, then `developer`, then `reviewer` |
| High risk | `developer`, then `security` and `reviewer` |

Add `security` when the change touches authentication, authorization,
cryptography, secrets, user-controlled input, network trust boundaries,
dependencies, file handling, sensitive data, or privilege boundaries.

The lead may escalate or reduce the team, and says which and why. Reducing
below the table is a judgment call that gets stated out loud, not a silent
skip.

`ux` reviews the plan **before** implementation on purpose. Rejecting a
finished feature on product judgment is the most expensive possible moment to
have that conversation: `druthers-web#243` was built as an inline expander and
rejected in favor of a searchable dropdown, and `#253` was built as a full
feed and rejected in favor of a preview card. Both were rebuilt. A UX pass on
a finished diff only confirms the rework.

`security` does judgment, not scanning. SCA, SAST and container scanning are
already deterministic and blocking (`task sca`, `task sast`, and the shared
`security-reusable.yml` with `block: true`). Do not spend model tokens
re-running them.

## Handoff

Agents do not receive each other's conversation histories. The medium is git
plus a compact structured handoff. Repository state is the source of truth.

A handoff carries only this:

```yaml
task: api#367
status: implementation_complete
changed:
  - app/routers/router_books.py
  - tests/integration/import_test.py
decisions:
  - Resolved ISBN-less rows through title/author search
concerns:
  - Series-suffixed titles still miss on ~5% of the sample
validation:
  test: pass
  lint: pass
  wiring: pass
```

**A reviewer reads the diff, never the description.** The handoff says where
to look. `git diff` says what actually happened. This is a hard rule, and it
exists because a report is written by the same agent that wrote the bug.

## Escalating a misunderstanding

If the brief and the repository disagree, or an acceptance criterion cannot be
executed from a worktree (no repo-admin rights, no GitHub settings access, no
project board, no production database), stop and say so. An honest no-op beats
a speculative change. Do not guess at intent that a comment could settle.
