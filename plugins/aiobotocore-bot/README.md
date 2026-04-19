# aiobotocore-bot plugin

Slash commands used by this repo's Claude Code GitHub Actions workflows
(`.github/workflows/claude.yml`, `.github/workflows/botocore-sync.yml`).

See [docs/ai-workflows.md](../../docs/ai-workflows.md) for the full system
design; this README only covers the plugin itself.

## Commands

- `/aiobotocore-bot:review-pr [--comment]` — sequential PR code review
  (CLAUDE.md compliance, bugs in the diff, aiobotocore-specific async
  patterns). Scores each finding 0–100 and filters < 80. With
  `--comment`, posts inline review comments via
  `mcp__github_inline_comment__create_inline_comment`.
- `/aiobotocore-bot:analyze-pr-feedback [--focus=<databaseId>] [--resolve]`
  — fetch every review thread + top-level PR comment (including
  resolved) and synthesize the discussion into three buckets: what was
  asked, what was done, what's still outstanding. Act on the third
  bucket using the three-outcome rule (already-fixed, fix-now,
  ask-clarification).

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
