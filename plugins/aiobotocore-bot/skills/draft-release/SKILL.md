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
dependency-spec deltas), and **each signal independently** lands the
PR in its corresponding bucket and feeds the bump rule.

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
- **Changed files** -- the ``files`` field above; you need it for
  doc/contrib bucketing and to identify which PRs land in the
  ``dep-bump`` bucket (PRs whose ``files`` includes ``pyproject.toml``).

**Net pyproject.toml transition** -- compute once for the whole window,
not per PR (avoids an N+1 API call pattern):

```bash
git diff "$FROM..$TO" -- pyproject.toml > /tmp/pyproject.diff
```

Parse the before/after lower bound (the version after ``>=``) of
``botocore`` and ``aiohttp`` from this single diff and label the net
transition (major / minor / patch / range-only) per the rules in
Step 3. The bump rule keys off this net transition. Per-PR
classification of "did this PR touch pyproject.toml" comes from the
``files`` field above; per-PR transition labels (for the breakdown)
can be inferred from the per-PR slice ``git show <mergeCommit> --
pyproject.toml`` if needed -- but if the window has many small bumps,
collapse them in the body to one bullet citing the net transition
rather than listing each.

**Direct commits.** Also walk ``git log --no-merges $FROM..$TO`` for
commits not associated with a PR. Branch protection should make this a
null set (the project requires PR + merge queue), but if a commit
appears, it's still a release-window change and gets categorized by its
own subject prefix.

## Step 3: Categorize each PR / standalone commit

A PR can carry **multiple signals** -- e.g. a ``feat:`` PR that also
bumps the ``botocore`` lower bound, or a ``fix:`` PR that pulls in a
``BREAKING CHANGE:`` footer. Each signal goes in its own bucket
*independently*; do not collapse to a single primary bucket.
Buckets and the signals that place a PR in each:

- **breaking** -- ``BREAKING:`` prefix in title, or ``BREAKING CHANGE:``
  footer in PR body / merge commit message, or label ``breaking``
- **dep-bump** -- ``pyproject.toml`` diff changes the ``botocore`` (or
  ``boto3`` / ``aiohttp``) dependency line. See "Dep-bump bucket" below
  for the transition-level sub-classification (major / minor / patch /
  range-only)
- **feature** -- ``feat:`` prefix, or label ``enhancement``/``feature``
- **bugfix** -- ``fix:`` prefix, or label ``bug``
- **doc** -- ``docs:`` prefix, or only files under ``docs/`` and
  ``*.md``/``*.rst`` touched
- **contrib** -- ``ci:``/``chore:``/``test:`` prefix, or only files
  under ``.github/``, ``tests/``, ``Makefile``, ``pyproject.toml``
  (without source changes) touched
- **misc** -- anything else with a user-visible effect that didn't
  match any rule above

A PR present in N buckets shows up in N sections of the PR-body
breakdown (with its own signal trace each time), and contributes to
the bump rule via *each* of its signals -- it's the union of
signals across all PRs in the window that drives the bump level.

Most PRs have exactly one signal and land in one bucket. The mixed
case -- the user's ``feat: ... and bump botocore minor`` shape, or
a ``fix:`` that also corrects docs -- gets treated honestly: the PR
appears in both ``feature`` and ``dep-bump``, both of which feed the
bump rule.

### Dep-bump bucket

aiobotocore re-exports much of botocore, so the underlying-dep range
matters to users. The bucket has its own bump-forcing rule (see Step
4b) keyed off the *transition level*, not just bucket presence:

For each PR in this bucket, parse the before/after text of the
``botocore`` and ``aiohttp`` dependency lines from the
``pyproject.toml`` diff (``boto3`` follows ``botocore``; treat as a
duplicate signal, not an additional one). Compare the **lower
bound** (the version after ``>=``) before and after, and label the
transition:

- ``botocore >= 1.42.79`` → ``botocore >= 1.43.0`` = **minor advance**
- ``botocore >= 1.42.79`` → ``botocore >= 2.0.0`` = **major advance**
- ``botocore >= 1.42.79`` → ``botocore >= 1.42.90`` (same major.minor) = **patch advance**
- Upper-bound-only changes (``< 1.42.85`` → ``< 1.42.92`` with
  lower unchanged) = **range-only** (no transition)

Bump-rule effect of dep-bump entries (Step 4b applies):

- Any **major-advance** dep-bump → forces MAJOR
- Any **minor-advance** dep-bump → forces at least MINOR
- **patch-advance** / **range-only** dep-bumps don't force anything;
  the release stays PATCH unless other buckets push it higher

Multi-PR windows: if several PRs in the window each advance the dep
bound, the **net transition** from ``$FROM`` to ``$TO`` is what
matters (compute once across the whole window). The individual PRs
still each get their own entry in the dep-bump bucket of the body
breakdown, but the bump rule fires off the aggregate.

### Signal trace

For each (PR, bucket) pair, **also record the specific signal that
placed the PR in that bucket** -- you'll cite this in the PR body's
breakdown section. Examples (note ``#1610`` appears twice because
it carried two distinct signals):

- ``#1539 → feature: title prefix 'feat:'``
- ``#1602 → breaking: BREAKING CHANGE: footer in PR body``
- ``#1610 → feature: title prefix 'feat:'``
- ``#1610 → dep-bump (minor advance): botocore 1.42 → 1.43``
- ``#1587 → bugfix: title prefix 'fix:'``
- ``#1591 → doc: only docs/ + .readthedocs.yml touched``
- ``#1589 → contrib: title prefix 'ci:'``

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
past the latest PyPI release, OR the top entry in ``CHANGES.rst`` may
be a draft from a prior run that was never merged. In either case
the top entry is **unreleased** and is fair game to fully rewrite --
treat it as a draft, not as history.

Concretely: if ``__version__`` (or the top CHANGES.rst entry) is
strictly newer than the latest PyPI version, recompute the release
window from ``released..$TO`` (i.e. start from the *released* boundary,
not the unreleased boundary), redo the categorization (Step 3), and
**replace** the existing top entry's version, date, and bullets in
Step 5. Don't try to preserve hand-polished wording from the prior
draft -- if a maintainer polished bullets, those edits are lost on
the next run, which is correct: the latest run reflects the latest
reality. (Avoids the failure mode in #1588 where two unreleased
entries stack and one becomes a phantom on release.)

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

Compute the *target* bump from buckets populated in Step 3 (first
match wins):

- Any **breaking** entry → ``BREAKING``
- Any **dep-bump** with **major-advance** transition → ``BREAKING``
- Any **feature** entry → ``MINOR``
- Any **dep-bump** with **minor-advance** transition → ``MINOR``
- Else → ``PATCH`` (covers bugfix-only releases, doc/contrib-only
  releases, and dep-bumps with patch-advance / range-only
  transitions)

Whichever clause fires, cite it explicitly in the PR body's
"Forcing signal(s)" line so reviewers see the trail.

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

- If Step 4a found ``unreleased=1`` (top entry is an unreleased draft):
  **delete the existing top entry entirely** (header + underline +
  bullets, up to but not including the next version header) and write
  a fresh entry in its place. The new entry's version, date, and
  bullets all come from the recomputed ``released..$TO`` window.
  Don't try to preserve prior bullets -- if the maintainer polished
  them, that polish is lost on this run, which is correct: the goal
  is "what should ship in the next release given current main",
  not "additive accretion on top of an old draft".
- Else (top entry is already released on PyPI): insert a fresh
  section between the ``Changes\n-------`` header and the most
  recent existing entry. Don't touch the existing entries.

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

   ### Categorization breakdown (N unique PRs included)

   *Group by bucket. **A PR can appear in multiple buckets if it
   carried multiple signals** (e.g. a ``feat:`` PR that also bumped
   botocore minor shows up in both **Features** and **Dependency
   bumps**) -- list it under each, with the appropriate signal each
   time. The unique-PR count above is for the header; bucket totals
   may add up to more than N.*

   *Each entry: PR ref, one-line title, signal that placed the PR in
   this bucket.*

   **Breaking changes (M)**

   - #NNNN -- `<summary>` -- *signal*

   **Dependency bumps (M)**

   - #NNNN -- `<summary>` -- *transition: botocore A.B → A'.B'*

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
   were many and clustered on the same dep, list all of them under
   **Dependency bumps** but combine them into a single bullet that
   cites the net transition: ``#A, #B, #C -- bump botocore -- net
   1.42.79 → 1.42.92 (patch)``.)*

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
