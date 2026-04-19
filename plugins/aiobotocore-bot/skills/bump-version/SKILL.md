---
description: Use when applying the mechanical version + CHANGES.rst + pyproject.toml + uv.lock updates for a botocore sync (no-port = patch bump; port = minor bump, lower bound moves). Handles the Sphinx `^`-underline exact-length rule and drives `uv lock`. Caller commits the result.
argument-hint: "--mode=no-port|port --target=<version> [--changelog=<text>] [--extra-changelog=<text>]"
allowed-tools: Bash(uv lock:*) Bash(date:*) Bash(cat:*) Bash(grep:*) Bash(sed:*) Bash(printf:*) Bash(wc:*) Bash(seq:*) Bash(tr:*) mcp__github_file_ops__commit_files
---

Apply the mechanical version+changelog+pyproject changes for a botocore sync. Use this instead of
hand-editing each file — the `^` underline length rule and `uv lock` step are both easy to miss.

## Arguments

- `--mode=no-port|port` (required)
- `--target=<version>` (required): the target botocore version, e.g. `1.42.89`
- `--changelog=<text>` (optional): custom CHANGES.rst bullet. Default for either mode is
  `bump botocore dependency specification to support "botocore >= <lower>, < <upper>"`.
  Single wording matches the unified "Bump ..." PR-title convention; the no-port vs
  port-required distinction lives in the PR body, not in changelog text.
- `--extra-changelog=<text>` (optional): additional bullets appended under the same version entry

## Step 1: Determine new version

Read `aiobotocore/__init__.py` to get the current `__version__`. Compute the new version:

- `--mode=no-port` → bump PATCH: `3.4.1 → 3.4.2`
- `--mode=port` → bump MINOR, reset patch: `3.4.1 → 3.5.0`

## Step 2: Update pyproject.toml bounds

- Locate the botocore dependency line (e.g. `botocore >= 1.42.79, < 1.42.90`).
- New upper bound is always one patch above `--target`: if target is `1.42.89`, upper is `< 1.42.90`.
- `--mode=no-port`: keep the lower bound unchanged, update the upper bound.
- `--mode=port`: set lower bound to `--target`, set upper bound as above.
- Do the same for the `boto3` dependency line if it's pinned (same range).

## Step 3: Update aiobotocore/**init**.py

Replace `__version__ = "<old>"` with `__version__ = "<new>"`.

## Step 4: Update CHANGES.rst

Insert a new entry at the top with today's date (`date +%Y-%m-%d`). **The `^` underline MUST match
the header length exactly** — miscounts break Sphinx rendering.

Build the header and underline mechanically — do not eyeball the length:

```text
HEADER="X.Y.Z ($(date +%Y-%m-%d))"
LEN=$(printf '%s' "$HEADER" | wc -c | tr -d ' ')
UNDERLINE=$(printf '^%.0s' $(seq 1 "$LEN"))
```

Then write:

```text
$HEADER
$UNDERLINE
* <default or --changelog>
* <each --extra-changelog entry, if any>
```

Verify before writing: `[ "${#HEADER}" -eq "${#UNDERLINE}" ]` — if not equal, abort.

## Step 5: Run uv lock

```text
uv lock
```

This updates `uv.lock` to reflect the new constraints.

## Step 6: Output

Print the new version. The caller is responsible for committing (via
`mcp__github_file_ops__commit_files`) — this skill only mutates files.

## Honesty

If the current version in `aiobotocore/__init__.py` cannot be parsed, or the `^` count would not
match, abort with an explicit error. Don't silently produce a broken CHANGES.rst.
