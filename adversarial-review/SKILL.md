---
name: adversarial-review
description: Adversarially review a branch or PR diff in any repository — independent reviewers find defects, skeptics try to refute each one, and only what survives is reported. Use before merging a PR, and whenever asked to review a diff, check a branch, or sanity-check work before it lands.
---

# Adversarial review of a diff

Reviewers find defects; separate skeptics then try to kill each one; only findings that survive
get reported.

The refutation step is the point. Without it a review returns a long list of plausible-sounding
items, most of them wrong, and the reader learns to skim — which is worse than no review, because
it costs attention and buys false confidence. With it, the output is short enough to act on.

Repository-agnostic. Anything specific to one project's business rules belongs in that project's
own instructions, not here; pass it per-run via `focus`.

## Which tier

`workflows/pr-review-quick.js` — the default, for any PR. Three lenses, one refuter per finding.

`workflows/pr-review-deep.js` — six lenses, three-way refutation per finding (majority rules, so
one contrarian cannot sink a real finding and one credulous reader cannot save a bad one), plus a
critic that asks what the fixed lenses structurally could not see. Several times the cost.

Reach for deep when the diff touches any of:

- data correctness, or anything downstream systems trust
- money, billing, auth, permissions, or personal data
- a database migration, or code that writes to a shared datastore
- secrets, credentials, logging or an error tracker
- concurrency, retries, or anything that sends messages or email
- a shared helper with many callers, or a signature other code depends on
- a large or long-lived branch, or one that took a messy rebase

When unsure, run deep. A missed data-correctness bug is not visible until someone notices the
numbers are wrong weeks later.

## Running it

```
Workflow({scriptPath: "~/.claude/skills/adversarial-review/workflows/pr-review-quick.js",
          args: {repo:  "<absolute path to the checkout under review>",
                 base:  "origin/main",
                 scope: "<one line: what the PR is meant to do>",
                 focus: "<optional: project-specific concerns>"}})
```

Address the script by `scriptPath`. `name:` resolves workflow names relative to the session's root
checkout, so it will not find a globally-installed skill.

`scope` does double duty and is worth writing properly. Reviewers given the author's intent catch
mismatches between what the diff claims and what it does; reviewers left to infer it mostly
re-describe the diff back at you. It is also the tripwire for reviewing the wrong tree.

`focus` is where project knowledge goes: "this service is on the payment path", "every timestamp
here is UTC", "this repo targets Python 3.14, `except A, B:` is valid".

## Give it a quiescent checkout

Point `repo` at a checkout that will not move while the review runs, and that you are not working
in. A dedicated worktree is the reliable way to get one:

```
git fetch origin <branch> && git worktree add /tmp/review-<n> origin/<branch>
```

Two ways this goes wrong, both observed:

Reviewers default to the session's working directory rather than `repo`. If that is another branch
— worse, one with uncommitted work — they will review it instead and return confident,
well-evidenced findings about code that is not under review. The scripts scope every command with
`git -C` and treat the path as a hard boundary, and reviewers are told to stop if the diff looks
unrelated to `scope`. An accurate `scope` is what makes that tripwire fire.

Checking out a different branch inside `repo` *while the review runs* moves the target underneath
it. Reviewers take minutes; leave that checkout alone until it finishes.

Fetch `base` first. A stale base produces confident findings about conflicts and regressions that
do not exist.

## Reading the result

`confirmed` is what survived refutation. `refuted` is titles only, so you can sanity-check that
the reviewers were looking at the right thing.

Treat `confirmed` as a strong signal, not a verdict. Refuters are told to default to "refuted"
when uncertain, which trades false alarms for occasionally dismissing something real — so a
refuted title that names something you already suspected is worth a second look. Judge the
findings yourself; these panels have been wrong in both directions, proposing a fix that did not
work and dismissing a real design gap as out of scope.

Zero confirmed findings is a normal, common outcome. It means the diff survived, not that the
review failed.

## After it runs

Fix what is real, and say plainly which findings you rejected and why — a dismissed finding with
no reason reads as one that was never read.

If a finding is real but out of scope, say so and leave a follow-up rather than quietly dropping
it.

Re-run the tier after a substantive fix. Fixes introduce bugs: one correction here traded a false
negative for a false positive, and only re-running against a real input caught it.
