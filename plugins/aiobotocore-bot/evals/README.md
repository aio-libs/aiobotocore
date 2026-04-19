# Evals

Narrow eval harness for plugin slash commands. One script per command, plus a
shared committed ground-truth file (`scenarios.yaml`) so the evals run fast
and the labels are reviewable artifacts in their own right.

## Layout

```text
evals/
├── README.md                  # this file
├── scenarios.yaml             # committed ground truth (PR # → expected verdict + rationale)
├── generate_scenarios.py      # stub generator: queries gh, writes YAML rows for humans to annotate
└── check_async_need.py        # eval runner for /aiobotocore-bot:check-async-need
```

## One-time setup

```bash
git clone --bare https://github.com/boto/botocore.git /tmp/botocore
git -C /tmp/botocore fetch --tags
```

The eval scripts expect the clone at `/tmp/botocore` (override with
`BOTOCORE_CLONE` env var).

## scenarios.yaml workflow

`scenarios.yaml` is the labeled ground truth. Each row is a historical sync PR:

```yaml
- pr: 1534
  title: "Bump `botocore` dependency specification"
  expected: bump-required
  from: "1.40.20"
  to: "1.41.10"
  merge_commit: abc123...
  botocore_diff: https://github.com/boto/botocore/compare/1.40.20...1.41.10
  aiobotocore_pr: https://github.com/aio-libs/aiobotocore/pull/1534
  botocore_files_touched:
    - botocore/httpchecksum.py
  rationale: |
    New httpchecksum logic added async-relevant I/O in an overridden file.
    Hash test flagged _checksum_calculate; the aiobotocore override for this
    function needed to await the new internal call.
  notes: null
```

**Regenerate the stub** (fresh PRs, or after an override file is added/removed):

```bash
uv run python plugins/aiobotocore-bot/evals/generate_scenarios.py
```

The generator:

- Queries merged PRs titled `Relax ...`/`Bump ...`
- Derives `from`/`to` from the `pyproject.toml` upper-bound diff on the merge commit
- Records which overridden files were touched by the botocore diff
- Leaves `rationale` and `notes` blank for human annotation

**Rationale quality matters.** Each row's rationale explains *why* the classification
is what it is. Good rationales cite specific functions, files, or hash failures.
Reviewing and writing these is also a forcing function to find gaps in the
workflow prompts — if a past PR had a subtle nuance that isn't in the prompts,
note it in `notes` and consider a prompt change.

## check_async_need.py

Replays `/aiobotocore-bot:check-async-need` against every row in
`scenarios.yaml` (or derives from `gh pr list` if the YAML is missing),
comparing the classifier's verdict to `expected`.

**Narrow on purpose.** The script pre-computes the filtered botocore diff
and passes it to Claude as the user message, bypassing the command's
tool-orchestration layer. That isolates classification quality — the thing
we actually worry about when the prompt drifts.

### Run

```bash
export ANTHROPIC_API_KEY=sk-...
uv run --with anthropic python plugins/aiobotocore-bot/evals/check_async_need.py \
    --runs 3 --limit 8
```

Options:

- `--runs N` — runs per case; majority vote decides pass/fail (default 3)
- `--limit N` — max scenarios to evaluate (default 8)
- `--case N` — only evaluate specific PR number (repeatable)
- `--model <id>` — Anthropic model ID (default `claude-opus-4-7`)
- `--json-out <path>` — write per-run results as JSON

### What it checks

- Each `expected: relax-safe` row must classify as `relax-safe`
- Each `expected: bump-required` row must classify as `bump-required`
- Cases pass if `⌈runs/2⌉ + 1` or more runs agree with the expected verdict
- Script exits 1 if any case fails

### When to run

- After editing `plugins/aiobotocore-bot/commands/check-async-need.md`
- Before merging changes to the classifier criteria
- After an override file is added/removed (the override set affects the filter)
- Periodically to catch model-behavior regressions

### Known limitations

- LLM-driven and non-deterministic. `--runs` tolerates that; re-run with
  `--runs 5` before declaring a real regression.
- Only covers the classification step. Tool orchestration (git diff
  invocation, filesystem mirror lookup) is exercised in production.
- Only compares the top-line `CLASSIFICATION:` verdict. Per-function verdicts
  inside the report aren't validated.
- The file-set filter uses *today's* override list, which may be broader than
  what the original reviewer saw if mirrors were added since. This biases
  toward safe failures, not dangerous ones.
- Historical PRs merged with the wrong classification would poison the ground
  truth. Use `notes: historical mislabel, ...` on the affected row and
  exclude it via `--case` filtering if needed.
