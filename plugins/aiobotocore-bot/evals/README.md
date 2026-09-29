# Evals

Narrow eval harness for plugin skills. One script per skill, plus a
shared committed ground-truth file (`scenarios.yaml`) so the evals run fast
and the labels are reviewable artifacts in their own right.

## Layout

```text
evals/
├── README.md                  # this file
├── scenarios.yaml             # ground truth for check-async-need (sync PRs)
├── drift_scenarios.yaml       # ground truth for check-override-drift (any PR)
├── generate_scenarios.py      # stub generator for scenarios.yaml
├── check_async_need.py        # eval runner for the check-async-need skill
├── check_override_drift.py    # eval runner for the check-override-drift skill
└── check_porting.py           # eval runner for the botocore-sync porting stage
```

## Running in CI (recommended)

`.github/workflows/evals.yml` runs both evals on manual trigger
(`workflow_dispatch`). Admins can trigger it from the Actions tab or via
the CLI:

```bash
gh workflow run evals.yml -f eval=both -f runs=3 -f limit=8
```

To compare models, add `-f model=claude-sonnet-5-5 -f effort=high`; empty
inputs fall back to the script defaults.

Permissions: `workflow_dispatch` requires repo write access, and the job
also uses `environment: claude` to gate the `ANTHROPIC_API_KEY` secret.
Between those, only the `aiobotocore-admins` team (see the team on
GitHub for current membership) plus users with explicit write access can
run it. No PR or schedule triggers — the eval is never auto-run, and
fork PRs can't touch it.

Results: a per-job step summary with the tail of stdout, plus full
per-run JSON uploaded as an artifact (30-day retention).

## Running locally

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
  expected: port-required
  from: "1.40.20"
  to: "1.41.10"
  merge_commit: abc123...
  aiobotocore_version: "3.2.0"     # the aiobotocore version this sync shipped
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

**aiobotocore ↔ botocore correlation:** `aiobotocore_version` is the version
released by this sync (read from `aiobotocore/__init__.py` at `merge_commit`).
Combined with `to` (the last supported botocore), the row records which
aiobotocore version a given botocore range shipped in *as of this sync PR*.

Caveat: aiobotocore can bump its own version for reasons unrelated to a
botocore sync (internal refactors, new features, bug fixes). So the version
shown here is the version the sync landed at, **not** the first or only
version that supports that botocore range — subsequent non-sync releases
keep the same range supported until the next sync lands. Sort by
`aiobotocore_version` to see sync cadence, not absolute support windows.

**Regenerate the stub** (fresh PRs, or after an override file is added/removed):

```bash
uv run python plugins/aiobotocore-bot/evals/generate_scenarios.py
```

The generator:

- Queries merged PRs titled `Bump botocore ...` (historical PRs titled
  `Relax botocore ...` are also picked up)
- Derives `from`/`to` from the `pyproject.toml` upper-bound diff on the merge commit
- Records which overridden files were touched by the botocore diff
- Leaves `rationale` and `notes` blank for human annotation

**Rationale quality matters.** Each row's rationale explains *why* the classification
is what it is. Good rationales cite specific functions, files, or hash failures.
Reviewing and writing these is also a forcing function to find gaps in the
workflow prompts — if a past PR had a subtle nuance that isn't in the prompts,
note it in `notes` and consider a prompt change.

## check_async_need.py

Replays the `check-async-need` skill against every row in
`scenarios.yaml` (or derives from `gh pr list` if the YAML is missing),
comparing the classifier's verdict to `expected`.

**Narrow on purpose.** The script pre-computes the filtered botocore diff
and passes it to Claude as the user message, bypassing the skill's
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
- `--model <id>` — Anthropic model ID (default: `DEFAULT_MODEL` in `_common.py`; `--help` prints it)
- `--effort <level>` — `low`/`medium`/`high`/`xhigh`/`max` (default: `DEFAULT_EFFORT` in `_common.py`)
- `--json-out <path>` — write per-run results as JSON

### What it checks

- Each `expected: no-port` row must classify as `no-port`
- Each `expected: port-required` row must classify as `port-required`
- Cases pass if `⌈runs/2⌉ + 1` or more runs agree with the expected verdict
- Script exits 1 if any case fails

### When to run

- After editing `plugins/aiobotocore-bot/skills/check-async-need/SKILL.md`
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

## check_override_drift.py

Replays the `check-override-drift` skill against labeled aiobotocore diffs in
`drift_scenarios.yaml`. Expected verdicts are `clean` | `cosmetic-drift` |
`behavioral-drift`, following the per-line table in the skill's Step 3.

### Ground truth — drift_scenarios.yaml

Each case is a committed diff under `drift_fixtures/`, the aiobotocore commit
it applies to, and the botocore version its overrides should mirror:

```yaml
- id: behavioral-retry-cap
  title: "Cap retries in AioEndpoint._needs_retry"
  expected: behavioral-drift
  fixture: behavioral-retry-cap.diff
  base_commit: 36935fecbb42e5ed11886bc4e8d44975c70d44bf
  botocore_version: "1.43.98"
  rationale: |
    Adds `if attempts >= 10: return False`; botocore has no attempt cap.
```

`base_commit` must be on `main`, since the override registry is read there;
branch commits vanish when a PR squash-merges. The runner gives the model the
diff, that registry, and the botocore source (from the bare clone at
`botocore_version`) of every function the diff touches.

Cases come from two sources:

- **Real PRs** — sync ports whose added lines all appear in the matching
  botocore file, or are async gaps (`clean`), and #1562 (`behavioral-drift`,
  its diff kept at `refs/pull/1562/head` after GitHub hid the PR).
- **Single-change edits of `main`** — one docstring, type hint, comment or
  import move (`cosmetic-drift`), or one guard, log line, changed condition
  or non-equivalent check (`behavioral-drift`). Make each with a string
  replacement on the base file and `git diff --no-index`, then confirm it
  applies: `git apply --cached --check` against a temporary index read from
  `base_commit`.

### Run

```bash
export ANTHROPIC_API_KEY=sk-...
uv run --with anthropic python plugins/aiobotocore-bot/evals/check_override_drift.py \
    --runs 3
```

Options:

- `--runs N` — runs per case; majority vote decides pass/fail (default 3)
- `--case ID` — only evaluate specific scenario ids (repeatable)
- `--model <id>` — Anthropic model ID (default: `DEFAULT_MODEL` in `_common.py`; `--help` prints it)
- `--effort <level>` — `low`/`medium`/`high`/`xhigh`/`max` (default: `DEFAULT_EFFORT` in `_common.py`)
- `--json-out <path>` — write per-run results as JSON

Needs the bare botocore clone (`BOTOCORE_CLONE`, default `/tmp/botocore`).

### When to run

- After editing `plugins/aiobotocore-bot/skills/check-override-drift/SKILL.md`
- Before merging changes to the drift criteria
- When adding new legitimate-async-gap patterns to the "OK" list — make sure
  clean cases still classify as clean.

### Known limitations

Same as `check_async_need.py` — LLM non-determinism mitigated via majority
vote, only top-line verdict compared, no tool-orchestration coverage. Plus
two drift-specific caveats:

- **Edited cases test detection, not prevalence.** They show whether the
  model spots a planted change, not how often real PRs contain one.
- The classifier is asked to reason about the diff without running the
  code. Some behavioral changes (subtle semantics, order-of-operations)
  may be miscategorized as cosmetic. Majority-vote helps but doesn't
  eliminate this class of miss.

## check_porting.py

Replays the botocore-sync porting stage on `port-required` rows of
`scenarios.yaml`. For each case it checks out aiobotocore at the sync's parent
commit, runs the classifier the way the `classify` job does, then runs Claude
Code headless with the production sync prompt, limited to Steps 5-6 (no
commits, pushes, PRs or issues). Grading needs no LLM judge:

- `tests/test_patches.py` passes against the target botocore
- the test suite passes (`-m "not localonly" --http-backend=all`)
- recall and precision of the override functions the agent changed, against
  the real port's diff (a different correct port can score below 1)

Each case is a full agent run of up to `--max-turns` (default 150), so measure
one case before running more. CI: `gh workflow run evals.yml -f
eval=check-porting -f cases=1744`, optionally with `-f model=sonnet -f
effort=high` (Claude Code model aliases or full ids). It is excluded from
`eval=both`.
