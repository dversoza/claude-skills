# claude-skills

A collection of reusable skills for extending coding assistants.

## Installation

Each skill is a self-contained directory with a `SKILL.md` file and supporting scripts. To install a skill, copy or symlink its directory into `~/.claude/skills/`:

```bash
# Clone this repo
git clone <repo-url> ~/claude-skills

# Symlink the skill you want
ln -s ~/claude-skills/SKILL_NAME ~/.claude/skills/SKILL_NAME
```

The assistant will automatically detect skills placed in `~/.claude/skills/` on the next session.


## Skills

| Skill | Description |
|-------|-------------|
| [1password](1password/) | Secure 1Password CLI (op) access patterns for secret retrieval without leakage |
| [postgres](postgres/) | Read-only PostgreSQL querying, schema introspection, and query planning |
| [pr-feedback](pr-feedback/) | Fetch and address PR review feedback, CI failures, and bot-generated reviews |
| [skill-creator](skill-creator/) | Guide for creating effective skills with scaffolding, validation, and style patterns |


## 1password

Guidance skill that teaches the assistant how to securely read secrets from 1Password using the `op` CLI. No scripts -- it provides security rules, patterns, and anti-patterns that prevent secret leakage into conversation context, terminal output, or environment variables.

Credentials are resolved from 1Password **private links** (right-click an item > "Copy Private Link"). The skill parses the link's URL parameters (`a`=account, `v`=vault, `i`=item) to construct `op read` commands, so no accounts or vault names need to be hardcoded.

### Requirements

- [1Password CLI](https://developer.1password.com/docs/cli/) (`op`) v2+
- At least one authenticated 1Password account (`op account list`)


## pr-feedback

Fetches all review feedback and CI status from a GitHub PR, triages each item, and guides through implementing fixes and proposing responses.

The skill auto-detects the repository and PR from the current branch. It works with any GitHub repository accessible via `gh` CLI.

### Subcommands

Fetch (read-only):

    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py threads    # unresolved inline review threads
    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py ci         # CI check status and failures
    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py comments   # general PR comments and PR description

Actions (write):

    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py resolve THREAD_ID           # mark thread as resolved
    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py react review DATABASE_ID     # thumbs-up on inline comment
    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py react issue DATABASE_ID      # thumbs-up on general comment
    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py reply DATABASE_ID "text"     # reply to a review thread
    python3 ~/.claude/skills/pr-feedback/scripts/pr_feedback.py comment "text"               # leave a general PR comment

All commands accept optional `--repo OWNER/REPO` and `--pr NUMBER` flags to override auto-detection.

### Workflow

The skill follows a structured workflow when invoked:

1. Fetch all feedback (threads, CI, comments) in parallel
2. Triage each item as implement, dismiss, or escalate
3. Apply fixes for items classified as implement
4. Present a summary grouped by action taken
5. After user approval, propose and execute responses (resolve threads, post replies)

### Requirements

- `gh` CLI authenticated with access to the target repository
- Python 3.6+


## postgres

Read-only access to PostgreSQL databases via `psql`. Enforces `default_transaction_read_only=on` at the PostgreSQL session level so writes and DDL are rejected by the server itself.

Database connection URIs are configured per-project in `CLAUDE.local.md` (gitignored) inside a `` ```pg-databases``` `` fenced code block. The script resolves aliases internally so credentials never appear on the command line.

### Subcommands

    python3 ~/.claude/skills/postgres/scripts/pg_query.py list
    python3 ~/.claude/skills/postgres/scripts/pg_query.py schemas <alias>
    python3 ~/.claude/skills/postgres/scripts/pg_query.py tables <alias> [--schema NAME]
    python3 ~/.claude/skills/postgres/scripts/pg_query.py describe <alias> <table>
    python3 ~/.claude/skills/postgres/scripts/pg_query.py indexes <alias> <table>
    python3 ~/.claude/skills/postgres/scripts/pg_query.py query <alias> "SELECT ..."
    python3 ~/.claude/skills/postgres/scripts/pg_query.py explain <alias> "SELECT ..."

### Requirements

- `psql` (PostgreSQL client tools)
- Python 3.6+
- Database aliases in a `pg-databases` block in the project's `CLAUDE.local.md`


## skill-creator

Meta-skill that guides the assistant through building effective skills. Provides a structured creation workflow (understand, plan, scaffold, edit, iterate) along with reference material on proven patterns.

### Scripts

    python3 ~/.claude/skills/skill-creator/scripts/init_skill.py <name> --path <dir>    # scaffold a new skill
    python3 ~/.claude/skills/skill-creator/scripts/quick_validate.py <skill-dir>         # validate frontmatter and structure

### References

- `references/style-guide.md` -- patterns for frontmatter descriptions, script design, subcommand docs, guidelines sections, output style, and triage workflows
- `references/workflows.md` -- sequential and conditional workflow patterns
- `references/output-patterns.md` -- template and example patterns for output formatting

### Requirements

- Python 3.6+
- `pyyaml` (for `quick_validate.py`)
