---
allowed-tools: Bash(cat:*), Bash(git diff:*), Bash(gh pr create:*), Bash(gh pr edit:*), Bash(gh pr view:*), mcp__github_file_ops__commit_files
description: Open or update a PR against main using the repo template, with optional sync-PR structure
---

Create or update a pull request whose body follows `.github/pull_request_template.md`, with
placeholders filled from the current branch's work and optional extra sections appended for
specialized PR types (e.g. botocore sync). Use this instead of hand-rolling `gh pr create` calls
so template changes flow through automatically and checklist-verification stays consistent.

## Arguments

- `--title=<title>` (required): PR title.
- `--mode=<generic|sync-relax|sync-bump>` (default `generic`): controls which extra sections are
  appended below the template and how the description is framed.
- `--description=<text>` (required for `generic`; optional for sync modes, which can synthesize it
  from the other fields): one or two paragraphs for the "Description of Change" slot.
- `--botocore-diff-url=<url>` (required for sync modes): e.g.
  `https://github.com/boto/botocore/compare/1.42.84...1.42.89`.
- `--async-need-summary=<text>` (required for `sync-relax`): the summary line from
  `/aiobotocore-bot:check-async-need` that justifies the relax. Do not paraphrase — quote it.
- `--assumptions=<text>` (optional): design decisions for the "Assumptions" slot (bumps only).
- `--changed-aiobotocore=<text>` (optional): summary of aiobotocore changes — files modified,
  classes added, tests ported. For relax: pass `"Version bounds updated only, no code changes."`.
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

### mode=sync-relax

Append:

```text
### What changed in botocore
- Schema/model-only, or [summarize categorized diff].
- Async-need check: <--async-need-summary verbatim>

### What changed in aiobotocore
<--changed-aiobotocore, default: Version bounds updated only, no code changes.>

### Reviewer checklist
- [ ] Botocore diff reviewed — confirms relax vs bump
- [ ] `test_patches.py` hashes current
- [ ] Version bump is patch (relax)
- [ ] `CHANGES.rst` entry added
- [ ] No unrelated changes

### How to help
- Review the botocore diff: <--botocore-diff-url>
- If something looks wrong, leave a review comment — the bot will attempt to fix straightforward
  issues automatically.
- Use `@claude` to ask questions or request modifications.
```

### mode=sync-bump

Same as `sync-relax` but:

- Description-of-change mentions it's a bump and why (new functionality requires async override).
- Include `--assumptions` under a dedicated "Assumptions" section if provided.
- "What changed in aiobotocore" should list files modified, classes added, tests ported.
- Reviewer checklist items change to: async patterns correct, hashes updated for new overrides,
  version bump is minor, tests ported from botocore where applicable.
- Omit the async-need summary line (the bump itself is the answer).

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
caller didn't run `/aiobotocore-bot:check-async-need`, refuse to use `mode=sync-relax` and tell
them to run it first.
