---
name: backlog-grooming
description: >-
  Groom a project's backlog end-to-end by analyzing every open ticket against the
  actual codebase and git history, across Jira or Asana. Use when the user asks to
  groom / triage / clean up / prioritize a backlog, find low-hanging fruit, detect
  tickets that are already done but still open, assess epics for closure, or
  propose new tickets. A provider adapter extracts the backlog into a canonical
  dataset, then provider-neutral analysis cross-references code and merged PRs and
  produces a prioritized, propose-only grooming report. It NEVER mutates the
  tracker unless the user explicitly approves a separate execution pass.
---

# Backlog Grooming

Act as a Technical Product Manager. Read the whole backlog, work out the *real*
status of each ticket from the code and git history rather than the ticket text,
then propose actions.

This skill is **propose-only**. Gather, analyze, recommend. Change nothing in the
tracker until the user has reviewed the proposals and explicitly asked for an
execution pass.

Extraction is provider-specific and everything after it is not. An adapter writes
the canonical dataset described in `references/dataset.md`; the analysis reads
only those files. Adding a tracker means adding an adapter, not touching the
engine.

## Step 1 — Resolve the provider and parameters

Supported providers: `jira` (`providers/jira.md`), `asana`
(`providers/asana.md`).

Infer the provider rather than asking, when the evidence is clear: an
authenticated `acli` plus a project key means Jira; an Asana URL or gid in the
request means Asana. If the user has both and gave no hint, ask which tracker.

Then resolve:

- `PROJECT` — project key (`VD`) or Asana project gid.
- `WORKDIR` — a persistent folder for captured data and outputs, e.g.
  `~/code/<project>-backlog-grooming`. Prefer a real folder over `/tmp` so the
  artifacts survive the session.
- `REPO` — the local checkout the backlog is about. Without it there is no
  evidence step and no real-status calls, only a tidy-up of ticket text. If the
  backlog does not correspond to a repo, say so before starting.

Read the provider doc before running anything. Each one lists gotchas that
silently corrupt the dataset.

## Step 2 — Extract

Run the provider's extraction script (see its doc for flags), then sanity-check
the result before spending analysis on it:

    jq '.counts' "$WORKDIR/data/meta.json"
    jq 'length' "$WORKDIR/data/all_index.json"
    ls "$WORKDIR"/data/issues | wc -l
    ls "$WORKDIR"/data/epics | wc -l

Stop and investigate if the totals look truncated, if `richSkipped` is non-zero
when you did not bound the run, or if `statusVocabulary` does not read like
workflow states.

## Step 3 — Build git and PR evidence

Merged work that references a still-open ticket is the strongest signal that the
ticket is already done. Collect the evidence once so analysts can grep it
cheaply. `keyPattern` and `urlPattern` come from `meta.json`, so this step is the
same for every provider:

    cd "$REPO"
    KEYPAT=$(jq -r '.keyPattern // empty' "$WORKDIR/data/meta.json")
    URLPAT=$(jq -r '.urlPattern // empty' "$WORKDIR/data/meta.json")
    git log --all --date=short --pretty=format:'%ad|%h|%s' > "$WORKDIR/data/git_log_all.txt"
    gh pr list --state all --limit 1000 --json number,title,state,mergedAt,headRefName,body \
      --jq '.[]|[.number,.state,(.mergedAt//"-"),.headRefName,.title]|@tsv' > "$WORKDIR/data/prs.tsv"

    [ -n "$KEYPAT" ] && grep -iE "$KEYPAT" "$WORKDIR/data/git_log_all.txt" > "$WORKDIR/data/git_refs.txt"
    [ -n "$KEYPAT" ] && grep -iE "$KEYPAT" "$WORKDIR/data/prs.tsv" > "$WORKDIR/data/pr_refs.txt"

Then intersect open tickets with keys that already appear in merged history.
Those are the prime done-but-open candidates:

    comm -12 \
      <(jq -r '.[]|select(.statusCat!="Done")|.key' "$WORKDIR/data/all_index.json" | sort -u) \
      <(cat "$WORKDIR"/data/git_refs.txt "$WORKDIR"/data/pr_refs.txt | grep -ioE "$KEYPAT" | tr a-z A-Z | sort -u)

When `keyPattern` is null the tracker's ids do not appear in commits, so this
intersection is unavailable. Fall back to `urlPattern` against PR bodies and rely
on feature-keyword search. Say plainly in the report that the done-but-open
detector ran degraded.

Many teams reference PR numbers rather than ticket keys, so keep the branch-name
and PR-title greps, and have analysts search the code by feature keyword too, not
only by key.

## Step 4 — Fan out the analysis

Multi-agent orchestration needs explicit user opt-in. If the user asked for a
thorough or multi-agent grooming, use the Workflow tool. Otherwise use a few
Agent calls, or work inline for a small backlog.

Write a shared `AGENT_CONTEXT.md` holding the product summary, repo layout, data
locations, evidence files, and the taxonomy and rubrics below. Split open
non-epic issues into batches of about 6. Per ticket, each analyst:

1. Reads the ticket JSON, description **and every comment**. The real decision
   usually lives in the last human comment: blocked on legal, done in PR,
   descoped.
2. Greps the git and PR evidence for the key.
3. Investigates the code. Greps for the feature's endpoints, components and model
   fields, and READS the files, to decide shipped, partial or absent.
4. Checks `all_index.json` for duplicates and superseding tickets, closed ones
   included.
5. Writes a per-ticket dossier and returns a structured record.

Analyze epics separately from `childStats`. An epic whose meaningful children are
all done or cancelled is a close candidate; one with `total: 0` is a placeholder
and a cancel candidate. Finish with a synthesis agent that clusters by theme,
maps dependency chains, dedupes, ranks priority and low-hanging fruit, and
proposes new tickets.

### Classification taxonomy

`CLOSE_DONE` shipped, cite PR, commit or file · `CLOSE_CANCEL` obsolete,
superseded or placeholder · `MERGE_DUPLICATE` set the canonical key ·
`KEEP_PRIORITIZE` real and actionable soon · `KEEP_BACKLOG` valid but later ·
`NEEDS_INFO` under-specified or blocked on a decision · `SPLIT` too big, propose
the breakdown.

Check the provider doc before proposing `CLOSE_CANCEL`. Not every tracker can
record a cancelled state.

### Rubrics

- `effort` — XS under half a day · S about a day · M two to four days · L one to
  two weeks · XL over two weeks or needs design.
- `value` — high, medium, low.
- `lowHangingFruit` — effort XS or S, **and** value at least medium, **and** not
  blocked.
- `confidence` — how sure the real-status call is, given the evidence found.

Require evidence for "appears done": a file path, PR number or commit hash, not a
guess. Lower confidence when the code and git history cannot confirm it.

## Step 5 — Deliver the report

Produce markdown, plus an Artifact when a shareable view helps. Include an
executive summary with counts; a per-ticket table of key, real status, proposed
action, effort, value, low-hanging fruit and a one-line rationale; consolidated
close-as-done and close-as-cancelled lists with evidence; a low-hanging-fruit
shortlist; a prioritized near-term roadmap; epic actions; suggested new tickets;
and proposed per-ticket comments.

State the provider and any degraded detector up front, so nobody reads an Asana
pass as if it were a Jira one.

End by asking whether the user wants an execution pass.

## Step 6 — Execution pass

**Never write to the tracker until the user approves.** The report is
propose-only.

When the user does ask, work one batch at a time and confirm each batch before
running it, e.g. "close these 8 as Done, go?". Use the write-back commands in the
provider doc. After each batch, re-run the extraction index to confirm the
changes landed, and report what was applied.

## Adding a provider

Write `providers/<name>.md` and `scripts/extract_<name>.py`. The script must emit
exactly the layout in `references/dataset.md`, including `meta.json` with
`keyPattern`, `urlPattern`, `doneStatuses` and `cancelledStatuses`. Nothing in
Steps 3 to 6 should need editing; if it does, the abstraction is leaking and the
dataset contract is the thing to fix.

Set `keyPattern` only when the tracker's ids genuinely appear in commit messages.
Claiming a pattern that is never matched is worse than declaring null, because it
makes the detector look like it ran.
