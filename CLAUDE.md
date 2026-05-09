# Branch protection

- `main` is protected: requires PR, merge queue, status checks, and **verified commit signatures**.
- All commits must be signed. Unsigned commits will block PR merges.
- Never push directly to `main`.
- Never push to fork branches. If a PR comes from a fork, only leave review comments.

# Environment

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

When making code changes (bug fixes, features, enhancements), always:

1. Bump the version in `aiobotocore/__init__.py` (patch for fixes, minor for features)
2. Add an entry at the top of `CHANGES.rst` with the new version, date, and description.
   The `^` underline under the header line must match the header length exactly — miscounts
   break Sphinx rendering.

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
