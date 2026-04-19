# AI Automation in aiobotocore

aiobotocore uses Claude-driven GitHub Actions to automate two recurring
maintenance burdens: reviewing pull requests and keeping the `botocore`
pin up to date. This document explains what the system does, how it is
triggered, how it is put together, and how to extend or debug it.

If you only want to *use* the bot (as a contributor or reviewer), jump to
[Using the bot](#using-the-bot). If you want to *change* it, read the
[Architecture](#architecture) and [Extending](#extending--debugging)
sections.

## Why it exists

aiobotocore's core maintenance cost is tracking a fast-moving upstream
(`botocore`) whose API we subclass rather than monkey-patch (see
[override-patterns.md](override-patterns.md)). Every new botocore
release risks breaking our async overrides in subtle ways that only
`tests/test_patches.py` catches. Before automation, each release
required a human to diff `botocore`, decide whether a "relax" (bump the
upper bound) or a "bump" (re-port overrides) was needed, and open a PR.

A parallel pain point: PR review latency. Simple bugs, CLAUDE.md
violations, and missed async patterns routinely slipped through because
no reviewer had the time to re-read every diff against every convention.

The AI automation was introduced in
[#1498](https://github.com/aio-libs/aiobotocore/pull/1498)
("Add Claude Code and Botocore Sync workflows") and has been iterated
heavily since — see [History](#history) below.

## Architecture

Two GitHub Actions workflows drive everything. Both invoke
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action)
with a prompt read from a template, signed commits via the GitHub
File Ops MCP server, and PreToolUse hooks as guardrails.

```
                 .github/workflows/
                 ├── claude.yml               ← PR review, @claude, issues
                 └── botocore-sync.yml        ← scheduled botocore upgrades

                 .github/
                 ├── claude-review-prompt.md   ← main prompt (event-dispatched)
                 ├── botocore-sync-prompt.md   ← sync prompt (stateful)
                 └── usage-summary.py          ← cost/turns reporter

                 .claude/commands/
                 ├── review-pr.md              ← /review-pr slash command
                 └── analyze-pr-feedback.md    ← /analyze-pr-feedback slash command
```

- **Workflow files** decide *when* the bot runs and set up the
  execution environment (Python, `uv`, bun, git auth, hooks).
- **Prompt files** decide *what* the bot does once invoked. They use
  `envsubst` to interpolate a small allowlist of workflow-provided
  variables (`$REPO`, `$NUMBER`, `$EVENT_NAME`, etc.).
- **Slash commands** are reusable procedures the prompts call into.
  They keep the top-level prompts short and let humans run the same
  workflow locally via `/review-pr` or `/analyze-pr-feedback`.
- **Hooks** in `settings:` block `git commit` (requires signing) and
  push-to-`main`/`master` (branch-protected). Commits go through
  `mcp__github_file_ops__commit_files` instead, which signs via the
  GitHub API and is attributed to `claude[bot]`.

### The two workflows at a glance

|-|-|-|-|
| Workflow | Triggers | Job flow | Outputs |
| `claude.yml` | PR opened/synchronized, issue/PR comment with `@claude`, PR review (mention or CHANGES\_REQUESTED on bot PR), issue opened/assigned with `@claude` | Single job, dispatches on `$EVENT_NAME` inside the prompt | Inline review comments, summary replies, signed commits on bot/fork-free PRs, new PRs for `issues` events |
| `botocore-sync.yml` | Cron (`0 10 */3 * *`) and `workflow_dispatch` | `detect` → `sync` (conditional) | PR to bump or relax `botocore` pin; feedback issue when bumps need human input |

## `claude.yml` — PR review & @claude responder

**Triggers (the `on:` block):**

- `pull_request: [opened, synchronize]`
- `issue_comment: [created]` (issues and PRs)
- `pull_request_review_comment: [created]` (inline comments)
- `pull_request_review: [submitted]`
- `issues: [opened, assigned]`

**Gating (the `if:` expression):**

The `if:` block expresses a single rule: *auto-review every non-draft
PR, but only respond to `@claude` mentions from trusted authors*
(`MEMBER`, `OWNER`, or `COLLABORATOR`). It also runs on a
`CHANGES_REQUESTED` review of a bot-authored PR, interpreting that as
an implicit "please fix this" from the reviewer. This prevents prompt
injection via drive-by comments from untrusted accounts.

**Prompt dispatch:**

`claude-review-prompt.md` branches on `$EVENT_NAME`:

- `pull_request` → run `/review-pr --comment` (sequential,
  cache-friendly code review with ≥80 confidence threshold).
  Additionally, if the PR is authored by `claude[bot]`, run
  `/analyze-pr-feedback` to address reviewer threads.
- `issue_comment`, `pull_request_review_comment`,
  `pull_request_review` → fetch the triggering comment, call
  `/analyze-pr-feedback --focus=$COMMENT_ID`, and act per the
  three-outcome rule (already-fixed / fix-now / ask-clarification).
- `issues` → implement the issue: create a `claude/`-prefixed
  branch, push signed commits, open a PR.

**Security model:**

- `IS_FORK` is computed from the PR metadata and plumbed into the
  hook settings. A PreToolUse hook hard-blocks
  `mcp__github_file_ops__commit_files` on fork PRs; the bot leaves
  review comments only.
- Trusted-author checks happen both in the workflow `if:` (coarse)
  and inside the prompt when reading comment bodies (fine).
- `persist-credentials: false` on checkout, explicit `permissions:`
  scope on the job, and a 30-minute timeout provide defense in
  depth.
- `environment: claude` gates `ANTHROPIC_API_KEY` behind a GitHub
  Environment so only approved workflows can read it.

**Cleanup protocol:**

Every run posts exactly one summary reply (inline thread reply for
inline comments, top-level PR comment otherwise, issue comment for
issues). It then swaps the 👀 reaction the action added on the
triggering entity for a 👍, signaling completion in the UI without
needing to read the summary body.

## `botocore-sync.yml` — scheduled botocore upgrades

This workflow is fundamentally different: it runs unattended on a
schedule, accumulates state across runs, and makes autonomous design
decisions inside a large-but-bounded problem space.

**Inputs:**

| Input | Type | Effect |
|-|-|-|
| `botocore_version` | string | Override auto-detection; must pass format + monotonicity checks |
| `enable_bump` | bool (default `false`) | When `false`, bumps that need code changes open a **feedback issue** instead of attempting the port |
| `dry_run` | bool (default `false`) | Analysis only — logs categorized diff to the run summary, no branches/PRs |

**Jobs:**

1. `detect` (≤5 min): fetch latest `botocore` from PyPI, parse the
   current specifier from `pyproject.toml`, decide whether the target
   version is already in range. Exposes `current_upper`,
   `current_lower`, `last_supported`, `needs_update` as job outputs.
2. `sync` (≤60 min, conditional on `needs_update == 'true'`): clone
   botocore into a cached bare repo at `/tmp/botocore`, diff
   `$LAST_SUPPORTED..$LATEST_BOTOCORE`, run the Claude agent with
   `--max-turns 100`.

**Two-PR model:**

The sync prompt explicitly uses two branches:

- `claude/botocore-sync-wip` — draft PR that accumulates incremental
  commits. Its description is the handoff document: "Completed",
  "Remaining", "Decisions made", "Context for next run", "Blockers".
  Run N+1 reads it and continues.
- `claude/botocore-sync` — the squashed, review-ready PR. Only
  created once tests pass.

For **relax** updates (no code changes needed, only bounds bump),
the bot skips the WIP PR entirely.

**Update-type decision tree:**

1. `tests/test_patches.py` is run. Hash failures are a signal, not a
   gate.
2. Each changed botocore file is checked for a mirror in
   `aiobotocore/` (filename-mirror convention: `botocore/foo.py` ↔
   `aiobotocore/foo.py`). Files without a mirror are skipped —
   do not grep aiobotocore for references to their internals.
3. **Relax** (patch bump) if diffs touch only schemas / untouched
   files. **Bump** (minor bump) if any overridden file has code
   changes or new logic needing async treatment. **Major** is
   flagged for human review.

**Feedback issue loop:**

When `enable_bump=false` (the default) and a bump is needed, the bot
opens a `botocore-sync-feedback`-labelled issue with:

- Botocore diff URL
- Numbered questions, each with context / options / trade-offs
- "How to respond" guidance (direct answers, `@claude` for more
  context, or suggest a different approach)

The next scheduled run reads the issue (filtering to trusted authors)
and applies answers. Reusable patterns learned this way are promoted
to [override-patterns.md](override-patterns.md) or CLAUDE.md in the
same PR — so the bot teaches itself across runs.

**Dirty-PR policy:**

If a final sync PR already exists and has human edits or reviews, the
bot will not clobber it. For relaxes, it applies the bounds change
in-place. For bumps, it refuses to touch the branch and posts a
comment noting the new version is blocked on the existing PR.

## Prompt templates

The prompt files in `.github/` are plain markdown. The workflow does:

```bash
prompt=$(envsubst '$VAR1 $VAR2 ...' < .github/<file>.md)
```

The explicit variable allowlist is important: without it, `envsubst`
expands every `$VAR` in the file — including shell variables in bash
examples (`$PARENT_ID`) and GraphQL query variables (`$owner`, `$name`,
`$pr`), which would silently become empty strings. See
[#1523](https://github.com/aio-libs/aiobotocore/pull/1523) for the
bug that motivated the allowlist.

**Editing conventions:**

- Reflow prose to ~120 chars (matches yamllint).
- Keep code blocks small and commented — the prompt is still the
  LLM's instruction set, not a paper.
- Any change to `claude-review-prompt.md` is effectively a behavior
  change across *all* `claude.yml` event types. Split-check the
  dispatch sections when editing.

## Slash commands (`.claude/commands/`)

These are reusable procedures invoked by the top-level prompts and
also available to humans running Claude Code locally. They live in
the repo so maintainers can iterate on them alongside the prompts.

| Command | Purpose |
|-|-|
| `/review-pr [--comment]` | Sequential (not parallel) PR review. Checks CLAUDE.md compliance, bugs in the diff, and async patterns. Scores each finding 0–100 and filters < 80. With `--comment`, posts inline review comments via `mcp__github_inline_comment__create_inline_comment`. |
| `/analyze-pr-feedback [--focus=<id>] [--resolve]` | Fetch every review thread + top-level comment (including resolved) and synthesize into three buckets: *what was asked*, *what was done*, *what's still outstanding*. Act on bucket C with the three-outcome rule. Optionally resolve threads after posting a "fixed" reply. |

**Design note — sequential review:** An earlier iteration launched
parallel subagents per file. It produced more findings but burned an
order of magnitude more cache tokens. The sequential path in
`/review-pr` was introduced in
[#1507](https://github.com/aio-libs/aiobotocore/pull/1507) and cut
cost per review dramatically while keeping signal quality.

**Design note — three-bucket synthesis:** `/analyze-pr-feedback`
explicitly forbids "process threads in isolation". A single comment
often makes no sense without the thread; a thread often makes no
sense without the PR-wide history. The bucket structure forces the
agent to build a coherent picture *before* acting.

## Guardrails

Listed in order of defense layer:

1. **Workflow `if:` gate** — filters events by author association
   and PR state (no drafts, no untrusted authors).
2. **GitHub Environment (`claude`)** — wraps the API key, provides
   audit trail of runs with the secret.
3. **Job `permissions:`** — least-privilege token: `contents: write`,
   `pull-requests: write`, `issues: write`, `id-token: write`,
   `actions: read`.
4. **PreToolUse hooks** (defined in `settings:` JSON):
   - Block `git commit` → forces MCP commits → signed.
   - Block `git push ... main|master` → branch protection would
     reject it anyway, but fails fast.
   - Block `mcp__github_file_ops__commit_files` when `IS_FORK=true`.
5. **Prompt-level rules**:
   - Trust only `MEMBER/OWNER/COLLABORATOR` comment bodies.
   - Confidence ≥ 80 before posting review findings.
   - Never merge or close PRs, never close issues.
   - Never push to human-authored PRs.

## Cost & observability

Every run ends with the `Usage summary` step, which parses the action's
`execution_file` (a JSON message log) and appends a markdown table to
the job summary:

```
## Usage
Turns: 12 | Duration: 84s | Total: $0.42

| Model  | Input | Output | Cache read | Cache create | Cost  |
|-|-|-|-|-|-|
| Opus   | ...   | ...    | ...        | ...          | $0.42 |
```

The step is `continue-on-error: true` — we don't want observability to
fail a run. If you're investigating cost regressions, that table is
your first stop; look for growth in `Cache create` tokens (indicates
prompt changes invalidating cache) or turn count.

## Using the bot

### As an external contributor (fork PR)

- Open a PR as usual. The bot will auto-review on open and on every
  push (non-draft PRs only).
- Review comments are the only output you'll see. **The bot will not
  push commits to your fork branch** — that's enforced by a hook.
- If you want a re-review after addressing feedback, just push. A
  new commit triggers re-review automatically.

### As a project member on your own branch

All of the above, plus:

- `@claude <instruction>` in an issue or PR comment, inline review
  comment, or review body triggers a response. Examples:
  - `@claude address the pyright errors in client.py`
  - `@claude explain why this hash changed`
  - `@claude port the failing test from botocore main`
- A CHANGES\_REQUESTED review on a `claude[bot]` PR is treated as an
  implicit "fix this" — no mention needed.
- Opening an issue with `@claude` in the title or body triggers
  implementation. The bot will open a `claude/`-prefixed branch
  with a PR.

### Interpreting bot state in the UI

- 👀 reaction on the triggering entity → bot is working.
- 👍 reaction → bot finished (swapped from 👀). Look for its reply.
- Sticky review summary comment → re-review on each push collapses
  into one evolving comment rather than N summaries.

### Stopping or skipping the bot

- Convert the PR to draft. The `if:` gate excludes drafts from the
  auto-review path (explicit `@claude` mentions still work).
- Close and reopen to force a re-review on the latest commit if you
  think the last run was stale.
- For sync: flip `enable_bump` to `false` on `workflow_dispatch` to
  force the feedback-issue path instead of a code-change attempt.

## Extending & debugging

### Local iteration on prompts

The prompt files are plain markdown and work directly with Claude
Code. To iterate on a prompt without burning CI cycles:

```bash
# Simulate the envsubst step
REPO=aio-libs/aiobotocore NUMBER=1234 EVENT_NAME=pull_request \
  IS_PR=true IS_FORK=false COMMENT_ID= \
  envsubst '$REPO $NUMBER $EVENT_NAME $IS_PR $IS_FORK $COMMENT_ID' \
  < .github/claude-review-prompt.md > /tmp/prompt.md

# Inspect and feed into a local claude-code run
```

The slash commands in `.claude/commands/` are picked up automatically
by Claude Code locally, so `/review-pr 1234` works against real PRs
from your workstation.

### Adding a new trigger

1. Add the event to `on:` in `claude.yml`.
2. Extend the `if:` expression with the author-association / state
   check for the new event.
3. Add a new dispatch section in `claude-review-prompt.md` and
   extend the "Dispatch by EVENT" list.
4. If new env vars are needed, add them to the `env:` of the
   "Read prompt" step **and** to the `envsubst` allowlist.
5. Test by dispatching the workflow manually with a hand-crafted
   payload, or push a throwaway PR that exercises the path.

### Adding a new slash command

1. Create `.claude/commands/<name>.md` with a frontmatter `description`.
2. Reference it from the top-level prompt where appropriate.
3. Keep it self-contained — slash commands run with no prior
   context from the invoking prompt.

### Changing guardrails

Hooks live inline in `settings:` JSON in the workflow file. To add a
new block rule:

```yaml
"PreToolUse": [{
  "matcher": "Bash",
  "command": "if echo \"$TOOL_INPUT\" | grep -qE 'PATTERN'; then echo 'BLOCKED: reason' >&2; exit 2; fi"
}]
```

Exit code 2 blocks with the message shown to the model; exit code 0
allows. Keep messages actionable — the model uses them to course-correct.

### Debugging a failed run

1. Open the Actions tab, find the run, expand the `claude` step.
2. The `show_full_output: true` setting dumps the full tool trace.
3. The `Usage summary` step shows cost and turn count — a run that
   hit `--max-turns` typically needs a different prompt, not a
   higher turn limit.
4. For sync runs, the WIP PR description is often the clearest
   record of where the previous run stopped. Read it first.

### Updating pinned actions

All third-party actions are pinned by commit SHA with a version
comment. Dependabot opens upgrade PRs; review the action's changelog
and upgrade the comment in the same commit. The bot reviews these
PRs on open and will often push the version-bump fix itself.

## History

Selected milestones (see `git log -- .github/workflows/claude.yml .github/workflows/botocore-sync.yml .github/claude-review-prompt.md .github/botocore-sync-prompt.md .claude/commands/` for the full list):

- [#1498](https://github.com/aio-libs/aiobotocore/pull/1498) — Initial
  workflows added.
- [#1500](https://github.com/aio-libs/aiobotocore/pull/1500) — Permissions
  scoped down, security hardening.
- [#1505](https://github.com/aio-libs/aiobotocore/pull/1505) — Auto-review
  only on open, not every push. *(Later partially reverted — see #1544
  context — synchronize is back for re-review-on-new-commits.)*
- [#1507](https://github.com/aio-libs/aiobotocore/pull/1507) — Sequential
  review replaces parallel-subagent plugin. Dramatic cost reduction.
- [#1511](https://github.com/aio-libs/aiobotocore/pull/1511),
  [#1518](https://github.com/aio-libs/aiobotocore/pull/1518) — Commit
  signing via GitHub API, enforced by hooks.
- [#1519](https://github.com/aio-libs/aiobotocore/pull/1519) — Cache the
  botocore bare clone at `/tmp/botocore` so diffs are fast.
- [#1523](https://github.com/aio-libs/aiobotocore/pull/1523) — Restrict
  `envsubst` to documented placeholders; fixes silent-empty-string bug.
- [#1536](https://github.com/aio-libs/aiobotocore/pull/1536) — Disambiguate
  PR vs issue number in `issue_comment` events.
- [#1544](https://github.com/aio-libs/aiobotocore/pull/1544) — Workflow
  efficiency pass (job-level concurrency, dependency caching).
- [#1551](https://github.com/aio-libs/aiobotocore/pull/1551) — Improved
  sync prompt: two-PR model, feedback-issue loop, dry-run input.
- [#1552](https://github.com/aio-libs/aiobotocore/pull/1552) — Guarantee
  summary replies on @claude runs (they were being silently dropped).
- [#1553](https://github.com/aio-libs/aiobotocore/pull/1553) — Extract
  `/analyze-pr-feedback` as its own slash command; auto-address
  reviewer threads on `claude[bot]` PRs.
- [#1554](https://github.com/aio-libs/aiobotocore/pull/1554),
  [#1555](https://github.com/aio-libs/aiobotocore/pull/1555) — Bypass
  bun's GitHub-API rate limit for setup (hit in busy repos).

## Ideas for future work

Captured as hooks for the next contributor, not commitments.

- **Flaky-test triage bot.** Scrape `actions/runs` for test failures on
  `main`, cluster by traceback, open or comment on a single tracking
  issue per cluster. Today this is implicit human work; the data is
  structured enough to automate.
- **"Why did this hash change?" command.** A `/explain-hash-change
  <file>::<function>` slash command that diffs the upstream function
  and summarizes whether the change is behavioral or cosmetic — what
  the sync bot does inline, exposed for humans reviewing sync PRs.
- **Scheduled dependency digest.** Extend `botocore-sync.yml` or add a
  sibling workflow that produces a weekly digest of dependency state
  (botocore, aiohttp, aws-sam-translator) and posts it as a comment on
  a pinned tracking issue.
- **Tighter confidence calibration.** The 0–100 score in `/review-pr`
  is currently self-assessed by the model. Wiring a post-hoc check
  against pyright/ruff output would let us raise the floor without
  losing signal.
- **Reviewer role for the bot.** Today the bot posts inline comments
  as "claude[bot]" but does not set a review state (APPROVE /
  REQUEST\_CHANGES). For bot PRs we could use `REQUEST_CHANGES` to
  block merge queue entry until findings are addressed, closing the
  loop without a human in the critical path.
- **Prompt test harness.** Snapshot-test the envsubst'd prompts for
  each event type against fixtures. Edits to the prompt would surface
  in diffs instead of requiring a live run to catch regressions.
- **Docs cross-link check.** The sync prompt and review prompt both
  reference files (`docs/override-patterns.md`, `CONTRIBUTING.rst`).
  A CI check that these files exist would catch a whole class of
  "moved file" bugs before they bite a run.

If you pick one up, please update this section with the PR link when
it lands — this doc should age into a map, not a museum.
