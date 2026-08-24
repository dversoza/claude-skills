# Provider: Jira

Primary interface is the Atlassian CLI (`acli`). A browser-session REST fallback
covers the case where acli is missing or unauthenticated.

## Check access

    which acli && acli jira auth status

Authenticated output prints `Site`, `Email` and `Authentication Type: api_token`.
If acli is installed but not authenticated, ask the user to run
`acli jira auth login` themselves. It is interactive, so do not attempt it for
them, then re-check status.

Find the project key with `acli jira project list --paginate --json`. The flag
group `[recent limit paginate]` is mandatory; a bare `project list` errors.

## Extract

    python3 scripts/extract_jira.py --project VD --workdir "$WORKDIR" --rich open

Add `--max-rich N` to bound a first pass on a large backlog. Skipped keys are
printed, never silently dropped. `--site` overrides the site that the script
otherwise reads from `acli jira auth status`.

Gotchas the script already handles, listed because they bite anyone calling acli
directly:

The `search` field allowlist rejects `parent`, `created`, `updated`,
`resolution` and `resolutiondate`. Allowed: `key,status,summary,issuetype,
assignee,priority,labels`. Anything else fails the whole call.

`view` defaults to `key,issuetype,summary,status,assignee,description` and
therefore returns **no comments**. Comments need `--fields "*all"`, which also
brings back `parent`, `resolution`, `resolutiondate` and `attachment`.

Because `search` cannot return `parent`, epic children need one search per epic
(`--jql "parent = VD-20"`). A single `parent in (...)` query returns every child
but no way to attribute each one to its epic. Budget about 1.5s per epic.

Descriptions and comments are Atlassian Document Format, not markdown or text.
The script flattens ADF, including mentions and inline cards.

`Canceled` normally sits in the `Done` status *category*, so `statusCat != Done`
correctly treats cancelled work as closed. The report still needs to separate
shipped from abandoned, which is why `meta.json` carries `doneStatuses` and
`cancelledStatuses` separately. Spelling varies by project (`Canceled` vs
`Cancelled`); the script reports what the project actually uses.

## Git evidence

Jira keys appear in commit subjects, branch names and PR titles as a matter of
habit, so `keyPattern` matching is reliable here. Teams that reference PR
numbers instead still need the keyword search described in the engine.

## Write back

Only after the user approves a batch. Prefer `--generate-json` then `--from-json`
for anything with a long body, to dodge shell escaping. Add `--yes` only once the
user has confirmed that specific batch.

    acli jira workitem transition --key "VD-416,VD-418" --status "Done" --yes
    acli jira workitem transition --key "VD-77" --status "Canceled" --yes
    acli jira workitem comment create --key "VD-123" --body-file "$WORKDIR/out/comment_123.txt"
    acli jira workitem edit --key "VD-1" --summary "Clarified: paginate /reports export" --yes
    acli jira workitem create --project VD --type Task --summary "Add retry to export job" --label grooming

`transition` and `comment create` accept comma-separated keys or `--jql`, so a
whole approved batch is one call. Re-run the extraction index afterwards to
confirm the changes landed.

## Fallback: browser-session extraction

Use only when acli is unavailable, or for bulk rich extraction when per-ticket
`view` calls are too slow. Jira Cloud's REST API is same-origin with a logged-in
Jira tab, so `fetch()` from that tab is authenticated by session cookie and needs
no token.

Load the browser tools in one call:

    ToolSearch: select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__tabs_create_mcp

Then `tabs_context_mcp` (createIfEmpty: true), `navigate` to any page on the Jira
origin such as `${BASE}/jira/software/projects/${PROJECT}/boards`, probe
`/rest/api/3/myself` to confirm auth, discover custom field ids from
`/rest/api/3/field`, and run `references/extraction.js`.

The dataset is too large to return through a browser tool, so the script
assembles it in the page and triggers a Blob download. Chrome allows **one**
automatic download per page load and silently suppresses a second, so do all
fetching, assembly and the download in a single `javascript_tool` call. If a
download already fired on that page, `navigate` to reload it first.

Stage the result into the canonical layout:

    mkdir -p "$WORKDIR"/data/{issues,epics}
    mv ~/Downloads/backlog_dump.json "$WORKDIR/data/backlog_dump.json"
    jq -c '.issues[]' "$WORKDIR/data/backlog_dump.json" | while read -r r; do k=$(jq -r .key <<<"$r"); jq . <<<"$r" > "$WORKDIR/data/issues/$k.json"; done
    jq -c '.epics[]'  "$WORKDIR/data/backlog_dump.json" | while read -r r; do k=$(jq -r .key <<<"$r"); jq . <<<"$r" > "$WORKDIR/data/epics/$k.json"; done
    jq '.allIndex' "$WORKDIR/data/backlog_dump.json" > "$WORKDIR/data/all_index.json"

The fallback predates the canonical contract and computes `childStats` itself.
Check its output against references/dataset.md and write `meta.json` by hand.
