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
3. **Registered override, substantive change**: this rule has a hard
   prerequisite — apply it ONLY after the following gate passes:

   **GATE — is `F` an aiobotocore-tracked override?**

   Scan the `overrides` list for an exact string match of:
   - `F`'s full dotted name (e.g. `ClientArgsCreator.get_client_args`), OR
   - `F`'s bare name when `F` is a module-level function (e.g.
     `_apply_request_trailer_checksum`).

   A bare class name in `overrides` (e.g. `URLLib3Session` with no
   dot) does NOT match any of that class's methods. Class-level
   tracking in `test_patches.py` only means the class's full source
   is hashed for detecting upstream edits; it says NOTHING about
   whether aiobotocore overrides any specific method. The only
   thing that makes `URLLib3Session._get_pool_manager_kwargs` a
   tracked override is `URLLib3Session._get_pool_manager_kwargs`
   appearing verbatim in `overrides`.

   If the gate fails — i.e. you cannot point to the exact string in
   `overrides` — rule #3 does NOT apply. Do not substitute reasoning
   about "parity", "divergence", "silent behavior drift", or "keeping
   in sync". Those concerns are about the broader project philosophy,
   not about this verdict. Proceed to the other Step-C rules; the
   function may still be flagged via rule #1 (network I/O) or rule #2
   (calls something in `async_methods` / `aio_classes`). Absent any
   of those, it's `pure-sync` — aiobotocore inherits it unchanged.

   If the gate PASSES, then: the body change is `port-required` if
   it's anything other than pure-cosmetic (see #4) — including a line
   removal, a call swap, a rename, a control-flow tweak, or a new
   guard. aiobotocore's override file has to be updated to match the
   new upstream body. "Cosmetic/minor refactor" is NOT a pass here —
   if a code line was removed, added, or substituted inside a tracked
   override, it's substantive.

   **STOP-RULE**: if your draft rationale contains phrases like
   "ClassName is in overrides, method is a method of ClassName,
   therefore..." or "even though the method isn't overridden, we
   should flag for parity/divergence" — ABORT that reasoning. Return
   to the gate. If the exact name isn't in `overrides`, rule #3 is
   not applicable regardless of how philosophically appealing a
   port-required verdict feels.
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

## Step 4: Output format

**When invoked via the eval harness**: the caller forces a structured
tool call (`record_async_need_classification`). Reason through each
changed function in text (quote the exact `overrides` / `async_methods`
/ `aio_classes` match for every port-required verdict), then call the
tool ONCE with `verdict`, `summary`, and `per_function_verdicts`. The
tool call is the authoritative output.

**When invoked directly (human via slash command or agent without tool
schema)**: emit the following plain text at the END of your response,
after reasoning:

```text
## Per-function verdicts

### botocore/<file> → aiobotocore/<file>
- `<name>` (added|changed|removed|renamed): <verdict>
  Reason: <one-line justification naming the specific signal or absence thereof>

Summary: <N> functions inspected across <M> overridden files. <P> pure-sync, <Q> needs-async, <R> ambiguous.

CLASSIFICATION: no-port | port-required | ambiguous
```

Either way, the roll-up rule: any function port-required → top-line
port-required; any ambiguous and none port-required → ambiguous; every
function pure-sync → no-port.

### HASH-BUMP IS NOT A PORT CONCERN

A recurring trap: when an upstream change will cause a
`tests/test_patches.py` hash to change, the temptation is to flag
port-required because "the hash will fail." That is WRONG.

`tests/test_patches.py` hashes get bumped mechanically as part of every
sync (port or no-port). A hash change is a build consequence, not a
classification signal. If the only reason you can articulate for
`port-required` is "the hash will break" or "the test will fail", the
correct verdict is NOT port-required — it's whatever the async-need
rules say (typically `pure-sync`, with a hash bump done by the caller
mechanically during sync).

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
