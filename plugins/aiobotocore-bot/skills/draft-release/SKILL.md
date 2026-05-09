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

## Step 2: Enumerate merged PRs, their commits, and changed files

For each PR in the release window, gather every signal that influences
the bump rule. PR title alone is not enough; a single PR can contribute
several signals (title prefix, body footer, labels, files changed,
dependency-spec deltas), and the strongest signal wins.

```bash
from_iso=$(git log -1 --format=%cI "$FROM")
gh pr list --state merged --base main \
  --search "merged:>$from_iso" \
  --json number,title,body,labels,mergedAt,url,mergeCommit,closingIssuesReferences,files \
  --limit 200 > /tmp/release-prs.json
```

Filter to PRs whose ``mergeCommit.oid`` is reachable from ``$TO`` (use
``git merge-base --is-ancestor <oid> $TO``). This avoids pulling in
PRs merged after the cutoff or onto sibling branches.

For each remaining PR, also capture:

- **Merge commit message** -- ``git log --no-walk --format='%B' <oid>``.
  Squash-merged PRs put the PR title + body into this message, so it's
  where ``BREAKING CHANGE:`` footers surface when the PR title doesn't
  carry them. Merge-commit-merged PRs have a trivial ``Merge pull
  request #N`` message; scan the PR body directly in that case.
- **Changed files** -- the ``files`` field above; you need it for both
  doc/contrib bucketing and for detecting dependency-spec bumps.
- **pyproject.toml diff if touched** -- ``gh api repos/$REPO/pulls/$N/files``
  (or ``git show <mergeCommit> -- pyproject.toml``) to get the actual
  before/after text of the ``botocore`` (and ``boto3`` / ``aiohttp``)
  dependency lines. See Step 3, "Dependency-bound transitions".

**Direct commits.** Also walk ``git log --no-merges $FROM..$TO`` for
commits not associated with a PR. Branch protection should make this a
null set (the project requires PR + merge queue), but if a commit
appears, it's still a release-window change and gets categorized by its
own subject prefix.

## Step 3: Categorize each PR / standalone commit

For each item, classify into exactly one bucket. Priority order
(first match wins):

1. ``BREAKING:`` prefix in title, or ``BREAKING CHANGE:`` footer in
   PR body or merge commit message, or label ``breaking`` → **breaking**
2. **Dependency-bound major bump** in ``pyproject.toml`` (see below) → **breaking**
3. ``feat:`` prefix, or label ``enhancement``/``feature`` → **feature**
4. **Dependency-bound minor bump** in ``pyproject.toml`` (see below) → **feature**
5. ``fix:`` prefix, or label ``bug`` → **bugfix**
6. ``docs:`` prefix, or only files under ``docs/`` and ``*.md``/``*.rst``
   touched → **doc**
7. ``ci:``/``chore:``/``test:`` prefix, or only files under ``.github/``,
   ``tests/``, ``Makefile``, ``pyproject.toml`` (without source changes)
   touched → **contrib**
8. Anything else with a user-visible effect → **misc**

### Dependency-bound transitions (rules 2 + 4)

aiobotocore re-exports much of botocore, so a major bump of the
``botocore`` lower bound implies API breakage for users and forces
a MAJOR bump on aiobotocore. A minor bump implies new user-visible
features and forces MINOR. This applies to ``aiohttp`` similarly --
it's a public-API dependency.

For each PR that touches ``pyproject.toml``, parse the before/after
text of the ``botocore`` and ``aiohttp`` dependency lines (``boto3``
follows ``botocore``; treat as a duplicate signal, not an additional
one). Compare the **lower bound** (the version after ``>=``) before
and after:

- ``botocore >= 1.42.79`` → ``botocore >= 1.43.0``: minor advance
  (1.42 → 1.43) → **feature** bucket, signal
  ``dep-bound minor: botocore 1.42 → 1.43``.
- ``botocore >= 1.42.79`` → ``botocore >= 2.0.0``: major advance
  → **breaking** bucket.
- ``botocore >= 1.42.79`` → ``botocore >= 1.42.90``: patch only
  (same major.minor) → no forced bump from this signal; the PR
  goes to whichever bucket its title prefix lands in.
- Upper-bound-only changes (``< 1.42.85`` → ``< 1.42.92`` with
  lower unchanged) → no forced bump.

If multiple PRs in the window touch ``pyproject.toml``, the **net
transition** across the whole window matters, not any single PR's.
Compute the lower bound at ``$FROM`` and at ``$TO``; the strongest
transition (major > minor > none) is the forcing signal.

### Signal trace

For each item, **also record the specific signal that placed it in
its bucket** -- you'll cite this in the PR body's bump-reasoning
section. Examples:

- ``#1539: feature (title prefix 'feat:')``
- ``#1602: breaking (BREAKING CHANGE: footer in PR body)``
- ``#1610: feature (dep-bound minor: botocore 1.42 → 1.43)``
- ``#1587: bugfix (title prefix 'fix:')``
- ``#1591: doc (only docs/ + .readthedocs.yml touched)``
- ``#1589: contrib (title prefix 'ci:')``

**Skip noise.** Drop PRs whose title indicates pure dependency
patches from ``dependabot[bot]`` or the botocore-sync bot AND whose
body has no unique narrative (those entries cluster — represent
them as one combined line, see Step 5). The dependency-bound check
above still applies to the *aggregate* of these PRs even if the
individual entries are collapsed. Also skip the eventual release PR
itself (title ``Release v...``). Track the count of skipped PRs
separately so the body can mention them ("4 dependabot bumps
collapsed").

## Step 4: Compute the next version

### Step 4a: Detect whether the current version is unreleased

A previous draft-release run may have already bumped ``__version__``
past the latest PyPI release (e.g. an earlier release PR was
abandoned, or a maintainer bumped manually). In that case **the new
release inherits the existing unreleased version, not a fresh bump on
top of it** — otherwise we end up with ``3.7.0`` (unreleased) →
``3.7.1`` (this draft) → both shipping together as ``3.7.1`` and the
``3.7.0`` header becomes a phantom (#1588 review feedback from
@jakob-keller).

```bash
current=$(grep -oP "__version__\s*=\s*['\"]\\K[0-9]+\\.[0-9]+\\.[0-9]+" \
  aiobotocore/__init__.py)
released=$(curl -s https://pypi.org/pypi/aiobotocore/json \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])")
# `current` is unreleased iff it sorts strictly newer than `released`
unreleased=$(python3 -c "
from packaging.version import Version
print('1' if Version('$current') > Version('$released') else '0')")
```

### Step 4b: Apply the bump rule

Compute the *target* bump from PRs in the window (any → first match wins):

- Any **breaking** entry → ``BREAKING``
- Any **feature** entry → ``MINOR``
- Else → ``PATCH``

Reminder: a dep-bound major / minor transition (Step 3 rules 2 + 4)
already lands the PR(s) in the **breaking** / **feature** bucket
respectively, so this rule picks up dep-bound forcing automatically
without a separate clause. When that's what fired, the PR-body
"Forcing signal(s)" line should cite the dep-bound transition
explicitly so reviewers see the trail.

Then resolve the new version:

- If ``unreleased=0`` (top of CHANGES.rst is already shipped):
  apply the bump to ``current`` directly.
  - ``BREAKING`` → ``X+1.0.0``
  - ``MINOR``    → ``X.Y+1.0``
  - ``PATCH``    → ``X.Y.Z+1``
- If ``unreleased=1`` (``current`` is an in-progress draft):
  the new release replaces ``current``. Compute what ``current``
  *should be* given the combined window (``released..$TO``) and pick
  the **stronger** of (current's existing bump intent, this run's
  bump intent). That is:
  - If ``current`` is already a MINOR-or-greater bump over ``released``
    and this run only adds patches, keep ``current`` (no version change).
  - If this run adds a feature/breaking entry that lifts the bump
    level (e.g. ``current=3.6.1`` over ``released=3.6.0`` was a patch
    but a new ``feat:`` PR landed), bump ``current`` up to the right
    level (``3.6.1`` → ``3.7.0``).
  - Folding the new bullets into the existing unreleased entry
    happens in Step 5 — Step 4 only computes the version number.

Override with ``--version`` when provided. If the override skips a
level (e.g. current ``3.7.0``, override ``3.9.0``), proceed but
include the deviation in the PR body so the maintainer can confirm.

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

Where to write:

- If Step 4a found ``unreleased=1`` (top entry is in-progress):
  **fold into the existing entry** — replace the ``X.Y.Z (date)``
  header + underline with the new computed version, recompute the
  ``^`` underline length, append the new bullets under the same
  header (preserving prior bullets, in the bucket order above).
- Else: insert a fresh section between the ``Changes\n-------``
  header and the most recent existing entry.

## Step 6: Bump the version

Replace the ``__version__`` line in ``aiobotocore/__init__.py`` with the
new version. Don't touch any other line in the file.

## Step 7: Open the release PR

If ``--dry-run`` was passed: print the proposed entry + version + bump
reasoning to stdout and exit. Don't commit, don't push, don't open a PR.

Otherwise:

1. Create branch ``claude/release-X.Y.Z`` from ``$TO`` (typically ``origin/main``).
   The ``claude/`` prefix matches the convention in ``CLAUDE.md`` for
   bot-created branches; ``auto-release-on-merge.yml`` keys off the PR
   title (``Release v...``), not the branch name, so the branch name is
   purely informational.
2. Commit the two file changes via ``mcp__github_file_ops__commit_files``
   (signing required) with message:

   ```text
   chore(release): prepare X.Y.Z

   Auto-drafted by /aiobotocore-bot:draft-release. Edit the CHANGES.rst
   bullets in this PR if any need rewording — they're synthesized from
   merged PRs in the release window.
   ```

3. Open the PR. **Title MUST start with ``Release v``** (the auto-tag
   workflow keys off this prefix). Use ``Release vX.Y.Z``. The body
   does NOT duplicate the verbatim changelog (that lives in
   ``CHANGES.rst``, which the auto-release workflow extracts for the
   GitHub Release notes). Instead the body shows the *reasoning* --
   why this version, which PRs caused which bump signal, scope of the
   release at a glance.

   Template (fill in the placeholders from the data captured in
   Steps 2-4):

   ```markdown
   ## Release vX.Y.Z

   The verbatim changelog is in the **`CHANGES.rst` diff** in this
   PR's "Files changed" tab. The auto-release workflow reads that
   file for the GitHub Release notes, so any edits to bullets should
   happen there directly.

   ### Bump reasoning

   - **Current version:** Y.Y.Y (last released on PyPI)
   - **New version:** X.Y.Z
   - **Bump level:** *patch | minor | major*
   - **Forcing signal(s):** *the strongest signal(s) that landed this
     bump level. Cite the specific PR(s) and what triggered the
     classification, e.g.*
     - `#1602: BREAKING CHANGE: footer in body` -> bump MAJOR
     - `#1610: dep-bound minor (botocore 1.42 -> 1.43)` -> bump MINOR
   - **Window:** ``<FROM_REF>..<TO_REF>`` (`<short_FROM_SHA>` ...
     `<short_TO_SHA>`)

   ### Categorization breakdown (N PRs included)

   *Group every PR/commit by the bucket it landed in. Use this exact
   shape so reviewers can scan -- one section per non-empty bucket,
   in priority order. Each entry: PR/issue ref, one-line title or
   summary, and the signal that placed it in this bucket.*

   **Breaking changes (M)**

   - #NNNN -- `<summary>` -- *signal*

   **Features (M)**

   - #NNNN -- `<summary>` -- *signal*

   **Bug fixes (M)**

   - #NNNN -- `<summary>` -- *signal*

   **Documentation (M)**

   - #NNNN -- `<summary>` -- *signal*

   **Contributor-facing (M)**

   - #NNNN -- `<summary>` -- *signal*

   **Misc (M)**

   - #NNNN -- `<summary>` -- *signal*

   *(Omit empty buckets entirely. If dependabot / botocore-sync bumps
   were collapsed, add a final note: "K dependabot/sync bumps
   collapsed into the dep-bound signal above" and DO NOT list them
   individually.)*

   ### Dependency-bound check

   *State the net transition of the relevant dependency lower bounds
   across the window, even if the bump rule didn't end up forced by
   them. This is the trail that confirms aiobotocore is keeping pace
   with botocore correctly.*

   - `botocore`: ``>= X.Y.Z`` -> ``>= X'.Y'.Z'`` (*patch* | *minor* | *major* advance)
   - `aiohttp`: same shape
   - *(skip lines for unchanged dependencies)*

   ### What happens on merge

   Merging triggers ``.github/workflows/auto-release-on-merge.yml``,
   which:

   1. Validates ``CHANGES.rst`` matches ``aiobotocore/__init__.py``
      via ``scripts/changelog.py validate --expected-top-version X.Y.Z``.
   2. Creates the signed ``X.Y.Z`` git tag at the merge commit
      (Releases API path; tag is signed by GitHub's web-flow key).
   3. Drafts a GitHub Release with notes extracted from this version's
      ``CHANGES.rst`` entry via ``scripts/changelog.py extract``.
   4. Builds the wheel + sdist via ``reusable-build.yml``.
   5. Publishes to PyPI via OIDC trusted publishing using
      ``reusable-publish.yml``.

   The Release is drafted (not published); a maintainer reviews the
   notes in the GitHub UI and clicks Publish.

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

   The bump-reasoning + categorization sections are the most
   important parts of the body -- they're what a reviewer scans to
   decide if the agent's classification is correct. Be explicit
   about *which signal* placed each PR in each bucket, not just the
   bucket itself.

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
