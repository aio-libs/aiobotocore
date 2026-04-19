---
allowed-tools: Bash(git worktree:*), Bash(git diff:*), Bash(git fetch:*), Bash(uv run:*), Bash(mktemp:*), Bash(mkdir:*), Bash(rm:*)
description: Run pyright against HEAD and against origin/main in an isolated worktree, report new errors in touched files
---

aiobotocore has a long-standing baseline of pyright errors (intentional async-overriding-sync
patterns plus legacy type gaps). Absolute counts are not a gate — what we care about is **drift
introduced by the current changes**, especially in files the changes touched.

This command captures the delta using `git worktree` rather than `git stash`: a throwaway worktree
at `origin/main` provides the baseline without touching the primary tree. An earlier version of
this flow used `git stash push/pop` (inherited from the pre-refactor inline sync-prompt); that
approach is fragile because a failed `git stash pop` leaves the primary tree in a half-applied
state just before the MCP commit step runs. Worktrees sidestep that class of failure entirely.

## Arguments

- `--path=<path>` (optional, default `aiobotocore/`): path to run pyright against
- `--base=<ref>` (optional, default `origin/main`): git ref to use as the baseline
- `--touched-files=<file1,file2,...>` (optional): restrict the delta report to these files. If
  omitted, derived from `git diff --name-only <base>`.

## Step 1: Resolve arguments

Bind the args (or their defaults) into shell variables used by later steps:

```text
PYRIGHT_PATH="${PATH_ARG:-aiobotocore/}"   # --path value
BASE="${BASE_ARG:-origin/main}"            # --base value
```

Then resolve touched files:

```text
git diff --name-only "$BASE"
```

Call this `TOUCHED`. If `--touched-files` is set, use it instead. If the result is empty, return
`delta: no changes to compare` and exit — there is nothing to measure.

## Step 2: Create a baseline worktree

```text
git fetch origin main --quiet
WORKTREE=$(mktemp -d -t pyright-baseline-XXXXXX)
git worktree add --detach "$WORKTREE" "$BASE"
```

Use `--detach` so the worktree is not tied to a branch (the caller may already have `origin/main`
checked out as a branch somewhere). `mktemp -d` gives a unique path that can't collide under
concurrent invocations — `/tmp/pyright-baseline-$$` (PID) would collide for two shells with the
same PID on different hosts or in edge cases.

## Step 3: Run pyright baseline in the worktree

```text
uv run --with pyright pyright "$WORKTREE/$PYRIGHT_PATH" > /tmp/pyright-before.txt 2>&1
tail -1 /tmp/pyright-before.txt
```

The baseline runs against the baseline worktree's files. `uv run` resolves Python dependencies
from the caller's venv, which is fine — pyright typechecks files by path, and the baseline's
dependencies are the same ones pinned at `$BASE`.

## Step 4: Remove the baseline worktree

```text
git worktree remove --force "$WORKTREE"
```

`--force` ensures removal even if pyright left `__pycache__` dirs behind. Because the worktree
lives under `/tmp` and was never branch-tracking, there is nothing to lose.

## Step 5: Run pyright with the current changes

```text
uv run --with pyright pyright "$PYRIGHT_PATH" > /tmp/pyright-after.txt 2>&1
tail -1 /tmp/pyright-after.txt
```

## Step 6: Compute delta restricted to touched files

Compare the two outputs. A new error counts toward the delta only if its filename (after stripping
the `$WORKTREE/` prefix from baseline paths) matches a file in `TOUCHED`. Errors in untouched
files are pre-existing baseline noise — ignore them.

Output:

```text
Baseline: <N> errors, <W> warnings
With changes: <N'> errors, <W'> warnings
Touched files: <list>

New errors in touched files:
  <path>:<line>: <message>
  ...

No new errors: <true|false>
```

## Failure handling

- **`git fetch` fails** (network): retry once, then return `error: could not fetch $BASE`. The
  caller should treat this like any other transient failure.
- **`git worktree add` fails** (e.g. `$BASE` not resolvable): return `error: could not create
  baseline worktree at $BASE` — never fall back to running only the "after" pyright, since a
  one-sided run cannot produce a delta.
- **Pyright crash** (import error, internal assertion) in either run: surface the failure
  instead of silently returning `no new errors`. A crashed run is not a passing run.
- **Cleanup on failure:** if the command errors after creating the worktree but before Step 4,
  call `git worktree remove --force "$WORKTREE"` in a final cleanup to avoid orphaned worktree
  entries. Safe to call even if the worktree is already gone.
