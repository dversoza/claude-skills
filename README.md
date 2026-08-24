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
| [concise-writing](concise-writing/) | Simplified Technical English (ASD-STE100) style with per-artifact budgets, enforced by a blocking PreToolUse hook |
| [backlog-grooming](backlog-grooming/) | Groom a Jira or Asana backlog against the codebase and git history; propose-only |
| [1password](1password/) | Secure `op` CLI read patterns that keep secrets out of the transcript |
| [skill-creator](skill-creator/) | Guide for writing and updating skills |


## backlog-grooming

Reads a whole backlog, determines each ticket's *real* status from the code and
git history rather than the ticket text, and produces a prioritized grooming
report. Propose-only: it never mutates the tracker without an explicit approval
pass.

Extraction is provider-specific; everything after it is not. An adapter writes a
canonical dataset and the analysis reads only that, so adding a tracker means
adding an adapter rather than editing the engine.

### Providers

| Provider | Interface | Done-but-open detection |
|----------|-----------|-------------------------|
| jira | `acli`, with a browser-session REST fallback | reliable, keys appear in commits |
| asana | the `asana` CLI from this repo | degraded, gids never appear in commits |

Asana task gids do not show up in commit messages, so the detector that finds
shipped-but-open tickets falls back to permalink and keyword search there. The
skill reports when it ran degraded rather than presenting the two as equivalent.

### Usage

    python3 ~/.claude/skills/backlog-grooming/scripts/extract_jira.py \
      --project VD --workdir ~/code/vd-grooming --rich open

    python3 ~/.claude/skills/backlog-grooming/scripts/extract_asana.py \
      --project <PROJECT_GID> --workdir ~/code/asana-grooming --rich open

Both accept `--max-rich N` to bound a first pass; skipped keys are always
reported. `references/dataset.md` defines the contract an adapter must satisfy.

### Requirements

- Python 3.6+, `jq`, `gh` for the PR evidence step
- Jira: `acli` authenticated (`acli jira auth status`)
- Asana: `uv tool install ~/.claude/skills/asana/cli` then `asana setup --token ...`


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
    asana tasks create --name "..." [--notes ...] [--parent GID] [--project GID] [--assignee me] [--due YYYY-MM-DD]
    asana tasks update --gid GID [--name ...] [--due ...] [--add-project GID] [--add-follower GID] [--complete]
    asana tasks reorder --parent GID --order gid1,gid2,gid3
    asana api --path /PATH [--method GET|POST|PUT|PATCH] [--query k=v ...] [--data '<json>']

`asana api` is an escape hatch to any Asana REST endpoint (metadata, sections,
stories, search); `DELETE` requires an explicit `--allow-delete`.

### Requirements

- Python 3.9+ and `uv`
- An Asana Personal Access Token (https://app.asana.com/0/my-apps)


## concise-writing

Enforced writing style for everything the assistant produces: PR descriptions, commits, code comments, reviews, tickets, chat messages, and docs. Adapts ASD-STE100 (Simplified Technical English) structural rules and adds hard per-artifact budgets (PR body <=150 words, commit subject <=50 chars, review comments 1-2 lines, no narration code comments, no emojis or em dashes).

Unlike advisory style instructions, a PreToolUse hook (`scripts/concise_check.py`) blocks the offending tool call (`git commit`, `gh pr create`, `Edit`/`Write`, ticket and chat MCP tools) and returns the violation list, forcing a rewrite before the action executes. See the skill's Installation section for the settings.json wiring.

Rule sources and citations live in `references/writing-rules.md`; worked before/after rewrites in `examples/before-after.md`. The ASD-STE100 dictionary is not redistributable and is not included.

### Requirements

- Python 3.6+ (hook script, stdlib only)
- Hook entries in `~/.claude/settings.json` (snippet in `SKILL.md`)
