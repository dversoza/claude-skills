---
name: asana
description: >-
  Create, read, and update Asana tasks from the command line. Use whenever the
  user wants to interact with Asana: list their tasks or a project's tasks, look
  up a task by id or link, create a new task, update a task's title, notes, due
  date, assignee, or project membership, or mark a task complete. Triggers on
  requests like "what's on my Asana", "list my Asana tasks", "create an Asana
  task for X", "add a task to project Y", "update the due date on that task",
  "mark this Asana task done", "show me the tasks in <project>". Wraps the Asana
  REST API in an `asana` CLI. Deliberately does not delete tasks.
---

# Asana

Task CRUD against the Asana REST API through the installed `asana` CLI. Every
subcommand prints JSON to stdout; errors print `{"error": ...}` to stderr and
exit non-zero. There is intentionally no delete: only list, get, create, update
(completing a task is an update).

## Setup

The CLI installs as a `uv` tool and exposes an `asana` command:

```bash
uv tool install ~/.claude/skills/asana/cli   # or --force to reinstall after edits
asana setup --token <PERSONAL_ACCESS_TOKEN>  # optionally --workspace <GID>
```

Create a Personal Access Token at https://app.asana.com/0/my-apps. If the token
lives in 1Password, source it without exposing it in shell history:

```bash
asana setup --token "$(op read -n 'op://<vault>/<item>/token')"
```

`setup` writes the token to `~/.config/asana-cli/config.env` (mode `0600`) and
verifies it by calling `/users/me`. Confirm auth anytime with `asana whoami`.

Token resolution order (first hit wins): `ASANA_ACCESS_TOKEN` env var, then the
config file. So a token exported in the shell (e.g. from `.zshrc`) overrides the
stored one.

If `asana: command not found`, the uv tool bin dir is not on `PATH`; run
`uv tool install ~/.claude/skills/asana/cli` (uv installs into `~/.local/bin`).

## Subcommands

```bash
asana whoami                                    # authenticated user + workspaces
asana workspaces                                # accessible workspaces
asana projects [--workspace GID] [--limit N]    # projects in a workspace

asana tasks list --assignee me [--include-completed] [--limit N]   # my tasks
asana tasks list --project GID [--include-completed] [--limit N]   # a project's tasks
asana tasks get --gid GID [--fields a,b,c]

asana tasks create --name "..." [--notes "..."] [--project GID]
                   [--workspace GID] [--assignee me] [--due YYYY-MM-DD]

asana tasks update --gid GID [--name "..."] [--notes "..."] [--due YYYY-MM-DD]
                   [--assignee me] [--add-project GID] [--remove-project GID]
                   [--complete | --incomplete]

# Escape hatch: call any Asana REST endpoint not covered above
asana api --path /PATH [--method GET|POST|PUT|PATCH] [--query k=v ...] [--data '<json>']
```

All arguments are named (no positional args). Task ids are passed with `--gid`.

Workspace resolution for commands that need one: `--workspace` flag,
`ASANA_WORKSPACE_GID` env var, config file, then auto (used automatically when
the token has access to exactly one workspace — the common case).

## Working with tasks

Discover ids before creating or filtering. A task is a "ticket"; its `gid` is
the numeric id in its URL (`.../task/<gid>`). To find a project's gid, run
`asana projects`. To find your own tasks, `asana tasks list --assignee me`.

`asana tasks list` excludes completed tasks by default; pass
`--include-completed` to include them.

Field selection uses Asana `opt_fields`. Defaults cover the common case; pass
`--fields` to narrow or widen (e.g. `--fields name,due_on,assignee.name`,
or dotted paths like `--fields "projects.name,memberships.section.name"`).

Updates are sparse — only the flags you pass change. Clear the due date with
`--due ''`. Project membership is managed with `--add-project` /
`--remove-project` (Asana does not let you set `projects` via a plain update).

Completing a task is `asana tasks update --gid GID --complete`. This is the
safest way to "remove" a task from active lists; the CLI never hard-deletes.

## Any endpoint (`asana api`)

For anything the dedicated commands don't cover — project/section/user/team
metadata, custom fields, subtasks, comments (stories), search — use the raw
`api` command. It prints the full response envelope (including `next_page`).

```bash
asana api --path /projects/1201234/tasks --query opt_fields=name,completed --query limit=5
asana api --path /tasks/1201234/stories --query opt_fields=text,created_by.name
asana api --path /tasks --method POST --data '{"name":"New task","workspace":"1200000000000000"}'
```

`--data` takes the inner JSON object; it is sent wrapped as `{"data": ...}` (the
Asana convention). `--query` is repeatable. `DELETE` is refused unless
`--allow-delete` is also passed. See `references/api.md` for endpoints and
`opt_fields` semantics.

## Extending the skill

The full REST reference — endpoints, request/response shapes, pagination,
`opt_fields` semantics, and gotchas — is in `references/api.md`. Read it before
adding new subcommands (comments/stories, sections, custom fields, subtasks,
attachments, search). Add new commands in `cli/asana_cli/cli.py` following the
existing `cmd_*` pattern, then `uv tool install --force ~/.claude/skills/asana/cli`.
