---
description: Use at the end of every aiobotocore-bot workflow run to post the summary reply and swap the 👀 reaction for 👍. Handles the event-dependent target (inline-thread reply vs top-level PR/issue comment) and scopes reaction deletion to claude[bot] so human 👀 reactions aren't lost.
argument-hint: "--event=EVENT --number=N [--comment-id=ID] [--summary=TEXT] [--skip-reply] [--repo=OWNER/REPO]"
allowed-tools: Bash(gh api:*)
---

End-of-run cleanup: posts the summary reply to the correct target based on the triggering event,
then swaps the 👀 reaction you added at run start for a 👍 to signal completion. Use instead of
hand-rolling the two `gh api` blocks — the event-vs-comment-vs-PR target logic is easy to get wrong.

## Arguments

- `--event=<event_name>` (required): `$EVENT_NAME` from the workflow, one of `pull_request`,
  `pull_request_review`, `pull_request_review_comment`, `issue_comment`, `issues`.
- `--number=<n>` (required): `$NUMBER` — PR or issue number.
- `--comment-id=<id>` (optional): `$COMMENT_ID` — required for comment-triggered events.
- `--summary=<body>` (optional, required unless `--skip-reply`): summary text to post.
- `--skip-reply` (optional): reaction swap only. Use from `pull_request` flows where
  the `review-pr` skill already posted its own review comment.
- `--repo=<owner/repo>` (optional): defaults to `$REPO`.

## Step 1: Post summary reply

Skip if `--skip-reply` is set. Pick the target by event:

- `pull_request_review_comment`: reply in the **same inline thread** so the discussion stays
  attached to the code:

  ```text
  gh api repos/$REPO/pulls/$NUMBER/comments/$COMMENT_ID/replies \
    --method POST -f body='<summary>'
  ```

- `pull_request_review` or `issue_comment` (on a PR): post as a top-level PR comment via the
  issues endpoint (works for PR conversation too):

  ```text
  gh api repos/$REPO/issues/$NUMBER/comments \
    --method POST -f body='<summary>'
  ```

- `issues`: top-level issue comment via the same endpoint as above.

## Step 2: Swap reaction

Delete the 👀 (`eyes`) reaction and add 👍 (`+1`). Target depends on whether `--comment-id` is set:

The jq filter scopes the deletion to the bot's own reaction so a human
coincidentally reacting with 👀 doesn't lose their reaction when the run
completes.

**Comment events** (`--comment-id` set):

```text
gh api repos/$REPO/issues/comments/$COMMENT_ID/reactions \
  --jq '.[] | select(.content == "eyes" and .user.login == "claude[bot]") | .id' \
  | xargs -I{} gh api repos/$REPO/issues/comments/$COMMENT_ID/reactions/{} --method DELETE
gh api repos/$REPO/issues/comments/$COMMENT_ID/reactions \
  --method POST -f content=+1
```

**PR events** (no `--comment-id`):

```text
gh api repos/$REPO/issues/$NUMBER/reactions \
  --jq '.[] | select(.content == "eyes" and .user.login == "claude[bot]") | .id' \
  | xargs -I{} gh api repos/$REPO/issues/$NUMBER/reactions/{} --method DELETE
gh api repos/$REPO/issues/$NUMBER/reactions \
  --method POST -f content=+1
```

GitHub's reactions API does not support a literal checkmark, so `+1` is used as the "done" marker.

## Honesty

If the 👀 reaction isn't found (workflow failed to add it, or a prior run already swapped), post
the 👍 anyway and proceed — missing the delete is not an error worth blocking on.
