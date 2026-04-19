---
allowed-tools: Bash(git stash:*), Bash(git status:*), Bash(git diff:*), Bash(uv run:*)
description: Run pyright against HEAD with and without the current staged/unstaged changes and report new errors in touched files
---

aiobotocore has a long-standing baseline of pyright errors (intentional async-overriding-sync
patterns plus legacy type gaps). Absolute counts are not a gate — what we care about is **drift
introduced by the current changes**, especially in files the changes touched. This command
captures the delta without requiring the caller to juggle `git stash` manually.

## Arguments

- `--path=<path>` (optional, default `aiobotocore/`): path to run pyright against
- `--touched-files=<file1,file2,...>` (optional): restrict the delta report to these files. If
  omitted, derived from `git diff --name-only HEAD`.

## Step 1: Record which files are currently modified

```text
git diff --name-only HEAD
```

Call this `TOUCHED`. If `--touched-files` is set, use it instead.

## Step 2: Stash current changes

```text
git stash push --include-untracked --keep-index=false -m "pyright-delta baseline"
```

If `git stash` reports "No local changes to save", skip Step 3/4 — there is no delta to compute
(either the caller already committed or nothing was changed). Return `delta: no changes to compare`.

## Step 3: Run pyright baseline

```text
uv run --with pyright pyright <path> 2>&1 > /tmp/pyright-before.txt
tail -1 /tmp/pyright-before.txt
```

## Step 4: Pop the stash

```text
git stash pop
```

If pop fails (conflicts), abort with an explicit error — do NOT proceed with a partial tree.

## Step 5: Run pyright with changes

```text
uv run --with pyright pyright <path> 2>&1 > /tmp/pyright-after.txt
tail -1 /tmp/pyright-after.txt
```

## Step 6: Compute delta restricted to touched files

Compare the two outputs. A new error counts toward the delta only if its filename is in `TOUCHED`.
Errors in untouched files are pre-existing baseline noise — ignore them.

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

## Honesty

If the baseline or after-run crashes (import error, pyright itself fails), surface the failure
instead of silently returning `no new errors`. A crashed run is not a passing run.
