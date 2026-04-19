---
description: Use when classifying a botocore version bump as no-port, port-required, or ambiguous. Diffs `$FROM..$TO` in a bare botocore clone, inspects every added/changed function in overridden files, and emits `CLASSIFICATION:` plus per-function reasons. Shared by the sync bot (to pick no-port vs port path) and the PR reviewer (to sanity-check sync-bot PRs).
argument-hint: "--from=<version> --to=<version> [--aiobotocore-root=<path>] [--botocore-clone=<path>]"
allowed-tools: Bash(git -C /tmp/botocore:*) Bash(find:*) Bash(sed:*) Bash(cat:*) Bash(test:*) Bash(grep:*) Bash(uv run python:*)
---

Classify a botocore version bump for aiobotocore. Given a botocore tag
range, decide whether aiobotocore needs code changes (port-required),
just a version-bounds update (no-port), or the diff is non-trivial enough
to escalate (ambiguous).

The classifier answers: **"do aiobotocore overrides need to change to
stay correct?"** — where "correct" means aiobotocore's async flow
continues to work without runtime errors and without silently diverging
from botocore's behavior.

## Arguments

- `--from=<version>` (required): old botocore version tag, e.g. `1.42.84`
- `--to=<version>` (required): new botocore version tag, e.g. `1.42.89`
- `--aiobotocore-root=<path>` (optional, default `aiobotocore`): where to look for mirror files
- `--botocore-clone=<path>` (optional, default `/tmp/botocore`): path to the bare/working clone to diff in

Assume the clone already exists and has both tags fetched. If it does not, return `error: botocore clone
missing or tag unavailable` and stop — the caller is responsible for provisioning it.

## Step 1: Load the authoritative registries

Run the shared registry-emit script and parse its JSON output:

```text
uv run python plugins/aiobotocore-bot/scripts/registries.py
```

The payload has three fields:

- `overrides` — every botocore symbol aiobotocore has explicitly
  overridden (from `tests/test_patches.py`). Entries are either
  `ClassName.method_name` or bare function names or bare class names.
  Authoritative for "did we already override this exact symbol?".
- `async_methods` — every method name that `aiobotocore/**/*.py`
  defines as `async def`, plus a small curated set of sync-signature
  methods that delegate to async internals (e.g. `emit` delegating to
  `_emit`). Use for **duck-typed contamination**: a call `x.<name>(...)`
  on an unknown `x` is suspect if `<name>` is in this set.
- `aio_classes` — every `class AioX(...)` defined in aiobotocore. A new
  botocore call that instantiates or subclasses the non-Aio parent
  (e.g. `ClientCreator(...)` when `AioClientCreator` exists) needs to
  be routed through the Aio subclass.

Keep all three in working memory for Steps 3–4.

## Step 2: Enumerate the botocore diff

For each file in `$FROM..$TO` under `botocore/` that also has a mirror
in `aiobotocore/`:

```text
git -C $BOTOCORE_CLONE diff $FROM..$TO -- botocore/<path>
```

Scan each file's diff for changed/added/removed functions. For every
function identified, collect: name, change type (added / removed /
changed / renamed), and the relevant body lines.

`botocore/data/**/*.json` and non-Python files are out of scope —
schema/endpoint data is never ported.

## Step 3: Classify each function (apply the algorithm)

For each function `F`, execute:

### A. File scope

If `botocore/<F's file>` has no `aiobotocore/<same path>` mirror → skip
(inherited as-is by virtue of not being subclassed).

### B. Classify the change

- **Deleted** (removed from botocore, no replacement in diff): check
  whether aiobotocore still calls `F` anywhere. If yes → `port-required`
  (caller has to retarget). If no → `pure-sync` with reason
  "botocore deletion; aiobotocore does not reference".
- **Renamed** (paired `-def X` + `+def Y` with matching signature/body):
  `port-required` — any aiobotocore caller of `X` must update to `Y`.
- **Refactored** (body moved across functions): analyze each side's
  body for async contamination per Step C.
- **New** or **Changed**: go to Step C.

### C. Async-contamination check on F's body

Inspect the NEW body (for added/changed) or OLD body (for deleted):

1. **Network I/O**: does the body call network I/O primitives —
   `requests.*`, `urllib.*`, `http.client.*`, `socket.*`, anything
   that hits a remote endpoint? → contaminated. Note: file I/O
   (`open`, `os.fsync`, `tempfile`) is NOT a contamination signal —
   aiobotocore doesn't do async file I/O.
2. **Registry contamination**: does the body call a name in
   `async_methods`, or instantiate a class whose Aio-prefixed version
   is in `aio_classes`? → contaminated. Be strict about the match:
   `x.read(...)` on an unknown receiver is `ambiguous`; `self._emit(...)`
   or `self._client_creator(...)` where `_client_creator` is a known
   async factory is `port-required`.
3. **Registered override, substantive change**: if `F`'s dotted name
   is in `overrides` and the body change is anything other than
   pure-cosmetic (see #4 below) — **including a line removal, a call
   swap, a rename, a control-flow tweak, or a new guard** — flag
   `port-required`. The aiobotocore override MUST mirror the upstream
   body. The goal here is byte-level alignment with botocore, not
   whether the change affects async correctness: a registration-side-
   effect line removed upstream still has to be removed in the
   override to keep the sync diff minimal. "Cosmetic/minor refactor"
   is NOT a pass — if a code line was removed, added, or substituted,
   it's substantive.
4. **Cosmetic only**: docstring edits, pure whitespace/formatting, pure
   type-hint additions, import reorder — `pure-sync` with reason
   `cosmetic`. These bust hashes in `tests/test_patches.py` (mechanical
   bump only) but don't require a code port. If you're writing
   "removed a line / added a line but it's just cosmetic" that's a
   contradiction — removed/added code lines are substantive, not
   cosmetic.
5. **Nothing else matches**: `pure-sync`.

### D. Propagate up (call-graph back-track)

If `F` in Step C newly becomes `port-required` (i.e. it's now an async
function in aiobotocore's world), check the diff for callers:

- Any caller currently in `overrides` that now contains a new call to
  `F` (visible as an added line in the caller's body) → that caller is
  also `port-required` (needs to `await F` or the equivalent).
- Do NOT recursively invent back-track chains from class membership.
  Only flag callers where the new call appears as a concrete added line
  inside a registered-override body.

## Step 4: Output format — strict

**Output protocol — follow in this order:**

1. First, mentally work through each changed function and determine its
   verdict per Step 3. Do not write anything yet.
2. Apply the roll-up rule: if ANY per-function verdict is
   `needs-async` / `port-required`, the top-line MUST be
   `port-required`. If any is `ambiguous` and none are needs-async,
   the top-line is `ambiguous`. Only if EVERY verdict is `pure-sync`
   is the top-line `no-port`.
3. **Sanity check**: before you write the CLASSIFICATION line, confirm
   that the verdict on it is consistent with the per-function verdicts
   you're about to emit. If your detailed reasoning below arrives at
   `port-required` for any function but you're writing
   `CLASSIFICATION: no-port`, STOP — the top-line is wrong. Rewrite.
4. Now emit the response. The FIRST LINE must be
   `CLASSIFICATION: <verdict>` where `<verdict>` is one of `no-port`,
   `port-required`, or `ambiguous`. No preamble.

After the classification line, emit per-function detail:

```text
CLASSIFICATION: no-port | port-required | ambiguous

Summary: <N> functions inspected across <M> overridden files. <P> pure-sync, <Q> needs-async, <R> ambiguous.

## Per-function verdicts

### botocore/<file> → aiobotocore/<file>
- `<name>` (added|changed|removed|renamed): <verdict>
  Reason: <one-line justification naming the specific signal or absence thereof>
```

Roll-up rules:

- `no-port` iff every per-function verdict is `pure-sync`.
- `port-required` iff any verdict is `needs-async` (or `port-required`
  by any other path through the algorithm).
- `ambiguous` iff no `needs-async` but at least one `ambiguous` — the
  caller must resolve before treating as `no-port`.

## Honesty

Never claim `pure-sync` without inspecting the new body. If you could
not read a file (diff command failed, file missing), return
`error: <reason>` instead of guessing. Prefer `ambiguous` over a wrong
confident verdict — a false `pure-sync` on a function with async
contamination is how missed ports (#1126 class) slip through.

## Human-review territory

These patterns are hard for a static classifier to catch; emit
`ambiguous` and flag for human review when encountered:

- Context propagation through decorator chains (e.g. `with_current_context`).
- Callback identity bound at object construction time (e.g.
  `EPRBuiltins.ACCOUNT_ID = credentials.get_account_id` pattern).
- Async lifecycle semantics (session/client teardown, `__aenter__` /
  `__aexit__` contracts).

## Consumption

**Sync bot** (`botocore-sync-prompt.md`): runs this as the Step 3
classifier. If `no-port`, proceed to Step 4 (no-port path) and quote the
summary in the PR body. If `port-required`, go to Step 5 (bump path).
If `ambiguous`, escalate via Step 9 (feedback issue).

**Reviewer** (`review-pr` skill): runs this in Step 3d on sync-bot PRs,
extracting `$FROM` / `$TO` from the PR body. If the PR claims no-port
but this skill returns `port-required` or `ambiguous`, flag the
mismatch as high-confidence.
