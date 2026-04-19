# aiobotocore-bot plugin

Skills used by this repo's Claude Code GitHub Actions workflows
(`.github/workflows/claude.yml`, `.github/workflows/botocore-sync.yml`).
The agent invokes them via the `Skill` tool in agentic contexts; humans
can invoke them interactively as `/aiobotocore-bot:<name>` slash commands.

See [docs/ai-workflows.md](../../docs/ai-workflows.md) for the full system
design; this README only covers the plugin itself.

## Skills

- `/aiobotocore-bot:review-pr [--comment]` — sequential PR code review
  (CLAUDE.md compliance, bugs in the diff, aiobotocore-specific async
  patterns, port-vs-no-port sanity check for sync-bot PRs). Scores each
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
  `no-port`, `port-required`, or `ambiguous`. Single source of
  truth for the port-vs-no-port decision; used by both the sync bot
  (at classification time) and the reviewer (as a sanity check on
  sync-bot PRs).
- `/aiobotocore-bot:check-override-drift --pr=<number>` — flag
  unmatched behavioral changes or cosmetic additions in overridden
  code. Principle: unmatched behavioral changes should be avoided;
  legitimate async gaps are OK. Used by the reviewer on any PR
  that touches `aiobotocore/*.py` files with a botocore mirror.
- `/aiobotocore-bot:open-pr --title=... --mode=generic|sync-no-port|sync-port`
  — re-read `pull_request_template.md`, fill placeholders, verify
  checked boxes against the diff, append sync-specific extra sections
  when mode is `sync-no-port`/`sync-port`, create or update the PR.
- `/aiobotocore-bot:bump-version --mode=no-port|port --target=<ver>` —
  mechanical updates: `pyproject.toml` bounds, `aiobotocore/__init__.py`
  version, `CHANGES.rst` entry (with correct `^` underline length),
  and `uv lock`.
- `/aiobotocore-bot:pyright-delta` — create a baseline worktree at
  origin/main, run pyright there, remove worktree, run pyright with
  current changes, report only new errors in files the changes touched.
  Worktree instead of git-stash avoids the failed-stash-pop hazard.
- `/aiobotocore-bot:complete-run --event=... --number=... [--comment-id=...] [--skip-reply]`
  — end-of-run cleanup: post summary reply to the right target (inline
  thread vs top-level PR comment vs issue comment) and swap 👀→👍.

## Evals

Narrow LLM-driven evals for the skills live in `evals/`. Each one
replays the skill against labeled ground truth and asserts the output
matches, run N times with majority vote to tolerate non-determinism.

- `evals/check_async_need.py` — replays the async-need classifier against
  historical botocore sync PRs (title tells us the correct verdict).

See `evals/README.md` for setup and usage.

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
