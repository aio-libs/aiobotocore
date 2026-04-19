---
description: Use when porting botocore tests to aiobotocore (during a port-required sync, or when backfilling historical test coverage). Converts sync test files under `botocore/tests/...` to their async counterparts under `tests/botocore_tests/...`, validates each port with `pytest -x`, commits on pass, reverts on fail. Handles both new/changed tests from a botocore diff AND backfill of relevant-but-not-yet-ported tests.
argument-hint: "[--from=<version>] [--to=<version>] [--backfill] [--paths=<file1,file2,...>] [--dry-run]"
allowed-tools: Bash(git -C /tmp/botocore:*) Bash(uv run pytest:*) Bash(uv run python:*) Bash(ls:*) Bash(find:*) Bash(cat:*) Bash(diff:*) Bash(cp:*) mcp__github_file_ops__commit_files
---

Port botocore tests to aiobotocore. Two driving scenarios:

1. **Port PR workflow** (`--from=X --to=Y`): called from `botocore-sync-prompt.md` Step 5
   (port path). For each test file under `botocore/tests/` that has new or changed tests in the
   `X..Y` range AND has a mirror in `tests/botocore_tests/`, apply the sync→async conversion
   to the new/changed tests and write them into the mirror.
2. **Backfill mode** (`--backfill`): human-invoked when maintainers want to narrow the coverage
   gap. Walk every `botocore/tests/<unit|functional>/test_*.py` that has a partial aiobotocore
   mirror (same filename in `tests/botocore_tests/<unit|functional>/`) and port any test function
   missing from the mirror. Scoped so it doesn't silently port hundreds of tests at once — run
   with `--paths=` to limit to specific files.

Conversion rules are identical in both modes. The skill writes files, validates each with
`pytest -x`, and only commits what passes. Failures are preserved as advisory output so the
human reviewer can finish by hand.

## Arguments

- `--from=<version>`, `--to=<version>` (port-PR mode): botocore tag range. Used to diff for
  added/changed tests.
- `--backfill` (backfill mode): port every not-yet-ported test function from existing mirrored
  files, not just those changed in a version range.
- `--paths=<file1,file2,...>` (optional): restrict to specific test file basenames (e.g.
  `test_credentials.py,test_signers.py`). Required in `--backfill` mode to avoid wall-of-
  churn PRs. In `--from/--to` mode, defaults to "every file that appears in the diff".
- `--dry-run` (optional): emit the converted diffs to stdout; do not write files. Useful for
  review before letting the skill commit changes.

The modes are mutually exclusive: pass `--from/--to` OR `--backfill`, not both.

## Step 1: Enumerate candidate test files

**Port-PR mode (`--from/--to`):**

```text
git -C $BOTOCORE_CLONE diff --name-only $FROM..$TO -- 'botocore/tests/**/test_*.py'
```

Filter to files with an aiobotocore mirror — `tests/botocore_tests/<unit|functional>/<same-name>`
must exist. Files without a mirror are out of scope (we haven't ported that file at all; adding
a new mirror is a separate decision).

**Backfill mode:**

```text
find tests/botocore_tests -name 'test_*.py' -type f
```

For each aiobotocore test file, identify its botocore counterpart. Use that file pair as the
scope.

## Step 2: Extract candidate test functions

For each candidate file:

1. Parse the botocore file (via `uv run python -c "import ast; ..."`) to extract every
   top-level `def test_*` and every `class Test*/def test_*` method.
2. Parse the aiobotocore mirror similarly.
3. The port candidates are:
   - **Port-PR mode**: tests added/changed in the botocore diff AND not yet in the mirror.
   - **Backfill mode**: every botocore test whose name is not in the mirror.

Skip these categories regardless of mode:

- Tests that assert botocore-internal behavior aiobotocore doesn't override (e.g. synchronous
  urllib3 behavior when aiobotocore uses aiohttp). Use the `overrides` registry from
  `scripts/registries.py` to identify which tests exercise overridden code.
- Tests named `test_*_sync_*` or that import from `botocore.httpsession` directly — the sync
  HTTP path doesn't exist in aiobotocore.

## Step 3: Apply conversion rules

For each test function to port, apply these transformations in order:

### 3a. Decorators and signatures

- `def test_X(...)` → `async def test_X(...)`
- `class TestX` staying as a class is fine; convert its `def test_*` methods to `async def`.
- `@pytest.fixture` on a fixture that returns something created via `async def` → fixture itself
  becomes `async def` (pytest-asyncio with `asyncio_mode = "auto"` handles the await).
- `@mock.patch(...)` decorators: update string targets per §3b.
- Remove `unittest.TestCase` inheritance when porting — project uses top-level async test
  functions, not test classes.

### 3b. Imports and mock targets

Replace in both `import` statements AND `mock.patch("...")` / `mocker.patch("...")` strings:

| botocore symbol | aiobotocore equivalent |
|-|-|
| `from botocore.session import Session` | `from aiobotocore.session import AioSession` |
| `from botocore.credentials import CredentialResolver` | `from aiobotocore.credentials import AioCredentialResolver` |
| `from botocore.args import ClientArgsCreator` | `from aiobotocore.args import AioClientArgsCreator` |
| `from botocore.client import ClientCreator` | `from aiobotocore.client import AioClientCreator` |
| `from botocore.signers import ...` | `from aiobotocore.signers import ...` (no Aio prefix for module-level functions) |

Rule: look up the botocore class name in the `aio_classes` registry from `registries.py`. If
`AioX` exists, replace `X` with `AioX` in both imports and patch strings.

### 3c. Async-awareness in bodies

Inside each ported test body:

- Any call to a method or function in the `async_methods` registry gets awaited:
  `session.create_client(...)` → `await session.create_client(...)`.
- Context managers that are now async become `async with`: `with session.create_client(...)` →
  `async with session.create_client(...)`.
- `for chunk in stream` → `async for chunk in stream` when `stream` is a response body.
- `stream.read()` → `await stream.read()` (read is in async_methods).
- `mock.Mock(return_value=...)` on a coroutine target → `mock.AsyncMock(return_value=...)`.

### 3d. Stubber replacement

`botocore.stub.Stubber` and `botocore.client.ClientHTTPStubber` aren't directly compatible with
aiobotocore clients. Replace with the helpers already exported by `tests.botocore_tests`:

- `ClientHTTPStubber` → `from tests.botocore_tests import ClientHTTPStubber` (aiobotocore's
  version, already async-aware)
- `SessionHTTPStubber` → same import path
- `botocore.stub.Stubber` for low-level API stubbing: no direct equivalent; flag the test as
  requiring manual review in the advisory output.

## Step 4: Write, validate, commit

For each successfully converted file:

1. Write the new content to `tests/botocore_tests/<unit|functional>/<same-name>`. If the mirror
   file already has ported tests, APPEND the new tests at a sensible location (typically end of
   file) rather than overwriting — preserve existing work.
2. Run `uv run pytest -xvs tests/botocore_tests/<path>` on just that file. `-x` stops at the
   first failure; `-v` surfaces the failing test name; `-s` shows prints for debugging.
3. **If pytest passes**: the file is ready to commit via `mcp__github_file_ops__commit_files`
   (the caller handles the actual commit batching).
4. **If pytest fails**: revert the file to its pre-port state (`git checkout -- <path>` or
   delete if it was a new file), emit an advisory block describing which test(s) failed and
   which conversion rules were applied. The human reviewer can pick up from there.

## Step 5: Output report

Emit a structured report:

```text
## Port-tests report (mode: <port-pr|backfill>)

### Successfully ported
- tests/botocore_tests/unit/test_credentials.py
  - test_assume_role_refresh (new in 1.42.89)
  - test_sso_token_fetch (new in 1.42.89)
  - pytest: PASSED (2 new, 14 existing)

### Skipped (out of scope)
- botocore/tests/unit/test_awsrequest.py
  - Reason: no aiobotocore mirror at tests/botocore_tests/unit/test_awsrequest.py

### Failed — needs human review
- tests/botocore_tests/unit/test_signers.py
  - test_presigned_url_with_sigv4a: AssertionError on URL comparison
  - Conversion notes: converted `client.generate_presigned_url` to `await`; patched
    `aiobotocore.signers.generate_presigned_url`. Reverted write.
```

## Honesty

Never claim a test passes without actually running pytest on the ported file. If the botocore
clone isn't available or a tag is missing, return `error: <reason>` instead of attempting the
port. A falsely green port creates technical debt that's worse than not porting at all.

## Consumption

**Sync bot** (`botocore-sync-prompt.md` Step 5, port path): after `bump-version` lands the
version changes, invoke `/aiobotocore-bot:port-tests --from=$FROM --to=$TO`. Include the
resulting report in the port PR's "What changed in aiobotocore" section.

**Humans**: run `/aiobotocore-bot:port-tests --backfill --paths=test_foo.py,test_bar.py` to
narrow a coverage gap on specific files. The `--dry-run` flag is recommended first.
