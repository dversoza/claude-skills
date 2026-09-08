#!/usr/bin/env python3
"""Read-only Redis / ElastiCache query tool.

Every command issued to the server passes through a single choke point
(`_Client.execute`) that validates the resolved command name -- and its
subcommand where one exists -- against a hard allowlist of read-only commands.
Anything not on the allowlist is rejected before a connection is used, so
writes, evictions, config changes, and admin operations are impossible through
this tool.

Connection URLs are resolved from aliases defined in CLAUDE.local.md so that
credentials never appear on the command line.

Backends, in order of preference:
    1. the `redis` Python package, if importable
    2. the `redis-cli` binary, if on PATH (password passed via REDISCLI_AUTH,
       never in argv)

Subcommands:
    list                                       List configured instance aliases
    info     <alias> [--section S] [--full]    Server INFO (summary by default)
    dbsize   <alias>                           Key count for the db + keyspace
    scan     <alias> <pattern> [--limit N]     Cursor-based key scan (never KEYS)
                       [--type T]
    get      <alias> <key> [--limit N]         Type-aware read of a key, with TTL
    ttl      <alias> <key>                     TTL / type / existence for a key
    memory   <alias> <key>                     Memory usage + encoding for a key
    slowlog  <alias> [--limit N]               Recent slow commands
    clients  <alias> [--limit N]               Connected clients
    cmd      <alias> <read-only command...>    Allowlisted command passthrough

All subcommands return JSON to stdout. Errors go to stderr as JSON with an
`error` field.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse

# Output caps. Redis values can be arbitrarily large; keep replies small by
# default and say so explicitly rather than silently dropping data.
MAX_VALUE_CHARS = 500
MAX_ELEMENTS = 100
SCAN_COUNT = 500
SCAN_MAX_ITERATIONS = 200


def _die(payload):
    print(json.dumps(payload), file=sys.stderr)
    sys.exit(1)


def _output(data):
    json.dump(data, sys.stdout, indent=2, default=str)
    print()


# -- Read-only enforcement -------------------------------------------------
#
# Allowlist, not blocklist: a command absent from this table is rejected. The
# check runs on the resolved command token (and subcommand token, for container
# commands like CONFIG/CLIENT/DEBUG), never as a substring match, so neither
# `CONFIG SET` nor a key named "GET" can slip past it.
#
# Value is None when the command has no subcommand to constrain, otherwise the
# set of subcommands that are read-only. Container commands whose other
# subcommands mutate state (CONFIG SET, CLIENT KILL, LATENCY RESET, SLOWLOG
# RESET, MEMORY PURGE, DEBUG SEGFAULT, ...) are constrained here.

READ_ONLY_COMMANDS = {
    # keyspace / generic
    "EXISTS": None,
    "EXPIRETIME": None,
    "KEYS": None,
    "PEXPIRETIME": None,
    "PTTL": None,
    "RANDOMKEY": None,
    "SCAN": None,
    "TTL": None,
    "TYPE": None,
    # strings
    "BITCOUNT": None,
    "BITPOS": None,
    "GET": None,
    "GETBIT": None,
    "GETRANGE": None,
    "MGET": None,
    "STRLEN": None,
    "SUBSTR": None,
    # hashes
    "HEXISTS": None,
    "HGET": None,
    "HGETALL": None,
    "HKEYS": None,
    "HLEN": None,
    "HMGET": None,
    "HRANDFIELD": None,
    "HSCAN": None,
    "HSTRLEN": None,
    "HVALS": None,
    # lists
    "LINDEX": None,
    "LLEN": None,
    "LPOS": None,
    "LRANGE": None,
    # sets
    "SCARD": None,
    "SDIFF": None,
    "SINTER": None,
    "SINTERCARD": None,
    "SISMEMBER": None,
    "SMEMBERS": None,
    "SMISMEMBER": None,
    "SRANDMEMBER": None,
    "SSCAN": None,
    "SUNION": None,
    # sorted sets
    "ZCARD": None,
    "ZCOUNT": None,
    "ZLEXCOUNT": None,
    "ZMSCORE": None,
    "ZRANDMEMBER": None,
    "ZRANGE": None,
    "ZRANGEBYLEX": None,
    "ZRANGEBYSCORE": None,
    "ZRANK": None,
    "ZREVRANGE": None,
    "ZREVRANGEBYLEX": None,
    "ZREVRANGEBYSCORE": None,
    "ZREVRANK": None,
    "ZSCAN": None,
    "ZSCORE": None,
    # streams
    "XLEN": None,
    "XPENDING": None,
    "XRANGE": None,
    "XREVRANGE": None,
    "XINFO": {"CONSUMERS", "GROUPS", "STREAM"},
    # server / introspection
    "DBSIZE": None,
    "INFO": None,
    "LASTSAVE": None,
    "LOLWUT": None,
    "PING": None,
    "ROLE": None,
    "SORT_RO": None,
    "TIME": None,
    "CLIENT": {"GETNAME", "ID", "INFO", "LIST"},
    "CLUSTER": {"COUNTKEYSINSLOT", "INFO", "KEYSLOT", "NODES", "SHARDS", "SLOTS"},
    "COMMAND": {"COUNT", "DOCS", "GETKEYS", "INFO", "LIST"},
    "CONFIG": {"GET"},
    "DEBUG": {"OBJECT"},
    "LATENCY": {"DOCTOR", "GRAPH", "HISTORY", "LATEST"},
    "MEMORY": {"DOCTOR", "STATS", "USAGE"},
    "OBJECT": {"ENCODING", "FREQ", "HELP", "IDLETIME", "REFCOUNT"},
    "SLOWLOG": {"GET", "LEN"},
}

_MISSING = object()


def _normalize_command(argv):
    """Validate argv against the read-only allowlist; return normalized tokens.

    Exits with a JSON error if the command -- or its subcommand -- is not an
    allowlisted read-only operation.
    """
    argv = [a for a in argv if a is not None]
    if len(argv) == 1 and isinstance(argv[0], str) and re.search(r"\s", argv[0]):
        # Tolerate a whole command quoted as one shell argument.
        argv = shlex.split(argv[0])
    if not argv:
        _die({"error": "Empty command."})

    name = str(argv[0]).strip().upper()
    allowed_subcommands = READ_ONLY_COMMANDS.get(name, _MISSING)
    if allowed_subcommands is _MISSING:
        _die(
            {
                "error": f"Command not allowed: {name}",
                "reason": "This tool is read-only. Only allowlisted read "
                "commands may run; everything else is rejected.",
                "allowed_commands": sorted(READ_ONLY_COMMANDS),
            }
        )

    if allowed_subcommands is None:
        return [name] + [str(a) for a in argv[1:]]

    if len(argv) < 2:
        _die(
            {
                "error": f"{name} requires a subcommand.",
                "allowed_subcommands": sorted(allowed_subcommands),
            }
        )
    subcommand = str(argv[1]).strip().upper()
    if subcommand not in allowed_subcommands:
        _die(
            {
                "error": f"Subcommand not allowed: {name} {subcommand}",
                "reason": "This tool is read-only. Only allowlisted read "
                "subcommands may run.",
                "allowed_subcommands": sorted(allowed_subcommands),
            }
        )
    return [name, subcommand] + [str(a) for a in argv[2:]]


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


def _parse_instances(filepath):
    """Extract alias -> {url, options} from a ```redis-instances``` fenced block.

    A line is `alias=url [key=value ...]`. Recognized options:
        tls=skip   skip TLS certificate verification for this instance
        db=N       default db index when the URL carries no /N path
    """
    with open(filepath) as f:
        content = f.read()

    pattern = r"```redis-instances\s*\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return {}

    instances = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        alias, remainder = line.split("=", 1)
        parts = remainder.strip().split()
        if not parts:
            continue
        url, option_tokens = parts[0], parts[1:]
        options = {}
        for token in option_tokens:
            if "=" in token:
                key, value = token.split("=", 1)
                options[key] = value
        instances[alias.strip()] = {"url": url, "options": options}

    return instances


def _resolve_alias(alias):
    """Resolve an instance alias to its (url, options) pair."""
    filepath = _find_claude_local_md()
    if not filepath:
        _die(
            {
                "error": "No CLAUDE.local.md found in current or parent directories.",
                "hint": "Create CLAUDE.local.md with a ```redis-instances``` block.",
            }
        )

    instances = _parse_instances(filepath)
    if not instances:
        _die(
            {
                "error": f"No ```redis-instances``` block found in {filepath}.",
                "hint": "Add a ```redis-instances``` block with alias=url entries.",
            }
        )

    if alias not in instances:
        _die(
            {
                "error": f"Unknown redis alias: {alias}",
                "available": list(instances),
            }
        )

    entry = instances[alias]
    return entry["url"], entry["options"]


def _split_url(url, db_override=None, db_default=None):
    """Split a connection URL into its parts, applying a db override.

    Returns a dict with scheme/host/port/username/password/db, plus `url`
    rebuilt with the effective db index (credentials intact).
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("redis", "rediss"):
        _die(
            {
                "error": f"Unsupported redis URL scheme: {parsed.scheme or '(none)'}",
                "hint": "Use redis://host:port/db or rediss://host:port/db.",
            }
        )

    db = None
    path = (parsed.path or "").strip("/")
    if path:
        if not path.isdigit():
            _die({"error": f"Invalid db index in redis URL path: /{path}"})
        db = int(path)
    if db_override is not None:
        db = db_override
    elif db is None and db_default is not None:
        if not str(db_default).isdigit():
            _die({"error": f"Invalid db option on alias: db={db_default}"})
        db = int(db_default)
    if db is None:
        db = 0

    rebuilt = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{db}", parsed.query, "")
    )
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "username": urllib.parse.unquote(parsed.username) if parsed.username else None,
        "password": urllib.parse.unquote(parsed.password) if parsed.password else None,
        "db": db,
        "url": rebuilt,
    }


# -- Backends --------------------------------------------------------------


class _RedisPyClient:
    """Backend using the `redis` Python package."""

    name = "redis-py"

    def __init__(self, conn, insecure, timeout):
        import redis  # optional dependency, probed at runtime

        self._redis_module = redis
        kwargs = {
            "socket_timeout": timeout,
            "socket_connect_timeout": timeout,
            "decode_responses": False,
        }
        if conn["scheme"] == "rediss" and insecure:
            kwargs["ssl_cert_reqs"] = "none"
            kwargs["ssl_check_hostname"] = False
        try:
            self._client = redis.Redis.from_url(conn["url"], **kwargs)
        except Exception as e:  # noqa: BLE001 -- surface as JSON, not a traceback
            _die({"error": f"{type(e).__name__}: {e}"})
        # redis-py flattens INFO into a single dict, losing the section
        # grouping that redis-cli preserves. Return it raw so both backends
        # feed the same parser and produce identical output.
        self._client.set_response_callback("INFO", lambda response: response)

    def execute(self, *argv):
        argv = _normalize_command(argv)
        try:
            return self._client.execute_command(*argv)
        except self._redis_module.exceptions.ResponseError as e:
            _die({"error": f"Redis error: {e}", "command": argv[0]})
        except Exception as e:  # noqa: BLE001
            _die(
                {
                    "error": f"{type(e).__name__}: {e}",
                    "hint": "Connection problems on private-network instances "
                    "are usually a network route (VPN, tunnel) or firewall issue, "
                    "not TLS. Only a certificate error is fixed by `tls=skip` on "
                    "the alias.",
                }
            )


class _RedisCliClient:
    """Backend shelling out to redis-cli.

    The password goes through the REDISCLI_AUTH environment variable so it
    never lands in argv (and therefore never in `ps` output).
    """

    name = "redis-cli"

    def __init__(self, conn, insecure, timeout):
        self._timeout = timeout
        self._env = os.environ.copy()
        if conn["password"]:
            self._env["REDISCLI_AUTH"] = conn["password"]
        else:
            self._env.pop("REDISCLI_AUTH", None)

        self._base = [
            "redis-cli",
            "-h",
            conn["host"],
            "-p",
            str(conn["port"]),
            "-n",
            str(conn["db"]),
            "--no-auth-warning",
            "--json",
        ]
        if conn["username"]:
            self._base += ["--user", conn["username"]]
        if conn["scheme"] == "rediss":
            self._base.append("--tls")
            if insecure:
                self._base.append("--insecure")

    def execute(self, *argv):
        argv = _normalize_command(argv)
        try:
            proc = subprocess.run(
                self._base + argv,
                capture_output=True,
                text=True,
                check=False,
                env=self._env,
                timeout=self._timeout + 5,
            )
        except subprocess.TimeoutExpired:
            _die({"error": f"redis-cli timed out after {self._timeout + 5}s."})

        stderr = proc.stderr.strip()
        if proc.returncode != 0:
            _die({"error": stderr or f"redis-cli exited {proc.returncode}"})

        stdout = proc.stdout.strip()
        if stderr and not stdout:
            _die({"error": stderr})
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except ValueError:
            if re.match(r"^(ERR|WRONGTYPE|NOAUTH|NOPERM|MOVED|CROSSSLOT)\b", stdout):
                _die({"error": stdout})
            return stdout


def _connect(alias, args):
    """Build a client for `alias`, choosing the best available backend."""
    url, options = _resolve_alias(alias)
    conn = _split_url(
        url, db_override=getattr(args, "db", None), db_default=options.get("db")
    )
    insecure = getattr(args, "insecure", False) or options.get("tls") == "skip"
    timeout = getattr(args, "timeout", 30)

    backend = getattr(args, "backend", "auto")

    have_redis_py = True
    try:
        import redis  # noqa: F401
    except ImportError:
        have_redis_py = False
    have_redis_cli = shutil.which("redis-cli") is not None

    if backend == "redis-py" or (backend == "auto" and have_redis_py):
        if not have_redis_py:
            _die({"error": "The `redis` Python package is not importable."})
        return _RedisPyClient(conn, insecure, timeout), conn
    if backend == "redis-cli" or (backend == "auto" and have_redis_cli):
        if not have_redis_cli:
            _die({"error": "redis-cli not found on PATH."})
        return _RedisCliClient(conn, insecure, timeout), conn

    _die(
        {
            "error": "No Redis backend available.",
            "hint": "Install the Python client (`pip install redis`) or the CLI "
            "(`brew install redis`). Do not add redis as a project dependency "
            "just for this tool.",
        }
    )


# -- Reply rendering -------------------------------------------------------


def _text(value):
    """Decode a Redis reply scalar to str, replacing undecodable bytes."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def _truncate(value):
    """Truncate a long scalar, annotating the cut inline."""
    value = _text(value)
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return f"{value[:MAX_VALUE_CHARS]}... [truncated, {len(value)} chars total]"
    return value


def _render(value, limit=MAX_ELEMENTS):
    """Render an arbitrary Redis reply into small, JSON-safe output."""
    if isinstance(value, (bytes, str)):
        return _truncate(value)
    if isinstance(value, dict):
        rendered = {
            _truncate(k): _render(v, limit) for k, v in list(value.items())[:limit]
        }
        if len(value) > limit:
            rendered["__truncated__"] = f"{len(value)} entries total, showing {limit}"
        return rendered
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        rendered = [_render(v, limit) for v in items[:limit]]
        if len(items) > limit:
            rendered.append(f"... [truncated, {len(items)} items total]")
        return rendered
    return value


def _flat_to_dict(reply):
    """Normalize a field/value reply to a dict across both backends.

    redis-py applies response callbacks (HGETALL/CONFIG GET come back as
    dicts); redis-cli --json returns the flat array. Accept either.
    """
    if reply is None:
        return {}
    if isinstance(reply, dict):
        return {_text(k): _text(v) for k, v in reply.items()}
    items = [_text(v) for v in reply]
    return dict(zip(items[::2], items[1::2]))


def _human_bytes(value):
    if not isinstance(value, (int, float)):
        return None
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return None


def _parse_info(reply):
    """Parse an INFO reply into {section: {field: value}} across both backends."""
    if isinstance(reply, dict):
        # redis-py already flattened it; regroup is not possible, so return flat.
        return {"_flat": {_text(k): _text(v) for k, v in reply.items()}}

    sections = {}
    current = "default"
    for line in _text(reply).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current = line.lstrip("# ").strip().lower()
            sections.setdefault(current, {})
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        sections.setdefault(current, {})[key] = value
    return sections


def _info_lookup(sections, key):
    for fields in sections.values():
        if key in fields:
            return fields[key]
    return None


def _keyspace(sections):
    """Return {db0: {keys: N, expires: N}} from a parsed INFO reply."""
    keyspace = {}
    for fields in sections.values():
        for key, value in fields.items():
            if not re.match(r"^db\d+$", key):
                continue
            if isinstance(value, dict):
                # redis-py already parsed the "keys=N,expires=N" payload.
                keyspace[key] = value
                continue
            entry = {}
            for pair in str(value).split(","):
                if "=" in pair:
                    name, raw = pair.split("=", 1)
                    entry[name] = int(raw) if raw.isdigit() else raw
            keyspace[key] = entry
    return keyspace


# -- Subcommands -----------------------------------------------------------


def cmd_list(_args):
    filepath = _find_claude_local_md()
    if not filepath:
        _output({"instances": [], "source": None})
        return
    instances = _parse_instances(filepath)
    _output(
        {
            "instances": [
                {"alias": alias, "options": entry["options"]}
                for alias, entry in instances.items()
            ],
            "source": filepath,
        }
    )


_INFO_SUMMARY_FIELDS = (
    "redis_version",
    "redis_mode",
    "role",
    "uptime_in_days",
    "connected_clients",
    "blocked_clients",
    "used_memory_human",
    "used_memory_peak_human",
    "maxmemory_human",
    "maxmemory_policy",
    "mem_fragmentation_ratio",
    "evicted_keys",
    "expired_keys",
    "keyspace_hits",
    "keyspace_misses",
    "instantaneous_ops_per_sec",
    "total_commands_processed",
    "rejected_connections",
    "connected_slaves",
)


def cmd_info(args):
    client, conn = _connect(args.alias, args)
    reply = (
        client.execute("INFO", args.section) if args.section else client.execute("INFO")
    )
    sections = _parse_info(reply)

    if args.full:
        _output({"db": conn["db"], "sections": _render(sections, limit=500)})
        return
    if args.section:
        wanted = sections.get(args.section.lower()) or sections.get("_flat") or {}
        _output(
            {
                "db": conn["db"],
                "section": args.section,
                "fields": _render(wanted, limit=500),
            }
        )
        return

    summary = {
        field: _info_lookup(sections, field)
        for field in _INFO_SUMMARY_FIELDS
        if _info_lookup(sections, field) is not None
    }
    _output(
        {
            "db": conn["db"],
            "summary": summary,
            "keyspace": _keyspace(sections),
            "note": "Summary only. Use --section <name> or --full for everything.",
        }
    )


def cmd_dbsize(args):
    client, conn = _connect(args.alias, args)
    size = client.execute("DBSIZE")
    sections = _parse_info(client.execute("INFO", "keyspace"))
    _output(
        {
            "db": conn["db"],
            "dbsize": size,
            "keyspace": _keyspace(sections),
        }
    )


def cmd_scan(args):
    client, conn = _connect(args.alias, args)
    limit = max(1, args.limit)

    cursor = "0"
    keys = []
    iterations = 0
    while iterations < SCAN_MAX_ITERATIONS:
        argv = ["SCAN", cursor, "MATCH", args.pattern, "COUNT", str(SCAN_COUNT)]
        if args.type:
            argv += ["TYPE", args.type]
        reply = client.execute(*argv)
        cursor = str(_text(reply[0]))
        keys.extend(_text(k) for k in reply[1])
        iterations += 1
        if cursor == "0" or len(keys) >= limit:
            break

    scan_complete = cursor == "0"
    truncated = len(keys) > limit
    result = {
        "db": conn["db"],
        "pattern": args.pattern,
        "returned": len(keys[:limit]),
        "keys": keys[:limit],
        "truncated": truncated,
        "scan_complete": scan_complete,
        "cursor": cursor,
    }
    if truncated or not scan_complete:
        result["found_so_far"] = len(keys)
        result["note"] = (
            f"Stopped at limit={limit} after {iterations} SCAN batch(es); "
            f"scan_complete={scan_complete} (cursor {cursor}). Narrow "
            "--pattern or raise --limit. The scan is cursor-based, so this "
            "never blocks the server the way KEYS would."
        )
    _output(result)


def _collection(length, field, values, limit):
    """Shape a collection read, flagging explicitly when it was cut short."""
    rendered = _render(values, limit)
    shown = len(rendered) if isinstance(rendered, (list, dict)) else None
    result = {"length": length, field: rendered}
    if isinstance(length, int) and shown is not None and shown < length:
        result["truncated"] = True
        result["note"] = (
            f"Showing {min(shown, limit)} of {length}; raise --limit for more."
        )
    return result


def _read_collection(client, key, key_type, limit):
    """Type-aware read of a key, using SCAN-family commands on large collections.

    HGETALL/SMEMBERS are O(N) and block the server, so anything larger than the
    requested limit is read through HSCAN/SSCAN instead -- safe to run on prod.
    """
    if key_type == "string":
        length = client.execute("STRLEN", key)
        raw = _truncate(client.execute("GET", key))
        result = {"length": length, "value": raw}
        if isinstance(length, int) and isinstance(raw, str) and len(raw) < length:
            result["truncated"] = True
        return result

    if key_type == "list":
        length = client.execute("LLEN", key)
        items = client.execute("LRANGE", key, 0, limit - 1)
        return _collection(length, "items", [_text(i) for i in items], limit)

    if key_type == "hash":
        length = client.execute("HLEN", key)
        if isinstance(length, int) and length > limit:
            reply = client.execute("HSCAN", key, 0, "COUNT", str(limit))
            fields = _flat_to_dict(reply[1])
        else:
            fields = _flat_to_dict(client.execute("HGETALL", key))
        return _collection(length, "fields", fields, limit)

    if key_type == "set":
        length = client.execute("SCARD", key)
        if isinstance(length, int) and length > limit:
            reply = client.execute("SSCAN", key, 0, "COUNT", str(limit))
            members = [_text(m) for m in reply[1]]
        else:
            members = [_text(m) for m in client.execute("SMEMBERS", key)]
        return _collection(length, "members", members, limit)

    if key_type == "zset":
        length = client.execute("ZCARD", key)
        reply = client.execute("ZRANGE", key, 0, limit - 1, "WITHSCORES")
        if reply and isinstance(reply[0], (list, tuple)):
            members = [[_text(m), s] for m, s in reply]
        else:
            flat = [_text(v) for v in reply]
            members = [list(pair) for pair in zip(flat[::2], flat[1::2])]
        return _collection(length, "members", members, limit)

    if key_type == "stream":
        length = client.execute("XLEN", key)
        entries = client.execute("XRANGE", key, "-", "+", "COUNT", str(limit))
        return _collection(length, "entries", entries, limit)

    return {"note": f"No type-aware reader for type {key_type!r}."}


def cmd_get(args):
    client, conn = _connect(args.alias, args)
    key_type = _text(client.execute("TYPE", args.key))
    if isinstance(key_type, dict):  # some clients return {"type": ...}
        key_type = key_type.get("type")
    if key_type in (None, "none"):
        _output({"db": conn["db"], "key": args.key, "exists": False})
        return

    ttl = client.execute("TTL", args.key)
    result = {
        "db": conn["db"],
        "key": args.key,
        "exists": True,
        "type": key_type,
        "ttl_seconds": ttl,
        "expires": isinstance(ttl, int) and ttl >= 0,
    }
    result.update(_read_collection(client, args.key, key_type, max(1, args.limit)))
    _output(result)


def cmd_ttl(args):
    client, conn = _connect(args.alias, args)
    ttl = client.execute("TTL", args.key)
    _output(
        {
            "db": conn["db"],
            "key": args.key,
            "exists": ttl != -2,
            "type": _text(client.execute("TYPE", args.key)),
            "ttl_seconds": ttl,
            "ttl_meaning": {
                -2: "key does not exist",
                -1: "key exists with no expiry",
            }.get(ttl, "seconds until expiry"),
        }
    )


def cmd_memory(args):
    client, conn = _connect(args.alias, args)
    usage = client.execute("MEMORY", "USAGE", args.key)
    if usage is None:
        _output({"db": conn["db"], "key": args.key, "exists": False})
        return
    _output(
        {
            "db": conn["db"],
            "key": args.key,
            "exists": True,
            "type": _text(client.execute("TYPE", args.key)),
            "encoding": _text(client.execute("OBJECT", "ENCODING", args.key)),
            "bytes": usage,
            "human": _human_bytes(usage),
        }
    )


def _normalize_slowlog(reply):
    """Normalize SLOWLOG GET across backends into a list of dicts."""
    entries = []
    for item in reply or []:
        if isinstance(item, dict):
            command = item.get("command")
            entries.append(
                {
                    "id": item.get("id"),
                    "timestamp": item.get("start_time"),
                    "duration_us": item.get("duration"),
                    "command": _truncate(
                        " ".join(_text(c) for c in command)
                        if isinstance(command, (list, tuple))
                        else _text(command)
                    ),
                    "client_addr": _text(item.get("client_address")),
                    "client_name": _text(item.get("client_name")),
                }
            )
            continue
        item = list(item)
        entries.append(
            {
                "id": item[0] if len(item) > 0 else None,
                "timestamp": item[1] if len(item) > 1 else None,
                "duration_us": item[2] if len(item) > 2 else None,
                "command": _truncate(
                    " ".join(_text(c) for c in item[3]) if len(item) > 3 else None
                ),
                "client_addr": _text(item[4]) if len(item) > 4 else None,
                "client_name": _text(item[5]) if len(item) > 5 else None,
            }
        )
    return entries


def cmd_slowlog(args):
    client, _conn = _connect(args.alias, args)
    total = client.execute("SLOWLOG", "LEN")
    reply = client.execute("SLOWLOG", "GET", str(max(1, args.limit)))
    _output(
        {
            "slowlog_len": total,
            "returned": len(reply or []),
            "entries": _normalize_slowlog(reply),
        }
    )


_CLIENT_FIELDS = ("addr", "laddr", "name", "age", "idle", "db", "cmd", "user")


def _normalize_clients(reply):
    """Normalize CLIENT LIST across backends into a list of dicts."""
    if isinstance(reply, (list, tuple)):
        rows = [r if isinstance(r, dict) else {} for r in reply]
    else:
        rows = []
        for line in _text(reply).splitlines():
            line = line.strip()
            if not line:
                continue
            row = {}
            for pair in line.split(" "):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    row[key] = value
            rows.append(row)
    return rows


def cmd_clients(args):
    client, _conn = _connect(args.alias, args)
    rows = _normalize_clients(client.execute("CLIENT", "LIST"))
    limit = max(1, args.limit)

    by_command = {}
    by_db = {}
    for row in rows:
        by_command[row.get("cmd", "?")] = by_command.get(row.get("cmd", "?"), 0) + 1
        by_db[row.get("db", "?")] = by_db.get(row.get("db", "?"), 0) + 1

    result = {
        "connected_clients": len(rows),
        "by_last_command": dict(sorted(by_command.items(), key=lambda kv: -kv[1])),
        "by_db": by_db,
        "clients": [
            {f: _text(row.get(f)) for f in _CLIENT_FIELDS if row.get(f) is not None}
            for row in rows[:limit]
        ],
    }
    if len(rows) > limit:
        result["note"] = f"Showing {limit} of {len(rows)} clients (--limit to raise)."
    _output(result)


def cmd_cmd(args):
    client, conn = _connect(args.alias, args)
    argv = _normalize_command(args.command)
    reply = client.execute(*argv)
    has_subcommand = bool(READ_ONLY_COMMANDS.get(argv[0]))
    _output(
        {
            "db": conn["db"],
            "command": " ".join(argv[: 2 if has_subcommand else 1]),
            "result": _render(reply),
        }
    )


# -- Main ------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Read-only Redis query tool")

    def add_common(p):
        p.add_argument(
            "--db",
            type=int,
            default=None,
            help="Redis db index, overriding the alias URL and its db= option",
        )
        p.add_argument(
            "--no-verify-tls",
            "--insecure",
            dest="insecure",
            action="store_true",
            help="Skip TLS certificate verification (rediss:// only). Prefer "
            "declaring `tls=skip` on the alias in CLAUDE.local.md.",
        )
        p.add_argument(
            "--timeout", type=int, default=30, help="Socket timeout in seconds"
        )
        p.add_argument(
            "--backend",
            choices=("auto", "redis-py", "redis-cli"),
            default="auto",
            help="Force a client backend (default: auto)",
        )

    sub = parser.add_subparsers(dest="command_name", required=True)

    sub.add_parser("list", help="List configured redis aliases")

    p_info = sub.add_parser("info", help="Server INFO (summary by default)")
    p_info.add_argument("alias")
    p_info.add_argument("--section", default=None, help="INFO section (e.g. memory)")
    p_info.add_argument("--full", action="store_true", help="Return every section")
    add_common(p_info)

    p_dbsize = sub.add_parser("dbsize", help="Key count for the db plus keyspace")
    p_dbsize.add_argument("alias")
    add_common(p_dbsize)

    p_scan = sub.add_parser("scan", help="Cursor-based key scan (never blocking KEYS)")
    p_scan.add_argument("alias")
    p_scan.add_argument("pattern", help="Glob pattern, e.g. 'celery-task-meta-*'")
    p_scan.add_argument("--limit", type=int, default=100, help="Max keys (default 100)")
    p_scan.add_argument(
        "--type", default=None, help="Filter by value type (string, hash, list, ...)"
    )
    add_common(p_scan)

    p_get = sub.add_parser("get", help="Type-aware read of a key, with TTL")
    p_get.add_argument("alias")
    p_get.add_argument("key")
    p_get.add_argument(
        "--limit", type=int, default=MAX_ELEMENTS, help="Max collection elements"
    )
    add_common(p_get)

    p_ttl = sub.add_parser("ttl", help="TTL / type / existence for a key")
    p_ttl.add_argument("alias")
    p_ttl.add_argument("key")
    add_common(p_ttl)

    p_memory = sub.add_parser("memory", help="Memory usage and encoding for a key")
    p_memory.add_argument("alias")
    p_memory.add_argument("key")
    add_common(p_memory)

    p_slowlog = sub.add_parser("slowlog", help="Recent slow commands")
    p_slowlog.add_argument("alias")
    p_slowlog.add_argument("--limit", type=int, default=10, help="Entries (default 10)")
    add_common(p_slowlog)

    p_clients = sub.add_parser("clients", help="Connected clients")
    p_clients.add_argument("alias")
    p_clients.add_argument("--limit", type=int, default=25, help="Rows (default 25)")
    add_common(p_clients)

    p_cmd = sub.add_parser("cmd", help="Run an allowlisted read-only command")
    p_cmd.add_argument("alias")
    p_cmd.add_argument(
        "command",
        nargs="+",
        help="Read-only command and its arguments, e.g. LLEN celery. Put a "
        "bare -- before it when an argument starts with a dash "
        "(e.g. -- ZRANGEBYSCORE k -inf +inf).",
    )
    add_common(p_cmd)

    args = parser.parse_args()

    handlers = {
        "list": cmd_list,
        "info": cmd_info,
        "dbsize": cmd_dbsize,
        "scan": cmd_scan,
        "get": cmd_get,
        "ttl": cmd_ttl,
        "memory": cmd_memory,
        "slowlog": cmd_slowlog,
        "clients": cmd_clients,
        "cmd": cmd_cmd,
    }
    handlers[args.command_name](args)


if __name__ == "__main__":
    main()
