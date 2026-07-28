# Asana REST API reference

Working reference for extending the `asana` skill. Official docs:
https://developers.asana.com/reference/rest-api-reference

Contents:
- [Basics](#basics)
- [Response envelope and errors](#response-envelope-and-errors)
- [Field selection (opt_fields)](#field-selection-opt_fields)
- [Pagination](#pagination)
- [Tasks](#tasks)
- [Projects and sections](#projects-and-sections)
- [Stories (comments)](#stories-comments)
- [Users, workspaces, teams](#users-workspaces-teams)
- [Search](#search)
- [Gotchas](#gotchas)

## Basics

- Base URL: `https://app.asana.com/api/1.0`
- Auth: `Authorization: Bearer <PAT>`. Personal Access Tokens are created at
  https://app.asana.com/0/my-apps and carry the creating user's permissions.
- Every object is identified by a string `gid` (numeric, but treat as a string).
- Write bodies are wrapped in a `data` envelope: `{"data": {...}}`. Query
  parameters (`opt_fields`, `limit`, filters) go in the URL, not the body.
- Content type for writes: `application/json`.

The CLI's `_request(method, path, params=, body=)` handles the envelope: pass the
inner object as `body`, and it wraps it. `asana api` exposes this directly.

## Response envelope and errors

Success responses are `{"data": <object or array>, "next_page": {...}|null}`.
Some endpoints also return `{"data": ..., "errors": [...]}` for partial results.

Errors are `{"errors": [{"message": "...", "help": "..."}]}` with an HTTP 4xx/5xx
status. Common statuses: 400 (bad request / invalid field), 401 (bad token),
403 (no permission), 404 (missing or no access), 429 (rate limited), 451
(blocked). The CLI surfaces `errors[].message` as `{"error": ...}`.

## Field selection (opt_fields)

By default responses return only `gid` and a compact record. Request fields with
`opt_fields` (comma-separated). Dotted paths expand relations:

- `name,completed,due_on` — scalar fields
- `assignee.name` — a field on the related assignee
- `projects.name` — a field on each related project
- `memberships.section.name` — nested relation
- `custom_fields.name,custom_fields.display_value` — custom field values

`opt_pretty=true` pretty-prints (the CLI pretty-prints locally instead).

Useful task fields: `name`, `notes`, `html_notes`, `completed`, `completed_at`,
`due_on` (date), `due_at` (datetime), `start_on`, `assignee`, `assignee_status`,
`projects`, `parent`, `num_subtasks`, `tags`, `memberships`, `custom_fields`,
`permalink_url`, `created_at`, `modified_at`.

## Pagination

List endpoints accept `limit` (1-100) and return `next_page.offset` when more
results exist. Pass that value back as `offset` for the next page. The CLI's
`_paged()` helper loops until it reaches the requested `--limit`. `asana api`
returns the raw envelope so you can page manually with `--query offset=<cursor>`.

## Tasks

- List: `GET /tasks` requires one filter: `project`, `section`, `tag`, or
  `assignee` + `workspace`. Also `GET /projects/{gid}/tasks`,
  `GET /sections/{gid}/tasks`, `GET /user_task_lists/{gid}/tasks`.
  - `completed_since=now` returns only incomplete tasks. A timestamp returns
    tasks completed since then. Omit to include all.
  - `modified_since=<ISO8601>` filters by last modification.
- Get: `GET /tasks/{gid}`
- Create: `POST /tasks` with `data`. Requires either `projects: [gid,...]` or a
  `workspace` gid. Common fields: `name`, `notes`/`html_notes`, `assignee`
  (`"me"` or user gid), `due_on`/`due_at`, `followers: [gid,...]`,
  `custom_fields: {field_gid: value}`.
- Update: `PUT /tasks/{gid}` with `data`. Sparse — only included fields change.
  `completed: true|false`. Clear a date by sending `due_on: null`.
- Subtasks: `GET /tasks/{gid}/subtasks`, `POST /tasks/{gid}/subtasks`.
- Dependencies: `POST /tasks/{gid}/addDependencies`, `.../addDependents`.

Project membership is NOT settable via `PUT /tasks`. Use:
- `POST /tasks/{gid}/addProject` with `{"project": gid}` (optional `section`,
  `insert_before`, `insert_after`).
- `POST /tasks/{gid}/removeProject` with `{"project": gid}`.

## Projects and sections

- Projects: `GET /workspaces/{gid}/projects`, `GET /teams/{gid}/projects`,
  `GET /projects/{gid}`, `POST /projects`, `PUT /projects/{gid}`.
  Fields: `name`, `notes`, `archived`, `color`, `owner`, `team`, `workspace`,
  `current_status`, `due_date`.
- Sections (columns within a project): `GET /projects/{gid}/sections`,
  `POST /projects/{gid}/sections`, `GET /sections/{gid}`. Move a task into a
  section via `addProject` (with `section`) or
  `POST /sections/{gid}/addTask` with `{"task": gid}`.

## Stories (comments)

- List a task's activity/comments: `GET /tasks/{gid}/stories`
  (`opt_fields=text,created_by.name,created_at,type`). `type` is `comment` or
  `system`.
- Add a comment: `POST /tasks/{gid}/stories` with `{"text": "..."}` (or
  `html_text`).

## Users, workspaces, teams

- Current user: `GET /users/me` (`opt_fields=name,email,workspaces.name`).
- Users in a workspace: `GET /workspaces/{gid}/users` — use to resolve an
  assignee gid from a name/email.
- Workspaces: `GET /workspaces`, `GET /workspaces/{gid}`.
- Teams: `GET /workspaces/{gid}/teams` (org workspaces),
  `GET /users/{gid}/teams?organization={workspace_gid}`.

## Search

- Typeahead (fast, name match): `GET /workspaces/{gid}/typeahead`
  with `resource_type=task|project|user|...` and `query=<text>`.
- Advanced search (premium orgs): `GET /workspaces/{gid}/tasks/search` with rich
  filters (`assignee.any`, `projects.any`, `due_on.before`, `completed`, etc.).

## Gotchas

- `gid`s are strings. Do not coerce to int.
- `due_on` is a date (`YYYY-MM-DD`); `due_at` is a full timestamp. Setting one
  clears the other. To clear a due date send `null` (the CLI maps `--due ''`).
- Completing is an update (`completed: true`), not a delete. This skill exposes
  no delete; `asana api --method DELETE` is gated behind `--allow-delete`.
- `assignee` accepts the literal `"me"`.
- Rate limits: ~150 requests/min for PATs (free), higher for paid. On HTTP 429,
  honor the `Retry-After` header and back off.
- `notes` is plain text; `html_notes` accepts a restricted HTML subset and must
  be requested/round-tripped with the `html_notes` field, not `notes`.
- New tasks need a home: pass `projects` or `workspace`, or the create fails.
