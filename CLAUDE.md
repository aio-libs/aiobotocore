# Branch protection

- `main` is protected: requires PR, merge queue, status checks, and **verified commit signatures**.
- All commits must be signed. Unsigned commits will block PR merges.
- Never push directly to `main`.
- Never push to fork branches. If a PR comes from a fork, only leave review comments.

# Environment

Default branch is `main` (not `master`). Use `--base main` for PRs.

Dependencies are managed by `uv`. Never use `pip install`.
To read botocore source, find the installed path with
`python3 -c "import botocore; print(botocore.__file__)"`,
then read files directly — do not use `inspect.getsource()`.

# Pre-commit checks

Run before committing:

```bash
uv run pre-commit run --all --show-diff-on-failure
```

Key constraints:

- YAML lines must be ≤120 chars (`# yamllint disable-line rule:line-length` for exceptions)
- All workflow jobs must have `timeout-minutes`
- Python code formatted with `ruff`

# Tests

```bash
uv run pytest -sv tests/<path>           # run specific tests
uv run make mototest                     # moto-based tests (CI runs these)
uv run pytest -sv tests/test_patches.py  # hash validation
```

## Test directory structure

- `tests/` — aiobotocore-specific tests (parametrized with aiohttp+httpx via conftest.py)
- `tests/botocore_tests/` — tests ported from botocore (not parametrized with HTTP backends)

# Versioning

**Don't bump `aiobotocore/__init__.py` and don't edit `CHANGES.rst` in feature PRs.**
Both are owned by the AI-drafted release flow:

- `.github/workflows/draft-release.yml` (manual trigger) summarizes merged PRs
  since the last tag, computes the next version, writes the changelog entry,
  and opens a `Release vX.Y.Z` PR.
- `.github/workflows/auto-release-on-merge.yml` runs when that PR merges:
  creates the tag, drafts the GitHub Release, and the existing tag-push CI
  publishes to PyPI.

For your feature PR to land in the changelog cleanly, write a
Conventional-Commits-style title (`fix:`, `feat:`, `BREAKING:`, `docs:`,
`ci:`, `chore:`, `test:`) and a one-paragraph body describing the
user-visible effect. The bump rule keys off these:

- any `BREAKING:` or `breaking` label → MAJOR
- any `feat:` or `enhancement`/`feature` label → MINOR
- otherwise → PATCH

The `update-botocore-bounds` skill (the only thing the automated
`botocore-sync` workflow runs today, formerly named `bump-version`)
only updates `pyproject.toml` bounds and `uv.lock` — `__init__.py`
and `CHANGES.rst` are off-limits to per-PR automation.

# Overriding botocore code

When adding or modifying an override, update `tests/test_patches.py` with the SHA1 hash of the
overridden botocore function. See the existing entries and `CONTRIBUTING.rst` "Hashes of Botocore
Code" for details.

# AI workflow conventions

These rules apply to the Claude-driven GitHub Actions workflows (`.github/*-prompt.md`). They do
not apply to humans editing the repo directly.

- **Signed commits via MCP:** workflow runs must use `mcp__github_file_ops__commit_files` for
  every commit. It creates signed commits attributed to `claude[bot]`; plain `git commit`
  produces unsigned commits that block PR merges.
- **Branches:** always use the `claude/` prefix for branches created by the bot. Never push to
  `main` — it is protected.
- **Committing to a branch from a workflow:** `mcp__github_file_ops__commit_files` takes only
  `files` and `message`. It has **no branch argument** — passing `branch:` or `ref:` is silently
  ignored. The target comes from the `CLAUDE_BRANCH` env var on the action step, read once when
  the MCP server starts. Unset, it falls back to `GITHUB_REF_NAME`, so a `workflow_dispatch` run
  on `main` commits to `main` and is rejected by branch protection ("Changes must be made through
  the merge queue / pull request"). So: **set `CLAUDE_BRANCH: claude/<name>` in the step's `env:`**
  (see `draft-release.yml`). Don't pre-create the ref — the tool creates the branch off
  `BASE_BRANCH` on first commit. Conversely it *reuses* the branch if it already exists, so
  prefer a name scoped per *attempt* —
  `claude/release-${{ github.run_id }}-${{ github.run_attempt }}` — over one a failed attempt
  could have left behind with commits on it. `run_id` alone is not enough: it stays the same
  across re-runs, so a retry would land on the failed attempt's branch.
  Do **not** fall back to `git push` — workflow checkouts use `persist-credentials: false`, so it
  fails with "could not read Username", and a runner-side `git commit` is unsigned (no key on the
  runner), which blocks the merge.
- **What actually signs a bot commit:** GitHub signs any commit created through its API with the
  workflow token's identity. `commit_files` is a thin wrapper over `POST /git/blobs`,
  `POST /git/trees`, `POST /git/commits`, `PATCH /git/refs/heads/<branch>` — there is no signing
  key or GPG step in it. So the MCP tool is the *convenient* path, not a magic one: the same four
  `gh api` calls produce an identically verified commit (that fallback drafted v3.7.1 — commit
  `cc8814f6`, `verified: true`). Prefer the MCP tool; if it's ever blocked, the `gh api` git-data
  route is a legitimate escape hatch. Only `git push` is off-limits.
- **Pre-commit setup:** before committing, run `uv run pre-commit install` once, then
  `uv run pre-commit run --all --show-diff-on-failure` before pushing. If pre-commit modifies
  files, stage them and commit again (as a new commit — do not amend).
- **Fork PRs are read-only:** when `IS_FORK` is true, never push commits. Review comments only.
- **No `--amend`, no force-push to `main`:** the workflows never rewrite history. Every fix is
  a new commit on top of the branch's HEAD.

# Key documentation

- [CONTRIBUTING.rst](CONTRIBUTING.rst) — upgrade process, hash validation, test running
- [docs/override-patterns.md](docs/override-patterns.md) — async override patterns, test porting
- [docs/ai-workflows.md](docs/ai-workflows.md) — AI automation: triggers, prompts, trust model, guardrails

# How aiobotocore overrides botocore

aiobotocore makes botocore async by **subclassing** (never monkey-patching). Each override
follows the same pattern: subclass with `Aio` prefix, override sync methods with `async def`,
call `await` on I/O operations. See [docs/override-patterns.md](docs/override-patterns.md).

**Minimize divergence from botocore.** Unmatched behavioral changes to overridden code
should be avoided — they make future syncs harder. Legitimate async gaps
(`asyncio.Lock` replacing `threading.Lock`, `async with`, `resolve_awaitable()`,
`Aio*` class use) are OK; cosmetic additions (docstrings, comments, type hints,
refactors) that aren't in the matching botocore are drift. If you see an
improvement that would benefit sync users too, PR it upstream to botocore first,
then sync here. See docs/override-patterns.md §"Guiding principle: minimize
divergence from botocore" for details.

**Override chain:** `AioSession.create_client()` → `AioClientCreator.create_client()` →
`AioBaseClient._make_api_call()` → `AioEndpoint._send_request()` → `AIOHTTPSession.send()`

**Key files:** session.py, client.py, endpoint.py, httpsession.py, credentials.py, hooks.py,
args.py, config.py, _helpers.py — each overrides its botocore equivalent.
