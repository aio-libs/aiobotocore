# Evals

Narrow eval harness for plugin slash commands. One script per command.

## check_async_need.py

Replays `/aiobotocore-bot:check-async-need` against historical sync PRs
(known-correct relax-vs-bump verdicts from the PR titles) and reports
pass/fail per case.

**Narrow on purpose.** The script pre-computes the filtered botocore diff
and passes it to Claude as input, bypassing the command's tool-orchestration
layer. That isolates the classification quality — the thing we actually
worry about when the prompt drifts. The mock layer for full end-to-end
would cost more than the prompt is worth.

### Setup

```bash
# One-time: bare clone of botocore for fast diffs
git clone --bare https://github.com/boto/botocore.git /tmp/botocore
git -C /tmp/botocore fetch --tags

# Each run fetches new tags, so keep the clone up to date
git -C /tmp/botocore fetch --tags --force
```

### Run

```bash
export ANTHROPIC_API_KEY=sk-...
uv run --with anthropic python plugins/aiobotocore-bot/evals/check_async_need.py \
    --runs 3 --limit 8
```

Options:

- `--runs N` — runs per case; majority vote decides pass/fail (default 3)
- `--limit N` — max historical PRs to evaluate (default 8)
- `--case N` — only evaluate specific PR number (repeatable)
- `--model <id>` — Anthropic model ID (default `claude-opus-4-7`)
- `--json-out <path>` — write full per-run results as JSON

### What it checks

- Each merged PR titled `Relax botocore ...` must classify as `relax-safe`
- Each merged PR titled `Bump botocore ...` must classify as `bump-required`
- Cases pass if `⌈runs/2⌉ + 1` or more runs agree with the expected verdict
- The script exits 1 if any case fails

### When to run

- After editing `plugins/aiobotocore-bot/commands/check-async-need.md`
- Before merging changes to the classifier criteria
- Periodically to catch model-behavior regressions

### Known limitations

- LLM-driven and non-deterministic; the `--runs` flag exists to tolerate
  that, but unusual diffs can still flake. Re-run with `--runs 5` before
  declaring a real regression.
- Only covers the classification step. Tool orchestration (git diff
  invocation, filesystem mirror lookup) is assumed to work and is exercised
  in production on every sync run.
- Historical PRs merged with the wrong classification would poison the
  ground truth. None have been identified to date; if one is discovered,
  exclude it via `--case` filtering.
