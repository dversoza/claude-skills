#!/usr/bin/env python3
"""Read-only Zyte API stats and cost reporting.

Credentials resolve from a ```zyte-api``` fenced block in CLAUDE.local.md so
the key never appears on the command line or in output. Only GET endpoints of
the stats service are called, so this cannot spend credits or change anything.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone

STATS_URL = "https://zyte-api-stats.zyte.com/api/stats"
EXTRACT_URL = "https://api.zyte.com/v1/extract"
MICRO_USD = 1_000_000


def _find_claude_local_md():
    directory = os.getcwd()
    while True:
        candidate = os.path.join(directory, "CLAUDE.local.md")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def _parse_credentials(filepath):
    """Extract key=value pairs from a ```zyte-api``` fenced block."""
    with open(filepath) as f:
        content = f.read()
    match = re.search(r"```zyte-api\s*\n(.*?)```", content, re.DOTALL)
    if not match:
        return {}
    credentials = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        credentials[name.strip()] = value.strip()
    return credentials


def _load_credentials(required):
    filepath = _find_claude_local_md()
    if not filepath:
        _die(
            "No CLAUDE.local.md found in current or parent directories.",
            "Add a ```zyte-api``` block with api_key=, dashboard_api_key= and org_id=.",
        )
    credentials = _parse_credentials(filepath)
    missing = [f for f in required if not credentials.get(f)]
    if missing:
        _die(
            f"Missing {', '.join(missing)} in the ```zyte-api``` block of {filepath}.",
            "api_key is the scraping key (ZYTE_API_KEY). dashboard_api_key is a "
            "different key, from app.zyte.com/o/settings. org_id is in the "
            "app.zyte.com/o/<id>/ URL.",
        )
    return credentials


def _die(error, hint=None):
    payload = {"error": error}
    if hint:
        payload["hint"] = hint
    print(json.dumps(payload, indent=2))
    sys.exit(1)


def _request(url, api_key):
    header = b64encode(f"{api_key}:".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {header}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300]
        if exc.code == 403:
            _die(
                f"Zyte stats API returned 403: {body}",
                "The stats API needs dashboard_api_key from app.zyte.com/o/settings, "
                "which is NOT the Zyte API key the scrapers use. Check org_id too.",
            )
        _die(f"Zyte stats API returned {exc.code}: {body}")
    except urllib.error.URLError as exc:
        _die(f"Cannot reach the Zyte stats API: {exc.reason}")


def _window(args):
    if args.start:
        start = args.start
    else:
        days = timedelta(days=args.days)
        start = (datetime.now(timezone.utc) - days).strftime("%Y-%m-%dT00:00:00Z")
    end = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return start, end


def _fetch_rows(args, credentials):
    start, end = _window(args)
    query = {
        "organization_id": credentials["org_id"],
        "start_time": start,
        "end_time": end,
        "page_size": args.page_size,
    }
    if args.group_by:
        query["groupby_time"] = args.group_by
    if args.by_domain:
        query["groupby_domain"] = "true"
    url = f"{STATS_URL}?{urllib.parse.urlencode(query)}"
    payload = _request(url, credentials["dashboard_api_key"])
    return payload.get("data", payload.get("results", [])), start, end


def _number(value):
    """The stats API returns its numeric fields as strings."""
    if value is None:
        return None
    return float(value)


def _summarize(row):
    cost = _number(row.get("cost_microusd_total"))
    summary = {
        "requests": _number(row.get("request_count")),
        "cost_usd": round(cost / MICRO_USD, 4) if cost is not None else None,
    }
    for key in ("time", "domain"):
        if row.get(key) is not None:
            summary[key] = row[key]
    average = _number(row.get("cost_microusd_avg"))
    if average is not None:
        summary["cost_per_1k_usd"] = round(average / MICRO_USD * 1000, 4)
    if row.get("status_codes"):
        codes = row["status_codes"]
        summary["status_codes"] = {str(c["code"]): _number(c["count"]) for c in codes}
        billed = sum(_number(c["count"]) for c in codes if c["code"] == 200)
        summary["free_responses"] = (_number(row.get("request_count")) or 0) - billed
    latency = _number(row.get("response_time_sec_avg"))
    if latency is not None:
        summary["response_time_sec_avg"] = round(latency, 2)
    return summary


def cmd_usage(args, credentials):
    rows, start, end = _fetch_rows(args, credentials)
    summaries = [_summarize(r) for r in rows]
    total_requests = sum(s["requests"] or 0 for s in summaries)
    total_cost = sum(s["cost_usd"] or 0 for s in summaries)
    print(
        json.dumps(
            {
                "window": {"start": start, "end": end},
                "total_requests": total_requests,
                "total_cost_usd": round(total_cost, 4),
                "rows": summaries,
            },
            indent=2,
        )
    )


def cmd_estimate(args, credentials):
    """Project a run's cost from its request count at the observed rate."""
    rows, start, end = _fetch_rows(args, credentials)
    requests = sum(_number(r.get("request_count")) or 0 for r in rows)
    cost = sum(_number(r.get("cost_microusd_total")) or 0 for r in rows) / MICRO_USD
    if not requests:
        _die("No requests in the window, so there is no observed rate to project from.")
    rate = cost / requests
    print(
        json.dumps(
            {
                "window": {"start": start, "end": end},
                "observed": {
                    "requests": requests,
                    "cost_usd": round(cost, 4),
                    "cost_per_request_usd": round(rate, 8),
                    "cost_per_1k_usd": round(rate * 1000, 4),
                },
                "projection": {
                    "requests": args.requests,
                    "cost_usd": round(rate * args.requests, 2),
                },
            },
            indent=2,
        )
    )


def cmd_key_check(args, credentials):
    """Confirm the key is live without spending credits on a real target."""
    body = json.dumps({"url": "https://httpbin.org/ip", "httpResponseBody": True})
    header = b64encode(f"{credentials['api_key']}:".encode()).decode()
    req = urllib.request.Request(
        EXTRACT_URL,
        data=body.encode(),
        headers={"Authorization": f"Basic {header}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            print(json.dumps({"api_key": "live", "status": response.status}, indent=2))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200]
        print(json.dumps({"api_key": "rejected", "status": exc.code, "detail": detail}, indent=2))
        sys.exit(1)
    except urllib.error.URLError as exc:
        _die(f"Cannot reach the Zyte extract API: {exc.reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", help="ISO 8601 start, e.g. 2026-08-18T00:00:00Z")
    parser.add_argument("--end", help="ISO 8601 end (default: now)")
    parser.add_argument("--days", type=int, default=7, help="window size when --start is absent")
    parser.add_argument("--group-by", choices=["hour", "day", "month", "year"])
    parser.add_argument("--by-domain", action="store_true", help="break down by domain")
    parser.add_argument("--page-size", type=int, default=500)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("usage", help="requests and cost over a window")
    estimate = sub.add_parser("estimate", help="project cost for a request count")
    estimate.add_argument("requests", type=int, help="requests to project")
    sub.add_parser("key-check", help="confirm the API key is live")

    args = parser.parse_args()
    commands = {"usage": cmd_usage, "estimate": cmd_estimate, "key-check": cmd_key_check}
    # key-check proves the key is live, which is the thing to ask when the
    # org_id is unknown or the account is suspended.
    if args.command == "key-check":
        required = ("api_key",)
    else:
        required = ("dashboard_api_key", "org_id")
    commands[args.command](args, _load_credentials(required))


if __name__ == "__main__":
    main()
