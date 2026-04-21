---
description: Use when applying the mechanical version + CHANGES.rst + pyproject.toml + uv.lock updates for a botocore sync (no-port = patch bump; port = minor bump, lower bound moves). Handles the Sphinx `^`-underline exact-length rule and drives `uv lock`. Caller commits the result.
argument-hint: "--mode=no-port|port --target=<version> [--lower-bound=<version>] [--changelog=<text>] [--extra-changelog=<text>]"
allowed-tools: Bash(uv lock:*) Bash(uv pip:*) Bash(curl:*) Bash(date:*) Bash(cat:*) Bash(grep:*) Bash(sed:*) Bash(printf:*) Bash(wc:*) Bash(seq:*) Bash(tr:*) Bash(python3:*) mcp__github_file_ops__commit_files
---

Apply the mechanical version+changelog+pyproject changes for a botocore sync. Use this instead of
hand-editing each file — the `^` underline length rule and `uv lock` step are both easy to miss.

## Arguments

- `--mode=no-port|port` (required)
- `--target=<version>` (required): the target botocore version, e.g. `1.42.89`
- `--lower-bound=<version>` (optional, port mode only): the EARLIEST botocore version that
  shipped the breaking change requiring this port. Defaults to `--target` if omitted, but
  the caller should set this explicitly when the classifier identified the change as
  shipping in an earlier version of the `$FROM..$TO` range. Example: classifier finds the
  new `auth_scheme_preference` arrived in 1.42.90 and the target is 1.42.91 — pass
  `--lower-bound=1.42.90` so users on 1.42.90 still satisfy the spec.
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
- `--mode=port`: set lower bound to `--lower-bound` (or `--target` if not provided), set upper
  bound as above. **Lifting the lower bound past the version that introduced the breaking change
  is wrong** — it locks out users on the in-between version that already has the new feature.
- Do the same for the `boto3` dependency line if it's pinned (same range).

## Step 3: Update aiobotocore/**init**.py

Replace `__version__ = "<old>"` with `__version__ = "<new>"`.

## Step 4: Update CHANGES.rst

### Step 4a: Detect existing unreleased entry

A previous PR may have added an entry that hasn't shipped yet. Stacking a NEW entry on top in
that case clutters the changelog (#1571 review feedback). Detect:

```text
# Latest released aiobotocore version on PyPI
released=$(curl -s https://pypi.org/pypi/aiobotocore/json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])")
# Top entry's version in CHANGES.rst (first line that looks like "X.Y.Z (YYYY-MM-DD)")
top_entry=$(grep -m1 -E '^[0-9]+\.[0-9]+\.[0-9]+ \(' CHANGES.rst | sed -E 's/ .*//')
```

If `Version(top_entry) > Version(released)`, the top entry is **unreleased**. **Merge the new
bump into it** rather than inserting a new section above:

- Replace the existing version with the new computed version (recompute the `^` underline length).
- Append the new bullet (the default or `--changelog`) under the same header.
- Preserve any existing bullets — the merged entry covers both the prior unreleased work and
  this bump in a single release.

If `Version(top_entry) == Version(released)` (top entry is already shipped), proceed to Step 4b
and insert a fresh section.

### Step 4b: Write the entry

**The `^` underline MUST match the header length exactly** — miscounts break Sphinx rendering.

Build the header and underline mechanically — do not eyeball the length:

```text
HEADER="X.Y.Z ($(date +%Y-%m-%d))"
LEN=$(printf '%s' "$HEADER" | wc -c | tr -d ' ')
UNDERLINE=$(printf '^%.0s' $(seq 1 "$LEN"))
```

Then write (or, in the merge case, replace the existing header and append bullets):

```text
$HEADER
$UNDERLINE
* <default or --changelog>
* <each --extra-changelog entry, if any>
```

Verify before writing: `[ "${#HEADER}" -eq "${#UNDERLINE}" ]` — if not equal, abort.

## Step 5: Run uv lock

```text
uv lock --upgrade-package boto3 --upgrade-package botocore
```

The dual `--upgrade-package` is intentional: `boto3` and `botocore` are tightly version-coupled
upstream and a stale `boto3` against a freshly-bumped `botocore` produces confusing test failures
(#1571 review feedback). Always upgrade both together so the lockfile reflects a coherent pair.

## Step 6: Output

Print the new version. The caller is responsible for committing (via
`mcp__github_file_ops__commit_files`) — this skill only mutates files.

## Honesty

If the current version in `aiobotocore/__init__.py` cannot be parsed, or the `^` count would not
match, abort with an explicit error. Don't silently produce a broken CHANGES.rst.
