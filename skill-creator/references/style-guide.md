# Skill Style Guide

Patterns extracted from well-crafted skills. Use these as defaults when the skill's domain doesn't demand something different.

## Frontmatter Description

The description is the primary trigger mechanism. It must be exhaustive about when to use the skill.

Use YAML `>-` for multi-line descriptions. Include explicit trigger phrases, and define scope boundaries (what to prefer, what to fall back to).

```yaml
---
name: postgres
description: >-
  Read-only PostgreSQL database querying, schema introspection, and query
  planning. Use when the user asks to query a database, look up data, check
  a table, describe a schema, list tables, show indexes, explain a query
  plan, or any other read-only PostgreSQL interaction. Triggers on requests
  like "query the users table", "show me the schema", "what tables are in
  staging", "run this SQL", "describe the orders table", "check the
  database", "how many rows in X", "explain this query".
---
```

Key elements: what it does, trigger phrases covering how users actually ask for it, preference over alternatives, scope boundaries.

## SKILL.md Body Structure

Skills follow a consistent structure:

1. One-line intro stating what the skill does
2. Script invocation block (how to run it)
3. Subcommand reference (what it can do)
4. Domain context (only things Claude cannot know)
5. Guidelines (practical rules and defaults)

No "Overview" preamble, no verbose explanations. Jump straight into content.

Example opening:

```markdown
Read-only access to PostgreSQL databases via `psql`. All connections enforce
`default_transaction_read_only=on` at the server level -- writes and DDL are
rejected by PostgreSQL itself.
```

## Script Design

Skills wrap deterministic operations in a single entry-point script with subcommands. The SKILL.md describes how to invoke the script, not how to do the work manually.

Invocation pattern:

```
python3 ~/.claude/skills/<skill-name>/scripts/<script>.py <subcommand> [args]
```

Script conventions:
- Absolute path to the script (no `cd` required)
- Subcommand as first positional argument
- JSON output to stdout
- Errors to stderr as JSON with an `error` field
- Scripts auto-detect context from environment (cwd, git remote, config files)
- Credentials resolved from config files, never on the command line
- Include a `list` subcommand when the script needs environment/config discovery

## Subcommand Documentation

Use a compact one-paragraph-per-subcommand format. Backtick-wrapped name, double-dash separator, then description with inline flags:

```markdown
`schemas <alias>` -- list non-system schemas.

`tables <alias> [--schema S]` -- list tables in public schema. Use `--schema` to target
a specific schema.

`describe <alias> <table>` -- columns, types, nullability. Accepts schema-qualified names.

`query <alias> <sql>` -- execute a SELECT query. Returns JSON to stdout.

`explain <alias> <sql>` -- show the query plan without executing.
```

For skills with many subcommands, group them under headings (Introspection, Queries, etc.) but keep the same compact format within each group.

## Domain Context Sections

Include sections only for knowledge Claude cannot possess: environment-specific timezones, connection setup mechanics, config file formats. Skip explanations of standard tools or well-known APIs.

Example (config resolution that Claude would not know about):

```markdown
## Connection Setup

The script resolves database aliases from a `pg-databases` fenced code block
in the project's `CLAUDE.local.md`. Credentials never appear on the command
line -- only the alias is passed as an argument.
```

Example (environment-specific timezones):

```markdown
## Timezones

- **Application logs:** US Pacific (UTC-8 PST / UTC-7 PDT).
- **Metrics/raw data:** UTC.

Convert and label timezones explicitly when reporting findings.
```

## Guidelines Section

End with a short list of practical rules covering defaults, safety limits, exploration patterns, and scope boundaries:

```markdown
## Guidelines

- Always add `LIMIT` to queries unless the user explicitly asks for all rows. Default to 100.
- Prefer specific columns over `SELECT *` when the table structure is known.
- When exploring an unfamiliar database, start with `schemas` then `tables` then `describe`.
- For large result sets, suggest the user narrow the query rather than removing the limit.
```

## Output Style Definitions

When the skill produces styled output (not just data), provide concrete examples rather than abstract descriptions. Show real input/output pairs, then list rules after:

```markdown
## Voice and formatting

Match this tone:

investigated the flaky test in CI -- was a race condition in the cache layer
pushed two API endpoints for the billing integration
working on search indexing, should wrap up tomorrow

Rules:
- Plain text, no markdown formatting
- One line per topic, separated by newlines
- Present continuous for ongoing work, past tense for completed
- Keep it concise
```

## Decision/Triage Workflows

When a skill must classify items and act differently per category, define explicit categories with criteria and a tiebreaker rule:

```markdown
For each item, classify into one of three actions:

**Implement** when the comment is:
- A clear, unambiguous fix: bug, typo, missing import
- A code style correction aligned with project conventions

**Dismiss** (with concise explanation) when:
- A stylistic preference not backed by conventions
- Factually incorrect about what the code does

**Ask** (escalate to user) when:
- The suggestion is ambiguous or has multiple valid interpretations
- It requires domain knowledge to evaluate

When in doubt between Implement and Ask, prefer Ask.
```

Present results grouped by action taken.
