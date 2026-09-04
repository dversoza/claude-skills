# Provider: Asana

Uses the `asana` CLI from this repo. Install it with
`uv tool install ~/.claude/skills/asana/cli` and authenticate with
`asana setup --token <PERSONAL_ACCESS_TOKEN>`.

## Read this before grooming an Asana project

The engine's strongest signal is matching a ticket key against git history to
find work that shipped but stayed open. **Asana task gids never appear in commit
messages.** Nobody types `1204316564525896` into a commit subject. The gid is
therefore useless as a git-evidence handle, and `meta.json` sets
`keyPattern: null` for this provider.

What is left is weaker: grep PR bodies and commit messages for task permalinks
(`urlPattern`), and lean much harder on feature-keyword search through the code.
Expect fewer `CLOSE_DONE` findings and lower confidence on the ones you get. Say
so in the report rather than presenting an Asana pass as equivalent to a Jira
one.

Also check the project is actually a software backlog. Many Asana projects are
CRM or ops boards whose sections are categories, not workflow states. Grooming
one against a codebase produces nothing. If `statusVocabulary` in `meta.json`
reads like sectors or teams rather than states, stop and tell the user.

## Check access

    asana whoami
    asana workspaces
    asana projects --workspace <WORKSPACE_GID> --limit 100

## Extract

    python3 scripts/extract_asana.py --project <PROJECT_GID> --workdir "$WORKDIR" --rich open

Gotchas the script handles:

`asana tasks list` defaults to `--limit 50` and paginates only up to that limit,
so the default silently truncates any real backlog. The script passes 1000 and
warns when the returned count equals the limit, which usually means there is
more.

`asana api` returns the raw `{"data": ...}` envelope while the typed subcommands
unwrap it. The script normalizes both.

`/tasks/<gid>/stories` mixes real comments with audit events
(`added_to_project`, `section_changed`, `name_changed`). Only
`resource_subtype == "comment_added"` is a human comment; the rest buries the
decision you are looking for.

`notes` arrives with the list call, so rich extraction only needs the comments
request. This makes Asana cheaper to extract than Jira.

## Mapping to the canonical dataset

Asana has no status field. `status` is the task's section name, falling back to
`Open`, or `Completed` once done. The section is also kept in `labels` so it
survives completion.

`statusCat` is `Done` when `completed` is true. Otherwise the section name is
matched against `progress`, `doing`, `active`, `wip`, `review` and `qa` to guess
`In Progress`, else `To Do`. This is a heuristic, not a real state machine.
Treat an Asana `In Progress` with suspicion.

There is no cancelled state, only complete and incomplete, so
`cancelledStatuses` is empty and `childStats.cancelled` is always 0. A
`CLOSE_CANCEL` recommendation has to be expressed as completion plus a comment
explaining why, or a move to a section the team uses for dropped work. Flag the
distinction in the report because Asana cannot record it.

Epics are parent tasks: any task with `num_subtasks > 0`. Children come from
`/tasks/<gid>/subtasks`. If the user means portfolios or projects as their epic
level, ask instead of guessing.

## Write back

Only after the user approves a batch.

    asana tasks update --gid <GID> --complete
    asana tasks update --gid <GID> --name "Clarified: paginate export"
    asana api --path /tasks/<GID>/stories --method POST --data '{"text":"Verified shipped in PR #987. Closing."}'
    asana tasks create --name "Add retry to export job" --project <PROJECT_GID> --notes "..."

`asana api --data` wraps the payload as `{"data": ...}`, so pass the inner object
only. The CLI has no bulk flag, so batches are a loop; confirm the batch with the
user first, then run it, then re-extract the index to verify.
