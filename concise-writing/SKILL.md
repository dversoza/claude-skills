---
name: concise-writing
description: "Enforced concise style for everything the assistant writes: PR descriptions, commit messages, code comments, PR reviews and replies, tickets, chat messages, and docs. Simplified Technical English (adapted from ASD-STE100) plus hard per-artifact budgets, enforced by a blocking PreToolUse hook (scripts/concise_check.py). Use when writing or rewriting any of these artifacts, when a hook denial asks for a rewrite, or on requests like 'make this concise', 'STE rewrite', 'less verbose'."
---

# Concise Writing (STE + budgets)

Verbose generated text is a review tax: reviewers skim PR descriptions, tickets, and comments, and padding hides the substance. This skill makes objectiveness and simplified technical English enforced, not advisory. A hook denial means the text is too long or off-style: shorten it. Never loosen the validator, never bypass with CONCISE_SKIP unless the user explicitly instructs it.

## Source and scope

Adapted from the asd-ste100 skill (https://github.com/danyuchn/asd-ste100-skill), which encodes the rule categories of ASD-STE100 Issue 9 (Jan 2025): 53 writing rules across 9 sections, backed by a dictionary of ~900 approved words (one meaning, one part of speech each). The dictionary is not free to redistribute and is not reproduced here; request it at https://www.asd-ste100.org/STE_downloads.html. Apply the underlying principle instead: pick the plainest common word and use it the same way every time.

Read before rewriting anything non-trivial:
- `references/writing-rules.md` - the 9 rule sections summarized, with citations and official links.
- `examples/before-after.md` - worked rewrites showing which rule each change applies, including the modality trap (see below).

## Mode

STE defines two modes. Strict (every rule, for procedures, error messages, tool descriptions, safety text) and STE-flavored (structural rules only, for prose). For everyday engineering artifacts, always use STE-flavored: enforce the structural rules fully, treat one-word-one-meaning as a direction, keep some lexical range in prose.

## Structural rules (apply always)

| Rule | Do | Don't |
|---|---|---|
| Active voice | "The task retries." | "The task is retried." (unless the actor is unknown or irrelevant) |
| No phrasal verbs (Rule 9.3) | "Start the job." "Remove the panel." | "Spin up the job." "Take off the panel." |
| One instruction per sentence | "Open the file. Read line 3." | "Open the file and read line 3, then check it." |
| Sentence length | <=20 words for instructions, <=25 for descriptions | Compound and subordinate-clause chains |
| No semicolons (Rule 8.1) | Split into two sentences | Any semicolon |
| Noun clusters | <=3 stacked nouns | "high pressure fuel pump inlet valve assembly" |
| No ellipsis | Keep subject, verb, article explicit | Dropping words to save space |
| Verb, not noun (Rule 3.7) | "Analyze the log." | "Perform an analysis of the log." |
| One name per thing | Always "the agency" | Rotating "the agency"/"the tenant"/"the client" |
| Simple tenses | "We received the report." | "We have received the report." (keep the compound form only when it carries information, e.g. "may have failed") |
| Paragraphs | One topic, <=6 sentences | Multi-topic walls |
| Lists | Numbered/bulleted list for 3+ steps or conditions | A sequence buried in one prose sentence |

## Scan checklist (six mechanical habits)

Scan for all six before rewriting; each points at an exact word or mark, no judgment call:

1. Synonym rotation - one thing gets several names. Pick one name.
2. Hedge stacking - "it is important to note that this may potentially help". State the claim or delete it.
3. Nominalization - "perform an analysis of". Use the verb.
4. Marketing adjectives - comprehensive, robust, seamless, crucial, cutting-edge, effortless, blazing. Delete, or replace with the measurement that earns the claim.
5. Run-on sentences - ideas joined by semicolons or dashes. One idea per sentence.
6. Soft phrasal verbs - spin up, reach out, dive into, kick off. Use start, contact, read, begin.

## Modality (the most common rewrite failure)

Hedges carry the author's confidence, and confidence is content. "May have failed" never becomes "failed"; "could be caused by X" never becomes "X is the cause". A shorter sentence that upgrades a hedge to a fact is a different claim, not a simplification. Never add a cause, frequency, or mechanism the source did not state. When the tense rule and the modality rule conflict, modality wins. See Example B in `examples/before-after.md`.

Corollary: stop at unambiguous, not at shortest. Never drop a scope qualifier, condition, or number to save words. Delete hollow sentences instead of polishing them; STE fixes form, not substance.

## Hard budgets (hook-enforced)

The validator (`scripts/concise_check.py`, installed at `~/.claude/hooks/concise_check.py`) blocks the action when these are exceeded. Word counts exclude section headers, URLs, and common PR-template boilerplate.

| Artifact | Budget | Hook surface |
|---|---|---|
| Commit | conventional `type(scope): subject`; subject <=50 chars imperative (trailing `[AKT-NNNN]` excluded); body optional, <=6 lines x 72 chars; no Co-Authored-By, "Claude Code", "Generated with" | `git commit` (-m, -F, heredoc) |
| PR / issue body | keep your repo's PR template sections; <=150 words; <=6 bullets; no nesting; no bold | `gh pr create/edit`, `gh issue create` |
| PR review summary | <=3 sentences | `gh pr review`, `gh api .../reviews` |
| Inline review / PR / issue comment | 1-2 lines, <=60-80 words | `gh pr comment`, `gh issue comment`, `gh api` comments[] |
| Linear ticket description | <=120 words | `mcp__linear__save_issue` |
| Linear comment | <=2 sentences | `mcp__linear__save_comment` |
| Slack message | <=100 words | `mcp__claude_ai_Slack__slack_send_message*` |
| Code comment | only a constraint the code cannot show; one line <=100 chars; never narration ("this ensures", "we now", "note that"); <=20% of added lines once 3+ comments; docstrings follow project format, no extra prose | `Edit`/`Write` on code files, added lines only |
| Docs / README / .md | style only (no emoji, no em/en dash, headers <=###, <=3 bold spans); no length cap; earn every paragraph | `Edit`/`Write` on .md/.mdx/.rst, added lines only |
| Everywhere | no emojis; no em or en dashes, use "-" | all surfaces |

Not gated (style still applies): chat output (covered by `~/.claude/output-styles/concise.md`), files under `~/.claude/`, scratchpad, non-code non-doc extensions.

## On a hook denial

1. Read the listed violations.
2. Cut content, not precision: drop restatements, background the reader has, and anything the diff already shows. Keep Linear URLs, ticket IDs, and reviewer-critical warnings (breaking change, migration, deploy step) as one line each.
3. Retry the same action with the shortened text. Do not switch to an ungated path to avoid the check.

## Installation

1. Copy or symlink this directory into `~/.claude/skills/concise-writing/`.
2. Copy `scripts/concise_check.py` to `~/.claude/hooks/concise_check.py`.
3. Add PreToolUse hooks to `~/.claude/settings.json` (merge with existing keys):

```json
"hooks": {
  "PreToolUse": [
    {"matcher": "Bash", "hooks": [
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "if": "Bash(git commit*)", "timeout": 10},
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "if": "Bash(gh pr *)", "timeout": 10},
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "if": "Bash(gh issue *)", "timeout": 10},
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "if": "Bash(gh api *)", "timeout": 10}
    ]},
    {"matcher": "Edit|Write", "hooks": [
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "timeout": 10}
    ]},
    {"matcher": "mcp__linear__save_issue|mcp__linear__save_comment|mcp__linear__save_document|mcp__linear__submit_diff_review", "hooks": [
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "timeout": 10}
    ]},
    {"matcher": "mcp__claude_ai_Slack__slack_send_message|mcp__claude_ai_Slack__slack_send_message_draft|mcp__claude_ai_Slack__slack_create_canvas|mcp__claude_ai_Slack__slack_update_canvas", "hooks": [
      {"type": "command", "command": "python3 ~/.claude/hooks/concise_check.py", "timeout": 10}
    ]}
  ]
}
```

The script fails open on internal errors and skips files under `~/.claude/`,
scratchpad paths, and non-code non-doc extensions. Adjust the budget constants
at the top of the script to your team's numbers; do not loosen them to make one
output pass.
