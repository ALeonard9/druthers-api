# graphify: the repo knowledge graph

Load this when you need to orient in unfamiliar code. Skip it for a targeted
change in code you already know.

`druthers-api`, `druthers-web` and `druthers-mcp` carry a knowledge graph at
`graphify-out/` with god nodes, community structure and cross-file
relationships. `druthers-infra` has none: it is mostly bash and grep is
already the right tool there.

## Rules

- For codebase questions, run `graphify query "<question>"` first when
  `graphify-out/graph.json` exists. Use `graphify path "<A>" "<B>"` for
  relationships, `graphify explain "<concept>"` for focused concepts, and
  `graphify affected "<X>"` for blast radius. These return a scoped subgraph,
  usually much smaller than `GRAPH_REPORT.md` or raw grep output.
- If `graphify-out/wiki/index.md` exists, use it for broad navigation instead
  of raw source browsing.
- Read `graphify-out/GRAPH_REPORT.md` only for broad architecture review, or
  when `query` / `path` / `explain` do not surface enough context.
- The graph refreshes automatically through the post-commit and post-checkout
  git hooks (`graphify hook status` to confirm). If you have reason to think
  it lagged, `graphify update .` is AST-only and costs no API calls.

## When not to use it

**A diff review is the exception.** `git diff` is ground truth for a change
under review, and the graph describes the state of the tree, not the state of
the branch. Orienting from the graph instead of the diff is how a reviewer
ends up describing code that no longer exists. The reviewer and security roles
work from the diff.

The same caution applies anywhere the answer must be current rather than
approximate: confirm anything load-bearing against the file before you act on
it. The graph is a map. It is not the territory, and it is never the contract.

## Per-role guidance

| Role | Use it |
|---|---|
| `agilist` | Yes. Orientation and blast radius before slicing a story is exactly what it is for. |
| `developer` | Optional. Worth one query in unfamiliar territory; skip it for a targeted change. |
| `reviewer` | No. The diff is the subject. |
| `security` | No. The diff is the subject. |
| `ux` | No. It reads plans and rendered behavior, not call graphs. |

A PreToolUse hook nudges toward graphify on reads and searches in the repos
that have a graph. It is advisory. It cannot see which role you are, so use
your own judgment against the table above.
