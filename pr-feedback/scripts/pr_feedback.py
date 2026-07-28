#!/usr/bin/env python3
"""PR review feedback CLI for the pr-feedback skill.

Fetch commands (read-only):
    threads   [--repo OWNER/REPO] [--pr NUMBER]  Fetch unresolved inline review threads
    ci        [--repo OWNER/REPO] [--pr NUMBER]  Fetch CI check status and failed check details
    comments  [--repo OWNER/REPO] [--pr NUMBER]  Fetch general PR comments and PR description

Action commands (write):
    resolve THREAD_ID               Mark a review thread as resolved
    react   TYPE DATABASE_ID        Add thumbs-up (TYPE: review|issue)
    reply   DATABASE_ID BODY        Reply to a review thread comment
    comment BODY                    Leave a general PR comment

--repo and --pr are optional flags available on every subcommand.
They default to auto-detection from the current working directory and branch.
"""

import argparse
import json
import re
import subprocess
import sys


# ── Shared utilities ──────────────────────────────────────────────────


def run_gh(*args):
    result = subprocess.run(
        ["gh"] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def get_repo_info(repo_override=None):
    if repo_override:
        owner, name = repo_override.split("/", 1)
        return owner, name
    data = json.loads(run_gh("repo", "view", "--json", "owner,name"))
    return data["owner"]["login"], data["name"]


def get_pr_number(pr_override=None, repo_slug=None):
    if pr_override:
        return int(pr_override)
    cmd = ["pr", "view", "--json", "number"]
    if repo_slug:
        cmd.extend(["--repo", repo_slug])
    data = json.loads(run_gh(*cmd))
    return data["number"]


def _repo_slug(args):
    """Return 'owner/repo' string if --repo was provided, else None."""
    return args.repo if args.repo else None


def _parse_author(node):
    return (node.get("author") or {}).get("login", "unknown")


def _output(data):
    json.dump(data, sys.stdout, indent=2)
    print()


# ── Fetch: threads ────────────────────────────────────────────────────

THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              databaseId
              body
              author { login }
              createdAt
              diffHunk
            }
          }
        }
      }
    }
  }
}
"""


THREAD_COMMENTS_QUERY = """
query($threadId: ID!, $cursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          body
          author { login }
          createdAt
          diffHunk
        }
      }
    }
  }
}
"""


def _fetch_remaining_thread_comments(thread_id, cursor):
    """Page through the tail of a thread whose comments exceeded the first page.

    The newest comment carries the current ask, so truncating a long
    back-and-forth silently drops the only part that still matters.
    """
    extra = []
    while cursor:
        data = json.loads(run_gh(
            "api", "graphql",
            "-f", f"query={THREAD_COMMENTS_QUERY}",
            "-f", f"threadId={thread_id}",
            "-f", f"cursor={cursor}",
        ))
        page = data["data"]["node"]["comments"]
        extra.extend(page["nodes"])
        cursor = page["pageInfo"]["endCursor"] if page["pageInfo"]["hasNextPage"] else None
    return extra


def cmd_threads(args):
    owner, repo = get_repo_info(args.repo)
    pr_number = get_pr_number(args.pr, _repo_slug(args))
    all_threads = []
    cursor = None

    while True:
        cmd = [
            "api", "graphql",
            "-f", f"query={THREADS_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"repo={repo}",
            "-F", f"pr={pr_number}",
        ]
        if cursor:
            cmd.extend(["-f", f"cursor={cursor}"])

        data = json.loads(run_gh(*cmd))
        page = data["data"]["repository"]["pullRequest"]["reviewThreads"]

        for node in page["nodes"]:
            if not node["isResolved"]:
                all_threads.append(node)

        if page["pageInfo"]["hasNextPage"]:
            cursor = page["pageInfo"]["endCursor"]
        else:
            break

    threads = []
    for t in all_threads:
        comment_nodes = list(t["comments"]["nodes"])
        page_info = t["comments"]["pageInfo"]
        if page_info["hasNextPage"]:
            comment_nodes.extend(
                _fetch_remaining_thread_comments(t["id"], page_info["endCursor"])
            )

        threads.append({
            "thread_id": t["id"],
            "path": t["path"],
            "line": t["line"],
            "start_line": t.get("startLine"),
            "outdated": t["isOutdated"],
            "comments": [
                {
                    "node_id": c["id"],
                    "database_id": c["databaseId"],
                    "author": _parse_author(c),
                    "body": c["body"],
                    "diff_hunk": c["diffHunk"],
                    "created_at": c["createdAt"],
                }
                for c in comment_nodes
            ],
        })

    _output({
        "pr_number": pr_number,
        "owner": owner,
        "repo": repo,
        "unresolved_count": len(threads),
        "unresolved_threads": threads,
    })


# ── Fetch: ci ─────────────────────────────────────────────────────────


def cmd_ci(args):
    slug = _repo_slug(args)
    pr_number = get_pr_number(args.pr, slug)

    try:
        cmd = ["pr", "checks", str(pr_number),
               "--json", "name,bucket,link,workflow,description"]
        if slug:
            cmd.extend(["--repo", slug])
        raw = run_gh(*cmd)
    except subprocess.CalledProcessError:
        _output({"pr_number": pr_number, "ci_summary": {}, "failed_checks": []})
        return

    checks = json.loads(raw)
    failed = []
    for check in checks:
        if check.get("bucket") == "fail":
            match = re.search(r"/runs/(\d+)(?:/job/(\d+))?", check.get("link", ""))
            failed.append({
                "name": check.get("name", ""),
                "workflow": check.get("workflow", ""),
                "link": check.get("link", ""),
                "run_id": match.group(1) if match else None,
                "job_id": match.group(2) if match else None,
                "description": check.get("description", ""),
            })

    _output({
        "pr_number": pr_number,
        "ci_summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c.get("bucket") == "pass"),
            "failed": sum(1 for c in checks if c.get("bucket") == "fail"),
            "pending": sum(1 for c in checks if c.get("bucket") == "pending"),
            "skipping": sum(1 for c in checks if c.get("bucket") == "skipping"),
        },
        "failed_checks": failed,
    })


# ── Fetch: comments ───────────────────────────────────────────────────

COMMENTS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          databaseId
          body
          author { login }
          createdAt
          url
        }
      }
    }
  }
}
"""


def cmd_comments(args):
    owner, repo = get_repo_info(args.repo)
    slug = _repo_slug(args)
    pr_number = get_pr_number(args.pr, slug)

    cmd = ["pr", "view", str(pr_number), "--json", "body,author"]
    if slug:
        cmd.extend(["--repo", slug])
    pr_meta = json.loads(run_gh(*cmd))

    all_comments = []
    cursor = None

    while True:
        cmd = [
            "api", "graphql",
            "-f", f"query={COMMENTS_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"repo={repo}",
            "-F", f"pr={pr_number}",
        ]
        if cursor:
            cmd.extend(["-f", f"cursor={cursor}"])

        data = json.loads(run_gh(*cmd))
        page = data["data"]["repository"]["pullRequest"]["comments"]
        all_comments.extend(page["nodes"])

        if page["pageInfo"]["hasNextPage"]:
            cursor = page["pageInfo"]["endCursor"]
        else:
            break

    comments = [
        {
            "node_id": c["id"],
            "database_id": c["databaseId"],
            "author": _parse_author(c),
            "body": c["body"],
            "created_at": c["createdAt"],
            "url": c.get("url", ""),
        }
        for c in all_comments
    ]

    _output({
        "pr_number": pr_number,
        "owner": owner,
        "repo": repo,
        "pr_author": (pr_meta.get("author") or {}).get("login", "unknown"),
        "pr_body": pr_meta.get("body", ""),
        "pr_comments": comments,
    })


# ── Action: resolve ───────────────────────────────────────────────────


def cmd_resolve(args):
    mutation = (
        'mutation { resolveReviewThread(input: {threadId: "'
        + args.thread_id
        + '"}) { thread { isResolved } } }'
    )
    result = json.loads(run_gh("api", "graphql", "-f", f"query={mutation}"))
    resolved = result["data"]["resolveReviewThread"]["thread"]["isResolved"]
    _output({"thread_id": args.thread_id, "resolved": resolved})


# ── Action: react ─────────────────────────────────────────────────────


def cmd_react(args):
    owner, repo = get_repo_info(args.repo)
    db_id = args.database_id

    if args.comment_type == "review":
        endpoint = f"repos/{owner}/{repo}/pulls/comments/{db_id}/reactions"
    else:
        endpoint = f"repos/{owner}/{repo}/issues/comments/{db_id}/reactions"

    result = json.loads(run_gh("api", endpoint, "-f", "content=+1"))
    _output({"database_id": db_id, "reaction": result.get("content", "+1")})


# ── Action: reply ─────────────────────────────────────────────────────


def cmd_reply(args):
    owner, repo = get_repo_info(args.repo)
    slug = _repo_slug(args)
    pr_number = get_pr_number(args.pr, slug)

    result = json.loads(run_gh(
        "api",
        f"repos/{owner}/{repo}/pulls/{pr_number}/comments/{args.database_id}/replies",
        "-f", f"body={args.body}",
    ))
    _output({
        "database_id": args.database_id,
        "reply_id": result.get("id"),
        "url": result.get("html_url", ""),
    })


# ── Action: comment ───────────────────────────────────────────────────


def cmd_comment(args):
    slug = _repo_slug(args)
    pr_number = get_pr_number(args.pr, slug)
    cmd = ["pr", "comment", str(pr_number), "--body", args.body]
    if slug:
        cmd.extend(["--repo", slug])
    run_gh(*cmd)
    _output({"pr_number": pr_number, "commented": True})


# ── Main ──────────────────────────────────────────────────────────────


def main():
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--repo",
        help="Target repository as OWNER/REPO (default: auto-detect from cwd)",
    )
    shared.add_argument(
        "--pr", type=int,
        help="Target PR number (default: auto-detect from current branch)",
    )

    parser = argparse.ArgumentParser(
        description="PR review feedback CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # Fetch commands
    sub.add_parser("threads", help="Fetch unresolved inline review threads", parents=[shared])
    sub.add_parser("ci", help="Fetch CI check status", parents=[shared])
    sub.add_parser("comments", help="Fetch general PR comments and description", parents=[shared])

    # Action commands
    p_resolve = sub.add_parser("resolve", help="Resolve a review thread", parents=[shared])
    p_resolve.add_argument("thread_id", help="GraphQL node ID of the thread")

    p_react = sub.add_parser("react", help="Add thumbs-up reaction", parents=[shared])
    p_react.add_argument(
        "comment_type", choices=["review", "issue"],
        help="Comment type (review=inline, issue=general)",
    )
    p_react.add_argument("database_id", help="REST API database ID of the comment")

    p_reply = sub.add_parser("reply", help="Reply to a review thread comment", parents=[shared])
    p_reply.add_argument("database_id", help="Database ID of comment to reply to")
    p_reply.add_argument("body", help="Reply text")

    p_comment = sub.add_parser("comment", help="Leave a general PR comment", parents=[shared])
    p_comment.add_argument("body", help="Comment text")

    args = parser.parse_args()

    handlers = {
        "threads": cmd_threads,
        "ci": cmd_ci,
        "comments": cmd_comments,
        "resolve": cmd_resolve,
        "react": cmd_react,
        "reply": cmd_reply,
        "comment": cmd_comment,
    }

    try:
        handlers[args.command](args)
    except subprocess.CalledProcessError as e:
        print(f"gh error: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Parse error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
