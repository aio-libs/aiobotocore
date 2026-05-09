---
description: Use when drafting a new release. Reads merged PRs since the last release tag, categorizes them by Conventional-Commit-style title prefix and labels, computes the next version (major/minor/patch), updates ``CHANGES.rst`` and ``aiobotocore/__init__.py`` in the existing repo style, and opens a release PR. The PR's merge is what triggers the actual tag + GitHub Release + PyPI publish (see `.github/workflows/auto-release-on-merge.yml`).
argument-hint: "[--version=X.Y.Z] [--from=REF] [--to=REF] [--date=YYYY-MM-DD] [--dry-run]"
allowed-tools: Bash(git:*) Bash(gh:*) Bash(date:*) Bash(grep:*) Bash(sed:*) Bash(awk:*) Bash(printf:*) Bash(wc:*) Bash(seq:*) Bash(tr:*) Bash(python3:*) Bash(cat:*) mcp__github_file_ops__commit_files
---

Draft a release PR. Replaces the old per-PR "bump version + add CHANGES.rst entry"
ceremony — contributors no longer touch either file. This skill reads what was
merged, summarizes it, and opens the release PR for human review.

## Arguments

- `--version=<X.Y.Z>` (optional): override the auto-derived next version. The
  default bump rule (see Step 4) usually does the right thing; pass this only
  when you want to force a different bump (e.g. ship a minor bump even though
  no PR carried a ``feat:`` prefix).
- `--from=<ref>` (optional): start of the release window, exclusive. Default:
  most recent tag matching ``[0-9]+\.[0-9]+\.[0-9]+``.
- `--to=<ref>` (optional): end of the release window, inclusive. Default:
  ``origin/main``.
- `--date=<YYYY-MM-DD>` (optional): override the release date in the
  ``CHANGES.rst`` header. Default: today (UTC).
- `--dry-run` (optional): print the proposed CHANGES.rst entry + computed
  version, don't open a PR.

## Step 1: Determine the release window

```bash
FROM="${ARG_FROM:-$(git tag -l | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
  | sort -V | tail -1)}"
TO="${ARG_TO:-origin/main}"

git rev-parse --verify "$FROM" >/dev/null \
  || { echo "Cannot resolve --from=$FROM" >&2; exit 1; }
git rev-parse --verify "$TO" >/dev/null \
  || { echo "Cannot resolve --to=$TO" >&2; exit 1; }
```

If ``$FROM == $TO`` or the range contains no commits, abort: nothing to release.

## Step 2: Enumerate merged PRs in the window

```bash
from_iso=$(git log -1 --format=%cI "$FROM")
gh pr list --state merged --base main \
  --search "merged:>$from_iso" \
  --json number,title,body,labels,mergedAt,url,mergeCommit,closingIssuesReferences \
  --limit 200 > /tmp/release-prs.json
```

Filter to PRs whose ``mergeCommit.oid`` is reachable from ``$TO`` (use
``git merge-base --is-ancestor <oid> $TO``). This avoids pulling in PRs
merged after the cutoff or onto sibling branches.

## Step 3: Categorize each PR

For each PR, classify into exactly one bucket using this priority order
(first match wins):

1. ``BREAKING:`` prefix in title, or label ``breaking`` → **breaking**
2. ``feat:`` prefix, or label ``enhancement``/``feature`` → **feature**
3. ``fix:`` prefix, or label ``bug`` → **bugfix**
4. ``docs:`` prefix, or only files under ``docs/`` and ``*.md``/``*.rst``
   touched → **doc**
5. ``ci:``/``chore:``/``test:`` prefix, or only files under ``.github/``,
   ``tests/``, ``Makefile``, ``pyproject.toml`` (without source changes)
   touched → **contrib**
6. Anything else with a user-visible effect → **misc**

**Skip noise.** Drop PRs whose title indicates pure dependency bumps from
``dependabot[bot]`` or the botocore-sync bot AND whose body has no
unique narrative (those entries cluster — represent them as one
combined line, see Step 5). Also skip the eventual release PR itself
(title ``Release v...``).

## Step 4: Compute the next version

```bash
current=$(grep -oP "__version__\s*=\s*['\"]\\K[0-9]+\\.[0-9]+\\.[0-9]+" \
  aiobotocore/__init__.py)
```

Bump rule (any → first match wins):

- Any **breaking** entry → MAJOR (``X+1.0.0``)
- Any **feature** entry → MINOR (``X.Y+1.0``)
- Else                    → PATCH (``X.Y.Z+1``)

Override with ``--version`` when provided. If the override skips a level
(e.g. current ``3.7.1``, override ``3.9.0``), proceed but include the
deviation in the PR body so the maintainer can confirm.

## Step 5: Build the CHANGES.rst entry

Use the existing repo style (``^^^^`` underline, single bulleted list per
release). Generate **one bullet per PR** in this order: breaking, then
feature, then bugfix, then doc, then contrib, then misc. Within each
bucket, preserve merge order.

Each bullet:

- One sentence in user-facing language (not commit-message language).
  Derive from PR title + body. Strip the conventional prefix
  (``fix:``, ``feat:``, ``BREAKING:``).
- Trailing reference: ``(#NNNN)`` for the PR. If the PR closes a
  concrete user-reported issue, also include ``closes #MMMM``.
- Bumps from dependabot/botocore-sync that share a target collapse
  into a single bullet — e.g. ``bump botocore dependency specification
  to support "botocore >= X.Y.Z, < A.B.C" (#NNNN, #PPPP)``.

The ``^`` underline length MUST equal the header length exactly. Build
mechanically:

```bash
HEADER="$NEW_VERSION ($DATE)"
LEN=$(printf '%s' "$HEADER" | wc -c | tr -d ' ')
UNDERLINE=$(printf '^%.0s' $(seq 1 "$LEN"))
```

Insert the new section above the most recent existing entry in
``CHANGES.rst`` (between the ``Changes\n-------`` header and the first
existing version entry).

## Step 6: Bump the version

Replace the ``__version__`` line in ``aiobotocore/__init__.py`` with the
new version. Don't touch any other line in the file.

## Step 7: Open the release PR

If ``--dry-run`` was passed: print the proposed entry + version + bump
reasoning to stdout and exit. Don't commit, don't push, don't open a PR.

Otherwise:

1. Create branch ``release/X.Y.Z`` from ``$TO`` (typically ``origin/main``).
2. Commit the two file changes via ``mcp__github_file_ops__commit_files``
   (signing required) with message:

   ```text
   chore(release): prepare X.Y.Z

   Auto-drafted by /aiobotocore-bot:draft-release. Edit the CHANGES.rst
   bullets in this PR if any need rewording — they're synthesized from
   merged PRs in the release window.
   ```

3. Open the PR. **Title MUST start with ``Release v``** (the auto-tag
   workflow keys off this prefix). Use ``Release vX.Y.Z``. Body:

   ```markdown
   ## Release vX.Y.Z

   ### Changelog

   <the assembled CHANGES.rst entry, rendered as markdown>

   ### Bump reasoning

   - Current version: X.Y.(Z-1)
   - Bumping <patch|minor|major> because: <reason>
   - Window: <FROM>..<TO> (<N> PRs)

   ### What happens on merge

   Merging this PR triggers `.github/workflows/auto-release-on-merge.yml`
   which creates the `X.Y.Z` git tag, drafts the GitHub Release, and the
   existing tag-push CI publishes to PyPI.

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

## Honesty

- **Never invent changes.** Every bullet must trace to a concrete PR in
  the window. If a PR's content can't be summarized confidently,
  reference it as ``* miscellaneous improvements (#NNNN)`` rather than
  fabricating detail.
- **Never silently skip a non-trivial PR.** Dependabot/sync-bot bumps
  can collapse; everything else gets a bullet even if it's just
  ``* internal cleanup (#NNNN)``.
- **Verify the underline length.** ``[ "${#HEADER}" -eq "${#UNDERLINE}" ]``
  before writing — abort with a clear error if mismatched.
- **Don't merge.** This skill opens the PR. The maintainer reviews,
  edits if needed, and merges manually.
