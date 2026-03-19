---
description: Review a pull request sequentially to minimize token usage
---

Provide a code review for the given pull request.

Do NOT launch parallel subagents. Perform all steps
sequentially in this conversation to minimize cache
token costs.

## Step 1: Eligibility check

Check if any of the following are true:
- The pull request is closed
- The pull request is a draft
- The pull request does not need code review (e.g.
  automated PR, trivial change that is obviously correct)
- Claude has already commented on this PR (check
  `gh pr view <PR> --comments` for comments from claude)

If any condition is true, stop and do not proceed.
Note: Still review Claude-generated PRs.

## Step 2: Gather context

Get the list of file paths for all relevant CLAUDE.md
files: root CLAUDE.md and any in directories containing
modified files. Read them.

Get the PR diff: `gh pr diff <PR>`
Get PR metadata: `gh pr view <PR> --json title,body`

## Step 3: Review the changes

Review the diff yourself, sequentially checking for:

a) **CLAUDE.md compliance**: audit changes against the
   CLAUDE.md rules. Only consider rules that apply to
   the modified files' directories.

b) **Bugs in the diff**: scan for obvious bugs in the
   changed code only. Focus on:
   - Code that will fail to compile or parse
   - Clear logic errors producing wrong results
   - Security issues in the introduced code
   - Incorrect API usage or missing error handling

c) **Async patterns** (aiobotocore-specific): check that
   any botocore overrides follow the patterns in
   `docs/override-patterns.md`:
   - Sync methods properly converted to async
   - Proper use of `resolve_awaitable()`
   - Resource cleanup via async context managers
   - Correct Aio prefix naming

**CRITICAL: Only flag HIGH SIGNAL issues.**

Flag issues where:
- Code will fail to compile or parse
- Code will definitely produce wrong results
- Clear CLAUDE.md violations you can quote

Do NOT flag:
- Code style or quality concerns
- Potential issues depending on specific inputs
- Subjective suggestions or improvements
- Issues a linter will catch
- Pre-existing issues not introduced in the PR
- General quality issues unless in CLAUDE.md
- Issues silenced by lint ignore comments

## Step 4: Validate findings

For each issue you found, verify it yourself:
- Is this actually a bug, or does it look like one?
- Is the CLAUDE.md rule actually being violated?
- Would a senior engineer flag this?

Score each issue 0-100:
- 0: false positive
- 25: might be real, can't verify
- 50: real but minor/nitpick
- 75: very likely real and important
- 100: definitely real, confirmed

Filter out anything below 80.

## Step 5: Post results

If `--comment` argument was NOT provided, output to
terminal and stop.

If `--comment` IS provided and NO issues >= 80, post:

---

### Code review

No issues found. Checked for bugs and CLAUDE.md compliance.

🤖 Generated with [Claude Code](https://claude.ai/code)

---

If issues >= 80 were found, post inline comments using
`mcp__github_inline_comment__create_inline_comment`
with `confirmed: true`:
- Brief description of the issue
- Small fixes: include committable suggestion block
- Large fixes: describe without suggestion block
- ONE comment per unique issue, no duplicates

When linking to code:
`https://github.com/OWNER/REPO/blob/FULL_SHA/path#L1-L5`
- Must use full 40-char SHA
- Must use `#L` notation with line range
- At least 1 line of context before and after
