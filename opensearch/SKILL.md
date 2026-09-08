---
name: opensearch
description: Read-only OpenSearch/Elasticsearch querying, index introspection, and search-query (DSL) execution. Use when the user asks to query OpenSearch or Elasticsearch, run a search query/DSL, check a cluster's health, list indices, inspect an index mapping, count documents, fetch a document by id, or debug why a search does/doesn't match. Triggers on requests like "query opensearch", "run this search DSL", "list the indices", "show the users mapping", "how many docs in X", "why doesn't this wildcard match", "check the cluster health".
---

# OpenSearch

Read-only access to OpenSearch / Elasticsearch clusters via the REST API. The
tool only issues read endpoints (`_cluster/health`, `_cat/indices`, `_mapping`,
`_count`, `_search`, `GET _doc`). No subcommand creates, updates, or deletes
data or indices, so it is read-only by construction.

## Connection Setup

The script resolves cluster aliases from an `` ```opensearch-clusters``` ``
fenced code block in the project's `CLAUDE.local.md`. It searches from the
current working directory upward. Credentials never appear on the command line;
only the alias is passed as an argument. Basic-auth credentials live in the URL
userinfo (`https://user:pass@host:443`); the script sends them as an
`Authorization` header and strips the userinfo before any URL is printed.

To discover available aliases:

```bash
python3 ~/.claude/skills/opensearch/scripts/os_query.py list
```

If no `CLAUDE.local.md` exists or it has no `opensearch-clusters` block, ask the
user for connection details and suggest they add a block like this to their
project's `CLAUDE.local.md`:

````markdown
```opensearch-clusters
local=http://localhost:9200
dev=https://admin:PASSWORD@opensearch.internal.example:443 tls=skip
prod=https://admin:PASSWORD@10.0.1.2:443 tls=skip aws=prod/my-domain
```
````

An alias line is `alias=url` followed by optional `key=value` options:

`tls=skip` turns off certificate verification for that cluster. A managed
OpenSearch domain addressed by private IP serves a cert that can never
validate, so declare it once on those aliases rather than passing a flag on
every call. Do not set it on a cluster whose cert is expected to validate; a
TLS failure there is a real signal.

`aws=<profile>/<domain>` enables endpoint rediscovery for AWS-managed domains.
A managed domain sits behind ENIs whose private IPs change when it is resized,
patched, or a node is replaced, and not every live ENI IP is reachable over a
given VPN route. A cached host therefore goes dead with no warning, and the
symptom is a connection timeout that looks like a VPN outage. With this option
set, a failed connection triggers a lookup of the domain's current ENI IPs
(`aws ec2 describe-network-interfaces --profile <profile>`), probes each one,
rewrites the alias host in `CLAUDE.local.md` with the one that answers, and
retries the request. The re-cache is reported as a `note` on stderr; only the
host is rewritten, credentials and options are left alone. Requires the named
AWS profile to be authenticated, and a host that carries the IP: a bare IPv4
(`10.0.1.2`) or a dashed IPv4 followed by a suffix (`10-0-1-2-via-1.example.ts.net`).
A plain DNS name is left alone.

Remind the user that `CLAUDE.local.md` should be in `.gitignore` to keep
credentials out of version control.

Do NOT pass connection URLs on the command line. Always use the alias.

## Subcommands

```bash
python3 ~/.claude/skills/opensearch/scripts/os_query.py list
python3 ~/.claude/skills/opensearch/scripts/os_query.py health <alias>
python3 ~/.claude/skills/opensearch/scripts/os_query.py indices <alias> [--pattern 'logs-*']
python3 ~/.claude/skills/opensearch/scripts/os_query.py mapping <alias> <index> [--field email]
python3 ~/.claude/skills/opensearch/scripts/os_query.py count <alias> <index> [--query '{"query":{...}}']
python3 ~/.claude/skills/opensearch/scripts/os_query.py search <alias> <index> '{"query":{...}}' [--size N] [--source f1,f2] [--explain]
python3 ~/.claude/skills/opensearch/scripts/os_query.py get <alias> <index> <doc_id>
```

The `search` body is the full `_search` request body (so you control `query`,
`size`, `sort`, `aggs`, etc.). `--size` and `--source` are conveniences applied
only when the body does not already set them. `--explain` adds Lucene scoring
explanations per hit; use it to debug why a document does or does not match.

All subcommands take optional `--no-verify-tls` (skip TLS certificate
verification for this call; `--insecure` is kept as an alias) and
`--timeout SECONDS` (default 30). Prefer `tls=skip` on the alias over the flag.
Output is JSON to stdout; errors are JSON with an `error` field on stderr.

A connection timeout is never a certificate problem; skipping TLS verification
will not fix it. Only a `TLS error` in the output is. The usual cause is a
stale endpoint IP, which `aws=<profile>/<domain>` repairs on its own; failing
that, check the VPN route and the security group.

## Query Guidelines

- Always cap result size. `search` defaults to `size: 10` if the body omits it;
  do not remove the cap to "see everything". Narrow the query instead.
- Multi-tenant indices (e.g. a shared index keyed by `tenant_id`) are NOT
  scoped automatically. Add the tenant filter yourself, e.g.
  `{"query":{"bool":{"filter":[{"term":{"tenant_id":NNN}}], "must":[...]}}}`.
- Watch `took_ms` in search output; it is the server-side latency. A leading
  wildcard (`*foo*`) on a plain `keyword` field scans the whole term dictionary
  and is slow on large indices; query a `wildcard`-type field or its `.wildcard`
  subfield instead.
- Use `mapping` before writing a query to confirm field names and types
  (`keyword` vs `text` vs `wildcard` vs `long` change which query clause works).
- Writes are not possible: the tool exposes only read endpoints. If you need to
  create or reindex an index, use the application's management commands, not
  this skill.

## Requirements

- Python 3.6+ (standard library only; no `opensearch-py` or `requests` needed)
- Network access to the cluster (VPN for VPC clusters)
- `aws` CLI with an authenticated profile, only for `aws=` endpoint rediscovery
- Cluster aliases in an `opensearch-clusters` block in the project's `CLAUDE.local.md`
