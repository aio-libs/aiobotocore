---
description: Use when reviewing an aiobotocore PR that touches `aiobotocore/*.py` files with a botocore mirror. Compares each added line against the matching botocore function and emits `OVERRIDE_DRIFT:` (`clean` | `cosmetic-drift` | `behavioral-drift`) plus per-function detail. Distinguishes legitimate async gaps from unmatched additions that widen the sync diff.
argument-hint: "(--pr=<number> | --diff=<path>) [--botocore-path=<path>]"
allowed-tools: Bash(gh pr diff:*) Bash(gh pr view:*) Bash(python3 -c:*) Bash(ls:*) Bash(grep:*) Bash(cat:*)
---

aiobotocore exists to mirror botocore as closely as possible, with `async` sprinkled on
top. The principle is:

> **Unmatched behavioral changes to overridden code should be avoided. Legitimate
> async gaps (e.g. a `threading.Lock` → `asyncio.Lock` because async needs it) are
> acceptable — those are the whole reason aiobotocore exists. Everything else that
> widens the diff from botocore without an async justification is drift.**

Every line of divergence makes future syncs harder and hides subtle behavioral
differences. This skill evaluates changes to aiobotocore files that have a botocore
mirror and flags:

- **Behavioral changes** to overridden code that aren't in the matching botocore (e.g.
  swapping `inspect.isawaitable` for `hasattr(obj, "__await__")` — not equivalent for
  all awaitable types).
- **Cosmetic additions** (docstrings, comments, type hints, log statements, null
  guards) that aren't in the matching botocore — these widen the diff without an
  async-explained justification.
- **Silent bug fixes** in overridden code — may be legitimate but still drift; must
  be called out so reviewers know.

Changes that *are* OK and should not be flagged — the "async-gap exceptions":

- Additions that mirror the same addition in the matching botocore version (i.e. an
  upstream sync that happens to land here).
- Divergences explicitly required by async semantics: `await`, `asyncio.Lock`
  replacing `threading.Lock`, `async with` / `async for`, `Aio*` class use,
  `resolve_awaitable` on something that may be awaitable in aiobotocore but isn't
  in botocore.
- Changes to aiobotocore-only code (files without a botocore mirror, e.g.
  `httpxsession.py`, functions in `_helpers.py` that have no botocore counterpart).
- Fixing a genuine async-only bug — if aiobotocore's async version of a function had
  a race, missing `await`, or broken cancellation and the fix has no counterpart in
  botocore because botocore simply can't have the bug. State this explicitly in the
  PR description.

**Not OK**, even if the change looks like an improvement:

- Null guards, error logging, docstrings, type hints, refactors — if they don't
  exist in the matching botocore, they're drift. The right path for "improvements
  to sync code" is a PR upstream to botocore, then a sync here.

## Arguments

- `--pr=<number>` (required unless `--diff=<path>`): PR to analyze via `gh pr diff`.
- `--diff=<path>` (optional): local diff file (e.g. `git diff > /tmp/d`); overrides `--pr`.
- `--botocore-path=<path>` (optional): location of installed botocore source. If omitted,
  discover via `python3 -c "import botocore; print(botocore.__file__)"`.

## Step 1: Identify overridden files touched in the diff

Parse the diff for modified `aiobotocore/*.py` paths. For each, check whether a
botocore mirror exists at `$BOTOCORE_PATH/<same-name>.py`. Files without a mirror
(e.g. `httpxsession.py`) are out of scope — aiobotocore-only code can diverge freely.

## Step 2: For each changed function in an overridden file

Identify the function name (by scanning the hunk for enclosing `def`/`async def`).
Read the matching function in botocore. If the function does not exist in botocore —
skip it (new aiobotocore-only helper; unusual but not in scope for drift checking).

**Authoritative override registry**: `tests/test_patches.py` enumerates every
botocore symbol aiobotocore overrides. If the changed function's dotted name
(`ClassName.method`) or bare name is NOT in that file, it is not a tracked
override — either it's new code aiobotocore is adding without a counterpart
(which should be flagged separately in Step 3) or it's a passthrough not under
drift-check scope. Use the registry to distinguish "tracked override we expect
to mirror botocore" from "something else".

## Step 3: Categorize each added/changed line

For every `+` line in the hunk (inside a function that has a botocore counterpart):

**OK** — do not flag:

- Line is identical to a line in the matching botocore function.
- Line is `async def` / `await X` / `async with X` / `async for X` — async-required
  syntax.
- Line uses a known async wrapper: `resolve_awaitable(`, `AsyncExitStack`,
  `asyncio.Lock`, `asyncio.Event`, `asyncio.sleep`, etc.
- Line instantiates or references an `Aio<Name>` class that subclasses a botocore
  class (the Aio prefix is the async marker).

**cosmetic-drift** — flag at medium confidence:

- Docstring additions (`"""..."""`) that aren't in botocore's version of the
  function. Exception: docstrings that specifically describe async-only behavior
  (mention of awaiting, event loop, coroutines) are acceptable.
- Type-hint additions on parameters / return that aren't in botocore.
- Import reordering / PEP8 cleanup not present upstream.
- Comment additions not in botocore.

**behavioral-drift** — flag at high confidence:

- Change to control flow, conditionals, function calls that differs from botocore
  AND is not async-required.
- Replacement of a stdlib call with a different stdlib call (even "equivalent" ones
  — e.g. `inspect.isawaitable(x)` vs `hasattr(x, "__await__")` — these have
  different semantics for some objects).
- Added guards (`if x is None: ...`), added logging, added error handling that
  aren't in botocore. May be legitimate bug fixes but must be called out.
- Removed logic that exists in botocore.

## Step 4: Output

Emit a structured report.

```text
OVERRIDE_DRIFT: clean | cosmetic-drift | behavioral-drift

## Per-function verdicts

### aiobotocore/_helpers.py :: resolve_awaitable
- verdict: behavioral-drift
- changes not in matching botocore:
  - line 12: `if hasattr(obj, '__await__'):` replaces `if inspect.isawaitable(obj):`
    (different semantics for some awaitable types — not a safe rewrite).
- async-explained: no

### aiobotocore/configprovider.py :: AioSmartDefaultsConfigStoreFactory.merge_smart_defaults
- verdict: cosmetic-drift
- changes not in matching botocore:
  - added docstring (lines 8-14): describes behavior that matches botocore; no
    async-specific content.
  - added return type annotation `-> None`.
- async-explained: no
```

The top-line `OVERRIDE_DRIFT` rolls up:

- `clean` — every change is either mirrored in botocore or async-explained.
- `cosmetic-drift` — only cosmetic drift present; no behavioral changes.
- `behavioral-drift` — at least one function has a behavioral change not in botocore.

## Consumption by the reviewer

The `review-pr` skill runs this in Step 3 for every non-sync PR that touches
`aiobotocore/*.py` files with botocore mirrors. Verdicts map to comment severity:

- `behavioral-drift` → post as a high-confidence inline comment at the offending line,
  quoting the specific line pair (aiobotocore vs botocore).
- `cosmetic-drift` → post as a single top-level soft comment listing the drift, with
  a note that cosmetic changes to overrides widen sync diffs and should be justified.
- `clean` → no comment.

The reviewer does NOT block a PR on cosmetic drift — maintainers may choose to accept
it. But every cosmetic/behavioral drift must be visible in review; silent acceptance
is how divergence accumulates.

## Honesty

Never claim `clean` without having read the matching botocore function. If the
botocore source is unreachable (no local install, no path override provided), return
`error: could not locate botocore source` and exit — the reviewer will fall back to
visual inspection and flag the run as needing human review.
