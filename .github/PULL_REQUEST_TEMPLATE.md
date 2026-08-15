<!-- See SDLC.md for the workflow. -->

## Summary

<!--
Bullets. Lead with the cause, then the fix - not a changelog of files touched.
Say what does NOT change if that's load-bearing (e.g. "no behavior change to X,
this only adds a field").
-->

-

## Test plan

<!--
Checked boxes with real evidence: actual pass counts, the specific cases added,
and what you verified against the local dev stack.
-->

- [ ] `pytest` - N passed, including <the new cases>
- [ ] Verified against the local dev stack: <what you clicked / curled and what happened>

## Checklist

- [ ] Branch named `feat/…`, `fix/…`, or `chore/…`
- [ ] New module has a test file in this PR (see CLAUDE.md → Testing)
- [ ] Per-domain change checked against the other three domains (movies/TV/books/games)
- [ ] CI green (lint + tests)
- [ ] No secrets committed
- [ ] Docs/README updated if behavior changed

<!--
Closing issues: repeat the keyword for EVERY issue. The comma form
("Closes #a, #b") silently closes only the first one.
Closes #a. Closes #b.
-->
