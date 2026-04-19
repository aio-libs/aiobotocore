## Problem

`ci-cd.yml` currently runs the full matrix (6 Python versions × 2
OS × several variants = ~13+ test jobs, plus build, zizmor, coverage,
check) on **every** PR, regardless of what changed. That includes:

- docs-only changes (`docs/**`, `*.md`, `*.rst`)
- `.github/workflows/claude.yml` + prompt edits (`.github/claude-*-prompt.md`,
  `plugins/aiobotocore-bot/**`) — the AI-automation code path
- `.github/workflows/*.yml` edits that don't affect the library
- `CHANGES.rst`, `CONTRIBUTING.rst`, `.pre-commit-config.yaml`,
  `pyproject.toml [tool.rumdl]`, `.serena/`, etc.

Running the full matrix for "fix a typo in `docs/override-patterns.md`"
burns ~15 CI minutes and blocks the merge queue for the sake of a
bytecode path the change didn't touch.

## Proposal

Split CI into **tiers** driven by `on.pull_request.paths` / `paths-ignore`,
with a single **umbrella check** (`check` job / `alls-green`) that
aggregates per-tier results so the merge-queue status check name stays
stable.

### Tier A — docs-only changes

Triggered when only paths in this set change:

- `docs/**`
- `**.md`, `**.rst` (except `CHANGES.rst` when `aiobotocore/__init__.py`
  also changed — treat as code then)
- `CONTRIBUTING.rst`, `README.rst`

Runs:

- `pre-commit run --all` (lint/format via ruff, yamllint, rumdl)
- `make docs` (Sphinx build)

Skips the Python test matrix entirely.

### Tier B — AI automation / workflow / tooling changes

Triggered when only these change:

- `.github/workflows/claude.yml`
- `.github/workflows/botocore-sync.yml`
- `.github/claude-*-prompt.md`
- `plugins/aiobotocore-bot/**`
- `.claude-plugin/**`
- `.github/usage-summary.py`
- `.github/ISSUE_TEMPLATE/**`, `.github/pull_request_template.md`
- `.pre-commit-config.yaml`, `pyproject.toml` (only `[tool.rumdl]`,
  `[tool.ruff]`, `[tool.uv]`, `[tool.pytest.ini_options]` sections —
  may be hard to detect precisely; fall back to Tier C if any non-tool
  section changed)
- `.github/dependabot.yml`, `.codecov.yml`, `.yamllint`, `.readthedocs.yml`

Runs:

- `pre-commit run --all`
- zizmor
- A **single** Python job (one version, one OS, core path only) to
  confirm the workflow/tooling changes didn't break the basic test
  harness. Not the full matrix.

### Tier C — library code / dependency changes

Triggered when `aiobotocore/**`, `tests/**`, `pyproject.toml`
(non-`[tool.*]`), `uv.lock`, or anything else changes — this is the
default path and runs today's full matrix.

### Implementation sketch

- Each tier becomes its own workflow (or one workflow with multiple
  top-level jobs + `paths:` filters).
- `alls-green` / `check` job requires whichever tier actually fired.
  Use `allowed-skips` in `re-actors/alls-green@…` to accept skipped
  Tier-C matrix when Tier A fired, and so on.
- The merge-queue required-status-check remains a single `check` name
  so branch protection rules don't need to change.

Alternative (simpler, less pure): use `paths-ignore:` on the existing
workflow to skip Tier-C on docs/AI paths, and add a lightweight
dedicated `docs.yml` / `ai.yml` for the other tiers. Same effect, less
refactoring.

## Motivation

PR #1556 ran the full CI matrix ~7 times during its back-and-forth —
each run ~15 minutes of CI time — for a PR that is 99% documentation
and automation config with no library code changes. Across all the AI
workflow changes landing lately, this has amounted to hours of pointless
matrix runs.

The zizmor rate-limit failure we just debugged was partly caused by
setup-uv fetching releases on every one of those full-matrix runs;
reducing matrix runs for docs/AI PRs also reduces API-budget pressure.

## Non-goals

- Don't reduce coverage for actual library changes. Tier C stays the
  current full matrix.
- Don't change test semantics. Just change when each tier fires.

## Related

- PR #1556 is the immediate motivator (docs + plugin + hardening).
- #1544 optimized Claude CI workflows for cost; this is the ci-cd.yml
  equivalent.
