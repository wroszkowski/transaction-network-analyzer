# transaction-network-analyzer

`uv` for envs, deps and running Python scripts, `just` as the command surface, `pre-commit` as the quality gate (ruff check, ruff format, ty, pytest). Setup: `just init`.

## Rules for AI sessions

- **Every reusable command becomes a `just` recipe.** If a command is worth running twice — run,
  seed, deploy, query, benchmark — add it to the `justfile` and invoke it from there. No ad-hoc
  shell one-liners that survive only in a transcript.
- **Run `just all-hooks` before claiming work is done.** It runs the whole gate: ruff check, ruff
  format, ty, pytest. Green output is the evidence; "should work" is not. The `Stop` hook in
  `.claude/settings.json` also runs it, but don't wait for it — run it yourself and read the output.
- **TDD: failing test first.** Write the test in `tests/`, run `just hook-unit-test` and watch it
  fail, then write the minimum code in `src/` to make it pass. Bugfixes too: reproduce with a test
  before fixing.

## Layout

- `src/<package>/` — package code
- `tests/` — pytest suite; `tests/report/` is generated output, never hand-edited
- `justfile` — the command surface; `.pre-commit-config.yaml` hooks shell out to `just hook-*`
