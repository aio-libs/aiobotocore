# Draft Release

You are drafting a new aiobotocore release.

## What to do

Run the `/aiobotocore-bot:draft-release` skill exactly once with these
arguments (omit any flag whose env var is empty):

- `--version=$VERSION` (only if `VERSION` is non-empty)
- `--dry-run` (only if `DRY_RUN` is `true`)

The skill will read merged PRs since the last release tag, categorize
them, compute the next version, write a `CHANGES.rst` entry, bump
`aiobotocore/__init__.py`, and open a release PR titled
`Release v<X.Y.Z>`.

## Constraints

- Don't merge anything. The maintainer reviews and merges the release
  PR manually.
- Don't comment on unrelated PRs or issues.
- Don't run any other skill in this run.
- If the skill aborts (e.g. nothing to release, or version arg
  conflicts with bump rule unsafely), report the abort reason and stop.

## After the skill finishes

Print a one-line summary to the workflow log:

- On success (PR opened): `Drafted release PR #<N>: <title>`
- On dry-run: `Dry-run: would release v<X.Y.Z> with <M> bullets`
- On abort: `Aborted: <reason>`

Inputs (for reference):

- `VERSION`: `${VERSION}`
- `DRY_RUN`: `${DRY_RUN}`
