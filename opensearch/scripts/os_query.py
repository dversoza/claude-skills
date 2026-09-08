#!/usr/bin/env python3
"""Read-only OpenSearch / Elasticsearch query tool.

Thin wrapper around the OpenSearch REST API over stdlib urllib. The tool only
ever issues read endpoints (_cluster/health, _cat/indices, _mapping, _count,
_search, _doc GET). There is no subcommand that creates, updates, or deletes
data or indices, so it is read-only by construction.

Cluster connection URLs are resolved from aliases defined in CLAUDE.local.md so
that credentials never appear on the command line. Basic-auth credentials are
taken from the URL userinfo (https://user:pass@host:443) and sent as an
Authorization header; the userinfo is stripped before the URL is ever printed.

Subcommands:
    list                                       List configured cluster aliases
    health   <alias>                           Cluster health
    indices  <alias> [--pattern GLOB]          List indices (doc counts, size)
    mapping  <alias> <index> [--field NAME]    Show field mappings
    count    <alias> <index> [--query JSON]    Count docs (optional query body)
    search   <alias> <index> <body-json>       Run a _search request body
                       [--size N] [--source f1,f2] [--explain]
    get      <alias> <index> <doc_id>          Fetch a single document by _id

All subcommands return JSON to stdout. Errors go to stderr as JSON with an
`error` field.
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# -- Connection resolution ------------------------------------------------


def _find_claude_local_md():
    """Walk up from cwd looking for CLAUDE.local.md."""
    directory = os.getcwd()
    while True:
        candidate = os.path.join(directory, "CLAUDE.local.md")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:
            return None
        directory = parent


def _parse_clusters(filepath):
    """Extract alias -> {url, options} from an ```opensearch-clusters``` fenced block.

    A line is `alias=url [key=value ...]`. Recognized options: `tls=skip`
    turns off certificate verification for that cluster (declare it once
    instead of passing --no-verify-tls on every call); `aws=<profile>/<domain>`
    enables endpoint rediscovery for an AWS-managed domain.
    """
    with open(filepath) as f:
        content = f.read()

    pattern = r"```opensearch-clusters\s*\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return {}

    clusters = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        alias, remainder = line.split("=", 1)
        url, *option_tokens = remainder.strip().split()
        options = {}
        for token in option_tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                options[key] = value
        clusters[alias.strip()] = {"url": url, "options": options}

    return clusters


def _die(payload):
    print(json.dumps(payload), file=sys.stderr)
    sys.exit(1)


def _resolve_alias(alias):
    """Resolve a cluster alias to its (url, options, source_filepath) triple."""
    filepath = _find_claude_local_md()
    if not filepath:
        _die(
            {
                "error": "No CLAUDE.local.md found in current or parent directories.",
                "hint": "Create CLAUDE.local.md with an ```opensearch-clusters``` block.",
            }
        )

    clusters = _parse_clusters(filepath)
    if not clusters:
        _die(
            {
                "error": f"No ```opensearch-clusters``` block found in {filepath}.",
                "hint": "Add an ```opensearch-clusters``` block with alias=url entries.",
            }
        )

    if alias not in clusters:
        _die({"error": f"Unknown cluster alias: {alias}", "available": list(clusters)})

    entry = clusters[alias]
    return entry["url"], entry["options"], filepath


# -- Endpoint rediscovery --------------------------------------------------
#
# A managed OpenSearch domain sits behind several ENIs and their private IPs
# change when the domain is resized, patched, or a node is replaced. The alias
# stores one host, so a cached IP goes stale (or points at a node that is no
# longer reachable) with no warning; the symptom is a connection timeout that
# looks like a VPN problem. When an alias declares `aws=<profile>/<domain>`
# we re-derive the domain's current ENI IPs, probe them, and rewrite the alias
# with the one that answers.
#
# The host must carry the IP: a bare IPv4 (`10.0.1.2`) or a dashed IPv4 plus a
# suffix (`10-0-1-2-via-1.example.ts.net`). A plain DNS name is left alone.

_BARE_IP_HOST = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_DASHED_IP_HOST = re.compile(r"^(\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3})([-.].+)$")


def _swap_host_ip(url, new_ip):
    """Return `url` with its IP-bearing host pointed at `new_ip`, or None."""
    parts = urllib.parse.urlsplit(url)
    hostname = parts.hostname or ""
    if _BARE_IP_HOST.match(hostname):
        new_host = new_ip
    else:
        match = _DASHED_IP_HOST.match(hostname)
        if not match:
            return None
        new_host = new_ip.replace(".", "-") + match.group(2)
    userinfo, at, hostport = parts.netloc.rpartition("@")
    netloc = userinfo + at + hostport.replace(hostname, new_host, 1)
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def _discover_domain_ips(aws_option):
    """Return (ips, error) for `aws=<profile>/<domain>` via the AWS CLI."""
    if "/" not in aws_option:
        return [], f"Malformed aws option {aws_option!r}; expected <profile>/<domain>."
    profile, domain = (part.strip() for part in aws_option.split("/", 1))

    def run(args, timeout):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return None
        except (OSError, subprocess.SubprocessError):
            return None

    region_proc = run(
        ["aws", "configure", "get", "region", "--profile", profile], timeout=15
    )
    if region_proc is None:
        return [], "aws CLI not available."
    region = region_proc.stdout.strip()

    command = [
        "aws",
        "ec2",
        "describe-network-interfaces",
        "--profile",
        profile,
        "--filters",
        f"Name=description,Values=*{domain}*",
        "--query",
        "NetworkInterfaces[].PrivateIpAddress",
        "--output",
        "json",
    ]
    if region:
        command += ["--region", region]

    proc = run(command, timeout=60)
    if proc is None:
        return [], "aws CLI not available."
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        if detail:
            return [], detail[-1]
        return [], "aws ec2 describe-network-interfaces failed."
    try:
        return json.loads(proc.stdout or "[]"), None
    except ValueError:
        return [], "Could not parse aws CLI output."


def _probe(url, insecure, timeout):
    """True if the cluster answers on `url`. Auth failures still prove reach."""
    base, auth_header = _split_url(url)
    headers = {"Accept": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    request = urllib.request.Request(
        base + "/_cluster/health", method="GET", headers=headers
    )

    context = None
    if base.startswith("https"):
        context = ssl.create_default_context()
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        return e.code in (401, 403)
    except Exception:
        return False


def _rewrite_alias_host(filepath, alias, old_url, new_url):
    """Point `alias` at `new_url`'s host in place, leaving credentials alone."""
    old_host = urllib.parse.urlsplit(old_url).hostname
    new_host = urllib.parse.urlsplit(new_url).hostname
    if not old_host or not new_host:
        return False

    with open(filepath) as f:
        lines = f.readlines()

    for index, line in enumerate(lines):
        if line.strip().startswith(f"{alias}=") and old_host in line:
            lines[index] = line.replace(old_host, new_host, 1)
            break
    else:
        return False

    with open(filepath, "w") as f:
        f.writelines(lines)
    return True


def _rediscover_url(alias, url, options, filepath, insecure, probe_timeout):
    """Find a reachable IP for `alias` and re-cache it. Returns (url, note)."""
    aws_option = options.get("aws")
    if not aws_option:
        return None, None
    if _swap_host_ip(url, "0.0.0.0") is None:
        return None, "Alias host does not embed an IP; cannot substitute one."

    ips, error = _discover_domain_ips(aws_option)
    if error:
        return None, f"Endpoint rediscovery failed: {error}"

    stale_host = urllib.parse.urlsplit(url).hostname
    candidates = [
        candidate
        for candidate in (_swap_host_ip(url, ip) for ip in ips)
        if candidate and urllib.parse.urlsplit(candidate).hostname != stale_host
    ]
    if not candidates:
        return None, f"No alternative ENI IPs found for {aws_option}."

    for candidate in candidates:
        if not _probe(candidate, insecure, probe_timeout):
            continue
        host = urllib.parse.urlsplit(candidate).hostname
        if _rewrite_alias_host(filepath, alias, url, candidate):
            note = (
                f"Alias '{alias}' re-cached to {host} (previous host was unreachable)."
            )
        else:
            note = f"Reached {host}; could not update {filepath}. Update it by hand."
        return candidate, note

    tried = ", ".join(urllib.parse.urlsplit(c).hostname for c in candidates)
    return None, f"No reachable endpoint. Tried: {tried}."


def _split_url(url):
    """Split a connection URL into (base_url_without_userinfo, auth_header_or_None).

    `auth_header` is the value for the Authorization header, or None.
    """
    parsed = urllib.parse.urlsplit(url)
    auth_header = None
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, hostport = netloc.rsplit("@", 1)
        import base64

        token = base64.b64encode(userinfo.encode()).decode()
        auth_header = f"Basic {token}"
        netloc = hostport
    base = urllib.parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")
    )
    return base, auth_header


# -- HTTP -----------------------------------------------------------------


def _send(url, method, path, data, headers, insecure, timeout):
    """Send one request. Returns (raw_body, None) or (None, URLError)."""
    base, auth_header = _split_url(url)
    full = base + path
    if auth_header:
        headers = dict(headers, Authorization=auth_header)

    req = urllib.request.Request(full, data=data, method=method, headers=headers)

    context = None
    if full.startswith("https"):
        context = ssl.create_default_context()
        if insecure:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.read().decode(), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail)
        except ValueError:
            pass
        _die({"error": f"HTTP {e.code}", "detail": detail})
    except ssl.SSLError as e:
        _die(
            {
                "error": f"TLS error: {e}",
                "hint": "If the cert is self-signed, add `tls=skip` to the "
                "alias in CLAUDE.local.md (or retry with --no-verify-tls).",
            }
        )
    except urllib.error.URLError as e:
        return None, e


def _request(alias, method, path, body=None, params=None, insecure=False, timeout=30):
    """Issue an HTTP request to the cluster and return parsed JSON.

    `path` is appended to the resolved base URL (must start with '/').
    Read-only: callers only ever pass read endpoints.

    If the connection fails and the alias declares `aws=<profile>/<domain>`,
    the domain's current ENI IPs are rediscovered, probed, and the reachable
    one is written back to the alias before the request is retried once.
    """
    url, options, filepath = _resolve_alias(alias)
    insecure = insecure or options.get("tls") == "skip"

    if params:
        path += "?" + urllib.parse.urlencode(params)

    data = None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()

    raw, error = _send(url, method, path, data, headers, insecure, timeout)

    if error is not None:
        new_url, note = _rediscover_url(
            alias, url, options, filepath, insecure, probe_timeout=min(timeout, 10)
        )
        if new_url:
            print(json.dumps({"note": note}), file=sys.stderr)
            raw, retry_error = _send(
                new_url, method, path, data, headers, insecure, timeout
            )
            if retry_error is not None:
                _die(
                    {
                        "error": f"Connection failed: {retry_error.reason}",
                        "detail": note,
                    }
                )
        else:
            _die(
                {
                    "error": f"Connection failed: {error.reason}",
                    "detail": note,
                    "hint": "Usually a stale endpoint IP or a VPN route "
                    "problem, never a certificate one, so "
                    "--no-verify-tls will not help. Add "
                    "`aws=<profile>/<domain>` to the alias so the endpoint IP "
                    "is rediscovered and re-cached automatically.",
                }
            )

    if not raw:
        return {}
    return json.loads(raw)


def _output(data):
    json.dump(data, sys.stdout, indent=2, default=str)
    print()


# -- Subcommands -----------------------------------------------------------


def cmd_list(_args):
    filepath = _find_claude_local_md()
    if not filepath:
        _output({"clusters": [], "source": None})
        return
    clusters = _parse_clusters(filepath)
    _output({"clusters": list(clusters), "source": filepath})


def cmd_health(args):
    resp = _request(
        args.alias,
        "GET",
        "/_cluster/health",
        insecure=args.insecure,
        timeout=args.timeout,
    )
    keys = (
        "cluster_name",
        "status",
        "number_of_nodes",
        "active_shards",
        "number_of_pending_tasks",
    )
    _output({k: resp.get(k) for k in keys})


def cmd_indices(args):
    params = {
        "format": "json",
        "s": "index",
        "h": "index,health,status,docs.count,store.size",
    }
    if args.pattern:
        path = f"/_cat/indices/{urllib.parse.quote(args.pattern, safe='*')}"
    else:
        path = "/_cat/indices"
    resp = _request(
        args.alias,
        "GET",
        path,
        params=params,
        insecure=args.insecure,
        timeout=args.timeout,
    )
    _output({"indices": resp})


def cmd_mapping(args):
    resp = _request(
        args.alias,
        "GET",
        f"/{urllib.parse.quote(args.index)}/_mapping",
        insecure=args.insecure,
        timeout=args.timeout,
    )
    # Unwrap to the single index's properties for readability.
    props = {}
    for idx, body in resp.items():
        props = body.get("mappings", {}).get("properties", {})
        break
    if args.field:
        props = {k: v for k, v in props.items() if args.field.lower() in k.lower()}
    _output({"index": args.index, "properties": props})


def _parse_body(raw, label):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as e:
        _die({"error": f"Invalid JSON for {label}: {e}"})


def cmd_count(args):
    body = _parse_body(args.query, "--query")
    resp = _request(
        args.alias,
        "POST" if body else "GET",
        f"/{urllib.parse.quote(args.index)}/_count",
        body=body,
        insecure=args.insecure,
        timeout=args.timeout,
    )
    _output({"index": args.index, "count": resp.get("count")})


def cmd_search(args):
    body = _parse_body(args.body, "search body") or {}
    if "size" not in body:
        body["size"] = args.size
    if args.source:
        body["_source"] = [s.strip() for s in args.source.split(",")]
    if args.explain:
        body["explain"] = True
    resp = _request(
        args.alias,
        "POST",
        f"/{urllib.parse.quote(args.index)}/_search",
        body=body,
        insecure=args.insecure,
        timeout=args.timeout,
    )
    hits = resp.get("hits", {})
    total = hits.get("total", {})
    _output(
        {
            "took_ms": resp.get("took"),
            "timed_out": resp.get("timed_out"),
            "total": total.get("value") if isinstance(total, dict) else total,
            "hits": [
                {
                    "_id": h.get("_id"),
                    "_score": h.get("_score"),
                    "_source": h.get("_source"),
                    **(
                        {"_explanation": h["_explanation"]}
                        if "_explanation" in h
                        else {}
                    ),
                }
                for h in hits.get("hits", [])
            ],
            **(
                {"aggregations": resp["aggregations"]} if "aggregations" in resp else {}
            ),
        }
    )


def cmd_get(args):
    resp = _request(
        args.alias,
        "GET",
        f"/{urllib.parse.quote(args.index)}/_doc/{urllib.parse.quote(args.doc_id)}",
        insecure=args.insecure,
        timeout=args.timeout,
    )
    _output(resp)


# -- Main ------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Read-only OpenSearch query tool")

    def add_common(p):
        p.add_argument(
            "--no-verify-tls",
            "--insecure",
            dest="insecure",
            action="store_true",
            help="Skip TLS certificate verification (self-signed certs only). "
            "Prefer declaring `tls=skip` on the alias in CLAUDE.local.md.",
        )
        p.add_argument(
            "--timeout", type=int, default=30, help="Request timeout in seconds"
        )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List configured cluster aliases")

    p_health = sub.add_parser("health", help="Cluster health")
    p_health.add_argument("alias")
    add_common(p_health)

    p_indices = sub.add_parser("indices", help="List indices")
    p_indices.add_argument("alias")
    p_indices.add_argument(
        "--pattern", default=None, help="Index name glob (e.g. 'logs-*')"
    )
    add_common(p_indices)

    p_mapping = sub.add_parser("mapping", help="Show field mappings")
    p_mapping.add_argument("alias")
    p_mapping.add_argument("index")
    p_mapping.add_argument(
        "--field",
        default=None,
        help="Filter to fields whose name contains this substring",
    )
    add_common(p_mapping)

    p_count = sub.add_parser("count", help="Count documents")
    p_count.add_argument("alias")
    p_count.add_argument("index")
    p_count.add_argument(
        "--query",
        default=None,
        help="Optional query body JSON, e.g. '{\"query\":{...}}'",
    )
    add_common(p_count)

    p_search = sub.add_parser("search", help="Run a _search request body")
    p_search.add_argument("alias")
    p_search.add_argument("index")
    p_search.add_argument(
        "body", help="_search request body JSON, e.g. '{\"query\":{...}}'"
    )
    p_search.add_argument(
        "--size",
        type=int,
        default=10,
        help="Result size if not set in body (default 10)",
    )
    p_search.add_argument(
        "--source", default=None, help="Comma-separated _source fields to return"
    )
    p_search.add_argument(
        "--explain",
        action="store_true",
        help="Include Lucene scoring explanation per hit",
    )
    add_common(p_search)

    p_get = sub.add_parser("get", help="Fetch a document by _id")
    p_get.add_argument("alias")
    p_get.add_argument("index")
    p_get.add_argument("doc_id")
    add_common(p_get)

    args = parser.parse_args()

    handlers = {
        "list": cmd_list,
        "health": cmd_health,
        "indices": cmd_indices,
        "mapping": cmd_mapping,
        "count": cmd_count,
        "search": cmd_search,
        "get": cmd_get,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
