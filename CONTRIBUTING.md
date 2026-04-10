# Contributing

Thanks for contributing to `mcp-bridge`.

## Workflow

1. Start from the latest `main`.
2. Create a focused branch for your change.
3. Make the smallest reviewable diff that solves the problem.
4. Run the relevant checks before opening a PR.
5. Open a PR with a clear summary, test notes, and any follow-up caveats.

## Development Expectations

- Use the project-standard toolchain: `uv sync` and `uv run ...`
- Keep code, comments, commit messages, and docs in English
- Preserve backward compatibility unless the change is explicitly meant to break it
- Keep the project framed as a REST bridge to the MCP ecosystem; A2A support is secondary
- Update docs when you change durable behavior, public API expectations, or operating assumptions

## Checks

Run the full test suite for app changes:

```bash
uv run pytest -q
```

If you touch formatting, typing, or lint-sensitive areas, run the relevant local checks before opening the PR.

## Branch And PR Hygiene

- One branch per task or fix
- Avoid unrelated refactors in the same PR
- Link the motivating issue or task when one exists
- Call out behavior changes, compatibility impact, and anything reviewers should verify manually
