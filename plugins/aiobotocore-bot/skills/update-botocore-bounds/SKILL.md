---
description: Use for botocore-sync PRs to update the botocore dependency bounds in pyproject.toml and refresh uv.lock. Does NOT bump version or edit CHANGES.rst — those are owned by the release-time `/aiobotocore-bot:draft-release` flow, which synthesizes the changelog from merged PRs at release time. The botocore-sync PR just carries the bound change; the next release picks it up automatically.
argument-hint: "--mode=no-port|port --target=VERSION [--lower-bound=VERSION]"
allowed-tools: Bash(uv lock:*) Bash(uv pip:*) Bash(curl:*) Bash(cat:*) Bash(grep:*) Bash(sed:*) Bash(python3:*) mcp__github_file_ops__commit_files
---

> **Scope:** botocore-sync only. The skill formerly known as
> `bump-version` was renamed and scoped down: contributors no longer
> bump `aiobotocore/__init__.py` or edit `CHANGES.rst` per PR. Both are
> handled at release time by `/aiobotocore-bot:draft-release`.

Update the botocore dependency bounds in `pyproject.toml` and refresh
`uv.lock`. That's it — two files, one mechanical change. Use this so
the bound calculation (lower-bound rule, upper-bound rule) and the
dual-package `uv lock` step are both correct without hand-editing.

## Arguments

- `--mode=no-port|port` (required)
- `--target=<version>` (required): the target botocore version, e.g. `1.42.89`
- `--lower-bound=<version>` (optional, port mode only): the EARLIEST
  botocore version that shipped the breaking change requiring this
  port. Defaults to `--target` if omitted, but the caller should set
  this explicitly when the classifier identified the change as
  shipping in an earlier version of the `$FROM..$TO` range. Example:
  classifier finds the new `auth_scheme_preference` arrived in 1.42.90
  and the target is 1.42.91 — pass `--lower-bound=1.42.90` so users
  on 1.42.90 still satisfy the spec.

## Step 1: Update pyproject.toml bounds

- Locate the botocore dependency line (e.g. `botocore >= 1.42.79, < 1.42.90`).
- New upper bound is always one patch above `--target`: if target is
  `1.42.89`, upper is `< 1.42.90`.
- `--mode=no-port`: keep the lower bound unchanged, update the upper bound.
- `--mode=port`: set lower bound to `--lower-bound` (or `--target` if
  not provided), set upper bound as above. **Lifting the lower bound
  past the version that introduced the breaking change is wrong** —
  it locks out users on the in-between version that already has the
  new feature.
- Do the same for the `boto3` dependency line if it's pinned (same range).

## Step 2: Run uv lock

```text
uv lock --upgrade-package boto3 --upgrade-package botocore
```

The dual `--upgrade-package` is intentional: `boto3` and `botocore`
are tightly version-coupled upstream and a stale `boto3` against a
freshly-bumped `botocore` produces confusing test failures (#1571
review feedback). Always upgrade both together so the lockfile
reflects a coherent pair.

## Step 3: Output

Print the new bound (e.g. `botocore >= 1.42.90, < 1.42.92`). The
caller is responsible for committing (via
`mcp__github_file_ops__commit_files`) — this skill only mutates files.

## What NOT to do

- **Don't bump `aiobotocore/__init__.py`.** The next release-PR
  (drafted by `/aiobotocore-bot:draft-release`) will compute the
  right bump (PATCH for no-port sync, MINOR if a feature also
  landed in the window) and write it.
- **Don't edit `CHANGES.rst`.** The release flow synthesizes the
  changelog from merged PRs in the release window; just give the
  PR a clean title (the unified `bump botocore dependency
  specification to support "botocore >= X, < Y"` convention is the
  expected wording).
- **Don't try to detect "unreleased" state.** The release flow sees
  the full window and handles it.

## Honesty

If `pyproject.toml`'s botocore line cannot be parsed, abort with an
explicit error. Don't silently produce a broken pyproject.
