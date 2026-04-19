# aiobotocore-bot plugin

Slash commands used by this repo's Claude Code GitHub Actions workflows
(`.github/workflows/claude.yml`, `.github/workflows/botocore-sync.yml`).

See [docs/ai-workflows.md](../../docs/ai-workflows.md) for the full system
design; this README only covers the plugin itself.

## Commands

- `/aiobotocore-bot:review-pr [--comment]` — sequential PR code review
  (CLAUDE.md compliance, bugs in the diff, aiobotocore-specific async
  patterns, relax-vs-bump sanity check for sync-bot PRs). Scores each
  finding 0–100 and filters < 80. With `--comment`, posts inline
  review comments via
  `mcp__github_inline_comment__create_inline_comment`.
- `/aiobotocore-bot:analyze-pr-feedback [--focus=<databaseId>] [--resolve]`
  — fetch every review thread + top-level PR comment (including
  resolved) and synthesize the discussion into three buckets: what was
  asked, what was done, what's still outstanding. Act on the third
  bucket using the three-outcome rule (already-fixed, fix-now,
  ask-clarification).
- `/aiobotocore-bot:check-async-need --from=<ver> --to=<ver>` —
  classify new/changed functions in overridden botocore files as
  `relax-safe`, `bump-required`, or `ambiguous`. Single source of
  truth for the relax-vs-bump decision; used by both the sync bot
  (at classification time) and the reviewer (as a sanity check on
  sync-bot PRs).
- `/aiobotocore-bot:open-pr --title=... --mode=generic|sync-relax|sync-bump`
  — re-read `pull_request_template.md`, fill placeholders, verify
  checked boxes against the diff, append sync-specific extra sections
  when mode is `sync-relax`/`sync-bump`, create or update the PR.
- `/aiobotocore-bot:bump-version --mode=relax|bump --target=<ver>` —
  mechanical updates: `pyproject.toml` bounds, `aiobotocore/__init__.py`
  version, `CHANGES.rst` entry (with correct `^` underline length),
  and `uv lock`.
- `/aiobotocore-bot:pyright-delta` — stash / run pyright baseline /
  pop / run pyright with changes, report only new errors in files
  the current changes touched.
- `/aiobotocore-bot:complete-run --event=... --number=... [--comment-id=...] [--skip-reply]`
  — end-of-run cleanup: post summary reply to the right target (inline
  thread vs top-level PR comment vs issue comment) and swap 👀→👍.

## Design notes

- **Sequential, not parallel.** Our review launches steps in the main
  conversation rather than fanning out to subagents
  ([#1507](https://github.com/aio-libs/aiobotocore/pull/1507)). The
  cost per review is lower than parallel-agent alternatives at
  comparable signal quality.
- **Local-load path only.** The marketplace at `.claude-plugin/` in
  the repo root is loaded by `claude-code-action` via
  `plugin_marketplaces: ./.` so PR branches test their own plugin
  changes.

## Using these locally

From the repo root:

```bash
claude /plugin marketplace add ./
claude /plugin install aiobotocore-bot@aiobotocore
```

Or pass directly with `--plugin-dir`:

```bash
claude --plugin-dir ./plugins/aiobotocore-bot
```
