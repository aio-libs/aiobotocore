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

Append:

```text
### What changed in botocore
- Schema/model-only, or [summarize categorized diff].
- Async-need check: <--async-need-summary verbatim>

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
- If something looks wrong, leave a review comment — the bot will attempt to fix straightforward
  issues automatically.
- Use `@claude` to ask questions or request modifications.
```

### mode=sync-port

Same as `sync-no-port` but:

- Description-of-change mentions it's a bump and why (new functionality requires async override).
- Include `--assumptions` under a dedicated "Assumptions" section if provided.
- "What changed in aiobotocore" should list files modified, classes added, tests ported.
- Reviewer checklist items change to: async patterns correct, hashes updated for new overrides,
  version bump is minor, tests ported from botocore where applicable.
- Omit the async-need summary line (the bump itself is the answer).

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
