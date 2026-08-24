#!/usr/bin/env python3
"""Extract an Asana project backlog into the canonical grooming dataset.

Writes meta.json, all_index.json, issues/<gid>.json and epics/<gid>.json under
<workdir>/data. See references/dataset.md for the contract. Read-only: every
call is a GET.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# `notes` arrives with the list call, so rich extraction only needs comments.
LIST_FIELDS = (
    "gid,name,completed,completed_at,notes,assignee.name,parent.gid,"
    "memberships.section.name,permalink_url,num_subtasks,created_at,modified_at"
)
STORY_FIELDS = "gid,created_at,created_by.name,text,resource_subtype"
IN_PROGRESS_HINTS = ("progress", "doing", "active", "wip", "review", "qa")


def asana(args):
    proc = subprocess.run(["asana"] + args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit("asana %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    out = proc.stdout.strip()
    if not out:
        return None
    payload = json.loads(out)
    # `asana api` returns the raw envelope; the typed subcommands unwrap it.
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def section_of(task):
    for membership in task.get("memberships") or []:
        section = (membership or {}).get("section") or {}
        if section.get("name"):
            return section["name"]
    return None


def status_of(task):
    """Asana has no status field. Section names are the closest thing teams use."""
    if task.get("completed"):
        return "Completed"
    return section_of(task) or "Open"


def status_cat_of(task):
    if task.get("completed"):
        return "Done"
    section = (section_of(task) or "").lower()
    if any(hint in section for hint in IN_PROGRESS_HINTS):
        return "In Progress"
    return "To Do"


def type_of(task):
    if (task.get("parent") or {}).get("gid"):
        return "Subtask"
    if (task.get("num_subtasks") or 0) > 0:
        return "Epic"
    return "Task"


def index_record(task):
    return {
        "key": task.get("gid"),
        "type": type_of(task),
        "status": status_of(task),
        "statusCat": status_cat_of(task),
        "assignee": (task.get("assignee") or {}).get("name"),
        "summary": task.get("name"),
        "priority": None,
        "labels": [section_of(task)] if section_of(task) else [],
    }


def comments_for(gid):
    stories = asana(
        ["api", "--path", "/tasks/%s/stories" % gid, "--query", "opt_fields=%s" % STORY_FIELDS]
    ) or []
    return [
        {
            "author": (s.get("created_by") or {}).get("name"),
            "created": s.get("created_at"),
            "body": s.get("text"),
        }
        for s in stories
        if s.get("resource_subtype") == "comment_added"
    ]


def rich_record(task, with_comments=True):
    record = index_record(task)
    record.update(
        {
            "description": (task.get("notes") or "").strip(),
            "comments": comments_for(task["gid"]) if with_comments else [],
            "attachments": [],
            "parent": (task.get("parent") or {}).get("gid"),
            "created": task.get("created_at"),
            "updated": task.get("modified_at"),
            "resolution": None,
            "resolutionDate": task.get("completed_at"),
            "url": task.get("permalink_url"),
        }
    )
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="Asana project gid")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--rich", choices=["open", "all", "none"], default="open")
    ap.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="max tasks to pull; the CLI default of 50 silently truncates a backlog",
    )
    ap.add_argument("--max-rich", type=int)
    args = ap.parse_args()

    data = Path(args.workdir).expanduser() / "data"
    (data / "issues").mkdir(parents=True, exist_ok=True)
    (data / "epics").mkdir(parents=True, exist_ok=True)

    project = asana(["api", "--path", "/projects/%s" % args.project]) or {}
    project_name = project.get("name")

    print("fetching tasks for %s" % (project_name or args.project), file=sys.stderr)
    tasks = asana(
        [
            "tasks",
            "list",
            "--project",
            args.project,
            "--include-completed",
            "--limit",
            str(args.limit),
            "--fields",
            LIST_FIELDS,
        ]
    ) or []
    if len(tasks) == args.limit:
        print(
            "WARNING: got exactly --limit (%d) tasks; raise --limit, the backlog is "
            "probably truncated" % args.limit,
            file=sys.stderr,
        )

    by_gid = {t["gid"]: t for t in tasks}
    index = [index_record(t) for t in tasks]
    (data / "all_index.json").write_text(json.dumps(index, indent=2))
    print("  %d tasks" % len(index), file=sys.stderr)

    epics = [r for r in index if r["type"] == "Epic"]
    open_items = [r for r in index if r["statusCat"] != "Done"]

    if args.rich == "all":
        targets = [r["key"] for r in index]
    elif args.rich == "open":
        wanted = {r["key"] for r in open_items} | {r["key"] for r in epics}
        targets = [r["key"] for r in index if r["key"] in wanted]
    else:
        targets = []

    skipped = []
    if args.max_rich and len(targets) > args.max_rich:
        skipped = targets[args.max_rich :]
        targets = targets[: args.max_rich]

    epic_keys = {r["key"] for r in epics}
    written = 0
    for gid in targets:
        record = rich_record(by_gid[gid])

        if gid in epic_keys:
            children = asana(
                [
                    "api",
                    "--path",
                    "/tasks/%s/subtasks" % gid,
                    "--query",
                    "opt_fields=%s" % LIST_FIELDS,
                ]
            ) or []
            child_records = [index_record(c) for c in children]
            record["children"] = [c["key"] for c in child_records]
            record["childStats"] = {
                "total": len(child_records),
                "done": sum(1 for c in child_records if c["statusCat"] == "Done"),
                "cancelled": 0,
                "open": sum(1 for c in child_records if c["statusCat"] != "Done"),
            }
            (data / "epics" / ("%s.json" % gid)).write_text(json.dumps(record, indent=2))
        else:
            (data / "issues" / ("%s.json" % gid)).write_text(json.dumps(record, indent=2))

        written += 1
        if written % 20 == 0:
            print("  %d/%d rich records" % (written, len(targets)), file=sys.stderr)

    meta = {
        "provider": "asana",
        "project": args.project,
        "projectName": project_name,
        "extractedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Asana gids never appear in commit messages, so the URL is the only
        # usable git-evidence handle. See providers/asana.md.
        "keyPattern": None,
        "urlPattern": r"app\.asana\.com/[^\s)]*/task/[0-9]+",
        "statusVocabulary": sorted({r["status"] for r in index if r["status"]}),
        "doneStatuses": ["Completed"],
        "cancelledStatuses": [],
        "counts": {
            "total": len(index),
            "open": len(open_items),
            "epics": len(epics),
            "rich": written,
            "richSkipped": len(skipped),
        },
    }
    (data / "meta.json").write_text(json.dumps(meta, indent=2))
    print("wrote %d rich records to %s" % (written, data), file=sys.stderr)
    if skipped:
        print(
            "SKIPPED %d gids due to --max-rich: %s" % (len(skipped), ", ".join(skipped)),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
