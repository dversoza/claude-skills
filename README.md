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
| [opensearch](opensearch/) | Read-only OpenSearch/Elasticsearch querying, index introspection, and search DSL execution |
| [redis](redis/) | Read-only Redis / ElastiCache querying, key inspection, and cache debugging |
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

Fetches all review feedback and CI status from a GitHub PR, triages each item, implements fixes, and reports back with a response plan. It runs as a subagent and never commits, pushes, or posts.

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
4. Report back grouped by action taken, with the verification results
5. Include a response plan (resolve threads, post replies) that the caller runs after user approval

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


## opensearch

Read-only access to OpenSearch and Elasticsearch clusters via the REST API. Only read endpoints are issued (`_cluster/health`, `_cat/indices`, `_mapping`, `_count`, `_search`, `GET _doc`), so the tool cannot write by construction.

Cluster URLs are configured per-project in `CLAUDE.local.md` (gitignored) inside a `` ```opensearch-clusters``` `` fenced code block. Each line is `alias=url` plus optional options: `tls=skip` turns off certificate verification for a self-signed cert; `aws=<profile>/<domain>` repairs a stale endpoint for an AWS-managed domain by re-deriving its ENI IPs with the AWS CLI, probing them, and rewriting the alias host with the one that answers. The script resolves aliases internally so credentials never appear on the command line.

    ```opensearch-clusters
    local=http://localhost:9200
    dev=https://admin:PASSWORD@opensearch.internal.example:443 tls=skip
    prod=https://admin:PASSWORD@10.0.1.2:443 tls=skip aws=prod/my-domain
    ```

### Subcommands

    python3 ~/.claude/skills/opensearch/scripts/os_query.py list
    python3 ~/.claude/skills/opensearch/scripts/os_query.py health <alias>
    python3 ~/.claude/skills/opensearch/scripts/os_query.py indices <alias> [--pattern 'logs-*']
    python3 ~/.claude/skills/opensearch/scripts/os_query.py mapping <alias> <index> [--field NAME]
    python3 ~/.claude/skills/opensearch/scripts/os_query.py count <alias> <index> [--query '{"query":{...}}']
    python3 ~/.claude/skills/opensearch/scripts/os_query.py search <alias> <index> '{"query":{...}}' [--size N] [--source f1,f2] [--explain]
    python3 ~/.claude/skills/opensearch/scripts/os_query.py get <alias> <index> <doc_id>

All subcommands accept `--no-verify-tls` and `--timeout SECONDS`.

### Requirements

- Python 3.6+ (stdlib only)
- Network access to the cluster
- `aws` CLI with an authenticated profile, only for `aws=` endpoint rediscovery
- Cluster aliases in an `opensearch-clusters` block in the project's `CLAUDE.local.md`

## redis

Read-only access to Redis / ElastiCache instances. Every command passes through a single choke point that checks the resolved command name, and its subcommand for container commands like `CONFIG` or `CLIENT`, against a hard allowlist of read-only commands. Anything else is rejected before a connection is opened. This is client-side enforcement; for a hard guarantee connect as a Redis ACL user limited to `+@read`.

Instance URLs are configured per-project in `CLAUDE.local.md` (gitignored) inside a `` ```redis-instances``` `` fenced code block. Each line is `alias=url` plus optional `tls=skip` (managed nodes addressed by IP) and `db=N` options. The script resolves aliases internally so credentials never appear on the command line; with the `redis-cli` backend the password travels through `REDISCLI_AUTH`.

### Subcommands

    python3 ~/.claude/skills/redis/scripts/redis_query.py list
    python3 ~/.claude/skills/redis/scripts/redis_query.py info <alias> [--section memory] [--full]
    python3 ~/.claude/skills/redis/scripts/redis_query.py dbsize <alias>
    python3 ~/.claude/skills/redis/scripts/redis_query.py scan <alias> '<pattern>' [--limit N] [--type hash]
    python3 ~/.claude/skills/redis/scripts/redis_query.py get <alias> <key> [--limit N]
    python3 ~/.claude/skills/redis/scripts/redis_query.py ttl <alias> <key>
    python3 ~/.claude/skills/redis/scripts/redis_query.py memory <alias> <key>
    python3 ~/.claude/skills/redis/scripts/redis_query.py slowlog <alias> [--limit N]
    python3 ~/.claude/skills/redis/scripts/redis_query.py clients <alias> [--limit N]
    python3 ~/.claude/skills/redis/scripts/redis_query.py cmd <alias> [--db N] LLEN celery

`scan` is cursor-based and never issues `KEYS`; `get` is type-aware and reads large collections with `HSCAN`/`SSCAN`. All subcommands accept `--db N`, `--timeout SECONDS`, and `--backend {auto,redis-py,redis-cli}`.

### Requirements

- Python 3.6+
- The `redis` Python package or `redis-cli` on PATH (auto-detected)
- Instance aliases in a `redis-instances` block in the project's `CLAUDE.local.md`


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
