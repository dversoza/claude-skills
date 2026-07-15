#!/usr/bin/env python3
"""asana-cli -- a thin CLI over the Asana REST API for task CRUD.

All subcommands print JSON to stdout. Errors print a JSON object with an
"error" field to stderr and exit non-zero. No task is ever deleted: the CLI
intentionally exposes list/get/create/update only.

Auth resolution order (first hit wins):
    1. ASANA_ACCESS_TOKEN environment variable
    2. config file written by `asana setup`
       (default: $XDG_CONFIG_HOME/asana-cli/config.env, else ~/.config/...)

Workspace resolution order:
    1. --workspace flag
    2. ASANA_WORKSPACE_GID environment variable
    3. workspace stored in the config file
    4. auto: if the token has access to exactly one workspace, use it
"""
import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://app.asana.com/api/1.0"

LIST_FIELDS = "name,completed,due_on,assignee.name,projects.name,permalink_url"
GET_FIELDS = LIST_FIELDS + ",notes,created_at,modified_at"


# -- Config ---------------------------------------------------------------


def _config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "asana-cli")


def _config_path():
    return os.path.join(_config_dir(), "config.env")


def _read_config():
    """Parse the KEY=value config file. Returns {} if absent."""
    path = _config_path()
    if not os.path.isfile(path):
        return {}
    values = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = val.strip().strip("\"'")
    return values


def _write_config(values):
    """Write KEY=value pairs to the config file with 0600 permissions."""
    directory = _config_dir()
    os.makedirs(directory, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)  # 0700
    path = _config_path()
    lines = [f"{key}={val}" for key, val in values.items() if val]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return path


# -- Errors ---------------------------------------------------------------


class AsanaError(Exception):
    """Raised for any user-facing failure; message becomes {"error": ...}."""


def _die(message):
    json.dump({"error": message}, sys.stderr, indent=2)
    sys.stderr.write("\n")
    sys.exit(1)


# -- Token / workspace resolution -----------------------------------------


def _token():
    token = os.environ.get("ASANA_ACCESS_TOKEN")
    if token:
        return token.strip()
    token = _read_config().get("ASANA_ACCESS_TOKEN")
    if token:
        return token.strip()
    raise AsanaError(
        "No Asana token found. Run `asana setup --token <PAT>` or set "
        "ASANA_ACCESS_TOKEN. Create a Personal Access Token at "
        "https://app.asana.com/0/my-apps"
    )


# -- HTTP -----------------------------------------------------------------


def _request(method, path, *, params=None, body=None, full=False):
    url = f"{BASE}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    data = None
    if body is not None:
        data = json.dumps({"data": body}).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
            return payload if full else payload["data"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        message = f"HTTP {exc.code}"
        try:
            errors = json.loads(detail).get("errors", [])
            if errors:
                message = "; ".join(e.get("message", "") for e in errors)
        except ValueError:
            if detail:
                message = detail
        raise AsanaError(message) from None
    except urllib.error.URLError as exc:
        raise AsanaError(f"Network error: {exc.reason}") from None


def _paged(path, params, limit):
    """Fetch up to `limit` items across pages (Asana caps a page at 100)."""
    items = []
    offset = None
    while len(items) < limit:
        page_params = dict(params, limit=min(100, limit - len(items)), offset=offset)
        payload = _request("GET", path, params=page_params, full=True)
        items.extend(payload["data"])
        next_page = payload.get("next_page")
        if not next_page:
            break
        offset = next_page["offset"]
    return items[:limit]


def _output(data):
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


# -- Workspace helper -----------------------------------------------------


def _resolve_workspace(explicit):
    if explicit:
        return explicit
    env = os.environ.get("ASANA_WORKSPACE_GID")
    if env:
        return env.strip()
    cfg = _read_config().get("ASANA_WORKSPACE_GID")
    if cfg:
        return cfg.strip()
    workspaces = _request("GET", "/users/me", params={"opt_fields": "workspaces.name"})[
        "workspaces"
    ]
    if len(workspaces) == 1:
        return workspaces[0]["gid"]
    raise AsanaError(
        "Multiple workspaces available; pass --workspace GID or run "
        "`asana setup --workspace GID`.",
    )


# -- Commands -------------------------------------------------------------


def cmd_setup(args):
    values = _read_config()
    if args.token:
        values["ASANA_ACCESS_TOKEN"] = args.token.strip()
    if args.workspace:
        values["ASANA_WORKSPACE_GID"] = args.workspace.strip()
    if not values.get("ASANA_ACCESS_TOKEN"):
        raise AsanaError(
            "No token to store. Pass --token <PAT> (create one at "
            "https://app.asana.com/0/my-apps)."
        )
    path = _write_config(values)
    # Verify the stored credentials without printing the token.
    os.environ["ASANA_ACCESS_TOKEN"] = values["ASANA_ACCESS_TOKEN"]
    me = _request("GET", "/users/me", params={"opt_fields": "name,email,workspaces.name"})
    _output(
        {
            "config": path,
            "authenticated_as": {"name": me["name"], "email": me["email"]},
            "workspaces": me["workspaces"],
            "default_workspace": values.get("ASANA_WORKSPACE_GID"),
        }
    )


def cmd_whoami(_args):
    _output(_request("GET", "/users/me", params={"opt_fields": "name,email,workspaces.name"}))


def cmd_workspaces(_args):
    _output(_request("GET", "/workspaces", params={"opt_fields": "name"}))


def cmd_projects(args):
    workspace = _resolve_workspace(args.workspace)
    items = _paged(
        f"/workspaces/{workspace}/projects",
        {"opt_fields": "name,archived"},
        args.limit,
    )
    _output(items)


def cmd_tasks_list(args):
    params = {"opt_fields": args.fields or LIST_FIELDS}
    if args.project:
        path = f"/projects/{args.project}/tasks"
    elif args.assignee:
        path = "/tasks"
        params["assignee"] = args.assignee
        params["workspace"] = _resolve_workspace(args.workspace)
    else:
        raise AsanaError("Provide --project GID or --assignee (e.g. --assignee me).")
    if not args.include_completed:
        params["completed_since"] = "now"
    _output(_paged(path, params, args.limit))


def cmd_tasks_get(args):
    _output(
        _request("GET", f"/tasks/{args.gid}", params={"opt_fields": args.fields or GET_FIELDS})
    )


def cmd_tasks_create(args):
    body = {"name": args.name}
    if args.notes is not None:
        body["notes"] = args.notes
    if args.due:
        body["due_on"] = args.due
    if args.assignee:
        body["assignee"] = args.assignee
    if args.parent:
        # A subtask inherits its home from the parent; project/workspace ignored.
        _output(
            _request(
                "POST",
                f"/tasks/{args.parent}/subtasks",
                params={"opt_fields": GET_FIELDS},
                body=body,
            )
        )
        return
    if args.project:
        body["projects"] = [args.project]
    else:
        body["workspace"] = _resolve_workspace(args.workspace)
    _output(_request("POST", "/tasks", params={"opt_fields": GET_FIELDS}, body=body))


def cmd_tasks_update(args):
    body = {}
    if args.name is not None:
        body["name"] = args.name
    if args.notes is not None:
        body["notes"] = args.notes
    if args.due is not None:
        body["due_on"] = args.due or None  # empty string clears the due date
    if args.assignee is not None:
        body["assignee"] = args.assignee
    if args.complete:
        body["completed"] = True
    if args.incomplete:
        body["completed"] = False

    result = None
    if body:
        result = _request(
            "PUT", f"/tasks/{args.gid}", params={"opt_fields": GET_FIELDS}, body=body
        )
    for project in args.add_project or []:
        _request("POST", f"/tasks/{args.gid}/addProject", body={"project": project})
    for project in args.remove_project or []:
        _request("POST", f"/tasks/{args.gid}/removeProject", body={"project": project})
    if args.add_follower:
        _request(
            "POST", f"/tasks/{args.gid}/addFollowers", body={"followers": args.add_follower}
        )
    if args.remove_follower:
        _request(
            "POST",
            f"/tasks/{args.gid}/removeFollowers",
            body={"followers": args.remove_follower},
        )
    membership_changed = (
        args.add_project or args.remove_project or args.add_follower or args.remove_follower
    )
    if result is None or membership_changed:
        result = _request("GET", f"/tasks/{args.gid}", params={"opt_fields": GET_FIELDS})
    _output(result)


def cmd_tasks_reorder(args):
    """Reorder a parent's subtasks into the given top-to-bottom gid order."""
    order = [g.strip() for g in args.order.split(",") if g.strip()]
    if not order:
        raise AsanaError("--order must be a comma-separated list of subtask gids.")
    current = _request(
        "GET", f"/tasks/{args.parent}/subtasks", params={"opt_fields": "name"}
    )
    # Anchor the first item at the top (unless already there), then chain the rest.
    if current and current[0]["gid"] != order[0]:
        _request(
            "POST",
            f"/tasks/{order[0]}/setParent",
            body={"parent": args.parent, "insert_before": current[0]["gid"]},
        )
    prev = order[0]
    for gid in order[1:]:
        _request(
            "POST",
            f"/tasks/{gid}/setParent",
            body={"parent": args.parent, "insert_after": prev},
        )
        prev = gid
    _output(
        _request(
            "GET",
            f"/tasks/{args.parent}/subtasks",
            params={"opt_fields": "name,completed"},
        )
    )


def cmd_api(args):
    """Escape hatch: call any Asana REST endpoint and print the full envelope."""
    method = args.method.upper()
    if method == "DELETE" and not args.allow_delete:
        raise AsanaError(
            "DELETE is blocked by default. Pass --allow-delete to override."
        )
    path = args.path if args.path.startswith("/") else "/" + args.path
    params = {}
    for pair in args.query or []:
        if "=" not in pair:
            raise AsanaError(f"--query expects key=value, got: {pair}")
        key, val = pair.split("=", 1)
        params[key] = val
    body = None
    if args.data is not None:
        try:
            body = json.loads(args.data)
        except ValueError as exc:
            raise AsanaError(f"--data is not valid JSON: {exc}")
    _output(_request(method, path, params=params or None, body=body, full=True))


# -- Argument parser ------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(prog="asana", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Store token/workspace in the config file")
    p_setup.add_argument("--token", help="Asana Personal Access Token")
    p_setup.add_argument("--workspace", help="Default workspace gid")
    p_setup.set_defaults(func=cmd_setup)

    sub.add_parser("whoami", help="Show the authenticated user").set_defaults(
        func=cmd_whoami
    )
    sub.add_parser("workspaces", help="List accessible workspaces").set_defaults(
        func=cmd_workspaces
    )

    p_projects = sub.add_parser("projects", help="List projects in a workspace")
    p_projects.add_argument("--workspace", help="Workspace gid (default: resolved)")
    p_projects.add_argument("--limit", type=int, default=100, help="Max projects")
    p_projects.set_defaults(func=cmd_projects)

    p_tasks = sub.add_parser("tasks", help="Task CRUD")
    tsub = p_tasks.add_subparsers(dest="tasks_command", required=True)

    p_list = tsub.add_parser("list", help="List tasks by project or assignee")
    p_list.add_argument("--project", help="Project gid")
    p_list.add_argument("--assignee", help="Assignee ('me' or user gid); needs workspace")
    p_list.add_argument("--workspace", help="Workspace gid (with --assignee)")
    p_list.add_argument(
        "--include-completed", action="store_true", help="Include completed tasks"
    )
    p_list.add_argument("--limit", type=int, default=50, help="Max tasks")
    p_list.add_argument("--fields", help="Comma-separated opt_fields override")
    p_list.set_defaults(func=cmd_tasks_list)

    p_get = tsub.add_parser("get", help="Get one task by gid")
    p_get.add_argument("--gid", required=True, help="Task gid")
    p_get.add_argument("--fields", help="Comma-separated opt_fields override")
    p_get.set_defaults(func=cmd_tasks_get)

    p_create = tsub.add_parser("create", help="Create a task or subtask")
    p_create.add_argument("--name", required=True, help="Task title")
    p_create.add_argument("--notes", help="Task description")
    p_create.add_argument(
        "--parent", help="Parent task gid (creates a subtask; project/workspace ignored)"
    )
    p_create.add_argument("--project", help="Project gid (task is added to it)")
    p_create.add_argument("--workspace", help="Workspace gid (if no --project)")
    p_create.add_argument("--assignee", help="Assignee ('me' or user gid)")
    p_create.add_argument("--due", help="Due date YYYY-MM-DD")
    p_create.set_defaults(func=cmd_tasks_create)

    p_update = tsub.add_parser("update", help="Update a task (never deletes)")
    p_update.add_argument("--gid", required=True, help="Task gid")
    p_update.add_argument("--name", help="New title")
    p_update.add_argument("--notes", help="New description")
    p_update.add_argument("--due", help="Due date YYYY-MM-DD (empty string clears it)")
    p_update.add_argument("--assignee", help="Assignee ('me' or user gid)")
    p_update.add_argument("--add-project", action="append", help="Add task to project gid")
    p_update.add_argument(
        "--remove-project", action="append", help="Remove task from project gid"
    )
    p_update.add_argument(
        "--add-follower", action="append", help="Add a follower (user gid, repeatable)"
    )
    p_update.add_argument(
        "--remove-follower", action="append", help="Remove a follower (user gid, repeatable)"
    )
    completion = p_update.add_mutually_exclusive_group()
    completion.add_argument("--complete", action="store_true", help="Mark completed")
    completion.add_argument("--incomplete", action="store_true", help="Mark incomplete")
    p_update.set_defaults(func=cmd_tasks_update)

    p_reorder = tsub.add_parser("reorder", help="Reorder a parent's subtasks")
    p_reorder.add_argument("--parent", required=True, help="Parent task gid")
    p_reorder.add_argument(
        "--order", required=True, help="Comma-separated subtask gids, top to bottom"
    )
    p_reorder.set_defaults(func=cmd_tasks_reorder)

    p_api = sub.add_parser("api", help="Call any Asana REST endpoint (escape hatch)")
    p_api.add_argument("--path", required=True, help="Endpoint path, e.g. /projects/123")
    p_api.add_argument(
        "--method",
        default="GET",
        type=str.upper,
        choices=["GET", "POST", "PUT", "PATCH", "DELETE"],
        help="HTTP method (default: GET)",
    )
    p_api.add_argument(
        "--query", action="append", help="Query param key=value (repeatable)"
    )
    p_api.add_argument(
        "--data",
        help="JSON object for the request body; sent wrapped as {\"data\": ...}",
    )
    p_api.add_argument(
        "--allow-delete", action="store_true", help="Permit --method DELETE"
    )
    p_api.set_defaults(func=cmd_api)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except AsanaError as exc:
        _die(str(exc))


if __name__ == "__main__":
    main()
