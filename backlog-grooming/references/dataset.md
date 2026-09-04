# Canonical dataset

Every provider adapter writes the same files into `$WORKDIR/data/`. The analysis
steps read only these files and never call a provider API directly. Adding a
provider means writing an adapter that satisfies this contract; nothing
downstream changes.

    $WORKDIR/data/
    ├── meta.json           provider, project, key pattern, counts
    ├── all_index.json      one lightweight record per work item
    ├── issues/<key>.json   rich record per non-epic item
    └── epics/<key>.json    rich record + childStats per epic

## meta.json

    {
      "provider": "jira",
      "project": "VD",
      "projectName": "Venture Database",
      "extractedAt": "2026-08-24T18:30:00Z",
      "keyPattern": "VD-[0-9]+",
      "urlPattern": "browse/VD-[0-9]+",
      "statusVocabulary": ["Blocked", "Canceled", "Done", "In Progress", "QA", "To Do"],
      "doneStatuses": ["Done"],
      "cancelledStatuses": ["Canceled"],
      "counts": {"total": 898, "open": 65, "epics": 51, "rich": 110}
    }

`keyPattern` and `urlPattern` are regexes the evidence step greps for in git
history and PR metadata. They are the only provider-specific input the
engine needs. `doneStatuses` and `cancelledStatuses` let the engine classify
without hardcoding a vocabulary; Jira spells it `Canceled` with one l in some
projects and `Cancelled` in others, so the adapter reports what it observed
rather than assuming.

## all_index.json

An array. One record per work item in the whole project, including closed ones:
duplicate and superseding detection needs items the open-only view would hide.

    [
      {
        "key": "VD-1",
        "type": "Story",
        "status": "Done",
        "statusCat": "Done",
        "assignee": "Jorge Escobar",
        "summary": "AWS RDS infrastructure setup",
        "priority": "Medium",
        "labels": []
      }
    ]

`statusCat` is the normalized bucket and must be one of `To Do`, `In Progress`,
`Done`. `status` is the provider's own label, preserved verbatim for the report.
`assignee` is a display name or null.

## issues/<key>.json and epics/<key>.json

Every `all_index.json` field, plus:

    {
      "description": "plain text, provider markup already flattened",
      "comments": [
        {"author": "Daniel Versoza", "created": "2025-08-25T10:55:35-0400", "body": "..."}
      ],
      "attachments": ["design.pdf"],
      "parent": "VD-518",
      "created": "2025-03-02T09:14:00Z",
      "updated": "2025-08-25T10:55:35Z",
      "resolution": null,
      "resolutionDate": null,
      "url": "https://ec-data-products.atlassian.net/browse/VD-59"
    }

`description` must be plain text. Adapters flatten provider markup themselves
(Jira ADF, Asana HTML) so analysts never parse markup. Comments are in
chronological order and exclude system events: the real decision often lives in
the last human comment, and audit noise buries it.

Epic files add:

    {
      "children": ["VD-60", "VD-61"],
      "childStats": {"total": 2, "done": 1, "cancelled": 0, "open": 1}
    }

An epic whose meaningful children are all done or cancelled is a close
candidate. An epic with `total: 0` is a placeholder and a cancel candidate.

## Which items get rich records

Rich extraction is the expensive part, so adapters default to the items the
analysis actually reads: every item whose `statusCat` is not `Done`, plus every
epic regardless of status. Closed non-epic items stay in `all_index.json` only.
Pass `--rich all` to fetch everything when a backlog is small enough to afford
it.
