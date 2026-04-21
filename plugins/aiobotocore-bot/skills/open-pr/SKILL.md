---
description: Use when opening or updating an aiobotocore PR against main. Re-reads `.github/pull_request_template.md` each run (authoritative), fills placeholders, verifies each ticked checklist box against the diff, and appends mode-specific sections for `generic`, `sync-no-port`, or `sync-port` PRs.
argument-hint: "--title=TITLE [--mode=generic|sync-no-port|sync-port] [--description=TEXT] [--botocore-diff-url=URL] [--async-need-summary=TEXT] [--assumptions=TEXT] [--changed-aiobotocore=TEXT] [--extra-sections=TEXT] [--update-only]"
allowed-tools: Bash(cat:*) Bash(git diff:*) Bash(gh pr create:*) Bash(gh pr edit:*) Bash(gh pr view:*) mcp__github_file_ops__commit_files
---

Create or update a pull request whose body follows `.github/pull_request_template.md`, with
placeholders filled from the current branch's work and optional extra sections appended for
specialized PR types (e.g. botocore sync). Use this instead of hand-rolling `gh pr create` calls
so template changes flow through automatically and checklist-verification stays consistent.

## Arguments

- `--title=<title>` (required): PR title.
- `--mode=<generic|sync-no-port|sync-port>` (default `generic`): controls which extra sections are
  appended below the template and how the description is framed.
- `--description=<text>` (required for `generic`; optional for sync modes, which can synthesize it
  from the other fields): one or two paragraphs for the "Description of Change" slot.
- `--botocore-diff-url=<url>` (required for sync modes): e.g.
  `https://github.com/boto/botocore/compare/1.42.84...1.42.89`.
- `--async-need-summary=<text>` (required for `sync-no-port`): the summary line from the
  `check-async-need` skill that justifies the no-port verdict. Do not paraphrase — quote it.
- `--classifier-verdicts=<text>` (optional, sync modes): the per-function verdict block from the
  `check-async-need` skill's `rationale` field. When provided, rendered as a markdown table in
  the PR body so a human reviewer can spot-check each classification without re-running the
  classifier. See "Classifier verdicts table" below for expected shape.
- `--assumptions=<text>` (optional): design decisions for the "Assumptions" slot (bumps only).
- `--changed-aiobotocore=<text>` (optional): summary of aiobotocore changes — files modified,
  classes added, tests ported. For no-port: pass `"Version bounds updated only, no code changes."`.
- `--extra-sections=<text>` (optional, `mode=generic` only): extra markdown sections appended
  below the template content. For sync modes the extra sections are generated from the
  mode-specific fields above; ignored here.
- `--update-only` (optional): if set, only update the existing PR's title/body (for dirty-PR in-place
  edits). Never create.

## Step 1: Re-read the template

Always at PR open time, never from memory:

```text
cat .github/pull_request_template.md
```

## Step 2: Fill the template

Treat the template as the foundation for the body. Apply these rules:

1. Preserve its headings and checklist items in the template's original order.
2. Replace every `*Replace this text with ...*` placeholder with concrete content. Never leave a
   placeholder behind.
3. Omit a section only when it clearly does not apply (e.g. "Assumptions" when there are none).
   Phrasing tweaks for clarity are fine.
4. If the template has new sections or checklist items compared to your memory, include them
   anyway — treat the file as authoritative on each run.

### Do NOT hard-wrap PR bodies or comments

GitHub renders Markdown with natural word-wrap; hard-wrapped lines display as awkwardly short
paragraphs and bloat cached input on every follow-up turn. Write each paragraph as ONE long
logical line. The same applies to inline review comments and top-level PR comments produced by
sibling skills (`review-pr`, `complete-run`, `analyze-pr-feedback`).

Hard-wrap is for source files only (yamllint compliance); rendered Markdown does not need it.

### Verbosity ceiling

Reviewers skim. Length costs cache on every follow-up @claude run. Defaults:

- **"Description of Change"**: at most 2 sentences. Say what version range and what theme of
  change (e.g. "Bump botocore to 1.42.91. New `auth_scheme_preference` threading needs async
  porting"). Per-symbol detail belongs in the classifier table, not here.
- **"Assumptions"**: include only if a non-obvious decision was made that the reviewer should
  validate. Inheriting from a base class without an override is NOT a non-obvious decision —
  it's the default. Omit the section if the only "assumptions" are restatements of the diff.
- **"What changed in aiobotocore"**: at most one short line per file. No prose paragraphs. Do
  NOT restate per-symbol botocore changes — the table covers those.
- **Reviewer checklist**: keep to ≤6 items. Don't pad with items that overlap.

Tick a checklist box only for work the current branch actually did. For items you didn't do,
either omit with a brief note or leave the box unchecked with a one-line reason, e.g.
`[ ] Detailed description of issue — N/A, no linked issue`. **Unchecked with a reason is always
better than a false check.**

## Step 3: Verify checked boxes against the diff

For every box you ticked, confirm the diff supports it:

- `CHANGES.rst` entry checked → `git diff origin/main -- CHANGES.rst` shows a new top entry.
- `test_patches.py` updated checked → the hashes file has a matching diff.
- CONTRIBUTING.rst followed checked → only tick for botocore/aiohttp upgrades when you actually
  ran those steps.

If a box fails verification, either uncheck it (with a reason) or do the work before opening the
PR. Don't open a PR with a false-positive check.

## Step 4: Append mode-specific extra sections

These go **after** the template's content, not instead of it.

### mode=generic

No extra sections unless the caller passes `--extra-sections=<text>`.

### mode=sync-no-port

Append (conditional sections marked with `[only if ...]`):

```text
[only if --classifier-verdicts NOT provided]
### What changed in botocore
One or two sentences at the topic level — e.g. "Model/schema-only updates
for N services" or "Adds `auth_scheme_preference` threading through args
+ client."

Async-need check: <--async-need-summary verbatim>

### What changed in aiobotocore
<--changed-aiobotocore, default: Version bounds updated only, no code changes.>

<Classifier verdicts table — see below — when --classifier-verdicts is provided>

### Reviewer checklist
- [ ] Botocore diff reviewed — confirms no-port vs port-required
- [ ] `test_patches.py` hashes current
- [ ] Version bump is patch (no-port)
- [ ] `CHANGES.rst` entry added
- [ ] No unrelated changes

### How to help
- Review the botocore diff: <--botocore-diff-url>
- `@claude <ask>` to request modifications. Reviewer comments → automatic fix attempt.
```

When `--classifier-verdicts` is provided, omit the "What changed in botocore" section entirely
— the table is the authoritative per-function view (see Anti-duplication rule below). Keep the
async-need summary line; it's a one-line top-line verdict the table doesn't repeat.

### mode=sync-port

Same as `sync-no-port` but:

- Description-of-change is one sentence: `Bump botocore to X.Y.Z. <one-clause summary of the
  feature requiring the port>`. Per-symbol detail belongs in the classifier table.
- Include `--assumptions` under an "Assumptions" section ONLY if provided AND non-tautological
  (see Anti-duplication rule).
- "What changed in aiobotocore": ≤1 line per file. List new `Aio*` classes, overridden methods,
  ported tests. Never restate the botocore-side change.
- Reviewer checklist items change to: async patterns correct, hashes updated for new overrides,
  version bump is minor, tests ported from botocore where applicable.
- Omit the async-need summary line (the bump itself is the answer).

### Anti-duplication rule (both sync modes)

When `--classifier-verdicts` is provided, the classifier verdicts table is the
authoritative per-function view. To prevent the description from saying the
same thing five times (description ¶1, assumptions, what-changed-in-botocore,
what-changed-in-aiobotocore, classifier table), apply these mechanical rules:

- **Drop the "What changed in botocore" section entirely** when
  `--classifier-verdicts` is provided. The table replaces it. Do not write
  prose bullets summarizing what the table already enumerates per-row.
- **Description of Change**: 2 sentences max — see "Verbosity ceiling" above.
  Never restate per-file or per-symbol changes; that's the table's job.
- **What changed in aiobotocore**: per-file aiobotocore-side work only — new
  classes, overridden methods, ported tests. ≤1 line per file. Never restate
  the botocore-side change for that file (the table already shows it).
- **Assumptions**: omit when "assumption" is a tautology of inheritance
  (e.g. "regions.py needs no changes because the subclass inherits"). Keep
  only assumptions a reviewer would actually want to validate.

If you find yourself writing the same fact in two sections, delete it from
the less-authoritative one. The hierarchy from most-to-least authoritative:
classifier table > what-changed-aiobotocore > description > assumptions.

### Classifier verdicts table (both sync modes)

When `--classifier-verdicts` is provided, append a `### Classifier verdicts` section with a
markdown table extracted from the classifier's per-function `rationale`. Each row is one
changed/added/removed function the classifier inspected. Columns:

| File | Function | Change | Verdict | Reason |
|-|-|-|-|-|
| botocore/args.py | `ClientArgsCreator.get_client_args` | changed | needs-async | Added call to async `self._emit`; override must mirror |
| botocore/httpchecksum.py | `Sha512Checksum` | added | pure-sync | New class, no I/O, not in overrides |
| botocore/utils.py | `has_checksum_header` | changed | pure-sync | Refactor delegates to new pure-sync helper |

This table is the primary signal the human reviewer uses to validate the classifier's work —
one row per function gives them the per-entry accountability the raw verdict hides. Parse the
rationale into rows verbatim; if the rationale doesn't have per-function detail, omit the
table (do not synthesize rows). Keep reason text to one short sentence — reviewers skim this.

## Step 5: Open or update

If an existing open PR on this branch is found (`gh pr view` succeeds) OR `--update-only` is set:

```text
gh pr edit <num> --title "<title>" --body "<body>"
```

Otherwise:

```text
gh pr create --base main --title "<title>" --body "<body>"
```

Never target a branch other than `main`. Never merge or close PRs — those require human approval.

## Step 6: Output

Print the PR URL. If `--update-only` was used and no PR existed, exit with an error — the caller
made a wrong assumption about PR state.

## Honesty

Never tick a checklist box you haven't verified. Never invent `--async-need-summary` — if the
caller didn't run the `check-async-need` skill, refuse to use `mode=sync-no-port` and tell
them to run it first.
