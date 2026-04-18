---
description: Fetch every PR comment + reply (including resolved), synthesize the discussion, and produce a grouped action plan
---

Fetch every piece of reviewer feedback on the PR — review threads and top-level PR comments, including resolved
ones — then synthesize the discussion as context and produce a grouped action plan. Use this whenever you need
to address reviewer feedback, either proactively (auto-review on claude[bot] PRs) or in response to an `@claude`
mention.

**Do not process threads in isolation.** Read the entire comment history first, including threads that are
already resolved or already ended with a claude[bot] reply, to build a coherent picture of what the reviewer
cares about. A single comment often makes no sense without the surrounding thread; a thread often makes no
sense without the PR-wide context of prior discussions. Only after synthesis should you decide actions.

## Arguments

- `--focus=<databaseId>` (optional): the triggering comment's `databaseId`. If set, the thread containing this
  comment is guaranteed to appear in the action plan (assuming it passes filters) and is ranked first. Other
  items are still included — the focus doesn't narrow scope, it only prioritizes.
- `--resolve` (optional): after posting a "fixed" or "already-addressed" reply on a thread, call the GraphQL
  `resolveReviewThread` mutation to mark the thread resolved. Opt-in only — never resolve automatically.
  Do NOT resolve threads where the outcome was `ask-clarification` (the reviewer still has the ball).

## Step 1: Fetch everything

Assumes `$REPO` and `$NUMBER` are set by the workflow environment. Include resolved threads too — they are
context even though you won't act on them.

```
gh api graphql -f query='
  query($o:String!, $n:String!, $p:Int!) {
    repository(owner:$o, name:$n) {
      pullRequest(number:$p) {
        reviewThreads(first:100) {
          nodes {
            id isResolved isOutdated path line
            comments(first:50) {
              nodes {
                databaseId
                author { login __typename }
                authorAssociation
                createdAt
                body
                url
              }
            }
          }
        }
        comments(last:100) {
          nodes {
            databaseId
            author { login __typename }
            authorAssociation
            createdAt
            body
            url
          }
        }
        reviews(last:50) {
          nodes {
            state
            body
            author { login __typename }
            authorAssociation
            submittedAt
          }
        }
      }
    }
  }' -F o=${REPO%/*} -F n=${REPO#*/} -F p=$NUMBER
```

## Step 2: Synthesize the discussion into three buckets

Read every thread, every reply, and every top-level comment in chronological order. **Do not act on anything
yet.** Build an internal synthesis with these three explicit buckets — this is the core output of the
synthesis step:

### Bucket A — What was asked

Every concrete request from trusted reviewers across the full PR history, including requests that have
already been addressed. Group related asks together (e.g. "three comments about CHANGES.rst convention"
becomes one grouped ask).

### Bucket B — What was done

For each ask in Bucket A, record what has already happened in response:
- A claude[bot] reply explaining what was done (or why not) + the commit SHA.
- A commit on the branch that addresses the ask, even without an explicit reply.
- The reviewer acknowledged the fix (`isResolved: true`, or a follow-up comment like "thanks, fixed").
- Nothing yet.

Use `git log -p -- <path>` and `Read` of the affected files to verify — do not assume the response matches
what the reviewer asked for; confirm the current code state.

### Bucket C — What is being asked that isn't resolved

The set difference: items from Bucket A where Bucket B is empty, or where the response didn't fully
address the ask. These are the candidates for action.

Filter Bucket C to what's actually actionable for Claude:

1. **Trusted author** — `authorAssociation` is `MEMBER`, `OWNER`, or `COLLABORATOR`. Others go to context,
   never to action.
2. **Not engaged by claude** — the most recent comment in the thread is not from `claude[bot]`
   (`author.login == "claude" && author.__typename == "Bot"`). If claude already replied last, the
   reviewer has the ball — belongs in Bucket B, not C.
3. **Actionable work for Claude** — the ask is a concrete code change, bug fix, code-level answer, or
   modification. Skip:
   - Comments addressed to other users (e.g. `@jakob-keller working on this...`) — human-to-human chatter.
   - Progress / status updates, praise, tangential discussion.
   - Meta-review chatter or explicit skip directives ("ignore this, I'll handle it").

   When in doubt, skip and note the item in the summary instead of acting. A wrong reply is more disruptive
   than a missing one.

Bucket C items are what become actions. Multiple items are common on a PR with real back-and-forth — plan
for multiple actions in one run, grouped where related.

## Step 4: For each action, pick an outcome

Before acting on any item, **read the current code state** with `git log -p -- <path>` and `Read` of the
file. The repo may already contain the fix from a prior Claude run; don't duplicate it.

Three outcomes per action (or per group of related actions):

1. **Already addressed in a prior commit** — reply `Addressed in commit <short-SHA> — <permalink>` on each
   relevant thread. No new commit. If `--resolve` was passed, resolve the thread.
2. **Not yet addressed, confidence >= 80** — push one targeted signed commit via
   `mcp__github_file_ops__commit_files` that addresses the whole group. **Before posting the reply, validate
   the commit** — run `uv run pre-commit run --all --show-diff-on-failure` at minimum, and any task-specific
   tests relevant to the change (e.g. `uv run pytest tests/test_patches.py -x` for botocore overrides). Only
   after validation passes, reply on each relevant thread with the new SHA and a one-line explanation. If
   `--resolve` was passed, resolve the thread. If validation fails, fix forward or downgrade to
   `ask-clarification`.
3. **Ambiguous or risky** — reply asking for clarification. No speculative fixes. Do NOT resolve the thread
   even if `--resolve` was passed.

To resolve a thread (GraphQL mutation — only when all of: `--resolve` flag AND outcome is 1 or 2 AND the
post-commit validation passed):
```
gh api graphql -f query='
  mutation($tid: ID!) {
    resolveReviewThread(input: {threadId: $tid}) {
      thread { isResolved }
    }
  }' -F tid=$THREAD_ID
```
`$THREAD_ID` is the `id` field (not `databaseId`) from the `reviewThreads.nodes` entry.

## Step 5: Reply targets

- **Inline review thread**: reply in the same thread so discussion stays attached to the code line:
  ```
  gh api repos/$REPO/pulls/$NUMBER/comments/$PARENT_COMMENT_ID/replies \
    --method POST -f body='…reply text…'
  ```
  `$PARENT_COMMENT_ID` is the first comment's `databaseId` in that thread's `comments.nodes`.

- **Top-level PR comment**: reply as a new top-level comment that quotes the original's first line so context
  is preserved:
  ```
  gh api repos/$REPO/issues/$NUMBER/comments \
    --method POST -f body='> <reviewer>: <quoted first line>\n\n…reply text…'
  ```

## Output contract

Before you act on anything, write out an explicit plan with these sections. Keep it structured — this is
your internal checklist, and the final top-level summary reply renders from the same data.

### Replies needed

For each item in Bucket C (after filters), list:
```
- thread_url: <permalink>
  kind: <"review_thread" | "top_level_comment">
  parent_comment_id: <databaseId to use as the reply target>
  path: <file path or null>
  line: <line number or null>
  last_author: <login>
  last_comment_preview: <first 200 chars>
  planned_outcome: <"already-fixed" | "fix-now" | "ask-clarification">
  planned_commit_group: <group name, or null if no commit planned>
```

This is the explicit "which comments need to be replied to" list. After acting, every entry here MUST
have had a reply posted. Before exiting, re-run a check of the form "for each parent_comment_id in my
plan, did I POST a reply?" to catch any skipped items.

### Commit groups

For each planned `fix-now` group, list:
```
- group: <short name>
  threads: [<list of thread_urls this group addresses>]
  change_summary: <one-line description of what the commit will do>
```

One commit per group; multiple threads can share a group.

### Final top-level summary

When posting the run-level summary reply (via the parent prompt's Cleanup section), include:

- **Discussion summary** (1–3 sentences): what the reviewer has raised, what patterns emerged across
  resolved + unresolved history.
- **Bucket A — What was asked**: the full list, grouped.
- **Bucket B — What was done**: per ask, the commit SHA or the resolution. Include items done in prior
  runs, not just this one.
- **Bucket C — What's still outstanding**: per ask, the planned action or "awaiting reviewer clarification".
- **Per-thread replies**: permalinks to every reply you posted this run.

This way the reviewer can scan one comment and understand the state of every discussion thread, rather
than chasing N separate replies.
