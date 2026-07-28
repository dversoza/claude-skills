---
name: pr-feedback
description: Address PR review feedback including inline threads, general comments, bot-generated reviews, PR description findings, check run annotations (inline lint/type/security findings shown in the Files changed tab), code scanning alerts, and CI failures. Use when asked to handle, address, resolve, respond to, or work through PR review comments, review feedback, inline findings, or CI check failures. Triggers on requests like "address PR comments", "handle review feedback", "resolve PR reviews", "fix review comments", "go through PR feedback", "check CI failures", "fix the inline findings on the PR".
---

# PR Feedback

Fetch all review feedback and CI status from the PR associated with the current branch, triage each item, implement fixes, and propose responses.

All commands auto-detect the repository and PR from the current branch.

## Step 1: Fetch All Review Feedback

Run all four fetch commands to collect every feedback surface:

```bash
python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py threads      # unresolved inline review threads
python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py ci           # CI check status and failures
python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py comments     # general PR comments, review bodies, PR description
python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py annotations  # check run annotations and code scanning alerts
```

If any fails (no PR for current branch, auth issues), report the error and stop.

`threads` returns `unresolved_threads` with thread_id, path, line, and comments (each with node_id, database_id, diff_hunk).

`ci` returns `ci_summary` (pass/fail/pending counts) and `failed_checks` (with run_id, job_id for log fetching). On a failed fetch it returns empty counts plus an `error` field -- see Step 3.

`comments` returns `pr_body` (may contain bot-appended review content), `pr_comments` (each with node_id, database_id), `review_bodies` (top-level bodies of submitted reviews -- where CodeRabbit, Copilot and similar bots post their summary), and `pr_author`.

`annotations` returns `annotations` (check run annotations) and `code_scanning_alerts`. Run it on every PR -- see Step 3 for why a green CI does not mean there are no annotations.

## Step 2: Triage and Process Review Comments

### Inline Review Threads

For each unresolved thread, read the file at the commented path and lines for context. Read the entire thread (including replies) to understand the final ask. Classify into one of three actions:

**Implement** when the comment is:
- A clear, unambiguous fix: bug, typo, missing import, null check, off-by-one
- A code style correction aligned with project conventions or linter rules
- Missing error handling at a system boundary
- A simple rename, refactor, or dead code removal with obvious improvement

**Dismiss** (with concise, respectful explanation) when:
- A stylistic preference not backed by project conventions or linter rules
- Suggesting over-engineering or premature abstraction
- Factually incorrect about what the code does
- Already addressed in a subsequent commit (verify via git log)
- Out of scope for the PR

**Ask** (escalate to user) when:
- The suggestion is ambiguous or has multiple valid interpretations
- It involves an architectural or design trade-off
- It requires domain or business knowledge to evaluate
- The scope extends significantly beyond the PR
- You are genuinely uncertain whether the concern is valid

When in doubt between Implement and Ask, prefer Ask.

### General PR Comments

Process comments from `pr_comments` that contain actionable review feedback. Skip CI status messages, merge bot noise, and other non-review content. Look for:
- Bot-generated code reviews (Greptile, CodeRabbit, etc.)
- Human reviewer comments left at the PR level rather than inline
- Specific concerns, suggestions, or questions that warrant a response

Triage these using the same Implement / Dismiss / Ask criteria. Since these are not tied to specific lines, read the relevant files mentioned in the comment for context.

### PR Description Bot Content

Scan `pr_body` for bot-appended review sections. Common patterns:
- Greptile: sections titled "Greptile Summary", "Greptile Overview", or containing "Confidence Score"
- Other bots: sections with "must-fix", "findings", "suggestions"

Extract actionable items and triage them. Pay particular attention to "must-fix" items -- they indicate merge-blocking concerns from the bot's perspective.

Flag any findings that are factually incorrect (hallucinations). These need correction in the response phase.

### Review Bodies

Process `review_bodies` the same way as general PR comments. These are the top-level bodies of submitted reviews, which are a different surface from `pr_comments` -- a bot review summary usually lands here, not in the comments list. `state` tells you how seriously to weigh it: `CHANGES_REQUESTED` is blocking, `COMMENTED` is advisory, `APPROVED` bodies are usually just a sign-off and can be skipped unless they raise a concern.

## Step 3: Address Check Run Annotations

These are the inline findings GitHub renders in the Files changed tab -- lint violations, type errors, security findings, and similar -- attached to a line of code rather than to a review comment. They are invisible to `threads`, `comments`, and `ci`, so they must be read from `annotations`.

Do not skip this step when CI is green. A check run can report `conclusion: success` while still carrying `failure`-level annotations, so `ci_summary.failed == 0` says nothing about whether annotations exist. Triage on the annotation contents, never on the check's pass/fail bucket.

Each annotation carries `check_run_name`, `annotation_level` (`failure`, `warning`, or `notice`), `path`, `start_line`, `title` (e.g. `ruff (TC002)`), `message`, and `in_files_changed`.

Split them on `in_files_changed` before deciding anything:

**`in_files_changed: true`** -- the annotation is on a file this PR touches. Treat it as in scope and fix it, subject to the same Implement / Dismiss / Ask criteria as review comments. A `failure`-level annotation on a changed file is the highest-priority item in the whole run.

**`in_files_changed: false`** -- the annotation is on a file the PR never touched, so it is almost always a pre-existing violation that the linter reports repo-wide. Do not fix these. Fixing them inflates the diff with unrelated changes and makes the PR harder to review. Report them in the summary as pre-existing, and let the user decide whether they want a separate cleanup PR.

Two details worth knowing:

GitHub Actions attaches a location-less annotation with the message `Process completed with exit code N` and a synthetic path like `.github`. It is a restatement of the job's exit status, not a finding. Ignore it; the real findings are the other annotations from the same check run. It is kept in the output so the count matches the `annotations_count` GitHub reports.

When several annotations share a `title` and `message` across many files, they are one rule firing repeatedly. Fix the ones in changed files together in a single pass and describe them as one item in the summary rather than listing each occurrence.

### Code Scanning Alerts

`code_scanning_alerts.alerts` holds GitHub code scanning (CodeQL and other SARIF uploads) results for the PR, each with `severity`, `rule_id`, `path`, and `message`. Treat anything at `high` or `critical` severity as merge-blocking and raise it even if the user did not ask about security.

If `code_scanning_alerts.available` is `false`, read `reason` and mention it once, then move on -- it is a capability gap, not a finding:
- `Advanced Security must be enabled` -- code scanning is not turned on for this repository. Nothing to fetch.
- `not authorized to read code scanning alerts` -- the token lacks the `security_events` scope. The user can grant it with `gh auth refresh -h github.com -s security_events`.

## Step 4: Address CI Failures

If `ci_summary.failed` is 0, skip this step. This does not let you skip Step 3 -- annotations are independent of the check's pass/fail bucket.

If `ci` returned an `error` field, no check data was retrieved. An empty result does not mean the build is clean, so never report it as passing. `no checks reported on the '<branch>' branch` means the PR genuinely has no CI configured -- say so and move on. Anything else is a fetch failure: report it verbatim and treat CI status as unknown.

For each entry in `failed_checks`, fetch the failed job logs:

```bash
gh run view {run_id} --log-failed 2>&1 | tail -200
```

Diagnose each failure and classify:

**Fix** when:
- A test failure caused by code changes in this PR
- A lint or formatting error introduced by this PR
- A pre-commit hook failure on files changed in this PR

**Skip** (with explanation) when:
- A flaky test unrelated to the PR's changes
- An infrastructure or CI configuration issue (timeout, runner failure, dependency fetch error)
- A pre-existing failure that also occurs on the base branch

When fixing, read the relevant test file and source file to understand the failure, then apply the fix.

## Step 5: Present Summary

After processing all feedback, annotations, and CI failures, present results grouped by action:

1. **Implemented** -- each change with file path, line, and what was done
2. **Dismissed** -- each with the explanation
3. **Needs input** -- each with your questions
4. **Annotations fixed** -- each with check run name, rule, file, and line
5. **Pre-existing annotations** -- those outside the changed files, grouped by rule with a file count, noted as not fixed
6. **Code scanning alerts** -- each with severity and rule, or one line stating why they were unavailable
7. **CI fixes** -- each failure with diagnosis and what was fixed
8. **CI skipped** -- each with why it was skipped

Wait for user review of code changes before proceeding to Step 6.

## Step 6: Propose Responses

After the user approves the code changes, propose a response plan. Present the full plan and wait for approval before executing any of it.

### For Implemented Threads
- Resolve the thread: `python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py resolve THREAD_ID`
- Optionally reply with a brief note about the fix: `python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py reply DATABASE_ID "Fixed: ..."`

### For Dismissed Threads
- Reply with the dismissal explanation: `python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py reply DATABASE_ID "Explanation..."`
- Or add a thumbs-up if acknowledged but no change needed: `python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py react review DATABASE_ID`
- Skip replying if the comment is clearly noise (bot false positive)

`react` takes an optional `--content` (`+1`, `-1`, `laugh`, `confused`, `heart`, `hooray`, `rocket`, `eyes`), defaulting to `+1`. Use `--content eyes` for items in the Ask bucket: it signals the comment was seen and is pending a decision, which `+1` wrongly reads as agreement.

### For General PR Comments and PR Body Findings

Draft a single follow-up PR comment addressing multiple non-threaded items together:
```bash
python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py comment "Response text..."
```

Use this for:
- Responding to bot reviews with corrections for hallucinated findings
- Providing missing context that reviewers may not have
- Pointing reviewers to the right code if they misread the implementation
- Summarizing what was fixed vs. what was intentionally left as-is

To react to a general PR comment: `python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py react issue DATABASE_ID`

## Guidelines

- Process threads in file order to keep edits coherent.
- Annotations have no thread to resolve and no one to reply to. They clear on their own when the check re-runs against the fix, so the only action they need is the code change.
- When multiple comments touch the same file, read the file once and process them together.
- Do not commit changes or post responses automatically. Present everything for user review first.
- Outdated threads still deserve attention -- the underlying concern may still apply. Flag them as outdated in the summary.
- If a thread has back-and-forth discussion, focus on the latest unresolved ask.
- Respect the codebase's project instructions (CLAUDE.md) when evaluating comments.
- When drafting response text, keep it factual and concise. Do not be defensive or dismissive.
- When correcting hallucinated findings, be specific: quote what the reviewer claimed, explain what actually happens, and point to the relevant code.
- The skill may be run repeatedly on the same PR as review rounds continue. A thread stays unresolved until someone resolves it, so a thread handled on an earlier run will appear again. Before acting on one, check whether the newest comment is already your reply or the PR author's: if so, the ask was likely answered and the thread is only awaiting the reviewer. Re-state it in the summary rather than implementing the same change twice.
