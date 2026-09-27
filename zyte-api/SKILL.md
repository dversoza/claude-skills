---
name: zyte-api
description: Read-only Zyte API usage, cost, and health reporting via the Stats API. Use when the user asks how many Zyte requests a scraper made, what a run cost, how much of the Zyte spending limit is left, whether Zyte usage looks healthy, which domains consumed credits, or to project the cost of a planned run. Triggers on requests like "how much did that run cost", "check Zyte usage", "are we burning credits", "what's our Zyte spend this month", "estimate the cost of 150k requests", "is the Zyte key live".
---

# Zyte API

Read-only reporting against the Zyte Stats API. Every subcommand issues GET
requests to the stats service, so it cannot spend credits or change account
settings. The one exception is `key-check`, which sends a single request to
httpbin.org to prove the key is live.

## Connection setup

The script reads credentials from a ```zyte-api``` fenced code block in the
project's `CLAUDE.local.md`, searching from the current directory upward.
`CLAUDE.local.md` is untracked, so the key never reaches the repository, and it
is never passed as an argument or printed.

````markdown
```zyte-api
api_key=YOUR_ZYTE_API_KEY
dashboard_api_key=YOUR_DASHBOARD_KEY
org_id=123456
```
````

The two keys are different and are not interchangeable. `api_key` is the
scraping key, the same value the scrapers pass as `ZYTE_API_KEY`, and only
`key-check` uses it. `dashboard_api_key` comes from
https://app.zyte.com/o/settings and is the only key the Stats API accepts -
sending the scraping key there returns 403 with an empty detail, which reads
like a bad `org_id` but is not.

`org_id` is the number in the dashboard URL, `app.zyte.com/o/<id>/`.

## Usage

Spend and request count for the last 7 days:

```bash
python3 ~/.claude/skills/zyte-api/scripts/zyte_stats.py usage
```

One day, broken down by hour, to see a single run:

```bash
python3 ~/.claude/skills/zyte-api/scripts/zyte_stats.py \
  --start 2026-08-18T00:00:00Z --group-by hour usage
```

Which domains consumed the credits:

```bash
python3 ~/.claude/skills/zyte-api/scripts/zyte_stats.py --by-domain usage
```

Project the cost of a planned run from the rate actually being billed:

```bash
python3 ~/.claude/skills/zyte-api/scripts/zyte_stats.py --days 1 estimate 150000
```

Confirm the key works, which distinguishes a suspended account from a blocked
target:

```bash
python3 ~/.claude/skills/zyte-api/scripts/zyte_stats.py key-check
```

## Reading the numbers

Zyte bills only successful responses. Rate limits (429, 503) and unsuccessful
responses (520 bans, 521, 500) are free, so a run with a high ban rate costs
less than its request count suggests. `cost_per_1k_usd` in the output is what
was actually charged, which is the honest figure to project from rather than a
published tier price.

The account's real backstop is the monthly spending limit, not any in-app cap.
The billing cycle is not the calendar month: it runs from the 17th to the 17th
(Aug 17 to Sep 17, 2026). A month-to-date query undercounts the cycle, so start
the window on the 17th:

```bash
python3 ~/.claude/skills/zyte-api/scripts/zyte_stats.py --start 2026-08-17T00:00:00Z usage
```

The limit is a fixed amount set in the dashboard. Zyte emails the account
owner when it is reached and suspends the key until the next cycle starts or
someone raises the limit. When it is reached Zyte suspends the account and
every request returns
`/auth/account-suspended`, which aborts scraper runs immediately. `key-check`
tells those apart: a live key with failing scrapes is a blocked target, a
rejected key is a billing problem.

Requests counted here are HTTP calls to Zyte. The scrapers' own
`zyte_requests` counter increments once per logical request and does not count
internal retries, so it reads about 1 percent lower than this API.

## Rate limit

The stats API allows 20 requests per minute. Batch a window into one call with
`--group-by` rather than looping over days.
