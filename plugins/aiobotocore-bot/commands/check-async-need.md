---
allowed-tools: Bash(git -C /tmp/botocore:*), Bash(ls:*), Bash(cat:*), Bash(test:*)
description: Classify new/changed botocore functions in overridden files as pure-sync, needs-async, or ambiguous
---

<!--
Tool note: Step 3's async-def lookup inside `aiobotocore/<file>.py` uses the Read tool, not
Bash, so no Bash(cat aiobotocore/*) entry is needed in allowed-tools above. Only the botocore
clone and filesystem-mirror checks go through Bash.
-->

Given a botocore version range, inspect every new or changed function in botocore files that have a mirror in
`aiobotocore/` and return a structured verdict for each. This is the shared async-need classifier used by both
the sync bot (to decide relax vs bump and justify it) and the PR reviewer (to independently sanity-check a
sync-bot PR).

The classifier answers: **"would aiobotocore need to override this function going forward?"** It does NOT
answer "did we already override it". The relevant distinction is async-worthiness of the new code, not the
historical override status.

## Arguments

- `--from=<version>` (required): old botocore version tag, e.g. `1.42.84`
- `--to=<version>` (required): new botocore version tag, e.g. `1.42.89`
- `--aiobotocore-root=<path>` (optional, default `aiobotocore`): where to look for mirror files
- `--botocore-clone=<path>` (optional, default `/tmp/botocore`): path to the bare/working clone to diff in

Assume the clone already exists and has both tags fetched. If it does not, return `error: botocore clone
missing or tag unavailable` and stop — the caller is responsible for provisioning it.

## Step 1: Enumerate overridden files

Use the filename-mirror convention: `botocore/foo.py` is overridden iff `aiobotocore/foo.py` exists. Do not
grep for references.

```text
ls $AIOBOTOCORE_ROOT/*.py | xargs -n1 basename
```

Call this set `OVERRIDDEN`. Any changed botocore file not in this set is out of scope for the classifier —
skip it.

## Step 2: Enumerate changed functions in overridden files

For each file `f` in `OVERRIDDEN` that appears in the diff:

```text
git -C $BOTOCORE_CLONE diff $FROM..$TO -- botocore/$f
```

Identify every function (top-level `def` / `async def` and class methods) that was:

- **added** (new `def` that did not exist at `$FROM`)
- **changed** (body differs, not just whitespace/comments/docstrings)

For a changed function, compare the new body against the old body — the relevant unit is the **delta**, not
the whole function. If a new branch adds `requests.get(...)` to an otherwise-pure function, that's a
needs-async verdict even if the rest of the function was fine.

Pure-docstring, pure-type-annotation, and pure-import reordering changes are not interesting — classify as
`pure-sync` with reason `cosmetic`.

## Step 3: Classify each function

For each added/changed function, inspect the **new code** for these signals:

**needs-async** — any of:

- Calls that perform network or disk I/O: `requests.*`, `urllib.*`, `http.client.*`, `socket.*`,
  `open(...)` for real files, `subprocess.*`, anything that hits the filesystem or network
- Calls to functions that aiobotocore already makes async — check `aiobotocore/$f` for an `async def`
  version (match by method name within the subclassed class)
- Instantiation or use of a botocore class that aiobotocore subclasses (search `aiobotocore/` for
  `class Aio<Name>(<Name>)` — if `<Name>` is instantiated in the new code, the override chain must
  extend to this new caller)
- Blocking primitives: `time.sleep`, `threading.*`, `queue.Queue.get` without timeout, `concurrent.*`
- New event-hook handler registrations that may be fed awaitables downstream (handlers registered for
  events that aiobotocore resolves via `resolve_awaitable`)

**pure-sync** — none of the above; the new code is dict/list/str manipulation, attribute access, regex,
arithmetic, or calls to other pure-sync botocore utilities.

**ambiguous** — calls an unfamiliar helper whose body you cannot inspect within this command run, OR the
call graph is deep enough that you can't rule out I/O without tracing. Prefer `ambiguous` over a wrong
confident verdict; the caller will escalate.

## Step 4: Output

Emit a structured report. Two sections: a summary line the caller can grep, followed by per-function detail.

```text
CLASSIFICATION: relax-safe | bump-required | ambiguous

Summary: <N> functions inspected across <M> overridden files. <P> pure-sync, <Q> needs-async, <R> ambiguous.

## Per-function verdicts

### botocore/handlers.py  →  aiobotocore/handlers.py
- `_set_sigv4a_signing_context` (added): pure-sync
  Reason: only dict manipulation and a call to `_resolve_sigv4a_region`, itself pure (attribute access and
  dict lookups).
- `_set_auth_scheme_preference_signer` (changed): pure-sync
  Reason: new call to `_set_sigv4a_signing_context`; the called helper is pure-sync (see above).

### botocore/<otherfile>.py  →  aiobotocore/<otherfile>.py
- `<function>` (added|changed): <verdict>
  Reason: <one-line justification, naming the specific signal or absence thereof>
```

The top-level `CLASSIFICATION` is:

- `relax-safe` if and only if every verdict is `pure-sync`
- `bump-required` if any verdict is `needs-async`
- `ambiguous` if there are no `needs-async` verdicts but at least one `ambiguous` (caller must resolve
  before concluding relax)

## Consumption

**Sync bot** (`botocore-sync-prompt.md`): runs this in Step 3. If `CLASSIFICATION: relax-safe`, proceed to
Step 4 (relax path) and quote the summary in the PR body as the async-need justification. If `bump-required`,
go to Step 5 (bump path). If `ambiguous`, go to Step 9 (feedback issue) with the ambiguous verdicts as the
questions.

**Reviewer** (`review-pr.md`): runs this in Step 3d for sync-bot-authored PRs, extracting `$FROM` / `$TO`
from the botocore diff URL in the PR body. If the PR claims relax but this command returns `bump-required`
or `ambiguous`, flag the mismatch as a high-confidence review issue.

## Honesty

Never classify as `pure-sync` without having read the new code. If you could not read a file (diff command
failed, file missing), return `error: <reason>` instead of guessing. A false `pure-sync` verdict on a
function with I/O would let a bad relax ship.
