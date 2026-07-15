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
| [pr-feedback](pr-feedback/) | Fetch and address PR review feedback, CI failures, and bot-generated reviews |
| [postgres](postgres/) | Read-only PostgreSQL querying, schema introspection, and query planning |
| [asana](asana/) | Asana task CRUD (list/get/create/update) via an installable `asana` CLI |


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


## asana

Task CRUD against the Asana REST API through an installable `asana` CLI. Exposes
list, get, create, and update only -- no delete. All output is JSON; all
arguments are named. The CLI has zero third-party dependencies (Python stdlib).

Unlike the other skills, this one ships a `uv`-installable package under `cli/`:

    uv tool install ~/.claude/skills/asana/cli
    asana setup --token <PERSONAL_ACCESS_TOKEN>

`setup` stores the token in `~/.config/asana-cli/config.env` (mode 0600); an
`ASANA_ACCESS_TOKEN` env var overrides it.

### Subcommands

    asana whoami
    asana workspaces
    asana projects [--workspace GID] [--limit N]
    asana tasks list --assignee me [--include-completed] [--limit N]
    asana tasks list --project GID [--include-completed] [--limit N]
    asana tasks get --gid GID [--fields a,b,c]
    asana tasks create --name "..." [--notes ...] [--project GID] [--assignee me] [--due YYYY-MM-DD]
    asana tasks update --gid GID [--name ...] [--due ...] [--add-project GID] [--complete]
    asana api --path /PATH [--method GET|POST|PUT|PATCH] [--query k=v ...] [--data '<json>']

`asana api` is an escape hatch to any Asana REST endpoint (metadata, sections,
stories, search); `DELETE` requires an explicit `--allow-delete`.

### Requirements

- Python 3.9+ and `uv`
- An Asana Personal Access Token (https://app.asana.com/0/my-apps)
