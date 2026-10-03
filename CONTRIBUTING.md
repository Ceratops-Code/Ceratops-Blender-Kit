# Contributing

Contributions should preserve deterministic versions, explicit review gates,
portable source, and bounded runtime data.

## Rules

- Keep MCP tools goal-oriented; do not add arbitrary Python execution.
- Preserve exact source-version selection and immutable completed versions.
- Keep artistic approval separate from successful rendering or validation.
- Keep the companion skill, MCP behavior, README, and design documentation
  aligned when changing public workflows.
- Do not add secrets, private endpoints, or local machine paths.

## Validation

Run the repository-owned validation and test entry points before opening a pull
request:

```powershell
npm --prefix scripts ci
uv sync --project scripts --locked
uv run --locked scripts/validate-repository.py
uv run --locked scripts/run-tests.py
```

The real Blender smoke test runs only when `CERATOPS_BLENDER_EXECUTABLE` is set.
Describe any manual Blender or review-gate exercise in the pull request.
