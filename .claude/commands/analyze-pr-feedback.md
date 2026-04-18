---
description: Fetch all review threads + top-level PR comments and surface the ones that need Claude's attention
---

Fetch every piece of reviewer feedback on the PR — review threads AND top-level PR comments — then filter to the
items that actually need Claude to do something. Use this whenever you need to address reviewer feedback, either
proactively (auto-review on claude[bot] PRs) or in response to an `@claude` mention.

## Arguments

- `--focus=<databaseId>` (optional): the triggering comment's `databaseId`. If set, prioritize the thread
  containing this comment in your output and analysis. Other threads are still returned as context but ranked
  after the focused one.

If `--focus` is omitted, all surviving threads are returned unranked.

## Step 1: Fetch everything

Assumes `$REPO` and `$NUMBER` are set by the workflow environment.

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
      }
    }
  }' -F o=${REPO%/*} -F n=${REPO#*/} -F p=$NUMBER
```

## Step 2: Apply filters

Apply ALL of these in order. Skip any item that fails any one filter.

### Filter 1 — Author must be trusted

`authorAssociation` must be `MEMBER`, `OWNER`, or `COLLABORATOR`. Skip `NONE`, `FIRST_TIMER`,
`FIRST_TIME_CONTRIBUTOR`, `CONTRIBUTOR`, `MANNEQUIN`.

### Filter 2 — Last entry must NOT be claude[bot]

For review threads: check the last element of `comments.nodes` (sorted by `createdAt`).
For top-level PR comments: the comment itself is its own "thread"; check its author.

If `author.login == "claude"` AND `author.__typename == "Bot"`, skip — claude already replied last, reviewer has
the ball. (Remember: GraphQL strips `[bot]` suffix, so bare `"claude"` + `Bot` type is the match.)

### Filter 3 — Item must represent actionable work for Claude

An item is actionable if it asks Claude to do something concrete: change code, fix a bug, answer a specific
question about code, or modify the PR. Items that do NOT pass this filter:

- Comments addressed to other users (e.g. `@jakob-keller working on this...`) — human-to-human status chatter.
- Progress / status updates (e.g. "still working on this", "LGTM once CI passes", "thanks!").
- Praise, acknowledgements, or tangential discussion unrelated to code.
- Meta-review chatter (e.g. "let me re-review this later").
- Explicit skip / ignore directives (e.g. "ignore this, I'll handle it").

When in doubt, skip. A wrong reply from Claude is more disruptive than a missing one. Mention skipped items
briefly in whatever caller-side summary you produce instead of replying on-thread.

## Step 3: Output structured list

For each surviving item, emit:

```
- thread_id: <review thread id, or "top-level" for PR comments>
  path: <file path, or null for top-level>
  line: <line number, or null for top-level>
  parent_comment_id: <databaseId of the first comment — target for reply>
  last_comment_author: <login>
  last_comment_body: <first 200 chars>
  thread_url: <permalink>
  is_focus: <true if --focus matched this thread, else false>
```

If `--focus` was provided and matched a thread, sort that thread first. Otherwise output in `createdAt` order.

## What the caller does next

This command returns data only — no replies, no commits. The caller decides how to handle each item:

1. **Already addressed in a prior commit** → caller replies `Addressed in commit <short-SHA> — <permalink>` on
   the thread. No duplicate fix.
2. **Not yet addressed, confidence >= 80** → caller pushes a targeted signed commit via
   `mcp__github_file_ops__commit_files`, then replies with the new SHA and one-line explanation.
3. **Ambiguous or risky** → caller replies asking for clarification. No speculative fix.

Reply targets:
- Inline thread: `gh api repos/$REPO/pulls/$NUMBER/comments/$PARENT_COMMENT_ID/replies --method POST -f body=...`
- Top-level comment: `gh api repos/$REPO/issues/$NUMBER/comments --method POST -f body=...`
  (quote the original's first line in a blockquote so context is preserved).

Before posting any reply or pushing any commit, **read the current code state** with `git log -p -- <path>`,
`git blame`, and direct Read of the file. Do not assume the repo state matches what the reviewer saw when they
wrote the comment; a prior Claude run may have already fixed the issue.
