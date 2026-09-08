---
name: redis
description: Read-only Redis / ElastiCache querying, key inspection, and cache debugging. Use when the user asks to query Redis, check a Celery queue depth, count keys, inspect or dump a key, check a TTL, look at the session cache, check Redis memory or evictions, see connected clients, read the slowlog, or find out whether something is cached. Triggers on requests like "query redis", "check the celery queue depth", "how many keys in redis", "inspect this redis key", "check redis memory", "look at the session cache", "is this cached", "what's the TTL on X", "is the queue backed up".
---

# Redis

Read-only access to Redis / ElastiCache instances. Every command goes through a
single choke point that validates the resolved command name -- and its
subcommand, for container commands like `CONFIG`/`CLIENT`/`DEBUG` -- against a
hard allowlist of read-only commands. Anything not on the allowlist is rejected
before a connection is used.

## Connection Setup

The script resolves instance aliases from a `` ```redis-instances``` `` fenced
code block in the project's `CLAUDE.local.md`. It searches from the current working
directory upward. Credentials never appear on the command line -- only the alias
is passed as an argument.

To discover available aliases:

```bash
python3 ~/.claude/skills/redis/scripts/redis_query.py list
```

If no `CLAUDE.local.md` exists or it has no `redis-instances` block, ask the user
for connection details and suggest they add a block like this:

````markdown
```redis-instances
local=redis://:password@localhost:6379/0
staging=rediss://user:PASSWORD@redis-staging.internal.example:6379/0 tls=skip
prod=rediss://user:PASSWORD@redis.internal.example:6379 db=0 tls=skip
```
````

An alias line is `alias=url` followed by optional `key=value` options:

`tls=skip` turns off certificate verification for that instance. A managed
node addressed by private IP serves a cert issued for its DNS name (for
ElastiCache, `*.cache.amazonaws.com`), so verification can never pass --
declare it once on those aliases rather than passing a flag on every call. Do
not set it on an instance whose cert is expected to validate; a TLS failure
there is a real signal.

`db=N` sets the default db index for the alias when the URL carries no `/N`
path. `--db N` on any call overrides both.

Remind the user that `CLAUDE.local.md` must be in `.gitignore` -- these URLs
carry the Redis password.

Do NOT pass connection URLs on the command line. Always use the alias.

## Subcommands

```bash
python3 ~/.claude/skills/redis/scripts/redis_query.py list
python3 ~/.claude/skills/redis/scripts/redis_query.py info <alias> [--section memory] [--full]
python3 ~/.claude/skills/redis/scripts/redis_query.py dbsize <alias>
python3 ~/.claude/skills/redis/scripts/redis_query.py scan <alias> '<pattern>' [--limit 100] [--type hash]
python3 ~/.claude/skills/redis/scripts/redis_query.py get <alias> <key> [--limit 100]
python3 ~/.claude/skills/redis/scripts/redis_query.py ttl <alias> <key>
python3 ~/.claude/skills/redis/scripts/redis_query.py memory <alias> <key>
python3 ~/.claude/skills/redis/scripts/redis_query.py slowlog <alias> [--limit 10]
python3 ~/.claude/skills/redis/scripts/redis_query.py clients <alias> [--limit 25]
python3 ~/.claude/skills/redis/scripts/redis_query.py cmd <alias> <read-only command...>
```

`get` is type-aware: it reads `TYPE`, dispatches to the right read command, and
returns the value together with its TTL and length. Collections larger than
`--limit` are read with `HSCAN`/`SSCAN` rather than `HGETALL`/`SMEMBERS`, so it
is safe to point at a large key on prod.

`scan` always uses cursor-based `SCAN` with `COUNT` batching and a hard limit --
never a blocking `KEYS`. The reply carries `truncated`, `scan_complete`, and the
`cursor` so a partial result is never mistaken for a complete one.

`cmd` is the allowlisted passthrough for anything the named subcommands do not
cover. Put a bare `--` before the command when an argument starts with a dash:

```bash
python3 ~/.claude/skills/redis/scripts/redis_query.py cmd prod --db 0 LLEN celery
python3 ~/.claude/skills/redis/scripts/redis_query.py cmd prod CONFIG GET maxmemory-policy
python3 ~/.claude/skills/redis/scripts/redis_query.py cmd prod --db 2 -- ZRANGEBYSCORE somekey -inf +inf
```

Every subcommand accepts `--db N`, `--timeout SECONDS` (default 30),
`--no-verify-tls` (alias `--insecure`; prefer `tls=skip` on the alias), and
`--backend {auto,redis-py,redis-cli}`.

Output is JSON on stdout; errors are JSON with an `error` field on stderr.

## Read-only guarantee, and its limits

Enforcement is an allowlist, not a blocklist: a command absent from the table is
rejected, so new or unknown commands fail closed. The check runs on the resolved
command token, not a substring match, so a key named `GET` or a value containing
`FLUSHALL` cannot smuggle anything through. Redis has no command chaining, so
there is no injection path through arguments either.

Rejected: every write and admin command, including `SET`, `DEL`, `EXPIRE`,
`RENAME`, `FLUSHALL`/`FLUSHDB`, `EVAL`/`SCRIPT`, `CONFIG SET`, `CONFIG
RESETSTAT`, `DEBUG SEGFAULT`, `DEBUG SLEEP`, `CLIENT KILL`, `SHUTDOWN`,
`MIGRATE`, `SWAPDB`, `MEMORY PURGE`, `SLOWLOG RESET`, `LATENCY RESET`, `SORT`
(it has a `STORE` variant -- `SORT_RO` is allowed), and `TOUCH` (it mutates
idle time / LRU).

Limits worth stating plainly:

- This is client-side enforcement, not a server-side read-only mode. It is a
  guardrail against accidents, not a substitute for a restricted Redis ACL or
  ElastiCache RBAC user. If you need a hard guarantee on prod, connect as a user
  whose access string grants only `+@read`.
- `KEYS` is on the allowlist for `cmd`, and it blocks the server for the length
  of the scan. Use `scan` instead on anything but a local instance.
- Read commands can still be expensive. `LRANGE key 0 -1` on a million-element
  list, or `MEMORY USAGE` without `SAMPLES`, will hurt a busy instance.
- `DEBUG OBJECT` is allowlisted but Redis 7 refuses it unless
  `enable-debug-command` is set, so it usually errors server-side.

## Db numbers

One instance often hosts several logical dbs, one per concern. A common Django
plus Celery layout, as an example only:

| DB | Contents | Key shape |
| -- | -------- | --------- |
| 0 | Celery broker + result backend | one list per queue (`celery`, ...), plus `_kombu.binding.*` and `celery-task-meta-*` |
| 1 | application cache | framework-specific, often `:<version>:<name>` |
| 2 | sessions | framework-specific |

Check the project's settings for the real assignments before reading a db.

Queue depth is `LLEN <queue>` on the broker db. That is the count of waiting
tasks only; tasks already prefetched by a worker are not in the list:

```bash
python3 ~/.claude/skills/redis/scripts/redis_query.py cmd prod --db 0 LLEN celery
```

## Query Guidelines

- Reach for the named subcommands before `cmd`; they cap output and add context
  (TTL, type, length, truncation flags) that a raw reply does not carry.
- Always narrow `scan` with a pattern. `scan <alias> '*'` on a session db walks
  hundreds of thousands of keys.
- Check `truncated` / `scan_complete` in the output before concluding "there are
  only N keys". Truncation is always reported explicitly, never silent.
- Say which db you queried. A key "missing" from one db usually just lives in
  another.
- A connection timeout is not a certificate problem, and `tls=skip` will not fix
  it -- check the network route (VPN, tunnel) and the firewall or security
  group. Only a TLS/certificate error in the output is a cert problem.
- Writes are not possible through this skill. If a fix genuinely needs one
  (purging a stuck queue, expiring a session), hand it to the user with the
  exact command rather than looking for a way around the allowlist.

## Requirements

- Python 3, standard library only.
- A backend: the `redis` Python package or `redis-cli` on PATH
  (`brew install redis`). Auto-detected, `redis` first. Do not add `redis` as a
  project dependency just for this tool.
- Network access to the instance (VPN or tunnel for instances in a private
  network).
- Instance aliases in a `redis-instances` block in the project's
  `CLAUDE.local.md`.
