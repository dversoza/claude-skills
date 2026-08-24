#!/usr/bin/env python3
"""Extract a Jira project backlog into the canonical grooming dataset.

Writes meta.json, all_index.json, issues/<key>.json and epics/<key>.json under
<workdir>/data. See references/dataset.md for the contract. Read-only: every
acli call is a search or a view.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# acli's search endpoint rejects parent, created, updated, resolution and
# resolutiondate. view --fields '*all' returns all of them, which is why epic
# children need their own per-epic search instead of a column on the index.
SEARCH_FIELDS = "key,status,summary,issuetype,assignee,priority,labels"
DONE_CAT = "Done"


def key_order(key):
    """Sort VD-59 before VD-168; plain sorted() puts them the other way round."""
    match = re.search(r"-(\d+)$", key or "")
    return (int(match.group(1)) if match else 0, key or "")


def acli(args):
    proc = subprocess.run(
        ["acli", "jira"] + args, capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.exit("acli %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc.stdout


def acli_json(args):
    out = acli(args + ["--json"]).strip()
    if not out:
        return []
    return json.loads(out)


def detect_site():
    for line in acli(["auth", "status"]).splitlines():
        if line.strip().startswith("Site:"):
            return line.split(":", 1)[1].strip()
    return None


def adf_text(node):
    """Flatten an Atlassian Document Format node into plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)

    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return "@" + node.get("attrs", {}).get("text", "").lstrip("@")
    if kind == "inlineCard":
        return node.get("attrs", {}).get("url", "")

    inner = adf_text(node.get("content"))
    if kind in ("paragraph", "heading", "codeBlock", "blockquote"):
        return inner + "\n\n"
    if kind == "listItem":
        return "- " + inner.strip() + "\n"
    if kind == "rule":
        return "---\n"
    return inner


def clean(text):
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def name_of(value):
    if isinstance(value, dict):
        return value.get("displayName") or value.get("name")
    return None


def index_record(raw):
    fields = raw.get("fields") or {}
    status = fields.get("status") or {}
    return {
        "key": raw.get("key"),
        "type": (fields.get("issuetype") or {}).get("name"),
        "status": status.get("name"),
        "statusCat": (status.get("statusCategory") or {}).get("name"),
        "assignee": name_of(fields.get("assignee")),
        "summary": fields.get("summary"),
        "priority": name_of(fields.get("priority")),
        "labels": fields.get("labels") or [],
    }


def rich_record(raw, site):
    record = index_record(raw)
    fields = raw.get("fields") or {}
    comments = (fields.get("comment") or {}).get("comments") or []
    parent = fields.get("parent") or {}
    resolution = fields.get("resolution") or {}
    record.update(
        {
            "description": clean(adf_text(fields.get("description"))),
            "comments": [
                {
                    "author": name_of(c.get("author")),
                    "created": c.get("created"),
                    "body": clean(adf_text(c.get("body"))),
                }
                for c in comments
            ],
            "attachments": [
                a.get("filename") for a in (fields.get("attachment") or [])
            ],
            "parent": parent.get("key"),
            "created": fields.get("created"),
            "updated": fields.get("updated"),
            "resolution": resolution.get("name"),
            "resolutionDate": fields.get("resolutiondate"),
            "url": "https://%s/browse/%s" % (site, record["key"]) if site else None,
        }
    )
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="project key, e.g. VD")
    ap.add_argument("--workdir", required=True)
    ap.add_argument(
        "--rich",
        choices=["open", "all", "none"],
        default="open",
        help="which items get description+comments (default: open items and every epic)",
    )
    ap.add_argument("--site", help="override the site detected from acli auth status")
    ap.add_argument(
        "--max-rich",
        type=int,
        help="stop after N rich records; the skipped keys are reported, never silently dropped",
    )
    args = ap.parse_args()

    project = args.project
    data = Path(args.workdir).expanduser() / "data"
    (data / "issues").mkdir(parents=True, exist_ok=True)
    (data / "epics").mkdir(parents=True, exist_ok=True)

    site = args.site or detect_site()

    print("fetching index for %s" % project, file=sys.stderr)
    raw_index = acli_json(
        [
            "workitem",
            "search",
            "--jql",
            "project = %s ORDER BY key ASC" % project,
            "--fields",
            SEARCH_FIELDS,
            "--paginate",
        ]
    )
    index = [index_record(r) for r in raw_index]
    (data / "all_index.json").write_text(json.dumps(index, indent=2))
    print("  %d items" % len(index), file=sys.stderr)

    epics = [r for r in index if r["type"] == "Epic"]
    open_items = [r for r in index if r["statusCat"] != DONE_CAT]

    if args.rich == "all":
        targets = sorted({r["key"] for r in index}, key=key_order)
    elif args.rich == "open":
        targets = sorted(
            {r["key"] for r in open_items} | {r["key"] for r in epics}, key=key_order
        )
    else:
        targets = []

    skipped = []
    if args.max_rich and len(targets) > args.max_rich:
        skipped = targets[args.max_rich :]
        targets = targets[: args.max_rich]

    epic_keys = {r["key"] for r in epics}
    written = 0
    for key in targets:
        raw = acli_json(["workitem", "view", key, "--fields", "*all"])
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        record = rich_record(raw, site)

        if key in epic_keys:
            children = [
                index_record(c)
                for c in acli_json(
                    [
                        "workitem",
                        "search",
                        "--jql",
                        "parent = %s ORDER BY key ASC" % key,
                        "--fields",
                        SEARCH_FIELDS,
                        "--paginate",
                    ]
                )
            ]
            record["children"] = [c["key"] for c in children]
            record["childStats"] = {
                "total": len(children),
                "done": sum(1 for c in children if c["statusCat"] == DONE_CAT),
                "cancelled": sum(
                    1
                    for c in children
                    if (c["status"] or "").lower().startswith("cancel")
                ),
                "open": sum(1 for c in children if c["statusCat"] != DONE_CAT),
            }
            (data / "epics" / ("%s.json" % key)).write_text(
                json.dumps(record, indent=2)
            )
        else:
            (data / "issues" / ("%s.json" % key)).write_text(
                json.dumps(record, indent=2)
            )

        written += 1
        if written % 20 == 0:
            print("  %d/%d rich records" % (written, len(targets)), file=sys.stderr)

    statuses = sorted({r["status"] for r in index if r["status"]})
    meta = {
        "provider": "jira",
        "project": project,
        "projectName": None,
        "extractedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "keyPattern": "%s-[0-9]+" % project,
        "urlPattern": "browse/%s-[0-9]+" % project,
        "statusVocabulary": statuses,
        "doneStatuses": sorted(
            {r["status"] for r in index if r["statusCat"] == DONE_CAT and r["status"]}
        ),
        "cancelledStatuses": sorted(
            {
                r["status"]
                for r in index
                if r["status"] and r["status"].lower().startswith("cancel")
            }
        ),
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
            "SKIPPED %d keys due to --max-rich: %s"
            % (len(skipped), ", ".join(skipped)),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
